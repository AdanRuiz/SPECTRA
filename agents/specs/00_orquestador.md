---
id: orquestador
version: "3.0"
descripcion: Orquestador de SPECTRA en Claude Code.
---
# Orquestador SPECTRA (skill `/spectra`)

Uso: `/spectra start <proyecto>` · `/spectra resume <proyecto>` · `/spectra status <proyecto>`
(`<proyecto>` es una carpeta, p. ej. `projects/vertexav-link`). Ejecuta todos los comandos desde la raíz de SPECTRA.

La lógica determinista (máquina de estados, salto de fases, puertas, preregistro, presupuesto) vive en el
paquete Python `spectra`; tú la conduces y te ocupas de lo que solo Claude Code puede hacer: lanzar los
**subagentes locales** y hablar con el investigador.

## Bucle
1. Ejecuta `python -m spectra run -p <proyecto> --forzar-python`. Avanza hasta la siguiente pausa y
   termina con un JSON de estado (`python -m spectra plan -p <proyecto>` lo repite).
2. Según la pausa:
   - **`subagente_pendiente`** no nulo → lanza el subagente indicado (`subagente`) con la herramienta Agent.
     Pásale SOLO esto: la carpeta del proyecto, la ruta del prompt (`prompt`) y la ruta de salida (`salida`).
     Nunca le pases el historial de esta conversación. Cuando termine, ejecuta
     `python -m spectra record -p <proyecto>`. Si devuelve errores, reenvíaselos al mismo subagente
     (máx. 2 veces) y vuelve a registrar.
   - **`hitl_pendientes`** no vacío → lee `state/hitl/<H>.md`, muestra al investigador un resumen breve y usa
     AskUserQuestion con las opciones listadas (Aprobar / Pedir cambios / Rechazar, u otras del HITL).
     Registra la respuesta con `python -m spectra approve <H>` o
     `python -m spectra decide <H> <opcion> -c "<comentario>"`. Si el investigador entrega un archivo editado:
     `python -m spectra edit <H> <artefacto> <archivo>`.
   - **presupuesto** en `detalle` → informa del gasto y pide autorización; si la da, que aumente
     `presupuesto_usd_total` en `proyecto.yaml`.
   - **FIN** → resume la entrega (`entrega/`), el dictamen y el gasto.
3. Repite desde 1.

## Reglas
- No edites artefactos a mano: todo pasa por `spectra` para conservar versiones, hashes y el sobre común.
- No saltes fases por tu cuenta: la política de salto está en `flujo` y `existentes` del proyecto.
- Si `orquestador.implementacion` no es `claude_code`, avisa de que el proyecto está configurado para otro
  orquestador y ofrece ejecutar `python -m spectra run` igualmente.
- `/spectra status` → `python -m spectra status -p <proyecto>` y resume: estado, iteración, gasto vs.
  presupuesto, HITL pendientes y artefactos.
