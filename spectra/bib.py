"""D1 — Generador de bibliografía (determinista): art_02 + art_03 + corpus -> art_refs.bib.
Única fuente de verdad para las citas del pipeline."""
from __future__ import annotations

import re
from typing import Any

from .tools import academic
from .util import strip_accents


def _field_escape(s: Any) -> str:
    s = str(s or "").replace("\\", "").replace("{", "").replace("}", "")
    return re.sub(r"([&%$#_])", r"\\\1", s)


def misc_entry(key: str, meta: dict, tipo: str = "misc") -> str:
    autores = meta.get("autores") or []
    if isinstance(autores, str):
        autores = [autores]
    fields = {
        "title": "{" + _field_escape(meta.get("titulo") or key) + "}",
        "author": " and ".join("{" + _field_escape(a) + "}" if " " not in str(a) or meta.get("autor_corporativo")
                               else _field_escape(a) for a in autores) or "{Anónimo}",
        "year": meta.get("anio") or "",
        "howpublished": _field_escape(meta.get("venue") or ""),
        "url": meta.get("url") or "",
        "doi": academic.clean_doi(meta.get("doi")) or "",
        "eprint": meta.get("arxiv") or "",
        "note": _field_escape(meta.get("nota") or ""),
        "version": _field_escape(meta.get("version") or ""),
    }
    if meta.get("arxiv"):
        fields["archiveprefix"] = "arXiv"
    body = ",\n".join(f"  {k} = {{{v}}}" if not str(v).startswith("{") else f"  {k} = {v}"
                      for k, v in fields.items() if v)
    return f"@{tipo}{{{key},\n{body}\n}}"


def rekey(bibtex: str, key: str) -> str:
    return re.sub(r"^@(\w+)\s*\{\s*[^,]+,", lambda m: f"@{m.group(1)}{{{key},", bibtex.strip(), count=1)


def parse_marco_refs(md: str) -> list[dict]:
    """Sección 'Referencias usadas' de art_02: '- clave: x · doi: y · archivo: z'."""
    out = []
    m = re.search(r"^#+\s*Referencias usadas\s*$(.*?)(?=^#+\s|\Z)", md, re.M | re.S | re.I)
    if not m:
        return out
    for line in m.group(1).splitlines():
        if "clave" not in line:
            continue
        d = {}
        for part in re.split(r"\s*[·|;]\s*", line.strip("-* \t")):
            if ":" in part:
                k, v = part.split(":", 1)
                d[strip_accents(k.strip().lower())] = v.strip().strip("`")
        if d.get("clave"):
            out.append(d)
    return out


def build_bib(art03: dict | None, marco_md: str | None, corpus_docs: dict, extra: list[dict] | None = None,
              online: bool = True) -> tuple[str, list[dict], list[str]]:
    """Devuelve (bib_text, informe, sin_resolver)."""
    entries: dict[str, str] = {}
    informe: list[dict] = []
    sin_resolver: list[str] = []
    seen_doi: dict[str, str] = {}

    def add(key: str, meta: dict, origen: str):
        if not key or key in entries:
            return
        doi = academic.clean_doi(meta.get("doi"))
        if doi and doi in seen_doi:
            informe.append({"clave": key, "accion": "duplicado", "de": seen_doi[doi]})
            return
        bt = None
        if doi and online:
            bt = academic.crossref_bibtex(doi)
        if bt:
            entries[key] = rekey(bt, key)
            informe.append({"clave": key, "origen": origen, "fuente": "crossref", "doi": doi})
        elif meta.get("titulo"):
            tipo = meta.get("tipo_bib") or ("software" if meta.get("tipo") == "software" else "misc")
            entries[key] = misc_entry(key, meta, tipo)
            informe.append({"clave": key, "origen": origen, "fuente": "metadatos" + (" (arxiv)" if meta.get("arxiv") else "")})
        else:
            sin_resolver.append(key)
        if doi:
            seen_doi[doi] = key

    for a in (art03 or {}).get("articulos", []):
        ident = a.get("id") or {}
        add(a.get("clave_bib"), {"titulo": a.get("titulo"), "autores": a.get("autores"), "anio": a.get("anio"),
                                 "venue": a.get("venue"), "doi": ident.get("doi"), "arxiv": ident.get("arxiv"),
                                 "url": ident.get("url")}, "art_03")
    for r in parse_marco_refs(marco_md or ""):
        k = r["clave"]
        doc = corpus_docs.get(k, {})
        add(k, {**doc, "doi": r.get("doi") or doc.get("doi")}, "art_02")
    for k, doc in corpus_docs.items():
        if doc.get("tipo") in ("documento_proyecto", "software"):
            add(k, doc, "corpus")
    for e in extra or []:
        add(e["clave"], e, "config")
    bib = "% art_refs.bib — generado por SPECTRA D1 (no editar a mano; regenerar con build_bib)\n\n" + "\n\n".join(entries.values()) + "\n"
    return bib, informe, sin_resolver


# --- lectura de .bib para el compilador interno ----------------------------------------
def parse_bib(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    i = 0
    while True:
        m = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,").search(text, i)
        if not m:
            break
        typ, key = m.group(1).lower(), m.group(2)
        j, depth = m.end(), 1
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[m.end():j - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            k = fm.group(1).lower()
            p = fm.end()
            if p < len(body) and body[p] == "{":
                d, q = 1, p + 1
                while q < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[q], 0)
                    q += 1
                v = body[p + 1:q - 1]
            elif p < len(body) and body[p] == '"':
                q = body.find('"', p + 1)
                v = body[p + 1:q]
            else:
                q = re.search(r"[,}\n]|$", body[p:]).start() + p
                v = body[p:q]
            if k not in fields:
                fields[k] = re.sub(r"\s+", " ", v.replace("{", "").replace("}", "")).strip()
        fields["_tipo"] = typ
        out[key] = fields
        i = j
    return out


def _authors(f: dict) -> list[str]:
    a = f.get("author", "")
    return [x.strip() for x in re.split(r"\s+and\s+", a) if x.strip()] if a else []


def _surname(name: str) -> str:
    return name.split(",")[0].strip() if "," in name else (name.split()[-1] if name.split() else name)


def cite_label(f: dict) -> str:
    au = _authors(f)
    y = f.get("year", "s. f.")
    if not au:
        return f"{f.get('title', '?')[:30]}, {y}"
    if len(au) == 1:
        return f"{_surname(au[0])}, {y}"
    if len(au) == 2:
        return f"{_surname(au[0])} y {_surname(au[1])}, {y}"
    return f"{_surname(au[0])} et al., {y}"


def format_ref(f: dict, estilo: str = "apa") -> str:
    au = _authors(f)
    def apa_name(n):
        if "," in n:
            sur, given = n.split(",", 1)
        else:
            parts = n.split()
            if len(parts) == 1:
                return n
            sur, given = parts[-1], " ".join(parts[:-1])
        ini = " ".join(p[0] + "." for p in given.split() if p)
        return f"{sur.strip()}, {ini}".strip(", ")
    names = [apa_name(a) for a in au]
    if len(names) > 1:
        autores = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        autores = names[0] if names else ""
    venue = f.get("journal") or f.get("booktitle") or f.get("howpublished") or f.get("publisher") or ""
    extra = []
    for k in ("volume", "number", "pages"):
        if f.get(k):
            extra.append({"volume": "", "number": "", "pages": "pp. "}[k] + f[k] if k != "number" else f"({f[k]})")
    doi = f.get("doi")
    link = f"https://doi.org/{doi}" if doi else f.get("url", "")
    if f.get("eprint") and not doi:
        link = link or f"https://arxiv.org/abs/{f['eprint']}"
    s = f"{autores} ({f.get('year', 's. f.')}). {f.get('title', '')}."
    if venue:
        s += f" <i>{venue}</i>" + (", " + ", ".join(extra) if extra else "") + "."
    if f.get("version"):
        s += f" Versión {f['version']}."
    if link:
        s += f' <a href="{link}">{link}</a>'
    return s
