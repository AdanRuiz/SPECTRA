"""D2: último art_07 + art_refs.bib -> paper/art_paper.{pdf,tex,html}. Uso: python scripts/compile.py -p projects/<proyecto>"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.cli import main  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("-p", "--proyecto", default=".")
main(["compile", "-p", ap.parse_args().proyecto])
