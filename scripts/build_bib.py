"""D1: art_02 + art_03 + corpus -> artifacts/art_refs.bib. Uso: python scripts/build_bib.py -p projects/<proyecto>"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.config import Config  # noqa: E402
from spectra.orchestrator import Orchestrator  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("-p", "--proyecto", default=".")
o = Orchestrator(Config(Path(ap.parse_args().proyecto)))
o.policies["D1_BIB"] = "ejecutar"
print(o.do_D1_BIB())
