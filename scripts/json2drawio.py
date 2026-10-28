"""Grafo JSON -> .drawio (+ .svg). Uso: python scripts/json2drawio.py grafo.json salida.drawio [tree|layered]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.drawio import build  # noqa: E402

g = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(build(g, Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "tree"))
