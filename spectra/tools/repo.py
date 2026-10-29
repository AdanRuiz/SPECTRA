"""Lectura de un repositorio existente y ejecución de comandos de evaluación declarados.
Mínimo privilegio: el agente solo puede LEER dentro del repo y EJECUTAR los comandos que el
investigador declaró en `existentes.implementacion.comandos_evaluacion`."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

IGNORAR = {".git", "node_modules", "__pycache__", ".pytest_cache", "build", "dist", ".venv", "venv", ".idea", ".vs"}
TEXTO = {".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".md", ".txt", ".js", ".ts", ".tsx", ".java", ".go", ".rs",
         ".json", ".yaml", ".yml", ".toml", ".cmake", ".sh", ".xml", ".ini", ".cfg", ".rst", ".qml", ".kt", ".cs"}
LENG = {".py": "Python", ".cpp": "C++", ".cc": "C++", ".h": "C/C++ header", ".hpp": "C++ header", ".c": "C",
        ".js": "JavaScript", ".ts": "TypeScript", ".java": "Java", ".go": "Go", ".rs": "Rust", ".md": "Markdown",
        ".sh": "Shell", ".cmake": "CMake", ".cs": "C#", ".kt": "Kotlin", ".qml": "QML"}


class Repo:
    def __init__(self, root: str | Path, comandos: list | None = None, cwd: str | None = None, timeout_s: int = 600,
                 adicionales: list[dict] | None = None, excluir: list[str] | None = None):
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"No existe el repositorio {self.root}")
        self.comandos = [c if isinstance(c, dict) else {"cmd": c} for c in (comandos or [])]
        self.cwd = cwd
        self.timeout = timeout_s
        self.ejecuciones: list[dict] = []
        self.excluir = [e.strip("/").replace("\\", "/") for e in (excluir or [])]
        # repositorios adicionales (p. ej. un fork de Mixxx): solo las rutas declaradas en `incluir`
        self.extra = []
        for a in adicionales or []:
            r = Path(a["repo"]).resolve()
            if r.exists():
                self.extra.append({"alias": a["alias"], "root": r, "incluir": a.get("incluir") or ["."],
                                   "base_diff": a.get("base_diff"), "descripcion": a.get("descripcion", "")})
        self._files = None

    def _walk(self, root: Path, sub: str = "."):
        base = (root / sub).resolve()
        if base.is_file():
            yield base
            return
        for p in base.rglob("*"):
            rel = p.relative_to(root)
            if p.is_file() and not any(part in IGNORAR for part in rel.parts) and not (
                    root == self.root and any(rel.as_posix() == e or rel.as_posix().startswith(e + "/") for e in self.excluir)):
                yield p

    def entries(self) -> list[tuple[str, Path]]:
        """[(ruta_visible, Path)]; los adicionales se muestran como 'alias:ruta'."""
        if self._files is None:
            out = [(p.relative_to(self.root).as_posix(), p) for p in self._walk(self.root)]
            for e in self.extra:
                for inc in e["incluir"]:
                    out += [(f"{e['alias']}:{p.relative_to(e['root']).as_posix()}", p) for p in self._walk(e["root"], inc)]
            self._files = sorted(set(out))
        return self._files

    def files(self) -> list[Path]:
        return [p for r, p in self.entries() if ":" not in r]

    def _safe(self, rel: str) -> Path:
        rel = rel.replace("\\", "/")
        for e in self.extra:
            if rel.startswith(e["alias"] + ":"):
                p = (e["root"] / rel[len(e["alias"]) + 1:]).resolve()
                if any(p == (e["root"] / i).resolve() or (e["root"] / i).resolve() in p.parents for i in e["incluir"]):
                    return p
                raise PermissionError(f"Ruta fuera de las rutas incluidas de {e['alias']}: {e['incluir']}")
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise PermissionError("Ruta fuera del repositorio")
        return p

    def summary(self) -> dict:
        files = self.entries()
        lines_by_lang: Counter = Counter()
        tree = []
        for rel, p in files:
            n = 0
            if p.suffix.lower() in TEXTO:
                try:
                    n = p.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
                except OSError:
                    pass
                lines_by_lang[LENG.get(p.suffix.lower(), p.suffix.lower())] += n
            tree.append(f"{rel} ({n} líneas)" if n else rel)
        tests = [t for t in tree if re.search(r"(^|/)(tests?|spec)/|test_|_test\.|tests?\.", t)]
        return {
            "raiz": str(self.root),
            "n_archivos": len(files),
            "lineas_por_lenguaje": dict(lines_by_lang.most_common()),
            "arbol": tree,
            "archivos_de_prueba": tests,
            "git": self.git_info(),
            "repos_adicionales": [{"alias": e["alias"], "descripcion": e["descripcion"], "incluir": e["incluir"],
                                   "git": self.git_info(e["root"], e.get("base_diff"))} for e in self.extra],
            "comandos_evaluacion_disponibles": [{"indice": i, **c} for i, c in enumerate(self.comandos)],
        }

    def git_info(self, root: Path | None = None, base_diff: str | None = None) -> dict:
        root = root or self.root

        def g(*a):
            try:
                return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=60).stdout.strip()
            except Exception:
                return ""
        info = {"commit": g("rev-parse", "HEAD"), "rama": g("rev-parse", "--abbrev-ref", "HEAD"),
                "remoto": g("remote", "get-url", "origin"),
                "log": g("log", "--format=%h %ad %s", "--date=short", "-40" if root == self.root else "-8"),
                "sucio": bool(g("status", "--porcelain", "--untracked-files=no"))}
        if base_diff:
            info["diff_vs_" + base_diff] = g("diff", "--shortstat", f"{base_diff}...HEAD")
            info["archivos_cambiados"] = g("diff", "--stat=120", f"{base_diff}...HEAD")[-4000:]
        return info

    # --- herramientas expuestas al LLM ---------------------------------------
    def read_file(self, ruta: str, desde: int = 1, hasta: int | None = None, max_chars: int = 30000) -> str:
        p = self._safe(ruta)
        if not p.is_file():
            return f"ERROR: no existe {ruta}"
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        hasta = hasta or len(lines)
        body = "\n".join(f"{i}: {l}" for i, l in enumerate(lines[desde - 1:hasta], desde))
        return body[:max_chars] + (f"\n…[truncado; usa desde/hasta, total {len(lines)} líneas]" if len(body) > max_chars else "")

    def grep(self, patron: str, glob: str = "*", max_res: int = 60) -> str:
        try:
            rx = re.compile(patron, re.I)
        except re.error as e:
            return f"ERROR regex: {e}"
        out = []
        for rel, p in self.entries():
            if p.suffix.lower() not in TEXTO or not fnmatch.fnmatch(rel, glob) and not fnmatch.fnmatch(p.name, glob):
                continue
            try:
                for i, l in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(l):
                        out.append(f"{rel}:{i}: {l.strip()[:200]}")
                        if len(out) >= max_res:
                            return "\n".join(out) + "\n…[más resultados omitidos]"
            except OSError:
                continue
        return "\n".join(out) or "(sin coincidencias)"

    def run_eval(self, indice: int) -> dict:
        if not (0 <= indice < len(self.comandos)):
            return {"error": f"índice inválido; disponibles: 0..{len(self.comandos) - 1}"}
        c = self.comandos[indice]
        cwd = self.root / (c.get("cwd") or self.cwd or "")
        cmd = c["cmd"]
        if cmd.startswith("python "):
            cmd = f'"{sys.executable}" ' + cmd[len("python "):]
        t0 = time.time()
        try:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"}
            r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=c.get("timeout_s", self.timeout), env=env)
            salida, code = (r.stdout + "\n" + r.stderr).strip(), r.returncode
        except subprocess.TimeoutExpired as e:
            salida, code = f"TIMEOUT tras {e.timeout}s", -1
        res = {"indice": indice, "comando": c["cmd"], "descripcion": c.get("descripcion", ""), "cwd": str(cwd),
               "codigo_salida": code, "duracion_s": round(time.time() - t0, 2),
               "salida": salida[-12000:], "sha256_salida": "sha256:" + hashlib.sha256(salida.encode()).hexdigest()}
        self.ejecuciones.append(res)
        return res


def entorno() -> dict:
    return {"python": platform.python_version(), "sistema": f"{platform.system()} {platform.release()}",
            "maquina": platform.machine(), "procesador": platform.processor()}
