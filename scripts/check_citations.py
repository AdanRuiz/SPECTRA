"""Puerta dura: claves citadas en el borrador ⊆ art_refs.bib. Uso: python scripts/check_citations.py borrador.md art_refs.bib"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.gates import check_citations  # noqa: E402

r = check_citations(Path(sys.argv[1]).read_text(encoding="utf-8"), Path(sys.argv[2]).read_text(encoding="utf-8"))
print(json.dumps(r, ensure_ascii=False, indent=2))
sys.exit(0 if r["ok"] else 1)
