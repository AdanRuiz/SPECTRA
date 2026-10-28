"""Almacén de artefactos versionados con sobre común (§5): meta JSON o front-matter YAML."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "3.0"

# id lógico -> extensión. Los artefactos no listados se tratan por su extensión.
ARTIFACTS = {
    "art_01_ideas": "json",
    "art_02_marco": "md",
    "art_03_literatura": "json",
    "art_04_propuesta": "md",
    "art_04_arquitectura.graph": "json",
    "art_05_resultados": "json",
    "art_06_mapa.graph": "json",
    "art_07_borrador": "md",
    "art_08_revision": "json",
    "art_09_asesor": "md",
    "art_10_dictamen": "md",
}

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def split_front_matter(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, text[m.end():]


def content_hash(art_id: str, content: Any) -> str:
    """Hash del contenido sin el propio campo sha256 ni costos (estables entre re-serializaciones)."""
    if isinstance(content, dict):
        c = json.loads(json.dumps(content))
        c.get("meta", {}).pop("sha256", None)
        c.get("meta", {}).pop("costo", None)
        return sha256_bytes(json.dumps(c, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return sha256_bytes(str(content).strip().encode("utf-8"))


class ArtifactStore:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir) / "artifacts"
        self.dir.mkdir(parents=True, exist_ok=True)

    # --- rutas y versiones ----------------------------------------------
    @staticmethod
    def ext(art_id: str) -> str:
        return ARTIFACTS.get(art_id, "md")

    def path_for(self, art_id: str, version: int) -> Path:
        return self.dir / f"{art_id}_v{version}.{self.ext(art_id)}"

    def versions(self, art_id: str) -> list[int]:
        pat = re.compile(rf"^{re.escape(art_id)}_v(\d+)\.{self.ext(art_id)}$")
        vs = [int(m.group(1)) for p in self.dir.iterdir() if (m := pat.match(p.name))]
        return sorted(vs)

    def latest_version(self, art_id: str) -> int | None:
        vs = self.versions(art_id)
        return vs[-1] if vs else None

    def exists(self, art_id: str) -> bool:
        return self.latest_version(art_id) is not None

    def latest_path(self, art_id: str) -> Path | None:
        v = self.latest_version(art_id)
        return self.path_for(art_id, v) if v else None

    # --- lectura ---------------------------------------------------------
    def read(self, art_id: str, version: int | None = None) -> tuple[dict, Any]:
        """Devuelve (meta, contenido). Para JSON el contenido es el dict completo; para MD, el cuerpo."""
        version = version or self.latest_version(art_id)
        if version is None:
            raise FileNotFoundError(f"No existe el artefacto {art_id}")
        p = self.path_for(art_id, version)
        text = p.read_text(encoding="utf-8")
        if self.ext(art_id) == "json":
            data = json.loads(text)
            return data.get("meta", {}), data
        fm, body = split_front_matter(text)
        return fm, body

    def read_text(self, art_id: str, version: int | None = None) -> str:
        version = version or self.latest_version(art_id)
        return self.path_for(art_id, version).read_text(encoding="utf-8")

    def ref(self, art_id: str, version: int | None = None) -> dict:
        version = version or self.latest_version(art_id)
        meta, _ = self.read(art_id, version)
        return {"artifact": art_id, "version": version, "sha256": meta.get("sha256", "")}

    # --- escritura -------------------------------------------------------
    def write(self, art_id: str, content: Any, *, agente: str, proveedor: str, modelo: str,
              version_prompt: str = "", entradas: list[dict] | None = None,
              costo: dict | None = None, extra_meta: dict | None = None) -> tuple[int, Path]:
        """Escribe una nueva versión (nunca sobrescribe)."""
        version = (self.latest_version(art_id) or 0) + 1
        meta = {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": art_id,
            "version": version,
            "generado_por": {"agente": agente, "proveedor": proveedor, "modelo": modelo,
                             "version_prompt": version_prompt},
            "timestamp": now_iso(),
            "entradas": entradas or [],
            "sha256": "",
            "costo": costo or {"tokens_entrada": 0, "tokens_salida": 0, "usd": 0.0},
        }
        if extra_meta:
            meta.update(extra_meta)
        p = self.path_for(art_id, version)
        if self.ext(art_id) == "json":
            if not isinstance(content, dict):
                raise TypeError(f"{art_id} debe ser un objeto JSON")
            content = dict(content)
            old = content.pop("meta", {}) or {}
            for k in ("iteracion", "responde_a"):
                if k in old and k not in meta:
                    meta[k] = old[k]
            content = {"meta": meta, **content}
            meta["sha256"] = content_hash(art_id, content)
            p.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            fm, body = split_front_matter(str(content))
            for k in ("iteracion", "responde_a", "incluye_arquitectura", "preregistro"):
                if k in fm and k not in meta:
                    meta[k] = fm[k]
            body = body.strip() + "\n"
            meta["sha256"] = content_hash(art_id, body)
            p.write_text("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n\n" + body,
                         encoding="utf-8")
        return version, p

    def import_file(self, art_id: str, src: Path, nota: str = "importado") -> tuple[int, Path]:
        src = Path(src)
        text = src.read_text(encoding="utf-8")
        if self.ext(art_id) == "json":
            content: Any = json.loads(text)
        else:
            content = text
        return self.write(art_id, content, agente="humano", proveedor="humano", modelo=nota,
                          version_prompt="", entradas=[{"artifact": str(src), "version": 0,
                                                        "sha256": sha256_bytes(text.encode("utf-8"))}])

    def copy_plain(self, src: Path, name: str) -> Path:
        dst = self.dir / name
        shutil.copyfile(src, dst)
        return dst
