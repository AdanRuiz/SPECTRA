"""Puerta dura: números del borrador ⊆ art_05 (Resultados/Resumen) y ⊆ artefactos (resto).
Uso: python scripts/check_numbers.py -p projects/<proyecto> [borrador.md]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.artifacts import split_front_matter  # noqa: E402
from spectra.config import Config  # noqa: E402
from spectra.gates import check_numbers  # noqa: E402
from spectra.orchestrator import Context  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("-p", "--proyecto", default=".")
ap.add_argument("borrador", nargs="?")
a = ap.parse_args()
ctx = Context(Config(Path(a.proyecto)))
text = Path(a.borrador).read_text(encoding="utf-8") if a.borrador else ctx.store.read_text("art_07_borrador")
strict, allp = ctx.allowed_numbers()
r = check_numbers(split_front_matter(text)[1], strict, allp, ctx.cfg.get("puertas", {}))
print(json.dumps(r, ensure_ascii=False, indent=2))
sys.exit(0 if r["ok"] else 1)
