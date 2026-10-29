# SPECTRA — Especificación de Arquitectura BMAD para Agentes de Investigación Académica — v3

> **SPECTRA:** *Scientific Paper Engineering & Collaborative Trained Research Agents*
> **Versión:** 3.0 (2026-10-28) — sustituye a la v2 (mismo archivo; el historial está en git)
> **Catálogo de referencia:** OpenRouter, verificado el 2026-10-28 con `python -m spectra models-check`. *Los IDs de modelo viven en `spectra.config.yaml`, no en el código.*
> **Orquestador:** intercambiable por configuración — Python (por defecto), Claude Code (skill `/spectra`) o una clase propia.
> **Propósito:** pipeline multi-agente orquestado mediante artefactos versionados, puertas de calidad deterministas y de LLM, e intervención humana (*Human-in-the-Loop*) para producción científica rigurosa y reproducible.

---

## 0. Cambios

### 0.1 Cambios respecto a v2

| # | Cambio | Motivo |
|---|---|---|
| 1 | El sistema se llama **SPECTRA** y está implementado como paquete Python `spectra` | Requisito nuevo |
| 2 | **Cada agente es reemplazable desde la configuración**: modelo, proveedor, prompt, clase, herramientas, temperatura, habilitado | Requisito nuevo |
| 3 | **El orquestador también es reemplazable**: `orquestador.implementacion: python \| claude_code \| auto \| "modulo:Clase"` y su propio modelo | Requisito nuevo |
| 4 | **Salto de fases si el artefacto ya existe** (`flujo.saltar_si_existe`, `flujo.fases`, `existentes`) y **presets de flujo** | Requisito nuevo |
| 5 | Preset **`implementacion_existente`**: A4b (ingeniería inversa) + A5b (documentar, evaluar y **sugerir mecanismos de evaluación** al investigador) + HITL 5 | Requisito nuevo: si el código ya existe, solo hay que documentar y evaluar |
| 6 | Preregistro con **tipo** (`prospectivo`, `post_hoc`, `importado`): el post hoc se declara en el texto y lo audita A10 | Honestidad cuando la implementación precede al protocolo |
| 7 | Evidencia tipificada en art_05: `medido` / `inspeccion_codigo` / `autoreportado` | Evita presentar lo que dice un README como medición |
| 8 | Proveedores con nombre: `openrouter`, `claude_cli` (`claude -p`), cualquier servidor compatible con OpenAI (p. ej. LM Studio) o una clase propia | Independencia de proveedor |
| 9 | HITL en tres modos: `terminal`, `archivo` (el pipeline se pausa y se responde con la CLI) y `auto` (solo pruebas) | Ejecución desatendida y reanudable |
| 10 | IDs de modelo verificados; sustituidos los inexistentes (`deepseek-v4-coder` → `deepseek/deepseek-v4-pro`, `llama-4.5` → `meta-llama/llama-4-maverick`, imagen → `google/gemini-3.1-flash-image`) | §10 de v2 (pendientes) |
| 11 | D2 con dos motores: Pandoc+LaTeX si existen; si no, motor interno Markdown→HTML/LaTeX y PDF con Edge/Chrome *headless* | Funciona sin TeX instalado |
| 12 | Vista previa `.svg` de los `.drawio` generada por SPECTRA (sin depender del CLI de draw.io) | Las figuras entran al paper |
| 13 | Repositorios adicionales de solo lectura (p. ej. un fork de Mixxx) con rutas incluidas explícitas | Integraciones que viven fuera del repo principal |
| 14 | **Tablas comparativas obligatorias** al final de *Marco Teórico* y de *Trabajos Relacionados* (puerta dura en A7 y A8; A2 entrega la del marco) | Que todo artículo las incluya |
| 15 | Comando `spectra revise` para reabrir un proyecto terminado (A7 → A8 → H3 → D2, con o sin nuevo Peer Review) | Ajustes del investigador después de FIN |
| 16 | D2 genera `art_paper.docx` editable con citas y bibliografía como campos vivos de Zotero; comando `spectra zotero` | Edición final en Word con el gestor de referencias del investigador |

### 0.2 Cambios de v2 respecto a v1 (se mantienen)

Agentes numerados por orden de ejecución · A2 ∥ A3 · D1 generador de `.bib` · A6 mapa mental · `arquitectura.drawio` · bucle Redactor ↔ Revisor con máximo de iteraciones · Revisor y Peer Review de familia distinta al Redactor · puertas duras separadas de las blandas · hash del preregistro congelado en HITL 2 · estados finales definidos · Peer Review ciego + auditoría · sobre común validado con JSON Schema · fuentes académicas con API oficial · declaración de uso de IA, similitud y presupuesto.

---

## 1. Principios del Enfoque BMAD

> **Nota terminológica:** el *BMAD Method* conocido públicamente significa *Breakthrough Method for Agile AI-Driven Development*. En este documento, **BMAD = Behavior, Memory, Action, Decision** es una reinterpretación propia; debe declararse así si se cita en un trabajo académico.

* **Behavior (Comportamiento):** roles, tono y restricciones negativas explícitas por agente (`agents/specs/*.md`).
* **Memory (Memoria / Handoffs):** los agentes se comunican **solo mediante artefactos** con esquema fijo, versionados y con hash. Ningún agente recibe la conversación de otro.
* **Action (Acciones y Herramientas):** mínimo privilegio. Cada agente tiene únicamente las herramientas que necesita (lista `herramientas` en la configuración). A5b solo puede ejecutar los comandos que declaró el investigador.
* **Decision (Decisión y Quality Gates):** el flujo avanza solo si se cumplen **todas** las puertas duras y las puertas blandas superan su umbral **por criterio**.

### Métodos de redacción referidos
* **Cubing** (seis caras): Describir, Comparar, Asociar, Analizar, Aplicar, Argumentar (a favor y en contra).
* **POWER:** *Plan*, *Organize*, *Write*, *Edit*, *Revise*.

---

## 2. Pipeline y Flujo de Artefactos

### 2.1 Diagrama

```
[A1 Ideas + chequeo de novedad] ──art_01──> HITL 1
        │
        ├──────────────> [A2 Marco Teórico] ──art_02──┐      (en paralelo)
        └──────────────> [A3 Estado del Arte] ─art_03─┤
                                                      ▼
                                   [D1 Generador .bib] ──art_refs.bib
                                                      ▼
            [A4 Propuesta | A4b Ingeniería inversa] ──art_04 (+ arquitectura.drawio/.svg)
                                                      ▼
                     HITL 2  (congela hash de art_04 = preregistro prospectivo | post_hoc)
                                                      ▼
            [A5 Implementador | A5b Documentador-Evaluador] ──art_05──┬── FAILED ──> A4 / humano
                                                      ▼
                        HITL 5 (solo A5b: mecanismos de evaluación sugeridos)
                                                      ▼
                              [A6 Mapa Mental] ──art_06 (mapa_mental.drawio/.svg [+ .png])
                                                      ▼
                                      HITL 2b (opcional, configurable)
                                                      ▼
                 ┌──────────> [A7 Redactor] ──art_07_vN──> [A8 Revisor] ──art_08_vN
                 │                                              │
                 └────── no aprueba y N < max_iter ─────────────┤
                                                                │ N = max_iter → HITL escalamiento
                                                                ▼ aprueba
                         [A9 Asesor de Tesis] ──art_09──> [A7 Redactor integra] ──> HITL 3
                                                                ▼
                                [D2 Compilador] ──art_paper.pdf / .tex / .html
                                                                ▼
                   [A10 Peer Review: pasada ciega + pasada de auditoría] ──art_10
                                                                ▼
     Accept → FIN │ Minor → A7 → A8 → HITL 3 (máx. 1 ronda) │ Major/Reject → humano (FIN, A7 o A4)
```

Cualquier caja cuyo artefacto de salida ya exista se **salta** (§2.3).

### 2.2 Máquina de estados

| Estado | Ejecuta | Éxito → | Fallo → |
|---|---|---|---|
| `S01_IDEAS` | A1 | `H1` | reintento (máx. 2) → error |
| `H1` | Humano | `S02_S03` | `S01_IDEAS` (con comentarios) |
| `S02_S03` | A2 ∥ A3 (solo los que falten) | `D1_BIB` | reintento del agente fallido |
| `D1_BIB` | script | `S04_PROPUESTA` | `HD1` humano (referencias sin resolver) |
| `S04_PROPUESTA` | A4 o A4b | `H2` | reintento |
| `H2` | Humano | `S05_IMPL` (congela hash) | `S04_PROPUESTA` |
| `S05_IMPL` | A5 o A5b | `H5` | `FAILED` → `H5F` (A4 o detener); `PARTIAL` → `H5P` |
| `H5` | Humano (solo si hay mecanismos sugeridos) | `S06_MAPA` | `medir` → `S05_IMPL` con las mediciones nuevas |
| `S06_MAPA` | A6 | `H2b` | reintento |
| `H2b` | Humano (opcional) | `S07_REDACCION` | `S06_MAPA` o importar `.drawio` editado |
| `S07_REDACCION` | A7 | `S08_REVISION` | reintento |
| `S08_REVISION` | A8 + puertas | `S09_ASESOR` (o `H3` en ronda de peer review) | `S07_REDACCION` si iter < max; si no, `H_ESCALA` |
| `S09_ASESOR` | A9 | `S09_INTEGRA` | — |
| `S09_INTEGRA` | A7 integra art_09 | `H3` | — |
| `H3` | Humano | `D2_COMPILA` | `S07_REDACCION` (con comentario) |
| `D2_COMPILA` | script | `S10_PEER` | `HD2` humano |
| `S10_PEER` | A10 | según dictamen (§6.3) | — |
| `FIN` | empaqueta `entrega/` | — | — |

El estado se persiste en `state/pipeline_state.json` (estado, historial, iteración, hash y tipo de preregistro, HITL, fases saltadas, gasto por agente) para **reanudar** tras cualquier interrupción con `python -m spectra resume`.

### 2.3 Salto de fases y presets de flujo

Una fase con salida se **salta** cuando se cumple alguna de estas condiciones:

1. Su política es `saltar` (en `flujo.fases` o en el preset). Si faltan sus artefactos, SPECTRA se detiene y pide declararlos en `existentes`.
2. Su política es `auto` (por defecto), `flujo.saltar_si_existe: true`, **todos** sus artefactos de salida existen y la fase **no se ha visitado** en esta ejecución. (Al volver a una fase por un bucle o un rechazo, se ejecuta.)
3. El agente asignado tiene `habilitado: false`.

`ejecutar` fuerza la ejecución aunque el artefacto exista (se crea una versión nueva). Los HITL cuyo artefacto revisado fue importado se saltan; si se salta la propuesta, el hash se congela con tipo `importado`.

**Artefactos existentes (`existentes`).** Al iniciar, cada ruta declarada (`art_01_ideas`, `art_02_marco`, `art_03_literatura`, `art_04_propuesta`, `art_05_resultados`, `art_06_mapa.graph`, `art_07_borrador`, `art_refs`) se importa como `v1` con `proveedor: humano`, se envuelve en el sobre común (§5) y luego aplica la regla de salto. `existentes.implementacion` declara un repositorio de código (y, opcionalmente, repositorios adicionales y comandos de evaluación).

**Presets (`flujo.preset`):**

| Preset | Qué cambia | Uso típico |
|---|---|---|
| `completo` | Nada: A1 → A10 | Investigación desde cero |
| `implementacion_existente` | `S04 → A4b`, `S05 → A5b`, HITL 5 activo. Se activa solo si hay `existentes.implementacion` | El sistema ya está programado: documentar, evaluar y sugerir mecanismos |
| `desde_propuesta` | Salta `S01`, `H1`, `S02_S03` | Ya hay ideas, marco y estado del arte |
| `solo_redaccion` | Salta todo hasta `H5` | Ya hay resultados (art_05) |
| `solo_revision` | Salta todo hasta `S07` | Ya hay borrador: solo revisar, asesorar, compilar y arbitrar |

`flujo.fases` y `flujo.agentes_por_estado` ganan sobre el preset.

### 2.4 Flujo con implementación existente

Cuando el código ya existe, construirlo de nuevo no tiene sentido y un preregistro "previo" sería falso. El flujo queda así:

1. **A1** recibe además el resumen del repositorio y formula la pregunta de investigación **que esa implementación responde**.
2. **Corpus**: SPECTRA indexa los documentos del repositorio (README, especificación, notas) como fuentes `documento_proyecto` y el propio repositorio como `software`; tienen clave BibTeX propia y se citan como fuentes técnicas, no como evidencia independiente.
3. **A4b — ingeniería inversa:** lee el código con herramientas de solo lectura, describe la arquitectura **real** (cada componente con su ruta), marca lo "especificado, no implementado" y define el protocolo de evaluación **post hoc**.
4. **HITL 2** congela el hash con `preregistro: post_hoc`.
5. **A5b — documentar y evaluar:** SPECTRA ejecuta primero, de forma determinista, **todos** los `comandos_evaluacion` declarados (pruebas, arneses, sesiones de medición) y guarda salida, código de salida y hash. A5b documenta los componentes, tipifica cada evidencia (`medido` / `inspeccion_codigo` / `autoreportado`), evalúa cada hipótesis (`no_evaluada` si no hay evidencia) y **sugiere mecanismos de evaluación** ejecutables por el investigador (objetivo, métricas, procedimiento, instrumentación, criterio de éxito, prioridad, esfuerzo).
6. **HITL 5:** el investigador ve la evidencia y los mecanismos. `aprobar` → se reportan como plan de validación; `medir` → tras ejecutar alguno y declarar `existentes.resultados_adicionales`, A5b re-documenta con esas mediciones.
7. El resto del pipeline es igual; A7 declara el preregistro post hoc y A10 lo audita.

---

## 3. Orquestación y configuración

### 3.1 Orquestador intercambiable

| `orquestador.implementacion` | Quién orquesta | Agentes con `claude_code` | Agentes sin `claude_code` | HITL |
|---|---|---|---|---|
| `python` (por defecto) | `spectra/orchestrator.py` | Llamada vía su proveedor (OpenRouter u otro) | Vía su proveedor | `hitl.modo` |
| `claude_code` | Skill `/spectra` en Claude Code, que conduce al paquete Python | **Subagentes locales** (`.claude/agents/*.md`) | Vía su proveedor (Python) | AskUserQuestion |
| `auto` | `claude_code` si existe `claude` y se ejecuta dentro de Claude Code; si no, `python` | | | |
| `"modulo:Clase"` | Tu orquestador (subclase de `spectra.orchestrator.Orchestrator`) | | | |

`orquestador.modelo` define el modelo del orquestador: en modo `claude_code` es el `model:` de la skill generada; en modo `python` queda disponible para tareas de apoyo. `orquestador.prompt` apunta a `agents/specs/00_orquestador.md`, del que se genera la skill.

**Regla de resolución de proveedor (por agente):**
1. Si el agente tiene `override`, sus campos ganan sobre el perfil.
2. Si el orquestador es `claude_code` y el perfil del agente tiene `claude_code.subagente` → subagente local.
3. En otro caso → `proveedor` del perfil (por defecto `openrouter`) con `modelo` u `openrouter_id`.

**Modo `claude_code` en detalle.** La lógica determinista (estados, saltos, puertas, preregistro, presupuesto) sigue en Python. Cuando un agente resuelve a subagente, `spectra` escribe su prompt ya ensamblado en `state/subagente/<agente>.prompt.md` y se pausa; la skill lanza el subagente (que solo lee ese archivo y escribe `state/subagente/<agente>.out.md`) y luego ejecuta `python -m spectra record`, que aplica **las mismas** comprobaciones y el mismo post-procesado que el modo Python. Así ambos modos producen artefactos idénticos en forma.

### 3.2 Fuente única de prompts

Los prompts **canónicos** viven en `agents/specs/NN_nombre.md` (front-matter con `id`, `version`, `descripcion`) más `agents/specs/_comun.md` (reglas comunes). `python -m spectra sync-agents` genera desde ellos los subagentes de `.claude/agents/` y la skill `.claude/skills/spectra/SKILL.md`; el runner los lee directamente. `version_prompt` (p. ej. `07_redactor@3.0`) se registra en el sobre de cada artefacto.

### 3.3 Estructura del proyecto

```
SPECTRA/
├── spectra.config.yaml           # configuración global: orquestador, flujo, proveedores, agentes, umbrales
├── CLAUDE.md                     # reglas para Claude Code
├── .env                          # OPENROUTER_API_KEY (no se versiona)
├── .claude/
│   ├── agents/                   # subagentes locales (GENERADOS por sync-agents)
│   └── skills/spectra/SKILL.md   # orquestador en Claude Code (GENERADO)
├── agents/specs/                 # prompts canónicos (fuente de verdad)
│   ├── _comun.md  00_orquestador.md  01_ideas.md ... 04b_arquitectura_inversa.md
│   └── 05b_documentador.md ... 10_peer_review.md
├── config/rubrica.yaml  config/checklist_peer_review.yaml
├── schemas/                      # JSON Schema de cada artefacto + envelope.schema.json
├── spectra/                      # paquete Python (orquestador, agentes, puertas, D1, D2, drawio)
│   ├── orchestrator.py  cli.py  config.py  artifacts.py  state.py  hitl.py  providers.py
│   ├── gates.py  bib.py  compile.py  drawio.py  sync_agents.py
│   ├── agents/base.py  agents/impl.py
│   └── tools/academic.py  tools/corpus.py  tools/repo.py
├── scripts/                      # envoltorios CLI de §6.1: or_call, build_bib, json2drawio, drawio2json,
│                                 #   check_citations, check_numbers, validate_artifact, compile, sync_agents
├── tests/                        # pruebas offline del núcleo
└── projects/<proyecto>/
    ├── proyecto.yaml             # sobrescribe cualquier clave global
    ├── artifacts/                # art_01_ideas_v1.json, art_07_borrador_v2.md, art_refs.bib, *.drawio, *.svg
    ├── corpus/                   # pdfs/, index.json, chunks.jsonl
    ├── state/                    # pipeline_state.json, hitl/<H>.md, subagente/
    ├── logs/                     # costos.jsonl, trazas.log, informes de D1, A5b y A10
    ├── paper/                    # art_paper.{md,html,tex,pdf} + figuras/
    └── entrega/                  # paquete final (§7)
```

### 3.4 Configuración (`spectra.config.yaml` + `projects/<p>/proyecto.yaml`)

La configuración del proyecto se fusiona en profundidad sobre la global (gana el proyecto).

```yaml
orquestador:
  implementacion: python          # python | claude_code | auto | "modulo:Clase"
  prompt: agents/specs/00_orquestador.md
  modelo: { proveedor: openrouter, modelo: anthropic/claude-haiku-5.5, claude_code_modelo: sonnet }
  perfil_modelos: principal       # principal | economico
  max_iteraciones_revision: 3
  max_reintentos_agente: 2
  max_rondas_peer_review: 1
  presupuesto_usd_total: 30
  presupuesto_usd_por_agente: 6
  tipo_investigacion: cuantitativa_ml   # cuantitativa_ml | estadistica | cualitativa | ingenieria_software

flujo:
  preset: completo                # §2.3
  saltar_si_existe: true
  fases: {}                       # p. ej. { S06_MAPA: saltar, S07_REDACCION: ejecutar }
  agentes_por_estado: {}          # p. ej. { S07_REDACCION: mi_redactor }

existentes:                       # §2.3 y §2.4
  implementacion:
    repo: C:/ruta/al/repo
    comandos_evaluacion:
      - { cmd: "python -m pytest -q", cwd: tools, descripcion: "pruebas" }
    repos_adicionales:
      - { alias: mixxx, repo: C:/ruta/mixxx, base_diff: main, incluir: [src/vertexav] }
  art_03_literatura: ruta/literatura.json
  resultados_adicionales: ruta/mediciones.json

hitl: { modo: archivo, mapa_mental: true, mecanismos: true }    # terminal | archivo | auto

puertas: { score_global_min: 85, criterio_min: 75, similitud_max: 0.15, ngram_similitud: 8 }

proveedores:
  openrouter: { tipo: openai_compat, base_url: https://openrouter.ai/api/v1, api_key_env: OPENROUTER_API_KEY }
  claude_cli: { tipo: claude_cli, binario: claude }
  lmstudio:   { tipo: openai_compat, base_url: http://localhost:1234/v1 }

agentes:
  a07_redactor:
    prompt: agents/specs/07_redactor.md   # o tu propio prompt
    clase: null                            # o "mi_paquete.agentes:MiRedactor"
    herramientas: []
    temperatura: 0.5
    max_tokens: 32000
    principal:
      claude_code: { subagente: a07-redactor, modelo: sonnet }
      openrouter_id: anthropic/claude-sonnet-5.5
    economico: { openrouter_id: meta-llama/llama-4-maverick }
    override: { proveedor: lmstudio, modelo: qwen3-32b }   # opcional: cambio rápido, gana siempre
```

**Claude siempre por Claude Code.** Para que los agentes Claude nunca pasen por OpenRouter (aunque el perfil activo sea `economico`), usa `override` con `claude_code` y `orquestador.implementacion: claude_code`:

```yaml
orquestador: { implementacion: claude_code, perfil_modelos: economico }
agentes:
  a04b_arquitectura_inversa: { override: { claude_code: { subagente: a04b-arquitectura-inversa, modelo: opus } } }
  a07_redactor:              { override: { claude_code: { subagente: a07-redactor, modelo: sonnet } } }
  a09_asesor:                { override: { claude_code: { subagente: a09-asesor, modelo: fable } } }
```

Los agentes no-Claude siguen por su proveedor; el `meta.generado_por.modelo` de los artefactos de subagentes queda como `claude-code/<modelo>` y su costo no se carga al presupuesto de OpenRouter.

**Agentes nuevos.** Un agente con otro id puede declararse con `basado_en: a07_redactor` (reutiliza la lógica) o `clase: "modulo:Clase"` (lógica propia), y asignarse a un estado con `flujo.agentes_por_estado`.

### 3.5 Ejemplo de subagente local generado (`.claude/agents/a07-redactor.md`)

````markdown
---
name: a07-redactor
description: Redactor académico del pipeline (Cubing + POWER)... Úsalo solo cuando la skill /spectra lo indique (agente a07_redactor).
tools: Read, Write, Glob
model: sonnet
---
## Entrada y salida en modo claude_code
- Tus entradas YA ensambladas por SPECTRA están en <proyecto>/state/subagente/a07_redactor.prompt.md.
- Escribe tu salida en <proyecto>/state/subagente/a07_redactor.out.md.
- No uses búsqueda web. Toda clave [@...] debe existir en artifacts/art_refs.bib.
(reglas comunes + prompt canónico completo)
````

### 3.6 CLI

```
python -m spectra init -p projects/<p>               # crea el proyecto
python -m spectra run|resume -p projects/<p>          # avanza hasta el siguiente HITL o el FIN
python -m spectra status -p projects/<p>              # estado, iteración, gasto, artefactos
python -m spectra approve|reject <H> [-c "..."]       # responder un HITL
python -m spectra decide <H> <opcion> [-c "..."]      # opciones especiales (medir, a4, a7, fin)
python -m spectra edit <H> <artefacto> <archivo>      # importar un artefacto editado (incluye .drawio)
python -m spectra plan | record [archivo]             # usados por la skill de Claude Code
python -m spectra gates [borrador.md] | compile       # puertas duras / D2 a mano (PDF, HTML, TeX y DOCX)
python -m spectra zotero [--etiqueta X]              # importa art_refs.bib a Zotero (app abierta)
python -m spectra revise -c "..." [--sin-peer]       # reabrir un proyecto (incluso en FIN): A7 -> A8 -> H3 -> D2 (-> A10)
python -m spectra sync-agents | models-check
```

---

## 4. Especificación de Agentes

> Herramientas como **capacidad lógica → implementación** (`spectra/tools`). Modelos: principal | económico (verificados en OpenRouter).

### Agente 1: Lluvia de Ideas + Chequeo de Novedad
* **Modelos:** `openai/gpt-6-astra` | `moonshotai/kimi-k3`
* **Action:** `buscar_academico` (OpenAlex, Crossref, arXiv, Semantic Scholar). *Sin* ejecución de código.
* **Behavior:** hipótesis novedosas, falsables e interdisciplinarias; para cada una, busca trabajos muy similares y asigna riesgo de novedad. Al menos dos enfoques metodológicos. Con implementación existente, formula la pregunta que el sistema responde.
* **Entradas:** tema y pregunta (`proyecto`), tipo de investigación, resumen del repositorio si existe, comentarios de H1.
* **Salida → `art_01_ideas.json`** (esquema `schemas/art_01_ideas.schema.json`):
  ```json
  {
    "titulo_propuesto": "string", "problema_investigacion": "string", "pregunta_investigacion": "string",
    "hipotesis_principal": { "id": "H1", "enunciado": "string", "riesgo_novedad": "bajo|medio|alto",
                             "justificacion_riesgo": "string", "trabajos_similares": ["doi_o_url"] },
    "hipotesis_secundarias": [{ "id": "H2", "enunciado": "string" }],
    "brechas_conocimiento": ["string"],
    "enfoques_metodologicos_sugeridos": [{ "nombre": "string", "pros": "string", "contras": "string" }],
    "contribuciones_esperadas": ["string"]
  }
  ```
* **Decision:** **HITL 1** — Aprobar, Pedir cambios o Rechazar (vuelve a A1 con comentarios), o `edit` (el humano guarda `art_01_v2`).

---

### Agente 2: Consultor de Marco Teórico
* **Modelos:** `moonshotai/kimi-k3` | `google/gemini-3.8-flash`
* **Action:** `buscar_corpus` (BM25 sobre `corpus/`), `listar_corpus`, `buscar_academico` + `agregar_al_corpus` (descarga el PDF open access o indexa la ficha con resumen).
* **Ingesta del corpus (sin LLM):** PDFs del usuario (`corpus/pdfs`, `existentes.corpus_pdfs`), trabajos similares de A1 (por DOI) y, con implementación existente, los documentos del repositorio.
* **Behavior:** sintetiza conceptos fundacionales. **Prohibido citar lo que no esté en el corpus** (puerta: toda clave citada ∈ corpus). Cita con página si el documento está paginado: `[@clave, p. 12]`.
* **Salida → `art_02_marco.md`**: conceptos (definición, formulación, relevancia), mapeo de variables, **tabla comparativa del marco teórico** (obligatoria: concepto · definición o aportación · fuente · relación con las hipótesis · cómo lo instancia este trabajo) y la sección **Referencias usadas**, que SPECTRA genera deterministamente a partir de las citas.

---

### Agente 3: Consultor de Estado del Arte
* **Modelos:** `google/gemini-3.8-flash` | `deepseek/deepseek-v4.1-flash`
* **Action:** `buscar_academico`, `resolver_doi`. *No* se usa Google Scholar.
* **Behavior:** bloques **fundacional** y **reciente** (últimos `busqueda.anios_recientes` años); solo trabajos devueltos por las herramientas; métricas solo si aparecen literalmente (si no, lista vacía).
* **Verificación determinista (post-LLM):** título (similitud ≥ 0.8), primer autor y año (±1) contra Crossref → OpenAlex → arXiv → búsqueda por título. Lo que no coincide pasa a `descartados` con motivo. Las claves se normalizan y desduplican.
* **Salida → `art_03_literatura.json`** (esquema `art_03_literatura`): `articulos[]` con `clave_bib`, `id {doi, arxiv, url}`, `verificado`, `bloque`, `titulo`, `autores`, `anio`, `venue`, `metodologia`, `metricas_clave[]`, `limitaciones`, `relevancia`; `descartados[]`; `tabla_comparativa_resumen`.

---

### Paso D1: Generador de Bibliografía (determinista, sin LLM)
* **Implementación:** `spectra/bib.py` (`scripts/build_bib.py`).
* **Entradas:** art_03 + "Referencias usadas" de art_02 + documentos del proyecto del corpus + `bibliografia_extra` de la configuración.
* **Proceso:** BibTeX de Crossref por DOI (renombrando la clave); si no hay DOI, entrada `@misc`/`@software` desde metadatos (arXiv con `eprint`); desduplicación por DOI.
* **Salida → `artifacts/art_refs.bib`:** **única fuente de verdad** de las citas. Informe en `logs/d1_informe.json`.
* **Fallo:** referencias sin resolver → **HD1** (continuar sin ellas o detener).

---

### Agente 4: Propuesta y Arquitectura
* **Modelos:** `anthropic/claude-opus-5.5` (subagente `a04-arquitectura`, model: opus) | `deepseek/deepseek-v4-pro-0813`
* **Behavior:** propuesta, formulación matemática y protocolo **preregistrable** (hipótesis → métricas → baselines → pruebas → criterios de éxito con umbral numérico). Solo claves de `art_refs.bib`.
* **Salidas:** `art_04_propuesta.md` (front-matter con `incluye_arquitectura` y `preregistro: prospectivo`); si hay arquitectura, el grafo va al final en un bloque ` ```json arquitectura ` que SPECTRA extrae a `art_04_arquitectura.graph_vN.json` y convierte en `arquitectura.drawio` + `arquitectura.svg` (§4, A6: el LLM nunca escribe XML).
* **Decision:** **HITL 2** congela el SHA-256 de art_04 (preregistro). Cualquier cambio posterior exige volver a HITL 2; A5 y A10 verifican el hash.

### Agente 4b: Propuesta por Ingeniería Inversa (implementación existente)
* **Modelos:** los de A4 (subagente `a04b-arquitectura-inversa`).
* **Action:** `leer_archivo`, `buscar_en_repo` (solo lectura; también `alias:ruta` de repositorios adicionales).
* **Behavior:** describe la arquitectura **real** con la ruta del código de cada componente, tabla de **estado de implementación** (implementado / parcial / especificado), formalización y **protocolo de evaluación post hoc** declarado como tal. No escribe resultados.
* **Salida:** igual que A4, con `preregistro: post_hoc`.

---

### Agente 5: Implementador y Resultados (implementación nueva)
* **Modelos:** `openai/gpt-6.1-sol` | `deepseek/deepseek-v4-pro`
* **Action:** `escribir_archivo`, `leer_archivo`, `ejecutar` dentro de `experiments/` (sandbox local con tiempo límite, o Docker `--network none` con `sandbox.tipo: docker`).
* **Behavior:** implementa **solo** el protocolo congelado; desviaciones registradas; varias semillas, media ± std, IC 95 %, pruebas preregistradas; baselines re-ejecutados o marcados "reportado". Ramas por `tipo_investigacion`.
* **Salida → `art_05_resultados.json`** (esquema `art_05_resultados`): `status_ejecucion`, `hash_propuesta` (lo pone SPECTRA), `entorno`, `corridas`, `resumen`, `baselines`, `pruebas_estadisticas`, `hipotesis_evaluadas`, `desviaciones`, `tablas_latex`, `rutas_graficos`, `codigo`, `ejecuciones_sandbox`.
* **Decision:** `FAILED` → reintento con diagnóstico (máx. 2) → **H5F** (A4 o detener). `PARTIAL` → **H5P**.

### Agente 5b: Documentador y Evaluador de Implementación Existente
* **Modelos:** los de A5.
* **Action:** `leer_archivo`, `buscar_en_repo`, `ejecutar_evaluacion(indice)` — **solo** los comandos declarados en `existentes.implementacion.comandos_evaluacion`.
* **Proceso:** (1) SPECTRA ejecuta todos los comandos declarados y conserva salida, código de salida, duración y hash; (2) A5b documenta componentes, tipifica la evidencia y evalúa hipótesis; (3) propone mecanismos de evaluación para lo no demostrado.
* **Reglas de evidencia:** todo número sale de `ejecucion:<i>`, `repo:<ruta>:<línea>` o `investigador`; tipo `medido` / `inspeccion_codigo` / `autoreportado`; hipótesis sin evidencia = `no_evaluada` + al menos un mecanismo.
* **Salida → `art_05_resultados.json`** con `modo: implementacion_existente` y además: `componentes_documentados[]`, `evidencia[]`, `hipotesis_evaluadas[]`, `limitaciones_evidencia[]`, `mecanismos_evaluacion_sugeridos[]` (`nombre`, `objetivo`, `hipotesis`, `metricas`, `procedimiento`, `instrumentacion`, `criterio_exito`, `prioridad`, `esfuerzo`), `ejecuciones[]` y `codigo {ruta, commit, remoto, sucio}`.
* **Decision:** **HITL 5** (§2.4).

---

### Agente 6: Mapa Mental
* **Modelos:** `google/gemini-3.8-flash` | `deepseek/deepseek-v4.1-flash` · imagen opcional `google/gemini-3.1-flash-image` (`imagen.habilitado`).
* **Behavior:** mapa del **argumento**: problema → hipótesis → conceptos → trabajos → método → resultados → contribuciones, limitaciones y mecanismos pendientes. Cada nodo con `origen`. Prohibido introducir conceptos ajenos a los artefactos.
* **Generación del `.drawio`:** el LLM emite `art_06_mapa.graph.json`; `spectra/drawio.py` calcula un árbol horizontal y produce mxGraph XML válido (estilo por tipo, `spectra_tipo` en el estilo para poder reimportar) + `.svg`. La imagen es **solo ilustrativa**.
* **Salidas:** `art_06_mapa.graph_vN.json` (con `secciones_borrador`, la **estructura obligatoria** del borrador), `mapa_mental.drawio`, `mapa_mental.svg`, `mapa_mental.png` (opcional).
* **Decision:** **HITL 2b** (opcional). El humano puede editar el `.drawio` y reimportarlo con `spectra edit H2b art_06_mapa.graph mapa_mental.drawio` (drawio → JSON conserva raíz y secciones).

---

### Agente 7: Redactor Académico (Cubing + POWER)
* **Modelos:** `anthropic/claude-sonnet-5.5` (subagente `a07-redactor`, model: sonnet) | `meta-llama/llama-4-maverick`
* **Action:** sin búsqueda externa.
* **Behavior:** POWER al proceso, Cubing a la discusión. Sigue `secciones_borrador` + **Limitaciones** + **Declaración de uso de IA**. Citas solo de `art_refs.bib`; números literales de art_05; distingue medido / inspeccionado / autoreportado; declara el preregistro post hoc; presenta los mecanismos sugeridos como plan de validación; incluye las figuras (`figuras/arquitectura.svg`, `figuras/mapa_mental.svg`). **Tablas comparativas obligatorias:** la sección *Marco Teórico* termina con la tabla comparativa del marco teórico (una fila por concepto de art_02) y la sección *Trabajos Relacionados* termina con la tabla comparativa del estado del arte (una fila por trabajo o familia de art_03 y una última fila con este trabajo; columnas mínimas: trabajo, enfoque, qué cubre, métricas tal cual o "—", limitaciones). Cada una va seguida solo de su leyenda. No nombra artefactos internos (art_05, R6...) fuera de la Declaración de uso de IA. La declaración de IA se construye con los `meta.generado_por` reales de los artefactos.
* **Comprobaciones antes de aceptar la salida:** citas ⊆ bib, secciones obligatorias presentes, tablas comparativas al final de Marco Teórico y Trabajos Relacionados, empieza con `# Título` (si fallan, reintento con los errores).
* **Salida → `art_07_borrador_vN.md`** con `iteracion`, `responde_a` (art_08 / art_09 / art_10 / comentarios humanos) y `modo` en el front-matter.

---

### Agente 8: Revisor (Quality Control)
* **Modelos:** `qwen/qwen3.8-max-0902` | `deepseek/deepseek-v4.1-flash` (**familia distinta** al Redactor)
* **Action:** recibe las **puertas duras calculadas por SPECTRA** (§6.1); no puede alterarlas: el orquestador sobrescribe `puertas_duras` en su salida.
* **Behavior:** cohesión, rigor de citas, claridad, estilo, soporte de las conclusiones; cada discrepancia numérica → cambio de severidad alta.
* **Salida → `art_08_revision_vN.json`** (esquema `art_08_revision`) + `quality_gate_1` calculado por SPECTRA.

---

### Agente 9: Asesor de Tesis (Mentoring Socrático)
* **Modelos:** `anthropic/claude-fable-5.1` (subagente `a09-asesor`, model: fable) | `anthropic/claude-sonnet-5.5`
* **Action:** rúbrica configurable (`config/rubrica.yaml`), norma de citación (`compilacion.csl`).
* **Behavior:** preguntas socráticas sobre discusión, limitaciones, amenazas a la validez y contribución. No pide resultados inexistentes: pide declararlos como limitación o plan.
* **Salida → `art_09_asesor.md`** (Preguntas críticas · Amenazas a la validez · Ajustes institucionales · Acciones obligatorias vs. sugeridas).
* **Decision:** A7 **integra** (`responde_a: art_09`) → **HITL 3** (borrador, diff, puertas duras y preguntas).

---

### Paso D2: Compilador (determinista)
* **Implementación:** `spectra/compile.py` (`scripts/compile.py`).
* **Motores:** `pandoc` (+ `--citeproc` y xelatex si existen) o **interno**: Markdown → HTML (citas APA/IEEE resueltas desde `art_refs.bib`, tablas, figuras SVG incrustadas, MathJax) y Markdown → LaTeX (`biblatex`, `\parencite`); PDF con Edge/Chrome *headless*.
* **Word editable con Zotero** (`spectra/docx_export.py` + `spectra/docx/build.js`, docx-js; `compilacion.docx`, `compilacion.zotero`): `art_paper.docx` con estilos de título, tablas, figuras (SVG → PNG con el navegador), fórmulas en línea como texto con subíndices/superíndices y **cada cita como campo vivo de Zotero** (`ADDIN ZOTERO_ITEM CSL_CITATION`, con localizador de página) más la bibliografía como `ADDIN ZOTERO_BIBL`, estilo APA y preferencias en `ZOTERO_PREF_n`. Si Zotero está instalado, cada cita se enlaza por título con el ítem de la biblioteca local (lectura inmutable de `zotero.sqlite`, sin bloquear la app); si no, el campo lleva los datos CSL incrustados. En Word, *Zotero → Refresh* regenera citas y bibliografía. `python -m spectra zotero` importa `art_refs.bib` a la colección seleccionada en Zotero con la etiqueta `SPECTRA-<proyecto>`. Requiere Node.js (el primer uso instala `docx` con npm); un fallo del .docx nunca bloquea D2.
* **Salida:** `paper/art_paper.{md,html,tex,pdf,docx}` + `paper/figuras/`. Si un archivo está abierto en otro programa, se escribe como `*_nuevo.*`. **Fallo:** **HD2**.

---

### Agente 10: Evaluador Peer Review (Red Team Adversarial)
* **Modelos:** `openai/gpt-6-astra` | `moonshotai/kimi-k3` (**familia distinta** al Redactor)
* **Action:** checklist configurable (`config/checklist_peer_review.yaml`).
* **Behavior:** Revisor #2 de una revista Q1. Pasada **ciega** (solo el texto del paper; se guarda en `logs/a10_pasada_ciega.md`) y pasada de **auditoría** (paper + art_03 + art_04 con hash y tipo de preregistro + art_05 + código). Un post hoc declarado no es motivo de rechazo; ocultarlo sí.
* **Salida → `art_10_dictamen.md`** con `**Decisión:** Accept | Minor Revision | Major Revision | Reject` (SPECTRA la extrae a `meta.decision`).

---

## 5. Sobre común de artefactos (metadatos)

Todo artefacto JSON incluye `meta`; todo artefacto Markdown, un front-matter YAML equivalente. Lo escribe **siempre** `ArtifactStore` (los agentes nunca lo redactan) y se valida con `schemas/envelope.schema.json` (`scripts/validate_artifact.py`).

```yaml
schema_version: "3.0"
artifact_id: art_07_borrador
version: 3
generado_por:
  agente: a07_redactor
  proveedor: openrouter         # openrouter | claude_code | claude_cli | lmstudio | humano | script
  modelo: anthropic/claude-sonnet-5.5
  version_prompt: "07_redactor@3.0"
timestamp: 2026-10-28T15:04:05Z
entradas:
  - { artifact: art_05_resultados, version: 1, sha256: "sha256:..." }
sha256: "sha256:..."            # del contenido (JSON sin meta.sha256/costo, o cuerpo Markdown)
costo: { tokens_entrada: 0, tokens_salida: 0, usd: 0.0 }
# campos específicos: iteracion, responde_a, modo, incluye_arquitectura, preregistro, decision, ronda
```

**Convenciones:** claves en ASCII y en español (`anio`, no `año`); los artefactos nunca se sobrescriben (`_v1`, `_v2`...); los importados o editados por humanos llevan `proveedor: humano`.

---

## 6. Puertas de calidad y ruteo

### 6.1 Puertas duras (deterministas, no negociables)
| Puerta | Implementación | Se aplica en |
|---|---|---|
| Artefacto válido según esquema | `gates.validate_json` / `validate_artifact.py` | Todas las salidas JSON (reintento con los errores) |
| Citas del marco ∈ corpus | `MarcoAgent.check` | A2 |
| Metadatos verificados (título, autor, año) | `academic.verify` | A3 |
| Toda cita ∈ `art_refs.bib` | `check_citations.py` | A4, A7, A8 |
| Todo número del texto ∈ artefactos; en Resumen/Resultados ∈ art_05 | `check_numbers.py` (normaliza coma decimal y porcentajes; ignora años, enteros ≤ 12, citas, código, fórmulas, URLs, identificadores) | A8 |
| Estructura = `secciones_borrador` + obligatorias | `check_structure` | A7, A8 |
| Tablas comparativas al final de *Marco Teórico* y *Trabajos Relacionados* (≥ 3 columnas, ≥ 2 filas, solo leyenda después) | `check_comparative_tables` | A7, A8 |
| Similitud máxima (n-gramas de 8 palabras) < umbral | `check_similarity` contra resúmenes de art_03 y el corpus | A8 |
| Hash de art_04 = hash congelado | orquestador | A5, A10 |
| Presupuesto no excedido (total y por agente) | `CostLog.check` | Antes de cada llamada |

### 6.2 Quality Gate 1 (A8)
```
aprueba = todas_las_puertas_duras_ok
          AND score_global >= puertas.score_global_min (85)
          AND min(criterios) >= puertas.criterio_min (75)

si aprueba                      → A9 (o HITL 3 si es una ronda de peer review)
si no aprueba y N < max_iter    → A7 con art_08_v{N}
si no aprueba y N == max_iter   → H_ESCALA: aceptar, una vuelta más con comentario, o detener
```

### 6.3 Ruteo del dictamen (A10)
| Decisión | Acción |
|---|---|
| Accept | `FIN`: se empaqueta la entrega (§7) |
| Minor Revision | A7 con art_10 → A8 → HITL 3 → D2 → A10 (máx. `max_rondas_peer_review`) |
| Minor sin rondas, Major Revision, Reject | **H_DICTAMEN**: `fin` (cerrar y empaquetar), `a7` (revisar texto) o `a4` (rediseño: se descongela el preregistro y se repite HITL 2) |

---

## 7. Integridad académica y entrega final

* **Declaración de uso de IA:** generada por A7 con los `meta.generado_por` de todos los artefactos; el humano la revisa en HITL 3. Los agentes **no** son autores.
* **Preregistro:** tipo `prospectivo`, `post_hoc` o `importado`, con su hash, en `entrega/preregistro.json`; un post hoc debe declararse en Metodología y Limitaciones.
* **Evidencia tipificada:** lo autoreportado por un README nunca se presenta como medido.
* **Similitud:** chequeo en A8; el informe queda en art_08.
* **Licencias:** datasets y PDFs con su licencia en art_04 y en el corpus.
* **Paquete de entrega (`FIN`, carpeta `entrega/`):** `art_paper.{pdf,docx,tex,html,md}`, `art_refs.bib`, `arquitectura.drawio/.svg`, `mapa_mental.drawio/.svg[/.png]`, art_04, art_05, art_08, art_10, `preregistro.json` y `costos.jsonl`. El código queda identificado por repositorio + commit en art_05.

---

## 8. Observabilidad y costos

* `logs/costos.jsonl`: una línea por llamada (agente, modelo, proveedor, tokens, USD reportado por OpenRouter, duración).
* `logs/trazas.log`: cada paso del orquestador y cada llamada a herramienta.
* `python -m spectra status` muestra estado, iteración, gasto acumulado frente al presupuesto (total y por agente), preregistro, fases saltadas, HITL pendientes y artefactos.
* Al 80 % del presupuesto se avisa; al 100 % (total o por agente) el pipeline se pausa: se sube el límite en `proyecto.yaml` y se reanuda.

---

## 9. Resumen de enrutamiento

| # | Agente | Principal | Económico | Claude Code local | Artefacto | Puerta / ruteo |
|:-:|:--|:--|:--|:-:|:--|:--|
| 1 | Ideas + novedad | `openai/gpt-6-astra` | `moonshotai/kimi-k3` | — | `art_01_ideas.json` | **HITL 1** |
| 2 | Marco Teórico | `moonshotai/kimi-k3` | `google/gemini-3.8-flash` | — | `art_02_marco.md` | citas ∈ corpus (∥ con 3) |
| 3 | Estado del Arte | `google/gemini-3.8-flash` | `deepseek/deepseek-v4.1-flash` | — | `art_03_literatura.json` | verificación de metadatos (∥ con 2) |
| D1 | Generador .bib | script | — | — | `art_refs.bib` | refs resueltas / HD1 |
| 4 | Propuesta / Arquitectura | `anthropic/claude-opus-5.5` | `deepseek/deepseek-v4-pro-0813` | ✅ `a04-arquitectura` | `art_04` + `arquitectura.drawio` | **HITL 2** + hash |
| 4b | Ingeniería inversa | `anthropic/claude-opus-5.5` | `deepseek/deepseek-v4-pro-0813` | ✅ `a04b-arquitectura-inversa` | `art_04` (post hoc) | **HITL 2** + hash |
| 5 | Implementador | `openai/gpt-6.1-sol` | `deepseek/deepseek-v4-pro` | — | `art_05_resultados.json` | FAILED → H5F |
| 5b | Documentador-Evaluador | `openai/gpt-6.1-sol` | `deepseek/deepseek-v4-pro` | — | `art_05` + mecanismos | **HITL 5** |
| 6 | Mapa Mental | `google/gemini-3.8-flash` (+ `gemini-3.1-flash-image`) | `deepseek/deepseek-v4.1-flash` | — | `mapa_mental.drawio` (+ `.png`) | **HITL 2b** (opcional) |
| 7 | Redactor | `anthropic/claude-sonnet-5.5` | `meta-llama/llama-4-maverick` | ✅ `a07-redactor` | `art_07_borrador_vN.md` | → A8 |
| 8 | Revisor | `qwen/qwen3.8-max-0902` | `deepseek/deepseek-v4.1-flash` | — | `art_08_revision_vN.json` | **QG 1**, máx. 3 vueltas |
| 9 | Asesor | `anthropic/claude-fable-5.1` | `anthropic/claude-sonnet-5.5` | ✅ `a09-asesor` | `art_09_asesor.md` | A7 integra → **HITL 3** |
| D2 | Compilador | script | — | — | `art_paper.pdf` | compila / HD2 |
| 10 | Peer Review | `openai/gpt-6-astra` | `moonshotai/kimi-k3` | — | `art_10_dictamen.md` | Ruteo §6.3 |
| — | Orquestador | Python (o `claude_code`, o clase propia) | — | skill `/spectra` | `pipeline_state.json` | — |

---

## 10. Pendientes

1. Plantilla LaTeX/CSL de la revista objetivo (hoy: APA o IEEE en el motor interno; Pandoc usa su CSL por defecto).
2. Umbral de similitud y checklist de reproducibilidad por área (configurables en `puertas` y `config/`).
3. Sandbox con aislamiento de red real en Windows para A5 (hoy: Docker opcional; sin Docker, subprocess con tiempo límite).
4. Probado de punta a punta (2026-10-28) con `projects/vertexav-link` en modo mixto: A1–A3 por OpenRouter con el orquestador Python; desde A4b, orquestador `claude_code` con A4b (Opus), A7 (Sonnet) y A9 (Fable) como subagentes locales y el resto por OpenRouter. Falta probar el subagente `a04-arquitectura` (preset `completo`) y el agente A5 de implementación nueva.
5. Abrir los `.drawio` generados en draw.io de escritorio y web (validados como XML bien formado y con ida y vuelta drawio → JSON en las pruebas).
