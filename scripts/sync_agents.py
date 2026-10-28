"""agents/specs + spectra.config.yaml -> .claude/agents/*.md y .claude/skills/spectra/SKILL.md"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spectra.cli import main  # noqa: E402

main(["sync-agents", *sys.argv[1:]])
