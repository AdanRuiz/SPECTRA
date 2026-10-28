"""D2 — Compilador determinista: borrador Markdown + art_refs.bib -> art_paper.{tex,html,pdf}.
Usa Pandoc (+ LaTeX) si está instalado; si no, un motor interno Markdown -> HTML/LaTeX y
el navegador (Edge/Chrome headless) para imprimir el PDF."""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path

from .bib import cite_label, format_ref, parse_bib

CITE_GROUP = re.compile(r"\[([^\[\]]*?@[^\[\]]+?)\]")
CITE_ONE = re.compile(r"-?@([\w:.#$%&+?<>~/-]*\w)(?:\s*,\s*([^;\]]+))?")


# --- citas ---------------------------------------------------------------------
class Citer:
    def __init__(self, bib: dict, estilo: str = "apa"):
        self.bib, self.estilo = bib, estilo
        self.order: list[str] = []
        # APA: mismo autor y año -> 2026a, 2026b... (orden por título)
        groups: dict[str, list[str]] = {}
        for k, f in bib.items():
            groups.setdefault(cite_label(f), []).append(k)
        for lbl, keys in groups.items():
            if len(keys) > 1 and f"{bib[keys[0]].get('year', '')}".strip():
                for i, k in enumerate(sorted(keys, key=lambda k: bib[k].get("title", "").lower())):
                    bib[k]["year"] = f"{bib[k]['year']}{chr(97 + i)}"

    def num(self, k: str) -> int:
        if k not in self.order:
            self.order.append(k)
        return self.order.index(k) + 1

    def group(self, inner: str, latex: bool = False) -> str:
        parts = []
        keys = []
        for piece in inner.split(";"):
            m = CITE_ONE.search(piece)
            if not m:
                continue
            k, loc = m.group(1), (m.group(2) or "").strip()
            keys.append((k, loc))
        if latex:
            out = []
            for k, loc in keys:
                out.append(f"\\parencite[{loc}]{{{k}}}" if loc else f"\\parencite{{{k}}}")
            return "; ".join(out)
        for k, loc in keys:
            f = self.bib.get(k)
            if self.estilo == "ieee":
                parts.append(f"{self.num(k)}" + (f", {loc}" if loc else ""))
            else:
                self.num(k)
                parts.append((cite_label(f) if f else f"¿{k}?") + (f", {loc}" if loc else ""))
        if self.estilo == "ieee":
            return "[" + "; ".join(parts) + "]"
        return "(" + "; ".join(parts) + ")"

    def references_html(self) -> str:
        keys = self.order if self.estilo == "ieee" else sorted(self.order, key=lambda k: cite_label(self.bib.get(k, {})).lower())
        items = []
        for i, k in enumerate(keys, 1):
            f = self.bib.get(k)
            if not f:
                continue
            pref = f"[{i}] " if self.estilo == "ieee" else ""
            items.append(f'<p class="ref" id="ref-{html.escape(k)}">{pref}{format_ref(f)}</p>')
        return "\n".join(items)


# --- Markdown mínimo -> HTML ------------------------------------------------------------
def _inline(s: str, citer: Citer, base: Path) -> str:
    codes: list[str] = []
    maths: list[str] = []
    s = re.sub(r"`([^`]+)`", lambda m: codes.append(m.group(1)) or f"\x00C{len(codes) - 1}\x00", s)
    s = re.sub(r"(\$\$?)(.+?)\1", lambda m: maths.append(m.group(0)) or f"\x00M{len(maths) - 1}\x00", s)
    cites: list[str] = []
    s = CITE_GROUP.sub(lambda m: cites.append(citer.group(m.group(1))) or f"\x00R{len(cites) - 1}\x00", s)
    s = html.escape(s, quote=False)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: _img(m.group(2), m.group(1), base), s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<em>\1</em>", s)
    s = re.sub(r"\x00R(\d+)\x00", lambda m: cites[int(m.group(1))], s)
    s = re.sub(r"\x00M(\d+)\x00", lambda m: html.escape(maths[int(m.group(1))], quote=False), s)
    s = re.sub(r"\x00C(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", s)
    return s


def _img(src: str, alt: str, base: Path) -> str:
    p = (base / src) if not re.match(r"^\w+://", src) else None
    if p is not None and p.suffix.lower() == ".svg" and p.exists():
        svg = p.read_text(encoding="utf-8")
        svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
        return f'<figure>{svg}<figcaption>{alt}</figcaption></figure>'
    if p is not None and p.exists():
        import base64
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(p.suffix.lower().strip("."), "image/png")
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<figure><img src="data:{mime};base64,{data}" alt="{alt}"/><figcaption>{alt}</figcaption></figure>'
    return f'<figure><img src="{src}" alt="{alt}"/><figcaption>{alt}</figcaption></figure>'


def md_to_html_body(md: str, citer: Citer, base: Path) -> str:
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if l.strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        if l.strip().startswith("$$"):
            j = i
            buf = [l]
            if not (l.strip().endswith("$$") and len(l.strip()) > 2):
                j = i + 1
                while j < len(lines) and "$$" not in lines[j]:
                    buf.append(lines[j])
                    j += 1
                if j < len(lines):
                    buf.append(lines[j])
            out.append('<div class="math">' + html.escape("\n".join(buf)) + "</div>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", l)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2), citer, base)}</h{n}>")
            i += 1
            continue
        if l.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = [c.strip() for c in l.strip().strip("|").split("|")]
            j = i + 2
            rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            t = "<table><thead><tr>" + "".join(f"<th>{_inline(c, citer, base)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{_inline(c, citer, base)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(t + "</tbody></table>")
            i = j
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", l):
            ordered = bool(re.match(r"^\s*\d+[.)]", l))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and (re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]) or
                                      (lines[i].startswith("  ") and lines[i].strip() and items)):
                if re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                    items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x, citer, base)}</li>" for x in items) + f"</{tag}>")
            continue
        if l.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> "))
                i += 1
            out.append("<blockquote>" + _inline(" ".join(buf), citer, base) + "</blockquote>")
            continue
        if not l.strip():
            i += 1
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\$\$|\||>|\s*([-*+]|\d+[.)])\s)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + _inline(" ".join(buf), citer, base) + "</p>")
        else:
            out.append("<p>" + _inline(l, citer, base) + "</p>")
            i += 1
    return "\n".join(out)


CSS = """
@page { size: A4; margin: 2.2cm 2cm; }
body { font-family: 'Cambria', 'Georgia', serif; font-size: 11pt; line-height: 1.45; color: #111; max-width: 46em; margin: 2em auto; padding: 0 1em; }
h1 { font-size: 19pt; text-align: center; margin-bottom: .3em; line-height: 1.2; }
h2 { font-size: 13.5pt; margin-top: 1.4em; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin-top: 1.1em; }
p { text-align: justify; hyphens: auto; margin: .45em 0; }
table { border-collapse: collapse; margin: .8em auto; font-size: 9.5pt; }
th, td { border-top: 1px solid #999; border-bottom: 1px solid #999; padding: 3px 7px; text-align: left; vertical-align: top; }
th { background: #f2f2f2; }
code { font-family: Consolas, monospace; font-size: 9pt; background: #f5f5f5; padding: 0 2px; }
pre { background: #f5f5f5; padding: 8px; font-size: 8.5pt; overflow-x: auto; white-space: pre-wrap; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure svg, figure img { max-width: 100%; height: auto; }
figcaption { font-size: 9.5pt; color: #333; margin-top: .3em; }
.math { text-align: center; margin: .6em 0; }
.ref { padding-left: 2em; text-indent: -2em; font-size: 10pt; text-align: left; }
.meta { text-align: center; color: #444; font-size: 10pt; }
"""


def md_to_html(md: str, bib_text: str, estilo: str, base: Path, titulo: str = "") -> str:
    citer = Citer(parse_bib(bib_text), estilo)
    body = md_to_html_body(md, citer, base)
    refs = citer.references_html()
    mj = ('<script>window.MathJax={tex:{inlineMath:[["$","$"]],displayMath:[["$$","$$"]]}};</script>'
          '<script async src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>')
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{html.escape(titulo)}</title>'
            f'<style>{CSS}</style>{mj}</head><body>{body}'
            + (f'<h2>Referencias</h2>\n{refs}' if refs else "") + "</body></html>")


# --- Markdown mínimo -> LaTeX ------------------------------------------------------------
TEX_ESC = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
           "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def _tex_inline(s: str, citer: Citer) -> str:
    keep: list[str] = []
    s = re.sub(r"(\$\$?)(.+?)\1", lambda m: keep.append(m.group(0)) or f"\x00K{len(keep) - 1}\x00", s)
    s = CITE_GROUP.sub(lambda m: keep.append(citer.group(m.group(1), latex=True)) or f"\x00K{len(keep) - 1}\x00", s)
    s = re.sub(r"`([^`]+)`", lambda m: keep.append(r"\texttt{" + "".join(TEX_ESC.get(c, c) for c in m.group(1)) + "}")
               or f"\x00K{len(keep) - 1}\x00", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: keep.append(
        "\\begin{figure}[h]\\centering\\includegraphics[width=\\linewidth]{" + re.sub(r"\.svg$", ".pdf", m.group(2)) +
        "}\\caption{" + "".join(TEX_ESC.get(c, c) for c in m.group(1)) + "}\\end{figure}") or f"\x00K{len(keep) - 1}\x00", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: keep.append(r"\href{" + m.group(2) + "}{" +
               "".join(TEX_ESC.get(c, c) for c in m.group(1)) + "}") or f"\x00K{len(keep) - 1}\x00", s)
    s = "".join(TEX_ESC.get(c, c) for c in s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"\\emph{\1}", s)
    return re.sub(r"\x00K(\d+)\x00", lambda m: keep[int(m.group(1))], s)


def md_to_latex(md: str, citer: Citer, titulo: str, bibfile: str) -> str:
    out = []
    lines = md.splitlines()
    i = 0
    sec = {1: None, 2: "section", 3: "subsection", 4: "subsubsection"}
    while i < len(lines):
        l = lines[i]
        m = re.match(r"^(#{1,4})\s+(.*)", l)
        if m:
            n = len(m.group(1))
            if n == 1:
                titulo = m.group(2)
            else:
                out.append(f"\\{sec[n]}{{{_tex_inline(m.group(2), citer)}}}")
            i += 1
            continue
        if l.strip().startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("\\begin{verbatim}\n" + "\n".join(lines[i + 1:j]) + "\n\\end{verbatim}")
            i = j + 1
            continue
        if l.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head = [c.strip() for c in l.strip().strip("|").split("|")]
            j, rows = i + 2, []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            cols = "p{%.2f\\linewidth}" % (0.95 / max(1, len(head)))
            t = ["\\begin{table}[h]\\centering\\small\\begin{tabular}{" + cols * len(head) + "}\\toprule",
                 " & ".join(_tex_inline(c, citer) for c in head) + r" \\ \midrule"]
            t += [" & ".join(_tex_inline(c, citer) for c in r[:len(head)]) + r" \\" for r in rows]
            t.append("\\bottomrule\\end{tabular}\\end{table}")
            out.append("\n".join(t))
            i = j
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", l):
            env = "enumerate" if re.match(r"^\s*\d+[.)]", l) else "itemize"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i]))
                i += 1
            out.append(f"\\begin{{{env}}}\n" + "\n".join(f"\\item {_tex_inline(x, citer)}" for x in items) + f"\n\\end{{{env}}}")
            continue
        if l.strip().startswith("$$"):
            out.append(l)          # $$...$$ es válido en LaTeX
            i += 1
            continue
        out.append(_tex_inline(l, citer) if l.strip() else "")
        i += 1
    body = "\n".join(out)
    return (r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}\usepackage[T1]{fontenc}\usepackage[spanish]{babel}
\usepackage{amsmath,amssymb,graphicx,booktabs,hyperref,geometry}
\usepackage[style=apa,backend=biber]{biblatex}
\addbibresource{""" + bibfile + r"""}
\geometry{margin=2.2cm}
\title{""" + _tex_inline(titulo, citer) + r"""}
\date{}
\begin{document}
\maketitle
""" + body + r"""
\printbibliography
\end{document}
""")


# --- PDF ----------------------------------------------------------------------------
def find_browser(pref: str = "auto") -> str | None:
    if pref and pref not in ("auto", "ninguno") and Path(pref).exists():
        return pref
    if pref == "ninguno":
        return None
    cands = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in cands:
        if Path(c).exists():
            return c
    for n in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(n):
            return shutil.which(n)
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, browser: str) -> bool:
    url = html_path.resolve().as_uri()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
           "--virtual-time-budget=15000", f"--print-to-pdf={pdf_path.resolve()}", url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception:
        return False
    return pdf_path.exists() and pdf_path.stat().st_size > 1000


def compile_paper(md: str, bib_text: str, out_dir: Path, cfg_comp: dict, titulo: str = "") -> dict:
    out_dir = Path(out_dir)
    bib_path = out_dir / "art_refs.bib"
    bib_path.write_text(bib_text, encoding="utf-8")
    md_path = out_dir / "art_paper.md"
    md_path.write_text(md, encoding="utf-8")
    estilo = cfg_comp.get("csl", "apa")
    motor = cfg_comp.get("motor", "auto")
    res: dict = {"motor": None, "errores": []}
    if motor in ("auto", "pandoc") and shutil.which("pandoc"):
        for fmt, name in (("latex", "art_paper.tex"), ("pdf", "art_paper.pdf"), ("html", "art_paper.html")):
            args = ["pandoc", str(md_path), "--citeproc", f"--bibliography={bib_path}", "-s", "-o", str(out_dir / name)]
            if fmt == "pdf" and shutil.which("xelatex"):
                args.append("--pdf-engine=xelatex")
            elif fmt == "pdf":
                continue
            r = subprocess.run(args, capture_output=True, text=True, cwd=out_dir)
            if r.returncode != 0:
                res["errores"].append(f"pandoc {fmt}: {r.stderr[:800]}")
        res["motor"] = "pandoc"
        if (out_dir / "art_paper.pdf").exists():
            res["pdf"] = str(out_dir / "art_paper.pdf")
            return res
    if motor == "pandoc" and not shutil.which("pandoc"):
        res["errores"].append("pandoc no está instalado")
    # motor interno
    res["motor"] = res["motor"] or "interno"
    h = md_to_html(md, bib_text, estilo, out_dir, titulo)
    hp = out_dir / "art_paper.html"
    hp.write_text(h, encoding="utf-8")
    res["html"] = str(hp)
    citer = Citer(parse_bib(bib_text), estilo)
    (out_dir / "art_paper.tex").write_text(md_to_latex(md, citer, titulo, "art_refs.bib"), encoding="utf-8")
    res["tex"] = str(out_dir / "art_paper.tex")
    b = find_browser(cfg_comp.get("pdf_navegador", "auto"))
    if b:
        pdf = out_dir / "art_paper.pdf"
        if pdf.exists():
            pdf.unlink()
        if html_to_pdf(hp, pdf, b):
            res["pdf"] = str(pdf)
        else:
            res["errores"].append("el navegador no generó el PDF")
    else:
        res["errores"].append("sin Pandoc+LaTeX ni navegador: solo HTML y .tex")
    return res


def html_text(path: Path) -> str:
    """Texto plano de art_paper.html para la pasada ciega del Peer Review."""
    t = Path(path).read_text(encoding="utf-8")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S)
    t = re.sub(r"<svg.*?</svg>", "[FIGURA]", t, flags=re.S)
    t = re.sub(r"</(p|h\d|li|tr|figcaption)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(re.sub(r"[ \t]+", " ", t))
