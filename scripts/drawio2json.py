""".drawio -> grafo JSON (para reimportar un mapa editado por el humano). Uso: python scripts/drawio2json.py mapa.drawio [salida.json]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.drawio import from_drawio  # noqa: E402

g = from_drawio(Path(sys.argv[1]))
txt = json.dumps(g, ensure_ascii=False, indent=2)
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(txt, encoding="utf-8")
print(txt)
