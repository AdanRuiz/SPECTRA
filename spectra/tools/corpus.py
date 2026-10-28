"""Corpus indexado (A2): PDFs del usuario, PDFs open access, documentos del proyecto y fichas
(resúmenes). Recuperación BM25 en Python puro, sin dependencias de embeddings."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import requests

from ..util import make_bib_key, norm_text

TOKEN = re.compile(r"[a-z0-9]{2,}")


def _tok(s: str) -> list[str]:
    return TOKEN.findall(norm_text(s))


class Corpus:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir) / "corpus"
        (self.dir / "pdfs").mkdir(parents=True, exist_ok=True)
        self.index_file = self.dir / "index.json"
        self.chunks_file = self.dir / "chunks.jsonl"
        self.docs: dict[str, dict] = json.loads(self.index_file.read_text(encoding="utf-8")) if self.index_file.exists() else {}
        self.chunks: list[dict] = []
        if self.chunks_file.exists():
            self.chunks = [json.loads(l) for l in self.chunks_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        self._bm25 = None

    def save(self) -> None:
        self.index_file.write_text(json.dumps(self.docs, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.chunks_file.open("w", encoding="utf-8") as f:
            for c in self.chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        self._bm25 = None

    # --- ingesta --------------------------------------------------------
    def _add_doc(self, meta: dict, pages: list[str]) -> str:
        clave = meta.get("clave") or make_bib_key(meta.get("autores"), meta.get("anio"), meta.get("titulo", ""))
        base, i = clave, 1
        while clave in self.docs and self.docs[clave].get("titulo") != meta.get("titulo"):
            i += 1
            clave = f"{base}{chr(96 + i)}"
        meta["clave"] = clave
        self.chunks = [c for c in self.chunks if c["clave"] != clave]
        for pno, text in enumerate(pages, 1):
            for j in range(0, len(text), 2500):
                seg = text[j:j + 2700].strip()
                if len(seg) > 80:
                    self.chunks.append({"clave": clave, "pagina": pno if meta.get("paginado", True) else None, "texto": seg})
        meta["n_paginas"] = len(pages)
        self.docs[clave] = meta
        return clave

    def add_pdf(self, path: Path, meta: dict | None = None) -> str | None:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None
        try:
            pdf = pdfium.PdfDocument(str(path))
            pages = [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
        except Exception:
            return None
        meta = dict(meta or {})
        meta.setdefault("titulo", Path(path).stem)
        meta.update({"archivo": str(path), "tipo": meta.get("tipo", "pdf"), "paginado": True})
        return self._add_doc(meta, pages)

    def add_text(self, text: str, meta: dict, paginado: bool = False, page_chars: int = 4000) -> str:
        if paginado:
            pages = [text[i:i + page_chars] for i in range(0, len(text), page_chars)]
        else:
            pages = [text]
        meta = dict(meta)
        meta["paginado"] = paginado
        return self._add_doc(meta, pages)

    def add_abstract(self, rec: dict) -> str:
        meta = {k: rec.get(k) for k in ("titulo", "autores", "anio", "venue", "doi", "arxiv", "url")}
        meta["tipo"] = "ficha_resumen"
        meta["clave"] = rec.get("clave_sugerida")
        return self._add_doc(meta | {"paginado": False}, [f"{rec.get('titulo', '')}. {rec.get('resumen', '')}"])

    def add_from_record(self, rec: dict, download: bool = True) -> str:
        """Intenta descargar el PDF open access; si no, guarda la ficha con el resumen."""
        if download and rec.get("pdf_oa"):
            try:
                r = requests.get(rec["pdf_oa"], timeout=40, headers={"User-Agent": "SPECTRA/3.0"})
                if r.status_code == 200 and r.content[:4] == b"%PDF" and len(r.content) < 40_000_000:
                    p = self.dir / "pdfs" / f"{rec.get('clave_sugerida', 'doc')}.pdf"
                    p.write_bytes(r.content)
                    meta = {k: rec.get(k) for k in ("titulo", "autores", "anio", "venue", "doi", "arxiv", "url")}
                    meta.update({"clave": rec.get("clave_sugerida"), "licencia": "open access (verificar)"})
                    k = self.add_pdf(p, meta)
                    if k:
                        return k
            except requests.RequestException:
                pass
        return self.add_abstract(rec)

    def ingest_dir(self, d: Path) -> list[str]:
        out = []
        for p in sorted(Path(d).glob("**/*.pdf")):
            if not any(doc.get("archivo") == str(p) for doc in self.docs.values()):
                k = self.add_pdf(p, {"titulo": p.stem, "tipo": "pdf_usuario"})
                if k:
                    out.append(k)
        return out

    # --- búsqueda BM25 ----------------------------------------------------
    def _build(self):
        docs = [_tok(c["texto"]) for c in self.chunks]
        df = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs) or 1
        avg = sum(len(d) for d in docs) / n
        idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._bm25 = (docs, idf, avg)

    def search(self, q: str, k: int = 6) -> list[dict]:
        if not self.chunks:
            return []
        if self._bm25 is None:
            self._build()
        docs, idf, avg = self._bm25
        qt = _tok(q)
        scores = []
        for i, d in enumerate(docs):
            tf = Counter(d)
            s = 0.0
            for t in qt:
                if t in tf:
                    f = tf[t]
                    s += idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(d) / avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        out = []
        for s, i in scores[:k]:
            c = self.chunks[i]
            out.append({"clave": c["clave"], "pagina": c["pagina"], "score": round(s, 2),
                        "titulo": self.docs.get(c["clave"], {}).get("titulo"), "texto": c["texto"][:1800]})
        return out

    def listing(self) -> list[dict]:
        return [{"clave": k, "titulo": v.get("titulo"), "anio": v.get("anio"), "tipo": v.get("tipo"),
                 "doi": v.get("doi"), "paginado": v.get("paginado")} for k, v in self.docs.items()]
