"""Utilidades comunes."""
from __future__ import annotations

import importlib
import json
import re
import unicodedata
from typing import Any


def load_object(path: str) -> Any:
    """'paquete.modulo:Nombre' -> objeto."""
    mod, _, name = path.partition(":")
    obj = importlib.import_module(mod)
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_text(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def title_similarity(a: str, b: str) -> float:
    """Similitud de títulos 0..1 (Dice sobre tokens + prefijo)."""
    ta, tb = norm_text(a).split(), norm_text(b).split()
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb))
    na, nb = " ".join(ta), " ".join(tb)
    if na.startswith(nb) or nb.startswith(na):    # subtítulos omitidos
        dice = max(dice, 0.9)
    return dice


STOP = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "el", "la", "los", "las",
        "de", "del", "un", "una", "y", "en", "por", "para", "towards", "toward", "via", "using"}


def make_bib_key(autores: list[str] | None, anio: Any, titulo: str) -> str:
    apellido = "anon"
    if autores:
        a = autores[0]
        if "," in a:
            apellido = a.split(",")[0]
        else:
            apellido = a.split()[-1] if a.split() else "anon"
    apellido = re.sub(r"[^a-z]", "", strip_accents(apellido).lower()) or "anon"
    palabra = next((w for w in norm_text(titulo).split() if w not in STOP and not w.isdigit()), "x")
    return f"{apellido}{anio or 'nd'}{palabra}"


def extract_json(text: str) -> Any:
    """Extrae el primer objeto JSON de una respuesta (con o sin bloque ```json)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    candidates = [m.group(1)] if m else []
    candidates.append(text)
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
        start = c.find("{")
        if start >= 0:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(c)):
                ch = c[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(c[start:i + 1])
                        except json.JSONDecodeError:
                            break
    raise ValueError("La respuesta no contiene un JSON válido")


def extract_fenced(text: str, tag: str) -> tuple[str | None, str]:
    """Extrae un bloque ```json <tag> ... ``` y devuelve (contenido, texto_sin_bloque)."""
    pat = re.compile(rf"```json\s+{re.escape(tag)}\s*\n(.*?)\n```", re.S)
    m = pat.search(text)
    if not m:
        return None, text
    return m.group(1), (text[:m.start()] + text[m.end():]).strip()


def strip_outer_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", t, re.S)
    return m.group(1) if m else t


def truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n…[truncado: {len(s) - n} caracteres]"


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, indent=2)
