"""D2 (Word): borrador Markdown + art_refs.bib -> art_paper.docx editable con citas vivas de Zotero.

Cada cita [@clave] se convierte en un campo `ADDIN ZOTERO_ITEM CSL_CITATION` y la bibliografía en
`ADDIN ZOTERO_BIBL`, con las preferencias del documento en las propiedades ZOTERO_PREF_n. Si Zotero está
instalado, cada cita se enlaza (por título) con el ítem de tu biblioteca; si no, el campo lleva los datos
CSL incrustados y Zotero los reconoce igual. Requiere Node.js y `npm install --prefix spectra/docx`.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import string
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .bib import cite_label, format_ref, parse_bib
from .compile import CITE_ONE, Citer, find_browser, split_row
from .util import norm_text

DOCX_DIR = Path(__file__).resolve().parent / "docx"
CSL_TYPE = {"article": "article-journal", "inproceedings": "paper-conference", "misc": "document",
            "software": "software", "book": "book", "techreport": "report", "incollection": "chapter"}
SYM = {r"\cdot": "·", r"\le": "≤", r"\ge": "≥", r"\times": "×", r"\theta": "θ", r"\delta": "δ", r"\Delta": "Δ",
       r"\tau": "τ", r"\lambda": "λ", r"\mu": "μ", r"\alpha": "α", r"\beta": "β", r"\sigma": "σ", r"\pi": "π",
       r"\lfloor": "⌊", r"\rfloor": "⌋", r"\lvert": "|", r"\rvert": "|", r"\|": "‖", r"\min": "min", r"\max": "max",
       r"\in": "∈", r"\to": "→", r"\approx": "≈", r"\neq": "≠", r"\pm": "±", r"\infty": "∞", r"\sum": "Σ",
       r"\ldots": "…", r"\left": "", r"\right": ""}
TOKEN = re.compile(r"(?P<cite>\[[^\[\]]*?@[^\[\]]+?\])|(?P<math>\$[^$]+\$)|(?P<code>`[^`]+`)|(?P<bold>\*\*[^*]+\*\*)"
                   r"|(?P<ital>(?<![*\w])\*[^*\n]+\*(?!\*)|(?<![\w])_[^_\n]+_(?![\w]))|(?P<link>\[[^\]]+\]\([^)]+\))")
LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def _rid(n: int = 8) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


# --- Zotero -----------------------------------------------------------------------------
def zotero_db() -> Path | None:
    p = Path(os.path.expanduser("~/Zotero/zotero.sqlite"))
    return p if p.exists() else None


def zotero_lookup(bib: dict, db: Path | None = None) -> tuple[dict, dict]:
    """{clave_bib: {id, uri}} por coincidencia de título con la biblioteca local (copia temporal + WAL, sin bloquear
    Zotero) y la info de la biblioteca."""
    db = db or zotero_db()
    if not db:
        return {}, {}
    try:
        # Copia temporal de la base + su WAL: incluye ítems recién importados con Zotero abierto, sin bloquearlo.
        tmpd = Path(tempfile.mkdtemp(prefix="spectra_zotero_"))
        shutil.copyfile(db, tmpd / "z.sqlite")
        for suf in ("-wal", "-shm"):
            if Path(str(db) + suf).exists():
                shutil.copyfile(str(db) + suf, str(tmpd / "z.sqlite") + suf)
        c = sqlite3.connect(str(tmpd / "z.sqlite"), timeout=5).cursor()
        user_key = c.execute("select value from settings where setting='account' and key='localUserKey'").fetchone()[0]
        version = (c.execute("select value from settings where setting='client' and key='lastVersion'").fetchone() or ["7.0"])[0]
        rows = c.execute("""select i.itemID, i.key, v.value from items i join itemData d on d.itemID=i.itemID
            join fields f on f.fieldID=d.fieldID join itemDataValues v on v.valueID=d.valueID
            left join deletedItems del on del.itemID=i.itemID
            where f.fieldName='title' and i.libraryID=1 and del.itemID is null""").fetchall()
    except Exception:
        return {}, {}
    by_title: dict[str, tuple[int, str]] = {}
    for iid, key, title in rows:          # si hay duplicados, gana el más reciente (itemID mayor)
        by_title[norm_text(title)] = (iid, key)
    out = {}
    for k, f in bib.items():
        hit = by_title.get(norm_text(f.get("title", "")))
        if hit:
            out[k] = {"id": hit[0], "uri": f"http://zotero.org/users/local/{user_key}/items/{hit[1]}"}
    return out, {"version": version}


def csl_item(k: str, f: dict, zid=None) -> dict:
    authors = []
    for a in re.split(r"\s+and\s+", f.get("author", "")) if f.get("author") else []:
        a = a.strip()
        if "," in a:
            fam, giv = [x.strip() for x in a.split(",", 1)]
        else:
            parts = a.split()
            fam, giv = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (a, "")
        authors.append({"family": fam, "given": giv} if giv else {"literal": fam})
    d = {"id": zid if zid is not None else k, "type": CSL_TYPE.get(f.get("_tipo"), "document"),
         "title": f.get("title", ""), "author": authors}
    year = re.match(r"\d{4}", f.get("year", "") or "")
    if year:
        d["issued"] = {"date-parts": [[int(year.group(0))]]}
    cont = f.get("journal") or f.get("booktitle") or f.get("howpublished")
    if cont:
        d["container-title"] = cont
    for s, t in (("doi", "DOI"), ("url", "URL"), ("volume", "volume"), ("number", "issue"), ("pages", "page"),
                 ("publisher", "publisher"), ("version", "version")):
        if f.get(s):
            d[t] = f[s]
    return d


# --- LaTeX en línea -> runs legibles (cursiva, subíndices, superíndices) -------------------------
def latex_runs(s: str) -> list[dict]:
    s = s.replace("{,}", ",").replace(r"\,", " ").replace(r"\ ", " ").replace(r"\_", "_").replace(r"\text{-}", "-")
    s = re.sub(r"\\(mathrm|text|texttt|operatorname|mathit|mathbf)\{([^{}]*)\}", r"\2", s)
    s = re.sub(r"\\hat\{?\\?(\w+)\}?", lambda m: SYM.get("\\" + m.group(1), m.group(1)) + "\u0302", s)
    s = re.sub(r"\\tilde\{?\\?(\w+)\}?", lambda m: SYM.get("\\" + m.group(1), m.group(1)) + "\u0303", s)
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    for k in sorted(SYM, key=len, reverse=True):
        s = re.sub(re.escape(k) + r"(?![A-Za-z])", SYM[k], s)
    runs, i, buf = [], 0, ""
    while i < len(s):
        ch = s[i]
        if ch in "_^":
            if buf:
                runs.append({"text": buf, "italics": True})
                buf = ""
            if i + 1 < len(s) and s[i + 1] == "{":
                j, depth = i + 2, 1
                while j < len(s) and depth:
                    depth += {"{": 1, "}": -1}.get(s[j], 0)
                    j += 1
                sub, i = s[i + 2:j - 1], j
            else:
                sub, i = s[i + 1:i + 2], i + 2
            runs.append({"text": sub.replace("{", "").replace("}", ""), "italics": True,
                         "subScript" if ch == "_" else "superScript": True})
            continue
        if ch not in "{}":
            buf += ch
        i += 1
    if buf:
        runs.append({"text": buf, "italics": True})
    return runs


class _Builder:
    def __init__(self, bib: dict, zmap: dict):
        self.bib, self.zmap = bib, zmap
        self.citer = Citer(bib, "apa")
        self.citations: list[dict] = []

    def citation(self, inner: str) -> int:
        items = []
        for piece in inner.split(";"):
            m = CITE_ONE.search(piece)
            if not m or m.group(1) not in self.bib:
                continue
            k, loc = m.group(1), (m.group(2) or "").strip()
            z = self.zmap.get(k)
            it = {"id": z["id"] if z else k, "itemData": csl_item(k, self.bib[k], z["id"] if z else None)}
            if z:
                it["uris"] = [z["uri"]]
                it["uri"] = [z["uri"]]
            if loc:
                lm = re.match(r"^(pp?\.|p[aá]g\.|cap\.|secc?\.|§)\s*(.+)$", loc, re.I)
                label = "page"
                if lm:
                    label = {"cap.": "chapter", "sec.": "section", "secc.": "section", "§": "section"}.get(lm.group(1).lower(), "page")
                    loc = lm.group(2)
                it["locator"], it["label"] = loc, label
            items.append(it)
        text = self.citer.group(inner)
        code = {"citationID": _rid(), "properties": {"formattedCitation": text, "plainCitation": text, "noteIndex": 0},
                "citationItems": items, "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"}
        self.citations.append({"code": "ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(code, ensure_ascii=False), "text": text})
        return len(self.citations) - 1

    def inline(self, text: str, base: dict | None = None) -> list[dict]:
        base = base or {}
        out, pos = [], 0
        for m in TOKEN.finditer(text):
            if m.start() > pos:
                out.append({**base, "text": text[pos:m.start()]})
            g, v = m.lastgroup, m.group(0)
            if g == "cite":
                out.append({"cite": self.citation(v[1:-1])})
            elif g == "math":
                out += [{**base, **r} for r in latex_runs(v[1:-1])]
            elif g == "code":
                out.append({**base, "text": v[1:-1], "font": "Consolas"})
            elif g == "bold":
                out += self.inline(v[2:-2], {**base, "bold": True})
            elif g == "ital":
                out += self.inline(v[1:-1], {**base, "italics": True})
            elif g == "link":
                out.append({**base, "text": re.match(r"\[([^\]]+)\]", v).group(1)})
            pos = m.end()
        if pos < len(text):
            out.append({**base, "text": text[pos:]})
        return [r for r in out if r.get("text") or "cite" in r]


def _svg_to_png(svg: Path, png: Path) -> bool:
    b = find_browser("auto")
    if not b:
        return False
    w = int(re.search(r'width="(\d+)', svg.read_text(encoding="utf-8")).group(1))
    h = int(re.search(r'height="(\d+)', svg.read_text(encoding="utf-8")).group(1))
    subprocess.run([b, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={w},{h}",
                    f"--screenshot={png.resolve()}", svg.resolve().as_uri()], capture_output=True, timeout=120)
    return png.exists()


def _png_size(p: Path) -> tuple[int, int]:
    b = p.read_bytes()[16:24]
    return int.from_bytes(b[:4], "big"), int.from_bytes(b[4:], "big")


def build_doc_json(md: str, bib_text: str, base_dir: Path, work: Path, zotero: bool = True, idioma: str = "es") -> dict:
    bib = parse_bib(bib_text)
    for f in bib.values():
        if f.get("doi") and not f["doi"].startswith("10."):
            del f["doi"]
    zmap, zinfo = zotero_lookup(bib) if zotero else ({}, {})
    B = _Builder(bib, zmap)
    blocks, lines, i = [], md.splitlines(), 0
    title = ""
    while i < len(lines):
        l = lines[i]
        if not l.strip():
            i += 1
            continue
        if l.startswith("# "):
            title = l[2:].strip()
            blocks.append({"t": "title", "runs": B.inline(title)})
        elif l.startswith("## "):
            blocks.append({"t": "h1", "runs": B.inline(l[3:].strip())})
        elif l.startswith("### "):
            blocks.append({"t": "h2", "runs": B.inline(l[4:].strip())})
        elif m := re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", l.strip()):
            src = (base_dir / m.group(2)).resolve()
            png = src if src.suffix.lower() == ".png" else work / (src.stem + ".png")
            if src.suffix.lower() == ".svg" and src.exists():
                _svg_to_png(src, png)
            if png.exists():
                w, h = _png_size(png)
                blocks.append({"t": "img", "path": str(png), "w": w, "h": h})
            blocks.append({"t": "caption", "runs": B.inline(m.group(1))})
        elif l.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = split_row(l)
            j, rows = i + 2, []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(split_row(lines[j]))
                j += 1
            blocks.append({"t": "table", "head": [B.inline(x) for x in head],
                           "rows": [[B.inline(x) for x in (r + [""] * len(head))[:len(head)]] for r in rows],
                           "lens": [max([len(head[k])] + [len(r[k]) if k < len(r) else 0 for r in rows]) for k in range(len(head))]})
            i = j
            continue
        elif LIST_RE.match(l):
            ordered = bool(re.match(r"^\s*\d+[.)]", l))
            items = []
            while i < len(lines) and (LIST_RE.match(lines[i]) or (lines[i].startswith("  ") and lines[i].strip() and items)):
                if LIST_RE.match(lines[i]):
                    items.append(LIST_RE.sub("", lines[i]).strip())
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            blocks.append({"t": "list", "ordered": ordered, "items": [B.inline(x) for x in items]})
            continue
        elif l.strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                blocks.append({"t": "p", "runs": [{"text": lines[j], "font": "Consolas"}]})
                j += 1
            i = j + 1
            continue
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\||!\[|```)", lines[i]) \
                    and not LIST_RE.match(lines[i]):
                buf.append(lines[i].strip())
                i += 1
            text = " ".join(buf)
            blocks.append({"t": "caption" if re.match(r"^(Tabla|Figura|Table|Figure)\s+\d+\.", text) else "p",
                           "runs": B.inline(text)})
            continue
        i += 1
    refs = []
    for k in sorted(B.citer.order, key=lambda k: cite_label(bib.get(k, {})).lower()):
        if k not in bib:
            continue
        h = re.sub(r"<a [^>]*>(.*?)</a>", r"\1", format_ref(bib[k]))
        runs, pos = [], 0
        for m in re.finditer(r"<i>(.*?)</i>", h):
            runs += [{"text": h[pos:m.start()]}, {"text": m.group(1), "italics": True}]
            pos = m.end()
        runs.append({"text": h[pos:]})
        refs.append([r for r in runs if r["text"]])
    if refs:
        blocks.append({"t": "h1", "runs": [{"text": "References" if idioma.startswith("en") else "Referencias"}]})
        blocks.append({"t": "bibliography", "refs": refs})
    prefs = (f'<data data-version="3" zotero-version="{zinfo.get("version", "7.0")}"><session id="{_rid()}"/>'
             '<style id="http://www.zotero.org/styles/apa" locale="{"en-US" if idioma.startswith("en") else "es-ES"}" hasBibliography="1" bibliographyStyleHasBeenSet="1"/>'
             '<prefs><pref name="fieldType" value="Field"/><pref name="automaticJournalAbbreviations" value="true"/></prefs></data>')
    return {"title": title, "blocks": blocks, "citations": B.citations,
            "prefs": [prefs[j:j + 255] for j in range(0, len(prefs), 255)],
            "bibl_code": 'ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY',
            "zotero_enlazadas": len(zmap), "referencias": len(bib)}


_RUN = re.compile(r"<w:r>(?:(?!</w:r>).)*?§§(CIT\d+|BIBL_BEGIN|BIBL_END)§§(?:(?!</w:r>).)*?</w:r>", re.S)


def inject_fields(base: Path, dst: Path, data: dict) -> int:
    with zipfile.ZipFile(base) as zin:
        xml = zin.read("word/document.xml").decode("utf-8")
        n = [0]

        def fld(kind, rpr=""):
            return f'<w:r>{rpr}<w:fldChar w:fldCharType="{kind}"/></w:r>'

        def instr(code, rpr=""):
            return f'<w:r>{rpr}<w:instrText xml:space="preserve"> {escape(code)} </w:instrText></w:r>'

        def repl(m):
            run, tag = m.group(0), m.group(1)
            rp = re.search(r"<w:rPr>.*?</w:rPr>", run, re.S)
            rpr = rp.group(0) if rp else ""
            if tag.startswith("CIT"):
                c = data["citations"][int(tag[3:])]
                n[0] += 1
                return (fld("begin", rpr) + instr(c["code"], rpr) + fld("separate", rpr) +
                        f'<w:r>{rpr}<w:t xml:space="preserve">{escape(c["text"])}</w:t></w:r>' + fld("end", rpr))
            return fld("begin") + instr(data["bibl_code"]) + fld("separate") if tag == "BIBL_BEGIN" else fld("end")

        xml = _RUN.sub(repl, xml)
        if "§§" in xml:
            raise RuntimeError("quedaron marcadores de cita sin convertir en campos de Zotero")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, xml.encode("utf-8") if item.filename == "word/document.xml" else zin.read(item.filename))
    return n[0]


def export_docx(md: str, bib_text: str, out: Path, base_dir: Path, zotero: bool = True, idioma: str = "es") -> dict:
    node = shutil.which("node")
    if not node:
        return {"error": "Node.js no está instalado: no se genera el .docx"}
    if not (DOCX_DIR / "node_modules" / "docx").exists():
        r = subprocess.run(["npm", "install", "--prefix", str(DOCX_DIR), "--silent"], capture_output=True, text=True,
                           shell=os.name == "nt", timeout=600)
        if r.returncode != 0:
            return {"error": f"npm install falló: {r.stderr[-500:]}"}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        data = build_doc_json(md, bib_text, base_dir, work, zotero, idioma)
        (work / "doc.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([node, str(DOCX_DIR / "build.js"), str(work / "doc.json"), str(work / "base.docx")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return {"error": f"docx-js falló: {r.stderr[-800:]}"}
        tmp_out = work / "final.docx"
        n = inject_fields(work / "base.docx", tmp_out, data)
        try:
            shutil.copyfile(tmp_out, out)
        except PermissionError:          # abierto en Word
            out = out.with_name(out.stem + "_nuevo.docx")
            shutil.copyfile(tmp_out, out)
    return {"docx": str(out), "citas_zotero": n, "enlazadas_biblioteca": data["zotero_enlazadas"],
            "referencias": data["referencias"]}
