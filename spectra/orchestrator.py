"""Orquestador de SPECTRA (modo python): máquina de estados §2.2 con salto de fases,
presets de flujo, HITL, puertas de calidad, preregistro congelado y presupuesto."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import drawio
from .agents.base import AgentError, NeedsClaudeCode
from .agents.impl import make_agent
from .artifacts import ArtifactStore, content_hash, now_iso
from .bib import build_bib
from .compile import compile_paper
from .config import Config
from .gates import check_citations, check_comparative_tables, check_numbers, check_similarity, check_structure, numbers_in
from .hitl import HITL
from .providers import ProviderError
from .state import BudgetExceeded, CostLog, State
from .tools.corpus import Corpus
from .tools.repo import Repo
from .util import dumps, make_bib_key, truncate

ORDER = ["S01_IDEAS", "H1", "S02_S03", "D1_BIB", "S04_PROPUESTA", "H2", "S05_IMPL", "H5", "S06_MAPA", "H2b",
         "S07_REDACCION", "S08_REVISION", "S09_ASESOR", "S09_INTEGRA", "H3", "D2_COMPILA", "S10_PEER", "FIN"]

DEFAULT_AGENTS = {"S01_IDEAS": "a01_ideas", "S02": "a02_marco", "S03": "a03_estado_arte",
                  "S04_PROPUESTA": "a04_arquitectura", "S05_IMPL": "a05_implementador", "S06_MAPA": "a06_mapa_mental",
                  "S07_REDACCION": "a07_redactor", "S08_REVISION": "a08_revisor", "S09_ASESOR": "a09_asesor",
                  "S10_PEER": "a10_peer_review"}

OUTPUTS = {"S01_IDEAS": ["art_01_ideas"], "S02": ["art_02_marco"], "S03": ["art_03_literatura"], "D1_BIB": ["art_refs"],
           "S04_PROPUESTA": ["art_04_propuesta"], "S05_IMPL": ["art_05_resultados"], "S06_MAPA": ["art_06_mapa.graph"],
           "S07_REDACCION": ["art_07_borrador"]}

_SALTAR_HASTA = lambda last: {s: "saltar" for s in ORDER[:ORDER.index(last) + 1] if s in OUTPUTS or s.startswith("H") or s == "S02_S03"}

PRESETS = {
    "completo": {"agentes": {}, "fases": {}},
    "implementacion_existente": {"agentes": {"S04_PROPUESTA": "a04b_arquitectura_inversa", "S05_IMPL": "a05b_documentador"},
                                 "fases": {}},
    "desde_propuesta": {"agentes": {}, "fases": {"S01_IDEAS": "saltar", "H1": "saltar", "S02_S03": "saltar"}},
    "solo_redaccion": {"agentes": {}, "fases": _SALTAR_HASTA("H5")},
    "solo_revision": {"agentes": {}, "fases": _SALTAR_HASTA("S07_REDACCION")},
}

IMPORTABLES = ["art_01_ideas", "art_02_marco", "art_03_literatura", "art_04_propuesta", "art_05_resultados",
               "art_06_mapa.graph", "art_07_borrador"]


class Pause(Exception):
    """Detiene el bucle (HITL pendiente, subagente pendiente, presupuesto)."""


class Context:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.project_dir = cfg.project_dir
        self.store = ArtifactStore(self.project_dir)
        self.state = State(self.project_dir)
        self.costs = CostLog(self.project_dir, self.state, cfg)
        self.lock = threading.RLock()
        self.seen_records: dict = {}
        self.sandbox_runs: list = []
        self.paper_dir = self.project_dir / "paper"
        ab = self.project_dir / "logs" / "a03_abstracts.json"
        self.abstracts: dict = json.loads(ab.read_text(encoding="utf-8")) if ab.exists() else {}
        self._corpus = None
        self._repo = None
        (self.project_dir / "logs").mkdir(exist_ok=True)

    # --- recursos perezosos -------------------------------------------------------
    @property
    def corpus(self) -> Corpus:
        if self._corpus is None:
            self._corpus = Corpus(self.project_dir)
        return self._corpus

    @property
    def repo(self) -> Repo | None:
        imp = self.cfg.get("existentes.implementacion")
        if not imp:
            return None
        if self._repo is None:
            self._repo = Repo(imp["repo"], imp.get("comandos_evaluacion"), imp.get("cwd"), imp.get("timeout_s", 600),
                              imp.get("repos_adicionales"), imp.get("excluir"))
        return self._repo

    def trace(self, who: str, msg: str) -> None:
        line = f"{now_iso()} [{who}] {msg}"
        with self.lock:
            print(line, flush=True)
            with (self.project_dir / "logs" / "trazas.log").open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    # --- implementación existente -------------------------------------------------------
    def repo_brief(self, limit: int = 40000) -> str:
        r = self.repo
        if not r:
            return ""
        s = r.summary()
        parts = [f"Raíz: {s['raiz']} · archivos: {s['n_archivos']} · líneas por lenguaje: {s['lineas_por_lenguaje']}",
                 f"Git: commit {s['git']['commit'][:12]} · remoto {s['git']['remoto']} · sucio={s['git']['sucio']}",
                 "## Historial (git log)\n" + s["git"]["log"],
                 "## Árbol\n" + "\n".join(s["arbol"][:400]),
                 "## Comandos de evaluación declarados\n" + dumps(s["comandos_evaluacion_disponibles"])]
        for e in s.get("repos_adicionales", []):
            parts.append(f"## Repositorio adicional `{e['alias']}` ({e['descripcion']}); lee sus archivos como `{e['alias']}:ruta`\n"
                         f"Rutas incluidas: {e['incluir']}\n```json\n{truncate(dumps(e['git']), 6000)}\n```")
        readme = r.root / "README.md"
        if readme.exists():
            parts.append("## README.md\n" + truncate(readme.read_text(encoding="utf-8", errors="ignore"), 12000))
        brief = "\n\n".join(parts)
        docs = self._repo_docs()
        if docs:
            brief += "\n\n## Documentos del proyecto (indexados en el corpus; cítalos por su clave)\n" + "\n".join(
                f"- @{k}: {d.get('titulo')} ({d.get('archivo_rel')})" for k, d in docs.items())
        return truncate(brief, limit)

    def _repo_docs(self) -> dict:
        return {k: d for k, d in self.corpus.docs.items() if d.get("tipo") in ("documento_proyecto", "software")}

    def ensure_corpus(self) -> None:
        with self.lock:
            if self.state.get("corpus_ingestado"):
                return
            c = self.corpus
            c.ingest_dir(self.project_dir / "corpus" / "pdfs")
            if self.cfg.get("existentes.corpus_pdfs"):
                c.ingest_dir(Path(self.cfg.get("existentes.corpus_pdfs")))
            r = self.repo
            if r:
                imp = self.cfg.get("existentes.implementacion")
                g = r.git_info()
                try:
                    autores = subprocess.run(["git", "-C", str(r.root), "log", "--format=%an"], capture_output=True, text=True,
                                             encoding="utf-8").stdout.split("\n")
                    autores = list(dict.fromkeys(a for a in autores if a.strip()))
                    anio = int(subprocess.run(["git", "-C", str(r.root), "log", "-1", "--format=%ad", "--date=format:%Y"],
                                              capture_output=True, text=True).stdout.strip() or 0) or None
                except Exception:
                    autores, anio = [], None
                autores = imp.get("autores") or autores
                remoto = (g.get("remoto") or "").removesuffix(".git")
                nombre = imp.get("nombre") or r.root.name
                c.add_text(f"{nombre}. Repositorio de software. " + (r.root / "README.md").read_text(encoding="utf-8", errors="ignore")
                           if (r.root / "README.md").exists() else nombre,
                           {"clave": imp.get("clave_bib") or make_bib_key(autores, anio, nombre), "titulo": f"{nombre}",
                            "autores": autores, "anio": anio, "url": remoto or str(r.root), "tipo": "software",
                            "version": (g.get("commit") or "")[:10], "archivo_rel": "README.md", "venue": "Repositorio de software"})
                docs = [p for p in r.files() if p.suffix.lower() == ".md" and p.name.lower() != "readme.md"
                        and ".github" not in p.parts and len(p.relative_to(r.root).parts) <= 3]
                docs += [p for p in r.files() if p.name.lower() == "readme.md" and p.parent != r.root
                         and len(p.relative_to(r.root).parts) <= 3]
                for p in docs[:20]:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"^#\s+(.+)$", text, re.M)
                    titulo = (m.group(1).strip() if m else p.stem)
                    rel = p.relative_to(r.root).as_posix()
                    if p.name.lower() == "readme.md":
                        titulo = f"{nombre}: {titulo}"
                    slug = re.sub(r"[^a-z0-9]", "", (p.parent.name if p.name.lower() == "readme.md" else p.stem).lower())
                    clave = re.sub(r"\d+[a-z]*$", "", make_bib_key(autores, anio, "x")) + str(anio or "nd") + slug
                    c.add_text(text, {"clave": clave, "titulo": titulo, "autores": autores,
                                      "anio": anio, "url": f"{remoto}/blob/{g.get('commit', 'main')[:10]}/{rel}" if remoto else rel,
                                      "tipo": "documento_proyecto", "archivo": str(p), "archivo_rel": rel,
                                      "venue": f"Documento técnico del repositorio {nombre}"}, paginado=True)
            c.save()
            self.state["corpus_ingestado"] = True
            self.state.save()

    # --- puertas duras ------------------------------------------------------------------------
    def allowed_numbers(self) -> tuple[set, set]:
        st = self.store
        strict = numbers_in(st.read("art_05_resultados")[1]) if st.exists("art_05_resultados") else set()
        extra = self.cfg.get("existentes.resultados_adicionales")
        if extra and Path(extra).exists():
            strict |= numbers_in(Path(extra).read_text(encoding="utf-8"))
        allp = set(strict)
        for a in ("art_01_ideas", "art_02_marco", "art_03_literatura", "art_04_propuesta", "art_06_mapa.graph", "art_04_arquitectura.graph"):
            if st.exists(a):
                allp |= numbers_in(st.read_text(a))
        bib = st.dir / "art_refs.bib"
        if bib.exists():
            allp |= numbers_in(bib.read_text(encoding="utf-8"))
        return strict, allp

    def similarity_sources(self) -> dict[str, str]:
        src = dict(self.abstracts)
        by: dict[str, list[str]] = {}
        for ch in self.corpus.chunks:
            by.setdefault(ch["clave"], []).append(ch["texto"])
        src.update({k: " ".join(v) for k, v in by.items()})
        return src

    def hard_gates(self, draft: str) -> dict:
        from .agents.impl import RedactorAgent, bib_text
        g = self.cfg.get("puertas", {})
        strict, allp = self.allowed_numbers()
        req = RedactorAgent("a07_redactor", self).required_sections()
        cit = check_citations(draft, bib_text(self))
        num = check_numbers(draft, strict, allp, g)
        est = check_structure(draft, req)
        tab = check_comparative_tables(draft)
        sim = check_similarity(draft, self.similarity_sources(), int(g.get("ngram_similitud", 8)), float(g.get("similitud_max", 0.15)))
        return {"citas_existentes": {"ok": cit["ok"], "faltantes": cit["faltantes"]},
                "numeros_consistentes": {"ok": num["ok"], "discrepancias": num["discrepancias"]},
                "estructura_valida": {"ok": est["ok"], "faltantes": est["faltantes"]},
                "tablas_comparativas": {"ok": tab["ok"], "faltantes": tab["faltantes"]},
                "similitud_max": {"ok": sim["ok"], "valor": sim["valor"], "fuente": sim["fuente_max"]}}


class Orchestrator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ctx = Context(cfg)
        self.st = self.ctx.state
        self.store = self.ctx.store
        self.hitl = HITL(self.ctx)
        preset = cfg.get("flujo.preset", "completo")
        if cfg.get("existentes.implementacion") and preset == "completo":
            preset = "implementacion_existente"
        self.preset = preset
        p = PRESETS.get(preset)
        if p is None:
            raise ValueError(f"Preset de flujo desconocido: {preset}")
        self.agent_map = {**DEFAULT_AGENTS, **p["agentes"], **(cfg.get("flujo.agentes_por_estado") or {})}
        self.policies = {**p["fases"], **(cfg.get("flujo.fases") or {})}

    # --- utilidades -------------------------------------------------------------------------
    def agent_for(self, state: str) -> str:
        return self.agent_map[state]

    def exists(self, art: str) -> bool:
        if art == "art_refs":
            return (self.store.dir / "art_refs.bib").exists()
        return self.store.exists(art)

    def should_skip(self, state: str, outputs_key: str | None = None) -> bool:
        key = outputs_key or state
        pol = self.policies.get(key) or self.policies.get(state) or "auto"
        visited = state in self.st.data.setdefault("visitados", [])
        agent = self.agent_map.get(key)
        if agent and self.cfg.get(f"agentes.{agent}.habilitado", True) is False:
            pol = "saltar"
        if pol == "ejecutar":
            return False
        done = self.st.get("subagente_completado") or {}
        if agent and done.get("agente") == agent:    # su artefacto lo acaba de producir un subagente: no es "preexistente"
            return False
        outs = OUTPUTS.get(key, [])
        have = all(self.exists(o) for o in outs) if outs else True
        if pol == "saltar":
            if outs and not have:
                raise RuntimeError(f"La fase {key} está marcada para saltarse pero faltan sus artefactos {outs}. "
                                   f"Decláralos en `existentes` del proyecto.")
            return True
        return bool(outs) and have and not visited and self.cfg.get("flujo.saltar_si_existe", True)

    def mark(self, state: str, resultado: str, nota: str = "") -> None:
        v = self.st.data.setdefault("visitados", [])
        if state not in v:
            v.append(state)
        if resultado == "saltado":
            self.st["saltados"].append(state)
        self.st.log(state, resultado, nota)

    def import_existing(self) -> None:
        ex = self.cfg.get("existentes") or {}
        done = self.st.data.setdefault("importados", [])
        for art in IMPORTABLES:
            src = ex.get(art)
            if src and art not in done:
                v, p = self.store.import_file(art, Path(src))
                done.append(art)
                self.ctx.trace("orquestador", f"importado {art} desde {src} -> {p.name}")
        if ex.get("art_refs") and "art_refs" not in done:
            shutil.copyfile(ex["art_refs"], self.store.dir / "art_refs.bib")
            done.append("art_refs")
        self.st.save()

    def run_agent(self, agent_id: str, **kw) -> dict:
        done = self.st.get("subagente_completado")
        if done and done.get("agente") == agent_id:          # salida ya registrada con `spectra record`
            self.st["subagente_completado"] = None
            self.st.save()
            self.ctx.trace("orquestador", f"<- {agent_id} (subagente de Claude Code, registrado) {done.get('resultado')}")
            return done.get("resultado") or {}
        tries = int(self.cfg.orq.get("max_reintentos_agente", 2))
        last = None
        for i in range(tries + 1):
            agent = make_agent(agent_id, self.ctx)
            self.ctx.trace("orquestador", f"-> {agent_id} [{agent.model.label()}] intento {i + 1}")
            try:
                res = agent.run(**kw)
                self.ctx.trace("orquestador", f"<- {agent_id} ok ({agent.usage['usd']:.4f} USD) {res}")
                return res
            except NeedsClaudeCode:
                self.request_subagent(agent, kw)
                raise Pause(f"subagente {agent.model.subagente} pendiente")
            except BudgetExceeded as e:
                self.st.set_state(self.st["estado"], f"presupuesto: {e}")
                raise Pause(str(e))
            except ProviderError as e:
                if "HTTP 402" in str(e) or "HTTP 401" in str(e):      # sin crédito / clave inválida: pausar
                    self.st.set_state(self.st["estado"], f"proveedor: {str(e)[:300]}")
                    raise Pause(f"proveedor sin crédito o sin acceso: {str(e)[:200]}")
                last = e
                self.ctx.trace("orquestador", f"!! {agent_id} error de proveedor: {str(e)[:300]}")
            except AgentError as e:
                last = e
                self.ctx.trace("orquestador", f"!! {agent_id} falló: {e}")
        raise AgentError(f"{agent_id} falló tras {tries + 1} intentos: {last}")

    def request_subagent(self, agent, kw: dict) -> None:
        d = self.ctx.project_dir / "state" / "subagente"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{agent.id}.system.md").write_text(agent.system_prompt(), encoding="utf-8")
        (d / f"{agent.id}.prompt.md").write_text(agent.build_user(**kw), encoding="utf-8")
        out = d / f"{agent.id}.out.md"
        self.st["subagente_pendiente"] = {"agente": agent.id, "subagente": agent.model.subagente,
                                          "modelo": agent.model.claude_code_modelo, "kwargs": kw,
                                          "prompt": str(d / f"{agent.id}.prompt.md"), "salida": str(out)}
        self.st.save()
        print(json.dumps({"accion": "invocar_subagente", **self.st["subagente_pendiente"]}, ensure_ascii=False))

    def record_subagent(self, out_file: Path | None = None) -> dict:
        p = self.st.get("subagente_pendiente")
        if not p:
            raise RuntimeError("No hay subagente pendiente")
        agent = make_agent(p["agente"], self.ctx)
        text = Path(out_file or p["salida"]).read_text(encoding="utf-8")
        errs = agent.check(text) if hasattr(agent, "check") else []
        if errs:
            return {"ok": False, "errores": errs}
        res = agent.postprocess(text, **p["kwargs"])
        self.st["subagente_pendiente"] = None
        self.st["subagente_completado"] = {"agente": p["agente"], "resultado": res}
        self.st.save()
        return {"ok": True, **res}

    def _resuming(self, *hs: str) -> bool:
        """True si alguno de estos HITL está pendiente o ya respondido (la fase se está reanudando)."""
        return any(h in self.st["hitl"] for h in hs)

    def comment_artifact(self, h: str, comentario: str) -> str:
        self.store.write("art_00_comentarios", f"# Comentarios del investigador ({h})\n\n{comentario}\n",
                         agente="humano", proveedor="humano", modelo=h)
        return "art_00_comentarios"

    # --- bucle principal --------------------------------------------------------------------------
    def run(self, max_pasos: int = 200) -> str:
        self.import_existing()
        fam = self.cfg.family_violations()
        if fam:
            msg = "Heterogeneidad de familias incumplida: " + "; ".join(fam)
            if self.cfg.get("puertas.exigir_familias_distintas", True):
                self.st.set_state(self.st["estado"] or "S01_IDEAS", msg)
                self.ctx.trace("orquestador", msg + " (cambia los modelos o pon puertas.exigir_familias_distintas: false)")
                return self.st["estado"]
            self.ctx.trace("orquestador", "AVISO: " + msg)
        if not self.st["estado"]:
            self.st.set_state("S01_IDEAS")
            self.ctx.trace("orquestador", f"inicio: preset={self.preset} modo={self.cfg.orchestrator_mode()} perfil={self.cfg.perfil}")
        for _ in range(max_pasos):
            s = self.st["estado"]
            if s == "FIN":
                self.do_FIN()
                return "FIN"
            try:
                nxt = getattr(self, f"do_{s}")()
            except Pause as e:
                self.ctx.trace("orquestador", f"pausa en {s}: {e}")
                return s
            if nxt is None:
                return s
            self.st.set_state(nxt)
        return self.st["estado"]

    # --- estados ---------------------------------------------------------------------------------
    def do_S01_IDEAS(self):
        if self.should_skip("S01_IDEAS"):
            self.mark("S01_IDEAS", "saltado", "art_01 ya existe")
            return "H1"
        self.run_agent(self.agent_for("S01_IDEAS"), comentarios=self.st.data.pop("comentarios_S01", None))
        self.mark("S01_IDEAS", "ok")
        return "H1"

    def do_H1(self):
        if "S01_IDEAS" in self.st["saltados"] or self.should_skip("H1") and self.policies.get("H1") == "saltar":
            self.mark("H1", "saltado")
            return "S02_S03"
        a = self.store.read("art_01_ideas")[1]
        hp = a.get("hipotesis_principal", {})
        res = [f"**Título:** {a.get('titulo_propuesto')}", f"**Problema:** {a.get('problema_investigacion')}",
               f"**H1:** {hp.get('enunciado')} — riesgo de novedad: **{hp.get('riesgo_novedad')}**",
               "**Secundarias:**\n" + "\n".join(f"- {h.get('id')}: {h.get('enunciado')}" for h in a.get("hipotesis_secundarias", [])),
               "**Enfoques:**\n" + "\n".join(f"- {e.get('nombre')}: + {e.get('pros')} / − {e.get('contras')}" for e in a.get("enfoques_metodologicos_sugeridos", [])),
               f"Artefacto: `{self.store.latest_path('art_01_ideas')}`"]
        d = self.hitl.ask("H1", "Aprobar ideas e hipótesis (art_01)", "\n\n".join(res))
        if d is None:
            return None
        self.mark("H1", d["decision"], d.get("comentario", ""))
        if d["decision"] == "aprobar":
            return "S02_S03"
        self.st["comentarios_S01"] = d.get("comentario") or "Rechazado: propone alternativas."
        self.st["visitados"].remove("S01_IDEAS") if "S01_IDEAS" in self.st["visitados"] else None
        self.policies["S01_IDEAS"] = "ejecutar"
        return "S01_IDEAS"

    def do_S02_S03(self):
        jobs = []
        for sub in ("S02", "S03"):
            if self.should_skip("S02_S03", sub):
                self.mark(sub, "saltado", f"{OUTPUTS[sub][0]} ya existe")
            else:
                jobs.append(self.agent_for(sub))
        if jobs:
            if "a02_marco" in jobs:
                self.ctx.ensure_corpus()
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = {ex.submit(self.run_agent, j): j for j in jobs}
                errors = []
                for f, j in futs.items():
                    try:
                        f.result()
                    except Pause:
                        raise
                    except Exception as e:
                        errors.append(f"{j}: {e}")
                if errors:
                    raise AgentError("; ".join(errors))
        self.mark("S02_S03", "ok")
        return "D1_BIB"

    def do_D1_BIB(self):
        if self.should_skip("D1_BIB") and self.policies.get("D1_BIB") == "saltar":
            self.mark("D1_BIB", "saltado")
            return "S04_PROPUESTA"
        self.ctx.ensure_corpus()
        art03 = self.store.read("art_03_literatura")[1] if self.store.exists("art_03_literatura") else None
        marco = self.store.read("art_02_marco")[1] if self.store.exists("art_02_marco") else None
        extra = self.cfg.get("bibliografia_extra") or []
        bib, informe, sin = build_bib(art03, marco, self.ctx.corpus.docs, extra)
        (self.store.dir / "art_refs.bib").write_text(bib, encoding="utf-8")
        (self.ctx.project_dir / "logs" / "d1_informe.json").write_text(dumps({"informe": informe, "sin_resolver": sin}), encoding="utf-8")
        self.ctx.trace("D1", f"art_refs.bib: {len(informe)} entradas, {len(sin)} sin resolver")
        if sin:
            d = self.hitl.ask("HD1", "Referencias sin resolver en D1",
                              "No se pudo resolver:\n" + "\n".join(f"- {k}" for k in sin) +
                              "\n\nAprobar = continuar sin ellas; rechazar = detener.")
            if d is None:
                return None
            if d["decision"] != "aprobar":
                raise Pause("D1 rechazado por el investigador")
        self.mark("D1_BIB", "ok", f"{len(informe)} entradas")
        return "S04_PROPUESTA"

    def do_S04_PROPUESTA(self):
        if self.should_skip("S04_PROPUESTA"):
            self.mark("S04_PROPUESTA", "saltado")
            return "H2"
        self.run_agent(self.agent_for("S04_PROPUESTA"), comentarios=self.st.data.pop("comentarios_S04", None))
        self.mark("S04_PROPUESTA", "ok")
        return "H2"

    def _freeze(self, tipo_default: str = "prospectivo"):
        meta, body = self.store.read("art_04_propuesta")
        self.st["hash_preregistro"] = content_hash("art_04_propuesta", body.strip() + "\n")
        self.st["preregistro_tipo"] = meta.get("preregistro") or tipo_default
        self.st["preregistro_version"] = meta.get("version")
        self.st.save()
        self.ctx.trace("orquestador", f"preregistro congelado ({self.st['preregistro_tipo']}): {self.st['hash_preregistro']}")

    def do_H2(self):
        if "S04_PROPUESTA" in self.st["saltados"] and self.st["hash_preregistro"] is None:
            self._freeze("importado")
            self.mark("H2", "saltado", "propuesta importada; hash congelado")
            return "S05_IMPL"
        meta, body = self.store.read("art_04_propuesta")
        m = re.search(r"^#+\s*(?:Protocolo|Experimental protocol|Evaluation protocol).*?(?=^#{1,2}\s|\Z)", body, re.M | re.S | re.I)
        res = [f"Propuesta: `{self.store.latest_path('art_04_propuesta')}` (v{meta.get('version')}, preregistro {meta.get('preregistro')})",
               f"Arquitectura: `{self.store.dir / 'arquitectura.drawio'}` (vista previa `arquitectura.svg`)" if meta.get("incluye_arquitectura") else "Sin arquitectura.",
               "### Protocolo\n" + truncate(m.group(0) if m else body[:3000], 5000),
               "Al aprobar se congela el hash SHA-256 de art_04 (preregistro). Cambios posteriores exigen volver a H2."]
        d = self.hitl.ask("H2", "Aprobar propuesta y congelar preregistro (art_04)", "\n\n".join(res))
        if d is None:
            return None
        self.mark("H2", d["decision"], d.get("comentario", ""))
        if d["decision"] == "aprobar":
            self._freeze()
            return "S05_IMPL"
        self.st["comentarios_S04"] = d.get("comentario") or "Rediseña la propuesta."
        self.policies["S04_PROPUESTA"] = "ejecutar"
        return "S04_PROPUESTA"

    def _check_hash(self):
        """El preregistro sigue intacto si (1) la última versión de art_04 es la congelada y (2) el hash RECALCULADO
        de su contenido coincide con el congelado (detecta ediciones a mano del archivo, no solo versiones nuevas)."""
        frozen = self.st["hash_preregistro"]
        if not frozen:
            return True
        meta, body = self.store.read("art_04_propuesta")
        actual = content_hash("art_04_propuesta", body.strip() + "\n")
        if meta.get("version") != self.st.get("preregistro_version") or actual != frozen:
            self.st["preregistro_violado"] = {"ts": now_iso(), "version": meta.get("version"), "hash_actual": actual}
            self.st.save()
            self.ctx.trace("orquestador", f"art_04 cambió tras el congelamiento ({actual} != {frozen}): vuelve a H2")
            return False
        return True

    def do_S05_IMPL(self):
        if not self._check_hash():
            return "H2"
        if self.should_skip("S05_IMPL"):
            self.mark("S05_IMPL", "saltado")
            return "S06_MAPA"
        agent = self.agent_for("S05_IMPL")
        diag = None
        if self._resuming("H5F", "H5P"):          # reanudación tras el HITL: no repetir el trabajo
            status = (self.store.read("art_05_resultados")[1] or {}).get("status_ejecucion", "SUCCESS")
        else:
            for i in range(int(self.cfg.orq.get("max_reintentos_agente", 2)) + 1):
                res = self.run_agent(agent, diagnostico=diag)
                status = (self.store.read("art_05_resultados")[1] or {}).get("status_ejecucion", "SUCCESS")
                if status != "FAILED":
                    break
                diag = truncate(dumps(self.store.read("art_05_resultados")[1]), 8000)
            self.mark("S05_IMPL", status)
        if status == "FAILED":
            d = self.hitl.ask("H5F", "La implementación falló (FAILED)", "Opciones: volver a A4 (rediseño) o detener.",
                              {"a4": "Volver a A4 (nueva propuesta, nuevo H2)", "rechazar": "Detener"})
            if d is None:
                return None
            if d["decision"] == "a4":
                self.policies["S04_PROPUESTA"] = "ejecutar"
                return "S04_PROPUESTA"
            raise Pause("detenido por el investigador")
        if status == "PARTIAL":
            d = self.hitl.ask("H5P", "Resultados parciales (PARTIAL)", f"Revisa `{self.store.latest_path('art_05_resultados')}`.")
            if d is None:
                return None
            if d["decision"] != "aprobar":
                self.policies["S05_IMPL"] = "ejecutar"
                return "S05_IMPL"
        return "H5"

    def do_H5(self):
        a5 = self.store.read("art_05_resultados")[1]
        mec = a5.get("mecanismos_evaluacion_sugeridos") or []
        if not mec or not self.cfg.get("hitl.mecanismos", True):
            return "S06_MAPA"
        res = [f"Resultados: `{self.store.latest_path('art_05_resultados')}` — estado {a5.get('status_ejecucion')}",
               "### Evidencia disponible\n" + "\n".join(f"- {e.get('afirmacion', e)} — fuente: {e.get('fuente', '')}" for e in (a5.get('evidencia') or [])[:25]),
               "### Mecanismos de evaluación sugeridos al investigador\n" + "\n".join(
                   f"{i + 1}. **{m.get('nombre')}** ({m.get('prioridad', '')}): {m.get('objetivo', '')}\n"
                   f"   - Métrica: {m.get('metricas', m.get('metrica', ''))}\n   - Procedimiento: {m.get('procedimiento', '')}"
                   for i, m in enumerate(mec)),
               "Si ejecutas alguno, guarda las mediciones en un JSON, decláralo en `existentes.resultados_adicionales` "
               "y elige `medir` para que A5b las incorpore."]
        d = self.hitl.ask("H5", "Mecanismos de evaluación sugeridos (implementación existente)", "\n\n".join(res),
                          {"aprobar": "Continuar: los mecanismos se reportan como plan de validación",
                           "medir": "Ya aporté mediciones: re-documentar con ellas"})
        if d is None:
            return None
        self.mark("H5", d["decision"], d.get("comentario", ""))
        if d["decision"] == "medir":
            self.policies["S05_IMPL"] = "ejecutar"
            return "S05_IMPL"
        return "S06_MAPA"

    def do_S06_MAPA(self):
        if self.should_skip("S06_MAPA"):
            self.mark("S06_MAPA", "saltado")
            return "H2b"
        try:
            self.run_agent(self.agent_for("S06_MAPA"), comentarios=self.st.data.pop("comentarios_S06", None))
        except AgentError as e:
            self.ctx.trace("orquestador", f"A6 falló: {e}")
            raise
        self.mark("S06_MAPA", "ok")
        return "H2b"

    def do_H2b(self):
        if not self.cfg.get("hitl.mapa_mental", True) or "S06_MAPA" in self.st["saltados"]:
            return "S07_REDACCION"
        g = self.store.read("art_06_mapa.graph")[1]
        res = [f"Mapa: `{self.store.dir / 'mapa_mental.drawio'}` (vista previa `mapa_mental.svg`)",
               "### Estructura obligatoria del borrador (secciones_borrador)\n" + "\n".join(
                   f"- {s['seccion']}: {', '.join(s.get('nodos', []))}" for s in g.get("secciones_borrador", [])),
               f"Nodos: {len(g.get('nodos', []))} · aristas: {len(g.get('aristas', []))}",
               "Puedes editar el .drawio y reimportarlo con `spectra edit H2b art_06_mapa.graph mapa_mental.drawio`."]
        d = self.hitl.ask("H2b", "Revisar mapa mental (art_06)", "\n\n".join(res))
        if d is None:
            return None
        self.mark("H2b", d["decision"], d.get("comentario", ""))
        if d["decision"] == "aprobar":
            return "S07_REDACCION"
        self.st["comentarios_S06"] = d.get("comentario")
        self.policies["S06_MAPA"] = "ejecutar"
        return "S06_MAPA"

    def do_S07_REDACCION(self):
        fb = self.st.data.pop("feedback_pendiente", None)
        if fb is None and self.should_skip("S07_REDACCION"):
            self.mark("S07_REDACCION", "saltado")
            return "S08_REVISION"
        modo = {"art_08_revision": "revision", "art_10_dictamen": "peer", "art_00_comentarios": "humano"}.get(fb, "inicial")
        self.run_agent(self.agent_for("S07_REDACCION"), modo=modo, feedback_art=fb)
        self.mark("S07_REDACCION", "ok", f"modo {modo}")
        return "S08_REVISION"

    def do_S08_REVISION(self):
        if self._resuming("H_ESCALA"):
            res = {"aprueba": False}
        else:
            res = self.run_agent(self.agent_for("S08_REVISION"))
            self.st["iteracion_revision"] += 1
        it, mx = self.st["iteracion_revision"], int(self.cfg.orq.get("max_iteraciones_revision", 3))
        if not self._resuming("H_ESCALA"):
            self.mark("S08_REVISION", "aprueba" if res["aprueba"] else "no_aprueba", f"iteración {it}, score {res.get('score')}")
        if res["aprueba"]:
            self.st["iteracion_revision"] = 0
            return "H3" if self.st.get("modo_ronda") == "peer" else "S09_ASESOR"
        if it < mx:
            self.st["feedback_pendiente"] = "art_08_revision"
            return "S07_REDACCION"
        rev = self.store.read("art_08_revision")[1]
        d = self.hitl.ask("H_ESCALA", f"El Revisor no aprobó tras {it} iteraciones",
                          f"Revisión: `{self.store.latest_path('art_08_revision')}` · score {rev.get('score_global')}\n\n"
                          f"Puertas duras:\n```json\n{truncate(dumps(rev.get('puertas_duras')), 4000)}\n```\n\n"
                          "Observaciones:\n" + "\n".join(f"- {o}" for o in rev.get("observaciones", [])[:15]),
                          {"aprobar": "Aceptar el borrador tal cual y seguir", "cambios": "Una vuelta más con mi comentario",
                           "rechazar": "Detener"})
        if d is None:
            return None
        self.st["iteracion_revision"] = 0
        if d["decision"] == "aprobar":
            return "H3" if self.st.get("modo_ronda") == "peer" else "S09_ASESOR"
        if d["decision"] == "cambios":
            self.st["feedback_pendiente"] = self.comment_artifact("H_ESCALA", d.get("comentario", "")) if d.get("comentario") else "art_08_revision"
            return "S07_REDACCION"
        raise Pause("detenido por el investigador")

    def do_S09_ASESOR(self):
        self.run_agent(self.agent_for("S09_ASESOR"))
        self.mark("S09_ASESOR", "ok")
        return "S09_INTEGRA"

    def do_S09_INTEGRA(self):
        self.run_agent(self.agent_for("S07_REDACCION"), modo="integra", feedback_art="art_09_asesor")
        self.mark("S09_INTEGRA", "ok")
        return "H3"

    def do_H3(self):
        meta, draft = self.store.read("art_07_borrador")
        gates = self.ctx.hard_gates(draft)
        prev = meta.get("version", 1) - 1
        diff = ""
        if prev >= 1:
            import difflib
            old = self.store.read("art_07_borrador", prev)[1].splitlines()
            dl = list(difflib.unified_diff(old, draft.splitlines(), lineterm="", n=0))
            diff = f"Diff vs v{prev}: +{sum(1 for l in dl if l.startswith('+') and not l.startswith('+++'))} / " \
                   f"-{sum(1 for l in dl if l.startswith('-') and not l.startswith('---'))} líneas"
        preg = ""
        if self.store.exists("art_09_asesor"):
            m = re.search(r"^#+\s*Preguntas cr[ií]ticas.*?(?=^#{1,2}\s|\Z)", self.store.read("art_09_asesor")[1], re.M | re.S)
            preg = truncate(m.group(0), 4000) if m else ""
        res = [f"Borrador: `{self.store.latest_path('art_07_borrador')}` (v{meta.get('version')}, responde a {meta.get('responde_a')})",
               diff, "### Puertas duras\n" + "\n".join(f"- {k}: {'OK' if v['ok'] else 'FALLA'}" for k, v in gates.items()),
               preg, "Aprobar = compilar el paper (D2) y enviarlo a Peer Review."]
        d = self.hitl.ask("H3", "Aprobar borrador para compilación", "\n\n".join(x for x in res if x))
        if d is None:
            return None
        self.mark("H3", d["decision"], d.get("comentario", ""))
        if d["decision"] == "aprobar":
            return "D2_COMPILA"
        self.st["feedback_pendiente"] = self.comment_artifact("H3", d.get("comentario") or "Revisa el borrador.")
        return "S07_REDACCION"

    def do_D2_COMPILA(self):
        self.ctx.paper_dir.mkdir(exist_ok=True)
        figs = self.ctx.paper_dir / "figuras"
        figs.mkdir(exist_ok=True)
        for f in list(self.store.dir.glob("*.svg")) + list(self.store.dir.glob("*.png")) + list(self.store.dir.glob("*.drawio")):
            shutil.copyfile(f, figs / f.name)
        md = self.store.read("art_07_borrador")[1]
        m = re.search(r"^#\s+(.+)$", md, re.M)
        res = compile_paper(md, (self.store.dir / "art_refs.bib").read_text(encoding="utf-8"), self.ctx.paper_dir,
                            {**self.cfg.get("compilacion", {}), "idioma": self.cfg.orq.get("idioma", "es")}, m.group(1) if m else "")
        self.ctx.trace("D2", dumps(res))
        if not res.get("pdf") and not res.get("html"):
            d = self.hitl.ask("HD2", "Error de compilación", "\n".join(res["errores"]))
            if d is None:
                return None
        self.mark("D2_COMPILA", "ok", res.get("motor", ""))
        if self.st.data.pop("detener_tras_D2", False):     # `spectra revise --sin-peer`
            self.st["entregado"] = False
            return "FIN"
        return "S10_PEER"

    def do_S10_PEER(self):
        if not self._check_hash():
            return "H2"
        if self._resuming("H_DICTAMEN"):
            dec = self.store.read("art_10_dictamen")[0].get("decision")
        else:
            res = self.run_agent(self.agent_for("S10_PEER"))
            self.st["rondas_peer_review"] += 1
            dec = res.get("decision")
            self.mark("S10_PEER", dec or "?")
        extra = self.st["rondas_peer_review"] <= int(self.cfg.orq.get("max_rondas_peer_review", 1))
        if dec == "Accept":
            return "FIN"
        if dec == "Minor Revision" and extra:
            self.st["modo_ronda"] = "peer"
            self.st["feedback_pendiente"] = "art_10_dictamen"
            return "S07_REDACCION"
        opciones = {"fin": "Cerrar aquí y empaquetar la entrega", "a7": "Revisar el texto (A7 con el dictamen)",
                    "a4": "Rediseñar (A4, nuevo H2)"}
        d = self.hitl.ask("H_DICTAMEN", f"Dictamen: {dec}",
                          f"Dictamen: `{self.store.latest_path('art_10_dictamen')}`\n\nRondas de peer review: {self.st['rondas_peer_review']}",
                          opciones)
        if d is None:
            return None
        if d["decision"] == "fin":
            return "FIN"
        if d["decision"] == "a7":
            self.st["modo_ronda"] = "peer"
            self.st["feedback_pendiente"] = "art_10_dictamen"
            return "S07_REDACCION"
        self.st["hash_preregistro"] = None
        self.policies["S04_PROPUESTA"] = "ejecutar"
        self.st["comentarios_S04"] = f"Rediseño solicitado por el dictamen {dec}. Ver art_10_dictamen."
        return "S04_PROPUESTA"

    def do_FIN(self):
        if self.st.get("entregado"):
            return "FIN"
        out = self.ctx.project_dir / "entrega"
        out.mkdir(exist_ok=True)
        pd = self.ctx.paper_dir
        nuevo = pd / "art_paper_nuevo.pdf"
        for f in ("art_paper.pdf", "art_paper.docx", "art_paper.tex", "art_paper.html", "art_paper.md"):
            src = nuevo if f == "art_paper.pdf" and nuevo.exists() and (not (pd / f).exists() or
                                                                         nuevo.stat().st_mtime > (pd / f).stat().st_mtime) else pd / f
            if src.exists():
                try:
                    shutil.copyfile(src, out / f)
                except PermissionError:
                    shutil.copyfile(src, out / f.replace(".", "_nuevo.", 1))
                    self.ctx.trace("FIN", f"{f} está abierto en otro programa; copiado como {f.replace('.', '_nuevo.', 1)}")
        for f in ("art_refs.bib", "arquitectura.drawio", "arquitectura.svg", "mapa_mental.drawio", "mapa_mental.svg", "mapa_mental.png"):
            if (self.store.dir / f).exists():
                shutil.copyfile(self.store.dir / f, out / f)
        for a in ("art_04_propuesta", "art_05_resultados", "art_08_revision", "art_10_dictamen"):
            p = self.store.latest_path(a)
            if p:
                shutil.copyfile(p, out / p.name)
        (out / "preregistro.json").write_text(dumps({"hash": self.st["hash_preregistro"], "tipo": self.st["preregistro_tipo"],
                                                     "version_art_04": self.st.get("preregistro_version")}), encoding="utf-8")
        costos = self.ctx.project_dir / "logs" / "costos.jsonl"
        if costos.exists():
            shutil.copyfile(costos, out / "costos.jsonl")
        self.st["entregado"] = True
        self.mark("FIN", "ok", f"entrega en {out}")
        self.ctx.trace("orquestador", f"FIN. Entrega en {out}. Gasto total {self.st['gasto_usd']:.4f} USD")
        return "FIN"

    # --- información para Claude Code / status ------------------------------------------------------
    def plan(self) -> dict:
        s = self.st["estado"] or "S01_IDEAS"
        info = {"estado": s, "detalle": self.st.get("estado_detalle"), "preset": self.preset,
                "modo_orquestador": self.cfg.orchestrator_mode(), "gasto_usd": self.st["gasto_usd"],
                "presupuesto_usd": self.cfg.orq.get("presupuesto_usd_total"),
                "hitl_pendientes": {k: v for k, v in self.st["hitl"].items() if v.get("pendiente")},
                "subagente_pendiente": self.st.get("subagente_pendiente")}
        key = {"S02_S03": None}.get(s, s)
        if key in self.agent_map:
            aid = self.agent_map[key]
            mc = self.cfg.resolve_model(aid)
            info["agente"] = aid
            info["proveedor"] = mc.label()
        return info
