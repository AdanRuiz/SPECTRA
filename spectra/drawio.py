"""Grafo JSON -> .drawio (mxGraph XML válido) + .svg de vista previa, y .drawio -> grafo JSON.
El LLM nunca escribe XML: emite el grafo y este módulo calcula posiciones y estilos."""
from __future__ import annotations

import json
import re
import textwrap
import uuid
from collections import defaultdict, deque
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

PALETA = {
    # tipo: (relleno, borde)
    "problema": ("#f8cecc", "#b85450"), "hipotesis": ("#fff2cc", "#d6b656"), "concepto": ("#dae8fc", "#6c8ebf"),
    "trabajo": ("#e1d5e7", "#9673a6"), "metodo": ("#d5e8d4", "#82b366"), "resultado": ("#ffe6cc", "#d79b00"),
    "contribucion": ("#d4e1f5", "#3a6fb0"), "limitacion": ("#f5f5f5", "#666666"), "mecanismo": ("#e6f4ea", "#2e7d32"),
    "componente": ("#dae8fc", "#6c8ebf"), "servicio": ("#d5e8d4", "#82b366"), "datos": ("#fff2cc", "#d6b656"),
    "externo": ("#f5f5f5", "#666666"), "interfaz": ("#e1d5e7", "#9673a6"), "protocolo": ("#ffe6cc", "#d79b00"),
    "default": ("#ffffff", "#333333"),
}
CHAR_W, LINE_H, PAD = 7.0, 16, 12


def _wrap(label: str, width: int = 26) -> list[str]:
    lines = []
    for part in str(label).split("\n"):
        lines += textwrap.wrap(part, width) or [""]
    return lines[:6]


def _size(label: str, width: int = 26) -> tuple[int, int]:
    lines = _wrap(label, width)
    w = max(len(l) for l in lines) * CHAR_W + 2 * PAD
    return int(max(110, min(260, w))), int(len(lines) * LINE_H + 2 * PAD)


# --- layouts ---------------------------------------------------------------
def layout_tree(g: dict) -> dict:
    """Árbol horizontal desde la raíz (mapa mental)."""
    nodes = {n["id"]: n for n in g["nodos"]}
    children = defaultdict(list)
    has_parent = set()
    for e in g.get("aristas", []):
        if e["de"] in nodes and e["a"] in nodes and e["a"] not in has_parent and e["a"] != g.get("raiz"):
            children[e["de"]].append(e["a"])
            has_parent.add(e["a"])
    root = g.get("raiz") or g["nodos"][0]["id"]
    orphans = [i for i in nodes if i not in has_parent and i != root]
    children[root] += orphans
    sizes = {i: _size(n.get("etiqueta", i)) for i, n in nodes.items()}
    pos: dict[str, tuple[float, float]] = {}
    depth_x: dict[int, float] = {}

    seen = set()

    def depth_w(i, d):
        if i in seen:
            return
        seen.add(i)
        depth_x[d] = max(depth_x.get(d, 0), sizes[i][0])
        for c in children[i]:
            depth_w(c, d + 1)
    depth_w(root, 0)
    xs, acc = {}, 40.0
    for d in sorted(depth_x):
        xs[d] = acc
        acc += depth_x[d] + 90
    cursor = [40.0]
    placed = set()

    def place(i, d):
        placed.add(i)
        kids = [c for c in children[i] if c not in placed]
        for c in kids:
            placed.add(c)
        if not kids:
            y = cursor[0]
            cursor[0] += sizes[i][1] + 18
        else:
            ys = []
            for c in kids:
                place(c, d + 1)
                ys.append(pos[c][1] + sizes[c][1] / 2)
            y = (min(ys) + max(ys)) / 2 - sizes[i][1] / 2
        pos[i] = (xs[d], y)
    place(root, 0)
    for i in nodes:          # nodos inalcanzables
        if i not in pos:
            pos[i] = (40, cursor[0])
            cursor[0] += sizes[i][1] + 18
    return {"pos": pos, "sizes": sizes, "groups": {}}


def layout_layered(g: dict) -> dict:
    """Capas: un carril por grupo (de arriba a abajo); sin grupos, capas por camino más largo."""
    nodes = {n["id"]: n for n in g["nodos"]}
    sizes = {i: _size(n.get("etiqueta", i), 22) for i, n in nodes.items()}
    grupos = g.get("grupos") or []
    pos, groups = {}, {}
    if grupos:
        gid_of = {n["id"]: n.get("grupo") for n in g["nodos"]}
        order = [gr["id"] for gr in grupos] + sorted({v for v in gid_of.values() if v and v not in {gr["id"] for gr in grupos}})
        labels = {gr["id"]: gr.get("etiqueta", gr["id"]) for gr in grupos}
        y = 40.0
        sin = [i for i, v in gid_of.items() if not v or v not in order]
        lanes = [(gid, [i for i, v in gid_of.items() if v == gid]) for gid in order] + ([("_otros", sin)] if sin else [])
        for gid, members in lanes:
            if not members:
                continue
            x = 30.0
            h = max(sizes[m][1] for m in members)
            for m in members:
                pos[m] = (40 + x, y + 40 + (h - sizes[m][1]) / 2)
                x += sizes[m][0] + 50
            groups[gid] = {"etiqueta": labels.get(gid, "Otros" if gid == "_otros" else gid),
                           "x": 40, "y": y, "w": x + 10, "h": h + 70}
            y += h + 110
        maxw = max(gr["w"] for gr in groups.values())
        for gr in groups.values():
            gr["w"] = maxw
        return {"pos": pos, "sizes": sizes, "groups": groups}
    # sin grupos: capas por camino más largo
    indeg = defaultdict(int)
    adj = defaultdict(list)
    for e in g.get("aristas", []):
        if e["de"] in nodes and e["a"] in nodes:
            adj[e["de"]].append(e["a"])
            indeg[e["a"]] += 1
    layer = {i: 0 for i in nodes}
    q = deque([i for i in nodes if indeg[i] == 0] or list(nodes)[:1])
    visits = defaultdict(int)
    while q:
        u = q.popleft()
        for v in adj[u]:
            if layer[v] < layer[u] + 1 and visits[v] < len(nodes):
                layer[v] = layer[u] + 1
                visits[v] += 1
                q.append(v)
    by = defaultdict(list)
    for i, l in layer.items():
        by[l].append(i)
    y = 40.0
    for l in sorted(by):
        x = 40.0
        h = max(sizes[i][1] for i in by[l])
        for i in by[l]:
            pos[i] = (x, y)
            x += sizes[i][0] + 50
        y += h + 80
    return {"pos": pos, "sizes": sizes, "groups": {}}


# --- salida .drawio ----------------------------------------------------------
def _style(tipo: str, shape: str = "rounded=1") -> str:
    fill, stroke = PALETA.get(tipo or "default", PALETA["default"])
    return f"{shape};whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=12;spectra_tipo={tipo or 'default'};"


def to_drawio(g: dict, layout: str = "tree", nombre: str = "SPECTRA") -> tuple[str, dict]:
    lay = layout_tree(g) if layout == "tree" else layout_layered(g)
    pos, sizes, groups = lay["pos"], lay["sizes"], lay["groups"]
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    node_parent = {}
    for gid, gr in groups.items():
        cid = f"grp_{gid}"
        cells.append(f'<mxCell id="{escape(cid)}" value="{escape(gr["etiqueta"], {chr(34): "&quot;"})}" '
                     f'style="swimlane;horizontal=0;startSize=28;fillColor=#f9f9f9;strokeColor=#999999;html=1;fontStyle=1;" '
                     f'vertex="1" parent="1"><mxGeometry x="{gr["x"]:.0f}" y="{gr["y"]:.0f}" width="{gr["w"]:.0f}" '
                     f'height="{gr["h"]:.0f}" as="geometry"/></mxCell>')
    for n in g["nodos"]:
        if n.get("grupo") in groups:
            node_parent[n["id"]] = n["grupo"]
        elif groups and "_otros" in groups:
            node_parent[n["id"]] = "_otros"
    for n in g["nodos"]:
        i = n["id"]
        x, y = pos[i]
        w, h = sizes[i]
        parent = "1"
        gk = node_parent.get(i)
        if gk:
            parent = f"grp_{gk}"
            x, y = x - groups[gk]["x"], y - groups[gk]["y"]
        val = escape(str(n.get("etiqueta", i)), {'"': "&quot;"})
        tip = n.get("origen") or n.get("descripcion")
        tooltip = f' tooltip="{escape(str(tip), {chr(34): "&quot;"})}"' if tip else ""
        cells.append(f'<mxCell id="{escape(i)}" value="{val}"{tooltip} style="{_style(n.get("tipo"))}" vertex="1" '
                     f'parent="{escape(parent)}"><mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    ids = {n["id"] for n in g["nodos"]}
    for k, e in enumerate(g.get("aristas", [])):
        if e["de"] not in ids or e["a"] not in ids:
            continue
        lbl = escape(str(e.get("etiqueta", "")), {'"': "&quot;"})
        st = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;fontSize=10;" if layout != "tree"
              else "curved=1;html=1;endArrow=block;fontSize=10;edgeStyle=entityRelationEdgeStyle;")
        cells.append(f'<mxCell id="e{k}" value="{lbl}" style="{st}" edge="1" parent="1" source="{escape(e["de"])}" '
                     f'target="{escape(e["a"])}"><mxGeometry relative="1" as="geometry"/></mxCell>')
    xml = (f'<mxfile host="SPECTRA" type="device"><diagram id="{uuid.uuid4().hex[:12]}" name="{escape(nombre)}">'
           f'<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
           f'fold="1" page="0" pageScale="1" math="0" shadow="0"><root>' + "".join(cells) +
           '</root></mxGraphModel></diagram></mxfile>')
    ET.fromstring(xml)       # garantiza XML bien formado
    return xml, lay


# --- vista previa SVG ----------------------------------------------------------
def to_svg(g: dict, lay: dict) -> str:
    pos, sizes, groups = lay["pos"], lay["sizes"], lay["groups"]
    W = max([pos[i][0] + sizes[i][0] for i in pos] + [gr["x"] + gr["w"] for gr in groups.values()]) + 40
    H = max([pos[i][1] + sizes[i][1] for i in pos] + [gr["y"] + gr["h"] for gr in groups.values()]) + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'font-family="Helvetica, Arial, sans-serif">',
           '<defs><marker id="arr" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
           '<path d="M0,0 L10,4 L0,8 z" fill="#555"/></marker></defs>', f'<rect width="100%" height="100%" fill="#ffffff"/>']
    for gr in groups.values():
        out.append(f'<rect x="{gr["x"]}" y="{gr["y"]}" width="{gr["w"]}" height="{gr["h"]}" rx="6" fill="#f9f9f9" stroke="#999"/>')
        out.append(f'<text x="{gr["x"] + 10}" y="{gr["y"] + 20}" font-size="13" font-weight="bold" fill="#444">{escape(gr["etiqueta"])}</text>')
    ids = set(pos)
    for e in g.get("aristas", []):
        if e["de"] not in ids or e["a"] not in ids:
            continue
        (x1, y1), (w1, h1) = pos[e["de"]], sizes[e["de"]]
        (x2, y2), (w2, h2) = pos[e["a"]], sizes[e["a"]]
        cx1, cy1, cx2, cy2 = x1 + w1 / 2, y1 + h1 / 2, x2 + w2 / 2, y2 + h2 / 2
        if abs(cx2 - cx1) >= abs(cy2 - cy1):
            sx, sy = (x1 + w1, cy1) if cx2 > cx1 else (x1, cy1)
            tx, ty = (x2, cy2) if cx2 > cx1 else (x2 + w2, cy2)
            mx = (sx + tx) / 2
            d = f"M{sx:.0f},{sy:.0f} C{mx:.0f},{sy:.0f} {mx:.0f},{ty:.0f} {tx:.0f},{ty:.0f}"
        else:
            sx, sy = (cx1, y1 + h1) if cy2 > cy1 else (cx1, y1)
            tx, ty = (cx2, y2) if cy2 > cy1 else (cx2, y2 + h2)
            my = (sy + ty) / 2
            d = f"M{sx:.0f},{sy:.0f} C{sx:.0f},{my:.0f} {tx:.0f},{my:.0f} {tx:.0f},{ty:.0f}"
        out.append(f'<path d="{d}" fill="none" stroke="#555" stroke-width="1.3" marker-end="url(#arr)"/>')
        if e.get("etiqueta"):
            out.append(f'<text x="{(sx + tx) / 2:.0f}" y="{(sy + ty) / 2 - 4:.0f}" font-size="10" fill="#333" '
                       f'text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="3">{escape(str(e["etiqueta"])[:40])}</text>')
    for n in g["nodos"]:
        i = n["id"]
        (x, y), (w, h) = pos[i], sizes[i]
        fill, stroke = PALETA.get(n.get("tipo") or "default", PALETA["default"])
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        lines = _wrap(n.get("etiqueta", i), 26 if lay["groups"] == {} else 22)
        ty = y + PAD + 12
        for l in lines:
            out.append(f'<text x="{x + w / 2:.0f}" y="{ty:.0f}" font-size="12" text-anchor="middle" fill="#111">{escape(l)}</text>')
            ty += LINE_H
    out.append("</svg>")
    return "\n".join(out)


def build(graph_json: dict, out_drawio: Path, layout: str = "tree", nombre: str = "SPECTRA") -> dict:
    xml, lay = to_drawio(graph_json, layout, nombre)
    out_drawio = Path(out_drawio)
    out_drawio.write_text(xml, encoding="utf-8")
    svg = out_drawio.with_suffix(".svg")
    svg.write_text(to_svg(graph_json, lay), encoding="utf-8")
    return {"drawio": str(out_drawio), "svg": str(svg)}


# --- .drawio -> grafo JSON (HITL 2b: el humano edita el mapa) -------------------
def from_drawio(path: Path) -> dict:
    root = ET.fromstring(Path(path).read_text(encoding="utf-8"))
    nodos, aristas = [], []
    for c in root.iter("mxCell"):
        style = c.get("style") or ""
        val = re.sub(r"<[^>]+>", " ", c.get("value") or "").strip()
        if c.get("vertex") == "1" and not style.startswith("swimlane"):
            m = re.search(r"spectra_tipo=([\w]+)", style)
            n = {"id": c.get("id"), "etiqueta": val, "tipo": m.group(1) if m else "default"}
            if c.get("tooltip"):
                n["origen"] = c.get("tooltip")
            par = c.get("parent")
            if par and par.startswith("grp_"):
                n["grupo"] = par[4:]
            nodos.append(n)
        elif c.get("edge") == "1" and c.get("source") and c.get("target"):
            aristas.append({"de": c.get("source"), "a": c.get("target"), "etiqueta": val})
    return {"nodos": nodos, "aristas": aristas}


if __name__ == "__main__":
    import sys
    g = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(build(g, Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "tree"))
