# SPECTRA — reglas para Claude Code

- La especificación es `especificacion_agentes_investigacion_v2.md` (v3). Si cambias el comportamiento, actualízala.
- Los prompts canónicos son `agents/specs/*.md`. `.claude/agents/` y `.claude/skills/spectra/` se GENERAN con
  `python -m spectra sync-agents`: no los edites a mano.
- No edites artefactos de `projects/*/artifacts/` a mano: usa `python -m spectra edit` para conservar
  versiones, hashes y el sobre común.
- La clave de OpenRouter está en `.env` (ignorado por git). Nunca la escribas en otros archivos.
- Pruebas offline: `python -m pytest -q tests`. En Windows usa `python -X utf8`.
- Para orquestar desde Claude Code: `/spectra start|resume|status projects/<proyecto>`.
