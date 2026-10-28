"""Llamada directa a un agente vía OpenRouter (o el proveedor configurado), con validación de esquema.
Uso: python scripts/or_call.py --agente a08_revisor --prompt entrada.md [--esquema art_08_revision] [-p proyecto] [--salida out.json]
En modo claude_code la skill lo usa para agentes no-Claude que quieras ejecutar sueltos."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spectra.agents.base import Agent  # noqa: E402
from spectra.config import Config  # noqa: E402
from spectra.orchestrator import Context  # noqa: E402
from spectra.util import dumps  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--agente", required=True)
ap.add_argument("--prompt", required=True, help="archivo con el mensaje de usuario")
ap.add_argument("--esquema")
ap.add_argument("-p", "--proyecto", default=".")
ap.add_argument("--salida")
a = ap.parse_args()
ctx = Context(Config(Path(a.proyecto)))
ag = Agent(a.agente, ctx)
user = Path(a.prompt).read_text(encoding="utf-8")
out = dumps(ag.ask_json(user, schema=a.esquema, use_tools=False)) if a.esquema else ag.ask_text(user, use_tools=False)
if a.salida:
    Path(a.salida).write_text(out, encoding="utf-8")
print(out)
print(f"\n[costo] {ag.usage}", file=sys.stderr)
