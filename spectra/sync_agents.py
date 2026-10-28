"""Genera los subagentes de Claude Code (.claude/agents/*.md) y la skill /spectra desde los prompts
canónicos (agents/specs) y la configuración. Así ambos modos ejecutan el mismo comportamiento."""
from __future__ import annotations

from pathlib import Path

from .artifacts import split_front_matter
from .config import ROOT, Config

TOOLS = {"a04_arquitectura": "Read, Write, Glob", "a04b_arquitectura_inversa": "Read, Write, Glob, Grep",
         "a07_redactor": "Read, Write, Glob", "a09_asesor": "Read, Write, Glob"}


def sync(cfg: Config) -> list[Path]:
    out = []
    adir = ROOT / ".claude" / "agents"
    adir.mkdir(parents=True, exist_ok=True)
    common = split_front_matter((ROOT / "agents/specs/_comun.md").read_text(encoding="utf-8"))[1]
    for aid, conf in cfg.data.get("agentes", {}).items():
        spec = conf.get(cfg.perfil) or conf.get("principal") or {}
        cc = spec.get("claude_code")
        if not cc:
            continue
        prompt_path = cfg.path(conf.get("prompt", f"agents/specs/{aid}.md"))
        meta, body = split_front_matter(Path(prompt_path).read_text(encoding="utf-8"))
        name = cc["subagente"]
        desc = meta.get("descripcion", f"Agente {aid} de SPECTRA")
        text = f"""---
name: {name}
description: {desc} Úsalo solo cuando la skill /spectra lo indique (agente {aid}).
tools: {TOOLS.get(aid, "Read, Write, Glob")}
model: {cc.get("modelo", "sonnet")}
---
<!-- GENERADO por `python -m spectra sync-agents` desde {Path(prompt_path).relative_to(ROOT).as_posix()}. No editar a mano. -->

## Entrada y salida en modo claude_code
- El orquestador te indica la carpeta del proyecto. Tus entradas YA ensambladas por SPECTRA están en
  `<proyecto>/state/subagente/{aid}.prompt.md`: léelo completo, es tu único contexto.
- No leas la conversación del orquestador ni otros artefactos que no se mencionen ahí
  (excepción A4b: puedes leer el código del repositorio indicado).
- Escribe tu salida (solo el contenido pedido, sin front-matter) en `<proyecto>/state/subagente/{aid}.out.md`.
- No uses búsqueda web. No inventes citas: toda clave [@...] debe existir en `<proyecto>/artifacts/art_refs.bib`.

{common}

{body}
"""
        p = adir / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        out.append(p)
    # skill del orquestador
    sdir = ROOT / ".claude" / "skills" / "spectra"
    sdir.mkdir(parents=True, exist_ok=True)
    om = cfg.orq.get("modelo", {}) or {}
    meta, body = split_front_matter(cfg.path(cfg.orq.get("prompt", "agents/specs/00_orquestador.md")).read_text(encoding="utf-8"))
    model_line = f"model: {om['claude_code_modelo']}\n" if om.get("claude_code_modelo") else ""
    skill = f"""---
name: spectra
description: Orquesta SPECTRA (pipeline de agentes de investigación). Uso - /spectra start|resume|status <carpeta_proyecto>
{model_line}---
<!-- GENERADO por `python -m spectra sync-agents` desde agents/specs/00_orquestador.md. No editar a mano. -->
{body}
"""
    p = sdir / "SKILL.md"
    p.write_text(skill, encoding="utf-8")
    out.append(p)
    return out
