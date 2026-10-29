"""Puertas duras deterministas (§6.1): citas, números, estructura y similitud."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .util import norm_text

# --- bibliografía -----------------------------------------------------------
BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.S)


def bib_keys(bib_text: str) -> set[str]:
    return {m.group(2) for m in BIB_ENTRY.finditer(bib_text) if m.group(1).lower() not in ("comment", "string", "preamble")}


CITE_BRACKET = re.compile(r"\[([^\[\]]*@[^\[\]]*)\]")
CITE_KEY = re.compile(r"(?<![\w.])-?@([A-Za-z0-9_][\w:.#$%&+?<>~/-]*[A-Za-z0-9_])")


def cited_keys(md: str) -> list[tuple[str, int]]:
    """Claves citadas estilo Pandoc ([@a; @b, p. 3] y @a en el texto), con número de línea.
    Ignora correos, bloques de código y código en línea."""
    out = []
    in_code = False
    for ln, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        line = re.sub(r"`[^`]*`", "", line)
        line = re.sub(r"\S+@\S+\.\w+", "", line)      # correos
        for m in CITE_KEY.finditer(line):
            out.append((m.group(1).rstrip(".:"), ln))
    return out


def check_citations(md: str, bib_text: str) -> dict:
    keys = bib_keys(bib_text)
    faltantes = sorted({k for k, _ in cited_keys(md) if k not in keys})
    usadas = sorted({k for k, _ in cited_keys(md) if k in keys})
    return {"ok": not faltantes, "faltantes": faltantes, "usadas": usadas, "n_bib": len(keys)}


# --- números ------------------------------------------------------------------
NUM = re.compile(r"(?<![\w.,])[-+−]?\d+(?:[.,]\d+)*(?:\s?%)?(?![\w])")


def _canon(tok: str) -> str | None:
    t = tok.replace("−", "-").replace(" ", "").rstrip("%").lstrip("+")
    # 1.234,5 / 1,234.5 / 0,962 -> normalizar
    # el punto es decimal salvo en "1.234.567" o "1.234,5" (separador de miles)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3}){2,}(,\d+)?", t) or re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d+", t):
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", t):
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    if t.count(".") > 1:              # versión (0.6.1) o IP: se compara literal
        return t
    try:
        f = float(t)
    except ValueError:
        return None
    return repr(round(f, 10)).rstrip("0").rstrip(".") if "." in repr(round(f, 10)) else str(int(f))


def numbers_in(obj: Any) -> set[str]:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    out = set()
    for m in NUM.finditer(s):
        c = _canon(m.group(0))
        if c is not None:
            out.add(c)
            if c.startswith("-"):
                out.add(c[1:])
    # también fracciones decimales como porcentajes (0.962 -> 96.2)
    extra = set()
    for c in out:
        try:
            f = float(c)
            if 0 < abs(f) < 1:
                extra.add(_canon(f"{f * 100:.10g}"))
        except ValueError:
            pass
    return out | {e for e in extra if e}


def _sections(md: str) -> list[tuple[str, int, str]]:
    """[(titulo_seccion, linea, texto_linea)] para cada línea, con la sección ## vigente."""
    sec, out = "", []
    in_code = False
    for ln, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            in_code = not in_code
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m and not in_code:
            sec = m.group(2).strip()
            continue
        out.append((sec, ln, "" if in_code else line))
    return out


def check_numbers(md: str, allowed_strict: set[str], allowed_all: set[str], cfg_gates: dict,
                  strict_sections: tuple[str, ...] = ("resultado", "resumen", "abstract", "evaluaci", "result")) -> dict:
    """Todo número del texto debe aparecer en los artefactos de entrada; en Resultados/Resumen
    debe aparecer en art_05 (allowed_strict)."""
    max_int = int(cfg_gates.get("enteros_ignorados_max", 12))
    y0, y1 = cfg_gates.get("anios_ignorados", [1900, 2100])
    discrepancias = []
    for sec, ln, line in _sections(md):
        if not line.strip() or line.lstrip().startswith(("|---", "<!--")):
            continue
        clean = re.sub(r"\[[^\]]*@[^\]]*\]", " ", line)           # citas (páginas)
        clean = re.sub(r"`[^`]*`", " ", clean)                     # código en línea
        clean = re.sub(r"\$[^$]*\$", " ", clean)                   # fórmulas
        clean = re.sub(r"https?://\S+|10\.\d{4,}/\S+", " ", clean)  # URLs y DOIs
        clean = re.sub(r"\b\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?\b|\b\d{1,2}:\d{2}(:\d{2})?Z?\b", " ", clean)  # fechas y horas
        clean = re.sub(r"\b[A-Za-z]+[-_]?\d+[\w.-]*", " ", clean)   # H1, Qt5, C++17, art_05, sha256...
        clean = re.sub(r"(§|Tabla|Figura|Table|Figure|Sección|Section|Fig\.)\s*\d+(\.\d+)*", " ", clean, flags=re.I)
        clean = re.sub(r"^\s*(\d+\.|\-|\*)\s+", " ", clean)          # numeración de listas
        for m in NUM.finditer(clean):
            raw = m.group(0)
            c = _canon(raw)
            if c is None:
                continue
            try:
                f = float(c)
                if f.is_integer() and (abs(f) <= max_int or y0 <= f <= y1) and "." not in raw and "," not in raw:
                    continue
            except ValueError:
                pass
            strict = any(s in norm_text(sec) for s in strict_sections)
            pool = allowed_strict if strict else allowed_all
            if c not in pool and c.lstrip("-") not in pool:
                discrepancias.append({"texto": raw.strip(), "linea": ln, "seccion": sec,
                                      "esperado": "valor presente en art_05" if strict else "valor presente en los artefactos"})
    return {"ok": not discrepancias, "discrepancias": discrepancias[:80], "n_discrepancias": len(discrepancias)}


# --- estructura -----------------------------------------------------------------
def headings(md: str) -> list[str]:
    return [m.group(2).strip() for m in re.finditer(r"^(#{1,3})\s+(.+)$", md, re.M)]


def check_structure(md: str, required: list[str]) -> dict:
    hs = [norm_text(h) for h in headings(md)]
    faltan = []
    for r in required:                      # "A|B": vale cualquiera de las alternativas
        alts = [norm_text(x) for x in r.split("|")]
        if not any(nr in h or h in nr for nr in alts for h in hs if h):
            faltan.append(r.split("|")[0])
    return {"ok": not faltan, "faltantes": faltan}


COMPARATIVAS = {
    "Marco Teórico": ("marco teorico", "marco conceptual", "theoretical framework", "theoretical background",
                      "conceptual framework"),
    "Trabajos Relacionados": ("trabajos relacionados", "estado del arte", "related work", "state of the art"),
}


def check_comparative_tables(md: str, required: dict | None = None) -> dict:
    """Cada sección indicada debe terminar con una tabla comparativa (tabla Markdown con ≥ 3 columnas
    y ≥ 2 filas de datos, seguida como mucho de su leyenda)."""
    required = required or COMPARATIVAS
    lines = md.splitlines()
    secs = [(i, norm_text(m.group(2))) for i, l in enumerate(lines) if (m := re.match(r"^(#{1,2})\s+(.+)$", l))]
    faltan = []
    for nombre, alias in required.items():
        idx = next((k for k, (_, h) in enumerate(secs) if any(a in h for a in alias)), None)
        if idx is None:
            continue                          # la ausencia de la sección la reporta check_structure
        start = secs[idx][0] + 1
        end = secs[idx + 1][0] if idx + 1 < len(secs) else len(lines)
        body = [l for l in lines[start:end] if l.strip() and not l.lstrip().startswith("###")]
        trail = 0
        while body and not body[-1].strip().startswith("|") and trail < 2 and len(body[-1]) < 400:
            body.pop()                        # leyenda o nota corta tras la tabla
            trail += 1
        rows = []
        while body and body[-1].strip().startswith("|"):
            rows.insert(0, body.pop())
        ok = len(rows) >= 4 and rows[0].count("|") >= 4 and re.match(r"^\s*\|?\s*:?-{2,}", rows[1] or "")
        if not ok:
            faltan.append(nombre)
    return {"ok": not faltan, "faltantes": faltan}


# --- similitud (n-gramas de palabras) -------------------------------------------------
def _ngrams(text: str, n: int) -> set[tuple]:
    t = norm_text(text).split()
    return {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}


def check_similarity(md: str, sources: dict[str, str], n: int = 8, umbral: float = 0.15) -> dict:
    """Fracción de n-gramas del borrador que aparecen literalmente en cada fuente."""
    body = re.sub(r"```.*?```", " ", md, flags=re.S)
    mine = _ngrams(body, n)
    if not mine:
        return {"ok": True, "valor": 0.0, "fuente_max": None, "detalle": {}}
    det = {}
    for k, txt in sources.items():
        s = _ngrams(txt, n)
        if s:
            det[k] = round(len(mine & s) / len(mine), 4)
    if not det:
        return {"ok": True, "valor": 0.0, "fuente_max": None, "detalle": {}}
    kmax = max(det, key=det.get)
    return {"ok": det[kmax] < umbral, "valor": det[kmax], "fuente_max": kmax,
            "detalle": dict(sorted(det.items(), key=lambda x: -x[1])[:10])}


# --- validación de esquema ----------------------------------------------------------
def validate_json(data: dict, schema_path: Path) -> list[str]:
    import jsonschema
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.message}" for e in sorted(v.iter_errors(data), key=str)][:30]
