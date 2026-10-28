"""CLI de SPECTRA:  python -m spectra <comando> -p projects/<nombre>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, Config
from .util import dumps, load_object

PROYECTO_TPL = """# Proyecto SPECTRA. Cualquier clave de spectra.config.yaml puede sobrescribirse aquí.
proyecto:
  nombre: {nombre}
  tema: "Describe aquí el tema de investigación"
  pregunta: "Pregunta de investigación o contexto"

orquestador:
  perfil_modelos: principal
  presupuesto_usd_total: 20

flujo:
  preset: completo            # completo | implementacion_existente | desde_propuesta | solo_redaccion | solo_revision

# existentes:
#   implementacion:
#     repo: C:/ruta/al/repositorio
#     comandos_evaluacion:
#       - {{ cmd: "python -m pytest -q", cwd: tests, descripcion: "pruebas automáticas" }}
#   art_03_literatura: ruta/a/literatura.json

hitl:
  modo: archivo
"""


def orchestrator(cfg: Config):
    impl = cfg.orq.get("implementacion", "python")
    if impl and ":" in impl:
        return load_object(impl)(cfg)
    from .orchestrator import Orchestrator
    return Orchestrator(cfg)


def _cfg(a) -> Config:
    over = {}
    if getattr(a, "perfil", None):
        over.setdefault("orquestador", {})["perfil_modelos"] = a.perfil
    if getattr(a, "hitl", None):
        over.setdefault("hitl", {})["modo"] = a.hitl
    return Config(Path(a.proyecto), over)


def cmd_init(a):
    d = Path(a.proyecto)
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("artifacts", "state", "logs", "corpus/pdfs", "paper"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    f = d / "proyecto.yaml"
    if not f.exists():
        f.write_text(PROYECTO_TPL.format(nombre=d.name), encoding="utf-8")
    print(f"Proyecto creado en {d}. Edita {f} y ejecuta: python -m spectra run -p {d}")


def cmd_run(a):
    cfg = _cfg(a)
    mode = cfg.orchestrator_mode()
    if mode == "claude_code" and not a.forzar_python:
        print("orquestador.implementacion = claude_code: abre Claude Code en la raíz de SPECTRA y usa la skill "
              f"`/spectra start {a.proyecto}`. (Usa --forzar-python para ejecutar con el orquestador Python.)")
        return
    o = orchestrator(cfg)
    final = o.run(max_pasos=a.max_pasos)
    print(dumps(o.plan()) if hasattr(o, "plan") else final)


def cmd_status(a):
    cfg = _cfg(a)
    from .state import State
    st = State(cfg.project_dir)
    from .artifacts import ArtifactStore
    store = ArtifactStore(cfg.project_dir)
    arts = sorted(p.name for p in store.dir.iterdir() if p.is_file())
    o = orchestrator(cfg)
    info = o.plan()
    info["iteracion_revision"] = st["iteracion_revision"]
    info["rondas_peer_review"] = st["rondas_peer_review"]
    info["preregistro"] = {"hash": st["hash_preregistro"], "tipo": st["preregistro_tipo"]}
    info["saltados"] = st["saltados"]
    info["gasto_por_agente"] = st["gasto_por_agente"]
    info["artefactos"] = arts
    info["ultimos_pasos"] = st["historial"][-8:]
    print(dumps(info))


def cmd_decide(a, decision=None):
    cfg = _cfg(a)
    from .hitl import record_decision
    from .state import State
    st = State(cfg.project_dir)
    record_decision(st, a.hitl_id, decision or a.opcion, a.comentario or "")
    print(f"{a.hitl_id}: {decision or a.opcion} registrado. Continúa con: python -m spectra resume -p {a.proyecto}")


def cmd_edit(a):
    cfg = _cfg(a)
    from .artifacts import ArtifactStore
    from .hitl import record_decision
    from .state import State
    store = ArtifactStore(cfg.project_dir)
    src = Path(a.archivo)
    if src.suffix == ".drawio" and a.artefacto == "art_06_mapa.graph":
        from .drawio import from_drawio
        prev = store.read(a.artefacto)[1] if store.exists(a.artefacto) else {}
        g = from_drawio(src)
        g["raiz"] = prev.get("raiz") if prev.get("raiz") in {n["id"] for n in g["nodos"]} else g["nodos"][0]["id"]
        ids = {n["id"] for n in g["nodos"]}
        g["secciones_borrador"] = [{**s, "nodos": [n for n in s.get("nodos", []) if n in ids]} for s in prev.get("secciones_borrador", [])]
        v, p = store.write(a.artefacto, g, agente="humano", proveedor="humano", modelo="drawio2json")
    else:
        v, p = store.import_file(a.artefacto, src, nota="editado por humano")
    st = State(cfg.project_dir)
    if a.hitl_id in st["hitl"]:
        record_decision(st, a.hitl_id, "aprobar", f"editado por el humano: {p.name}")
    print(f"Guardado {p} y {a.hitl_id} aprobado. Continúa con resume.")


def cmd_plan(a):
    print(dumps(orchestrator(_cfg(a)).plan()))


def cmd_record(a):
    o = orchestrator(_cfg(a))
    res = o.record_subagent(Path(a.archivo) if a.archivo else None)
    print(dumps(res))
    if not res.get("ok"):
        sys.exit(1)


def cmd_sync(a):
    from .sync_agents import sync
    for p in sync(Config(Path(a.proyecto) if a.proyecto else ROOT)):
        print("generado", p)


def cmd_models(a):
    import requests
    cfg = Config(Path(a.proyecto) if a.proyecto else ROOT)
    data = requests.get(cfg.provider_conf("openrouter")["base_url"] + "/models", timeout=60).json()["data"]
    ids = {m["id"]: m for m in data}
    ok = True
    for aid, conf in cfg.data["agentes"].items():
        for perfil in ("principal", "economico", "imagen", "override"):
            spec = conf.get(perfil) or {}
            mid = spec.get("openrouter_id")
            if not mid or spec.get("proveedor", "openrouter") != "openrouter":
                continue
            m = ids.get(mid)
            if m:
                p = m.get("pricing", {})
                print(f"OK   {aid:28s} {perfil:10s} {mid:42s} in ${float(p.get('prompt', 0)) * 1e6:.2f}/M out ${float(p.get('completion', 0)) * 1e6:.2f}/M")
            else:
                ok = False
                print(f"FALTA {aid:27s} {perfil:10s} {mid}")
    sys.exit(0 if ok else 1)


def cmd_gates(a):
    cfg = _cfg(a)
    from .orchestrator import Context
    ctx = Context(cfg)
    text = Path(a.archivo).read_text(encoding="utf-8") if a.archivo else ctx.store.read("art_07_borrador")[1]
    from .artifacts import split_front_matter
    print(dumps(ctx.hard_gates(split_front_matter(text)[1])))


def cmd_revise(a):
    """Reabre un proyecto (incluso en FIN) para que A7 revise el borrador con un comentario del investigador."""
    cfg = _cfg(a)
    from .orchestrator import Orchestrator
    o = Orchestrator(cfg)
    st = o.st
    st["feedback_pendiente"] = o.comment_artifact("REVISE", a.comentario)
    st["modo_ronda"] = "peer"                 # tras aprobar A8 va a H3 (sin repetir el Asesor)
    st["iteracion_revision"] = 0
    st["entregado"] = False
    if a.sin_peer:
        st["detener_tras_D2"] = True
    st.set_state("S07_REDACCION", "revisión solicitada por el investigador")
    st.log("REVISE", "abierta", a.comentario[:200])
    print(f"Revisión abierta. Continúa con: python -m spectra run -p {a.proyecto}")


def cmd_compile(a):
    cfg = _cfg(a)
    from .orchestrator import Orchestrator
    o = Orchestrator(cfg)
    o.do_D2_COMPILA()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spectra", description="SPECTRA — Scientific Paper Engineering & Collaborative Trained Research Agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def P(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("-p", "--proyecto", default=kw.pop("default_proj", None) or ".", help="carpeta del proyecto")
        p.add_argument("--perfil", choices=["principal", "economico"])
        p.add_argument("--hitl", choices=["terminal", "archivo", "auto"])
        p.set_defaults(fn=fn)
        return p

    P("init", cmd_init, help="crea la carpeta de un proyecto")
    for n in ("run", "resume", "start"):
        p = P(n, cmd_run, help="ejecuta/reanuda el pipeline hasta el siguiente HITL")
        p.add_argument("--max-pasos", type=int, default=200)
        p.add_argument("--forzar-python", action="store_true")
    P("status", cmd_status, help="estado, gasto y artefactos")
    P("plan", cmd_plan, help="siguiente paso en JSON (para la skill de Claude Code)")
    p = P("approve", lambda a: cmd_decide(a, "aprobar"), help="aprueba un HITL")
    p.add_argument("hitl_id")
    p.add_argument("-c", "--comentario")
    p = P("reject", lambda a: cmd_decide(a, "rechazar"), help="rechaza un HITL")
    p.add_argument("hitl_id")
    p.add_argument("-c", "--comentario")
    p = P("decide", cmd_decide, help="elige una opción de un HITL")
    p.add_argument("hitl_id")
    p.add_argument("opcion")
    p.add_argument("-c", "--comentario")
    p = P("edit", cmd_edit, help="importa un artefacto editado por el humano y aprueba el HITL")
    p.add_argument("hitl_id")
    p.add_argument("artefacto")
    p.add_argument("archivo")
    p = P("record", cmd_record, help="registra la salida de un subagente de Claude Code")
    p.add_argument("archivo", nargs="?")
    p = P("gates", cmd_gates, help="ejecuta las puertas duras sobre un borrador")
    p.add_argument("archivo", nargs="?")
    P("compile", cmd_compile, help="ejecuta D2 sobre el último borrador")
    p = P("revise", cmd_revise, help="reabre el proyecto: A7 revisa con tu comentario -> A8 -> H3 -> D2 (-> A10)")
    p.add_argument("-c", "--comentario", required=True)
    p.add_argument("--sin-peer", action="store_true", help="terminar tras compilar, sin nuevo Peer Review")
    P("sync-agents", cmd_sync, help="genera .claude/agents y la skill desde agents/specs + config")
    P("models-check", cmd_models, help="verifica los IDs de modelo contra OpenRouter")
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
