"""Valida un artefacto contra schemas/ (sobre común + contenido). Uso: python scripts/validate_artifact.py artifacts/art_05_resultados_v1.json"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spectra.artifacts import content_hash, split_front_matter  # noqa: E402
from spectra.gates import validate_json  # noqa: E402

p = Path(sys.argv[1])
art_id = re.sub(r"_v\d+$", "", p.stem)
text = p.read_text(encoding="utf-8")
errs = []
if p.suffix == ".json":
    data = json.loads(text)
    meta = data.get("meta", {})
    content = {k: v for k, v in data.items() if k != "meta"}
    sch = ROOT / "schemas" / f"{art_id}.schema.json"
    if sch.exists():
        errs += validate_json(content, sch)
    if meta.get("sha256") and meta["sha256"] != content_hash(art_id, data):
        errs.append("sha256 no coincide con el contenido")
else:
    meta, body = split_front_matter(text)
    if meta.get("sha256") and meta["sha256"] != content_hash(art_id, body.strip() + "\n"):
        errs.append("sha256 no coincide con el contenido")
errs += ["meta: " + e for e in validate_json(meta, ROOT / "schemas" / "envelope.schema.json")]
print(json.dumps({"artefacto": str(p), "ok": not errs, "errores": errs}, ensure_ascii=False, indent=2))
sys.exit(0 if not errs else 1)
