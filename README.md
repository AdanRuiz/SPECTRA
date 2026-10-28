# SPECTRA

**Scientific Paper Engineering & Collaborative Trained Research Agents** — pipeline multi-agente que produce
artículos científicos a partir de artefactos versionados, puertas de calidad deterministas, revisión por LLM
de familias distintas e intervención humana. La especificación completa está en
[especificacion_agentes_investigacion_v2.md](especificacion_agentes_investigacion_v2.md) (v3).

## Instalación

```sh
pip install -r requirements.txt
echo OPENROUTER_API_KEY=sk-or-... > .env       # no se versiona
python -m spectra models-check                  # verifica los modelos configurados
python -m pytest -q tests                       # pruebas offline del núcleo
```

Opcional: Pandoc + xelatex (si no están, D2 usa su motor interno y Edge/Chrome para el PDF).

## Uso

```sh
python -m spectra init -p projects/mi-paper     # crea projects/mi-paper/proyecto.yaml
# edita proyecto.yaml: tema, pregunta, preset, existentes...
python -m spectra run -p projects/mi-paper      # avanza hasta el siguiente HITL
cat projects/mi-paper/state/hitl/H1.md          # revisa
python -m spectra approve H1 -p projects/mi-paper   # o: decide H1 cambios -c "..."
python -m spectra resume -p projects/mi-paper
python -m spectra status -p projects/mi-paper
```

El resultado final queda en `projects/<p>/entrega/` (paper en PDF/HTML/TeX, `.bib`, diagramas `.drawio`,
preregistro, resultados, dictamen y costos).

## Cambiar agentes y orquestador

Todo está en `spectra.config.yaml` y se puede sobrescribir por proyecto en `proyecto.yaml`:

```yaml
orquestador:
  implementacion: python            # python | claude_code | auto | "mi_paquete:MiOrquestador"
  perfil_modelos: economico
agentes:
  a07_redactor:
    override: { proveedor: lmstudio, modelo: qwen3-32b }   # otro modelo/proveedor
    prompt: mis_prompts/redactor.md                         # otro prompt
  a08_revisor:
    clase: "mi_paquete.agentes:MiRevisor"                   # otra lógica
flujo:
  agentes_por_estado: { S07_REDACCION: a07_redactor }
```

Con `implementacion: claude_code`, ejecuta `python -m spectra sync-agents` y usa en Claude Code
`/spectra start projects/mi-paper`: los agentes con `claude_code` corren como subagentes locales.

## Saltar fases

- Si el artefacto de una fase ya existe (en `artifacts/` o declarado en `existentes`), la fase se salta.
- Presets: `completo`, `implementacion_existente`, `desde_propuesta`, `solo_redaccion`, `solo_revision`.
- Con `existentes.implementacion` (un repositorio ya programado), SPECTRA no implementa nada: A4b hace
  ingeniería inversa de la arquitectura, A5b ejecuta tus comandos de evaluación, documenta lo que la
  evidencia demuestra y **te sugiere mecanismos de evaluación** para lo que falta (HITL 5).

Los proyectos (`projects/*`) son locales y no se versionan: contienen artefactos, estado y costos de cada
investigación. La especificación (§2.4 y §10) describe una ejecución real con un protocolo que integra
Mixxx con QLC+.
