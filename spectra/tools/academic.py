"""Búsqueda académica con APIs oficiales: OpenAlex, Crossref, arXiv, Semantic Scholar (§4, A3).
Sin scraping de Google Scholar."""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from ..util import make_bib_key, norm_text, title_similarity

UA = "SPECTRA/3.0 (research pipeline)"
_session = requests.Session()


def _headers(email: str = "") -> dict:
    return {"User-Agent": UA + (f" mailto:{email}" if email else "")}


def _get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 30) -> requests.Response | None:
    for i in range(3):
        try:
            r = _session.get(url, params=params, headers=headers or _headers(), timeout=timeout)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 * (i + 1))
            continue
        return r
    return None


def clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    return d.lower() if d.startswith("10.") else None      # "—", "n/a"... no son DOI


def _abstract_from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = []
    for w, idxs in inv.items():
        for i in idxs:
            pos.append((i, w))
    return " ".join(w for _, w in sorted(pos))


def _rec(**kw) -> dict:
    r = {"titulo": "", "autores": [], "anio": None, "venue": "", "doi": None, "arxiv": None, "url": None,
         "resumen": "", "citas": None, "pdf_oa": None, "fuente": ""}
    r.update(kw)
    r["doi"] = clean_doi(r["doi"])
    r["clave_sugerida"] = make_bib_key(r["autores"], r["anio"], r["titulo"])
    return r


# --- OpenAlex --------------------------------------------------------------
def openalex_search(q: str, n: int = 10, email: str = "", desde: int | None = None) -> list[dict]:
    params = {"search": q, "per-page": min(n, 50)}
    if desde:
        params["filter"] = f"from_publication_date:{desde}-01-01"
    if email:
        params["mailto"] = email
    r = _get("https://api.openalex.org/works", params, _headers(email))
    if not r or r.status_code != 200:
        return []
    out = []
    for w in r.json().get("results", []):
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        arx = None
        for l in w.get("locations") or []:
            u = (l.get("landing_page_url") or "")
            m = re.search(r"arxiv\.org/abs/([\w.\-/]+?)(v\d+)?$", u)
            if m:
                arx = m.group(1)
        out.append(_rec(titulo=w.get("title") or "", anio=w.get("publication_year"),
                        autores=[a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")],
                        venue=src.get("display_name") or "", doi=w.get("doi"), arxiv=arx,
                        url=loc.get("landing_page_url") or w.get("id"),
                        resumen=_abstract_from_inverted(w.get("abstract_inverted_index"))[:1500],
                        citas=w.get("cited_by_count"), pdf_oa=(w.get("open_access") or {}).get("oa_url"),
                        fuente="openalex", tipo=w.get("type")))
    return out


def openalex_by_doi(doi: str, email: str = "") -> dict | None:
    r = _get(f"https://api.openalex.org/works/doi:{clean_doi(doi)}", {"mailto": email} if email else None)
    if not r or r.status_code != 200:
        return None
    w = r.json()
    loc = w.get("primary_location") or {}
    return _rec(titulo=w.get("title") or "", anio=w.get("publication_year"),
                autores=[a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")],
                venue=((loc.get("source") or {}).get("display_name")) or "", doi=w.get("doi"),
                url=loc.get("landing_page_url"), resumen=_abstract_from_inverted(w.get("abstract_inverted_index"))[:1500],
                citas=w.get("cited_by_count"), pdf_oa=(w.get("open_access") or {}).get("oa_url"), fuente="openalex")


# --- Crossref --------------------------------------------------------------
def _crossref_item(it: dict) -> dict:
    autores = []
    for a in it.get("author", []) or []:
        nm = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if nm:
            autores.append(nm)
    anio = None
    for k in ("published-print", "published-online", "issued", "created"):
        dp = (it.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            anio = dp[0][0]
            break
    return _rec(titulo=(it.get("title") or [""])[0], autores=autores, anio=anio,
                venue=(it.get("container-title") or [""])[0] if it.get("container-title") else "",
                doi=it.get("DOI"), url=it.get("URL"), fuente="crossref", tipo=it.get("type"),
                resumen=re.sub(r"<[^>]+>", "", it.get("abstract", ""))[:1500])


def crossref_search(q: str, n: int = 10, email: str = "") -> list[dict]:
    params = {"query.bibliographic": q, "rows": min(n, 40)}
    if email:
        params["mailto"] = email
    r = _get("https://api.crossref.org/works", params, _headers(email))
    if not r or r.status_code != 200:
        return []
    return [_crossref_item(it) for it in r.json().get("message", {}).get("items", [])]


def crossref_by_doi(doi: str, email: str = "") -> dict | None:
    r = _get(f"https://api.crossref.org/works/{clean_doi(doi)}", headers=_headers(email))
    if not r or r.status_code != 200:
        return None
    return _crossref_item(r.json().get("message", {}))


def crossref_bibtex(doi: str) -> str | None:
    r = _get(f"https://api.crossref.org/works/{clean_doi(doi)}/transform/application/x-bibtex")
    if r is not None and r.status_code == 200 and r.text.strip().startswith("@"):
        return r.text.strip()
    r = _get(f"https://doi.org/{clean_doi(doi)}", headers={"Accept": "application/x-bibtex", "User-Agent": UA})
    if r is not None and r.status_code == 200 and r.text.strip().startswith("@"):
        return r.text.strip()
    return None


# --- arXiv -----------------------------------------------------------------
ATOM = "{http://www.w3.org/2005/Atom}"


def _arxiv_parse(xml: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for e in root.findall(f"{ATOM}entry"):
        idu = (e.findtext(f"{ATOM}id") or "")
        m = re.search(r"abs/([\w.\-/]+?)(v\d+)?$", idu)
        doi = None
        for l in e.findall("{http://arxiv.org/schemas/atom}doi"):
            doi = l.text
        pub = e.findtext(f"{ATOM}published") or ""
        out.append(_rec(titulo=" ".join((e.findtext(f"{ATOM}title") or "").split()),
                        autores=[a.findtext(f"{ATOM}name") for a in e.findall(f"{ATOM}author")],
                        anio=int(pub[:4]) if pub[:4].isdigit() else None, venue="arXiv",
                        doi=doi, arxiv=m.group(1) if m else None, url=idu,
                        resumen=" ".join((e.findtext(f"{ATOM}summary") or "").split())[:1500],
                        pdf_oa=idu.replace("/abs/", "/pdf/") if idu else None, fuente="arxiv"))
    return out


def arxiv_search(q: str, n: int = 10) -> list[dict]:
    r = _get("http://export.arxiv.org/api/query", {"search_query": f"all:{q}", "max_results": n})
    return _arxiv_parse(r.text) if r is not None and r.status_code == 200 else []


def arxiv_by_id(aid: str) -> dict | None:
    r = _get("http://export.arxiv.org/api/query", {"id_list": aid})
    res = _arxiv_parse(r.text) if r is not None and r.status_code == 200 else []
    return res[0] if res else None


# --- Semantic Scholar -------------------------------------------------------
def s2_search(q: str, n: int = 10, key_env: str = "S2_API_KEY") -> list[dict]:
    h = _headers()
    if os.environ.get(key_env):
        h["x-api-key"] = os.environ[key_env]
    r = _get("https://api.semanticscholar.org/graph/v1/paper/search",
             {"query": q, "limit": min(n, 50), "fields": "title,authors,year,venue,externalIds,abstract,citationCount,openAccessPdf"}, h)
    if not r or r.status_code != 200:
        return []
    out = []
    for p in r.json().get("data", []) or []:
        ext = p.get("externalIds") or {}
        out.append(_rec(titulo=p.get("title") or "", autores=[a.get("name") for a in p.get("authors") or []],
                        anio=p.get("year"), venue=p.get("venue") or "", doi=ext.get("DOI"), arxiv=ext.get("ArXiv"),
                        url=f"https://www.semanticscholar.org/paper/{p.get('paperId')}",
                        resumen=(p.get("abstract") or "")[:1500], citas=p.get("citationCount"),
                        pdf_oa=(p.get("openAccessPdf") or {}).get("url"), fuente="semantic_scholar"))
    return out


# --- búsqueda combinada y verificación --------------------------------------
def search(q: str, n: int = 10, fuentes: list[str] | None = None, email: str = "", desde: int | None = None) -> list[dict]:
    fuentes = fuentes or ["openalex", "crossref", "arxiv"]
    res: list[dict] = []
    per = max(3, n // max(1, len(fuentes)) + 2)
    for f in fuentes:
        try:
            if f == "openalex":
                res += openalex_search(q, per, email, desde)
            elif f == "crossref":
                res += crossref_search(q, per, email)
            elif f == "arxiv":
                res += arxiv_search(q, per)
            elif f == "semantic_scholar":
                res += s2_search(q, per)
        except Exception:      # una fuente caída no detiene la búsqueda
            continue
    # deduplicar por DOI / arXiv / título
    seen, out = set(), []
    for r in res:
        k = r["doi"] or (r["arxiv"] and "arxiv:" + r["arxiv"]) or norm_text(r["titulo"])[:80]
        if not r["titulo"] or k in seen:
            continue
        seen.add(k)
        if desde and r["anio"] and r["anio"] < desde:
            continue
        out.append(r)
    return out[:n]


def _surname(name: str) -> str:
    if not name:
        return ""
    if "," in name:
        return norm_text(name.split(",")[0])
    parts = norm_text(name).split()
    return parts[-1] if parts else ""


def verify(articulo: dict, email: str = "") -> dict:
    """Verificación determinista (A3): título, primer autor y año contra Crossref/OpenAlex/arXiv."""
    ident = articulo.get("id") or {}
    doi = clean_doi(ident.get("doi") or articulo.get("doi"))
    arx = ident.get("arxiv") or articulo.get("arxiv")
    meta, fuente = None, None
    if doi:
        meta = crossref_by_doi(doi, email)
        fuente = "crossref"
        if not meta:
            meta, fuente = openalex_by_doi(doi, email), "openalex"
    if not meta and arx:
        meta, fuente = arxiv_by_id(arx), "arxiv"
    if not meta:
        # sin identificador: buscar por título en OpenAlex
        cands = openalex_search(articulo.get("titulo", ""), 3, email)
        cands = [c for c in cands if title_similarity(c["titulo"], articulo.get("titulo", "")) >= 0.85]
        if cands:
            meta, fuente = cands[0], "openalex_titulo"
    if not meta:
        return {"fuente": None, "coincide_titulo": False, "coincide_autor": False, "coincide_anio": False,
                "ok": False, "motivo": "identificador no resoluble"}
    t_ok = title_similarity(meta["titulo"], articulo.get("titulo", "")) >= 0.8
    a_given = (articulo.get("autores") or [""])[0]
    a_meta = [_surname(a) for a in meta.get("autores") or []]
    au_ok = bool(a_given) and (_surname(a_given) in a_meta[:3] or not a_meta)
    y_given, y_meta = articulo.get("anio"), meta.get("anio")
    y_ok = (not y_given or not y_meta or abs(int(y_given) - int(y_meta)) <= 1)
    return {"fuente": fuente, "coincide_titulo": t_ok, "coincide_autor": au_ok, "coincide_anio": y_ok,
            "ok": t_ok and au_ok and y_ok, "meta": meta,
            "motivo": "" if (t_ok and au_ok and y_ok) else "metadatos no coinciden"}
