"""Agentes A1..A10 de SPECTRA (y variantes A4b, A5b para implementaciones existentes)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from .. import drawio
from ..artifacts import sha256_bytes
from ..gates import bib_keys, check_citations, check_comparative_tables, check_structure, cited_keys, headings, validate_json
from ..tools import academic
from ..tools.repo import entorno
from ..util import dumps, extract_fenced, extract_json, norm_text, strip_outer_fence, truncate
from .base import Agent, AgentError, Tool


# --- herramientas compartidas ------------------------------------------------------
def tool_buscar_academico(ctx) -> Tool:
    def fn(consulta: str, max_resultados: int = 8, desde_anio: int | None = None):
        b = ctx.cfg.get("busqueda", {})
        res = academic.search(consulta, min(int(max_resultados or 8), int(b.get("max_resultados", 20))),
                              b.get("fuentes"), b.get("contacto_email", ""), desde_anio)
        out = []
        for r in res:
            ctx.seen_records[r["clave_sugerida"]] = r
            if r.get("doi"):
                ctx.seen_records[r["doi"]] = r
            out.append({"clave_sugerida": r["clave_sugerida"], "titulo": r["titulo"], "autores": r["autores"][:5],
                        "anio": r["anio"], "venue": r["venue"], "doi": r["doi"], "arxiv": r["arxiv"], "url": r["url"],
                        "citas": r["citas"], "resumen": (r["resumen"] or "")[:700]})
        return out or "Sin resultados. Prueba otra consulta (en inglés suele funcionar mejor)."
    return Tool("buscar_academico", "Busca trabajos académicos en OpenAlex, Crossref, arXiv y Semantic Scholar. "
                "Devuelve metadatos reales con DOI/arXiv. Usa consultas cortas en inglés.",
                {"consulta": {"type": "string"}, "max_resultados": {"type": "integer"},
                 "desde_anio": {"type": "integer", "description": "opcional: solo trabajos desde este año"}}, fn, ["consulta"])


def tool_resolver_doi(ctx) -> Tool:
    def fn(doi: str):
        m = academic.crossref_by_doi(doi, ctx.cfg.get("busqueda.contacto_email", "")) or academic.openalex_by_doi(doi)
        return m or f"DOI no resoluble: {doi}"
    return Tool("resolver_doi", "Obtiene los metadatos oficiales (Crossref/OpenAlex) de un DOI.", {"doi": {"type": "string"}}, fn)


def repo_tools(ctx, allow_eval: bool = False) -> list[Tool]:
    repo = ctx.repo
    ts = [
        Tool("leer_archivo", "Lee un archivo del repositorio (ruta relativa) con números de línea.",
             {"ruta": {"type": "string"}, "desde": {"type": "integer"}, "hasta": {"type": "integer"}},
             lambda ruta, desde=1, hasta=None: repo.read_file(ruta, int(desde or 1), int(hasta) if hasta else None), ["ruta"]),
        Tool("buscar_en_repo", "Busca una expresión regular en los archivos de texto del repositorio.",
             {"patron": {"type": "string"}, "glob": {"type": "string", "description": "p. ej. *.cpp o libvertexav/*"}},
             lambda patron, glob="*": repo.grep(patron, glob or "*"), ["patron"]),
    ]
    if allow_eval:
        ts.append(Tool("ejecutar_evaluacion", "Ejecuta uno de los comandos de evaluación declarados por el investigador "
                       "(por índice). No se pueden ejecutar otros comandos.", {"indice": {"type": "integer"}},
                       lambda indice: repo.run_eval(int(indice))))
    return ts


def compact_lit(art03: dict | None, n_res: int = 400) -> list[dict]:
    out = []
    for a in (art03 or {}).get("articulos", []):
        out.append({"clave_bib": a.get("clave_bib"), "titulo": a.get("titulo"), "anio": a.get("anio"),
                    "autores": (a.get("autores") or [])[:3], "venue": a.get("venue"), "bloque": a.get("bloque"),
                    "metodologia": truncate(a.get("metodologia") or "", n_res),
                    "metricas_clave": a.get("metricas_clave"), "limitaciones": truncate(a.get("limitaciones") or "", n_res)})
    return out


def bib_listing(ctx) -> str:
    from ..bib import cite_label, parse_bib
    p = ctx.store.dir / "art_refs.bib"
    if not p.exists():
        return "(aún no hay art_refs.bib)"
    bib = parse_bib(p.read_text(encoding="utf-8"))
    return "\n".join(f"- @{k}: {cite_label(f)} — {f.get('title', '')[:110]}" for k, f in bib.items())


def bib_text(ctx) -> str:
    p = ctx.store.dir / "art_refs.bib"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# --- A1 ------------------------------------------------------------------------------
class IdeasAgent(Agent):
    output_schema = "art_01_ideas"

    def tools(self):
        return [tool_buscar_academico(self.ctx)]

    def run(self, comentarios: str | None = None, **_):
        p = self.cfg.get("proyecto", {}) or {}
        user = [f"# Tema del investigador\n{p.get('tema', '')}",
                f"# Pregunta o contexto\n{p.get('pregunta', '') or p.get('contexto', '')}",
                f"# Tipo de investigación\n{self.cfg.orq.get('tipo_investigacion')}"]
        if self.ctx.repo:
            user.append("# Implementación existente (el paper documentará y evaluará este sistema)\n" + self.ctx.repo_brief(40000))
        prev = self.art("art_01_ideas")
        if comentarios and prev:
            user.append(f"# Versión anterior\n```json\n{truncate(dumps(prev), 20000)}\n```")
        if comentarios:
            user.append(f"# Comentarios del investigador (HITL 1) — deben atenderse\n{comentarios}")
        user.append("Usa `buscar_academico` para chequear la novedad de cada hipótesis antes de responder. "
                    "Responde SOLO con el objeto JSON de art_01_ideas.")
        data = self.ask_json("\n\n".join(user))
        v, path = self.write("art_01_ideas", data, [])
        return {"artefactos": [str(path)]}


# --- A2 ------------------------------------------------------------------------------
class MarcoAgent(Agent):
    def tools(self):
        c = self.ctx.corpus

        def buscar_corpus(consulta: str, k: int = 6):
            return c.search(consulta, int(k or 6)) or "Sin coincidencias en el corpus."

        def agregar(doi_o_clave: str):
            rec = self.ctx.seen_records.get(doi_o_clave) or self.ctx.seen_records.get(academic.clean_doi(doi_o_clave) or "")
            if not rec and academic.clean_doi(doi_o_clave):
                rec = academic.openalex_by_doi(doi_o_clave)
            if not rec:
                return "No encontrado: primero búscalo con buscar_academico y usa su clave_sugerida o DOI."
            k = c.add_from_record(rec)
            c.save()
            d = c.docs[k]
            return {"clave": k, "tipo": d.get("tipo"), "paginado": d.get("paginado"),
                    "nota": "PDF indexado" if d.get("paginado") else "solo resumen (cita sin página)"}

        return [Tool("buscar_corpus", "Recupera fragmentos del corpus indexado (BM25). Devuelve clave, página y texto.",
                     {"consulta": {"type": "string"}, "k": {"type": "integer"}}, buscar_corpus, ["consulta"]),
                Tool("listar_corpus", "Lista los documentos del corpus.", {}, lambda: c.listing(), []),
                tool_buscar_academico(self.ctx),
                Tool("agregar_al_corpus", "Añade al corpus un trabajo encontrado (descarga el PDF open access si existe; "
                     "si no, indexa su resumen).", {"doi_o_clave": {"type": "string"}}, agregar)]

    def run(self, **_):
        self.ctx.ensure_corpus()
        c = self.ctx.corpus
        a1 = self.art("art_01_ideas") or {}
        # trabajos similares de A1 al corpus (ingesta previa, sin LLM)
        for ref in (a1.get("hipotesis_principal") or {}).get("trabajos_similares", [])[:6]:
            d = academic.clean_doi(ref) if isinstance(ref, str) and "10." in ref else None
            if d and not any(doc.get("doi") == d for doc in c.docs.values()):
                rec = academic.openalex_by_doi(d)
                if rec:
                    c.add_from_record(rec)
        c.save()
        user = (f"# art_01_ideas (aprobado)\n```json\n{truncate(dumps(a1), 25000)}\n```\n\n"
                f"# Documentos en el corpus\n```json\n{dumps(c.listing())}\n```\n\n"
                "Construye el marco teórico. Recupera evidencia con `buscar_corpus` antes de cada afirmación; "
                "si falta un trabajo fundacional, búscalo y agrégalo con `agregar_al_corpus`. "
                "Cita SOLO claves del corpus. Devuelve SOLO el Markdown (sin front-matter).")

        def check(md: str) -> list[str]:
            md = strip_outer_fence(md)
            keys = {k for k, _ in cited_keys(md)}
            fuera = sorted(k for k in keys if k not in c.docs)
            errs = [f"La clave @{k} no está en el corpus indexado (usa listar_corpus)" for k in fuera]
            if not keys:
                errs.append("El marco no contiene ninguna cita [@clave]")
            return errs

        md = strip_outer_fence(self.ask_text(user, check=check, max_rounds=20))
        md = re.sub(r"^#+\s*Referencias usadas.*", "", md, flags=re.S | re.M).rstrip()
        refs = []
        for k in sorted({k for k, _ in cited_keys(md)}):
            d = c.docs[k]
            refs.append(f"- clave: {k} · doi: {d.get('doi') or '—'} · archivo: {d.get('archivo') or d.get('url') or '—'} · tipo: {d.get('tipo')}")
        md += "\n\n## Referencias usadas\n" + "\n".join(refs) + "\n"
        v, path = self.write("art_02_marco", md, ["art_01_ideas"])
        return {"artefactos": [str(path)]}


# --- A3 ------------------------------------------------------------------------------
class EstadoArteAgent(Agent):
    output_schema = "art_03_literatura"

    def tools(self):
        return [tool_buscar_academico(self.ctx), tool_resolver_doi(self.ctx)]

    def run(self, **_):
        a1 = self.art("art_01_ideas") or {}
        n_rec = int(self.cfg.get("busqueda.anios_recientes", 3))
        import datetime as _dt
        desde = _dt.date.today().year - n_rec
        user = (f"# art_01_ideas (aprobado)\n```json\n{truncate(dumps(a1), 25000)}\n```\n\n"
                f"Bloque 'reciente' = trabajos desde {desde}. Usa `buscar_academico` (usa desde_anio={desde} para el bloque reciente) "
                "y elige SOLO trabajos devueltos por las herramientas, copiando título, autores, año y DOI/arXiv tal cual. "
                "Devuelve SOLO el JSON de art_03_literatura.")
        data = self.ask_json(user, max_rounds=24)
        email = self.cfg.get("busqueda.contacto_email", "")
        ok, desc = [], list(data.get("descartados") or [])
        claves = set()
        for a in data.get("articulos", []):
            ver = academic.verify(a, email)
            meta = ver.pop("meta", None)
            a["verificado"] = {"fuente": ver["fuente"], "coincide_titulo": ver["coincide_titulo"],
                               "coincide_autor": ver["coincide_autor"], "coincide_anio": ver["coincide_anio"]}
            if not ver["ok"]:
                desc.append({"referencia": f"{a.get('titulo')} ({a.get('anio')})", "motivo": ver["motivo"]})
                continue
            if meta:   # normalizar con los metadatos oficiales
                a["titulo"] = meta.get("titulo") or a.get("titulo")
                a["anio"] = a.get("anio") or meta.get("anio")
                a.setdefault("id", {})
                a["id"]["doi"] = a["id"].get("doi") or meta.get("doi")
                if meta.get("resumen"):
                    self.ctx.abstracts[a.get("clave_bib", "")] = meta["resumen"]
            k = a.get("clave_bib") or "x"
            k = re.sub(r"[^A-Za-z0-9_:-]", "", k)
            base, i = k, 1
            while k in claves:
                i += 1
                k = f"{base}{chr(96 + i)}"
            a["clave_bib"] = k
            claves.add(k)
            ok.append(a)
        data["articulos"], data["descartados"] = ok, desc
        if not ok:
            raise AgentError("A3: ningún trabajo superó la verificación de metadatos")
        v, path = self.write("art_03_literatura", data, ["art_01_ideas"])
        (self.ctx.project_dir / "logs" / "a03_abstracts.json").write_text(dumps(self.ctx.abstracts), encoding="utf-8")
        return {"artefactos": [str(path)], "verificados": len(ok), "descartados": len(desc)}


# --- A4 / A4b ------------------------------------------------------------------------
class PropuestaAgent(Agent):
    inversa = False

    def tools(self):
        return repo_tools(self.ctx) if (self.inversa and self.ctx.repo) else []

    def build_user(self, comentarios: str | None = None, **_) -> str:
        user = [f"# art_01_ideas\n```json\n{truncate(dumps(self.art('art_01_ideas')), 20000)}\n```",
                f"# art_02_marco\n{self.art_text('art_02_marco', 30000)}",
                f"# art_03_literatura (resumen)\n```json\n{truncate(dumps(compact_lit(self.art('art_03_literatura'))), 30000)}\n```",
                f"# Claves disponibles en art_refs.bib (cita SOLO estas)\n{bib_listing(self.ctx)}",
                f"# Tipo de investigación\n{self.cfg.orq.get('tipo_investigacion')}"]
        if self.inversa and self.ctx.repo:
            user.append("# Implementación existente (ingeniería inversa; lee el código con las herramientas)\n" + self.ctx.repo_brief(50000))
        if comentarios:
            prev = self.art_text("art_04_propuesta", 40000)
            user.append(f"# Versión anterior\n{prev}\n\n# Comentarios del investigador (HITL 2) — deben atenderse\n{comentarios}")
        user.append("Devuelve SOLO el Markdown de la propuesta. Si hay arquitectura, inclúyela al final en un bloque "
                    "```json arquitectura ... ``` con el grafo (nodos, aristas, grupos).")
        return "\n\n".join(user)

    def check(self, md: str) -> list[str]:
        md = strip_outer_fence(md)
        bib = bib_text(self.ctx)
        schema = self.cfg.path("schemas/art_04_arquitectura.graph.schema.json")
        errs = [f"Cita inexistente en art_refs.bib: @{k}" for k in check_citations(md, bib)["faltantes"]]
        g, _ = extract_fenced(md, "arquitectura")
        if not g and self.inversa:
            errs.append("Falta el bloque ```json arquitectura``` con el grafo de la arquitectura real (obligatorio en A4b)")
        if g:
            try:
                errs += ["grafo: " + e for e in validate_json(json.loads(g), schema)]
            except json.JSONDecodeError as e:
                errs.append(f"El bloque json arquitectura no es JSON válido: {e}")
        st = check_structure(md, ["Protocolo experimental"])
        errs += [f"Falta la sección: {s}" for s in st["faltantes"]]
        return errs

    def run(self, **kw):
        md = self.ask_text(self.build_user(**kw), check=self.check, max_rounds=30 if self.inversa else 4)
        return self.postprocess(md, **kw)

    def postprocess(self, md: str, **_) -> dict:
        md = strip_outer_fence(md)
        g, md_body = extract_fenced(md, "arquitectura")
        out = []
        extra = {"incluye_arquitectura": bool(g), "preregistro": "post_hoc" if self.inversa else "prospectivo"}
        if g:
            graph = json.loads(g)
            gv, gp = self.ctx.store.write("art_04_arquitectura.graph", graph, agente=self.id, proveedor=self.model.proveedor,
                                          modelo=self.model.modelo, version_prompt=self.version_prompt)
            files = drawio.build(graph, self.ctx.store.dir / "arquitectura.drawio", "layered", "Arquitectura")
            out += [str(gp), files["drawio"], files["svg"]]
            md_body += "\n\n## Diagrama\nVer `artifacts/arquitectura.drawio` (vista previa: `arquitectura.svg`).\n"
        v, path = self.write("art_04_propuesta", md_body, ["art_01_ideas", "art_02_marco", "art_03_literatura"], extra_meta=extra)
        return {"artefactos": [str(path)] + out}


class PropuestaInversaAgent(PropuestaAgent):
    inversa = True


# --- A5 (implementación nueva) ---------------------------------------------------------
class ImplementadorAgent(Agent):
    output_schema = "art_05_resultados"

    def tools(self):
        exp = self.ctx.project_dir / "experiments"
        exp.mkdir(exist_ok=True)
        sb = self.cfg.get("sandbox", {}) or {}
        runs: list[dict] = self.ctx.sandbox_runs

        def safe(ruta: str) -> Path:
            p = (exp / ruta).resolve()
            if exp.resolve() not in p.parents:
                raise PermissionError("solo se puede escribir dentro de experiments/")
            return p

        def escribir(ruta: str, contenido: str):
            p = safe(ruta)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contenido, encoding="utf-8")
            return f"ok: {p.relative_to(self.ctx.project_dir)} ({len(contenido)} chars)"

        def leer(ruta: str):
            p = safe(ruta)
            return p.read_text(encoding="utf-8", errors="ignore")[:30000] if p.exists() else "no existe"

        def ejecutar(script: str, argumentos: str = ""):
            p = safe(script)
            if sb.get("tipo") == "docker":
                cmd = ["docker", "run", "--rm", "--network", "none", "-v", f"{exp.resolve()}:/w", "-w", "/w",
                       sb.get("imagen", "python:3.12-slim"), "python", p.name, *argumentos.split()]
            else:
                cmd = [sys.executable, str(p), *argumentos.split()]
            try:
                r = subprocess.run(cmd, cwd=exp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=int(sb.get("timeout_s", 900)))
                salida, code = (r.stdout + "\n" + r.stderr)[-15000:], r.returncode
            except subprocess.TimeoutExpired:
                salida, code = "TIMEOUT", -1
            runs.append({"script": script, "argumentos": argumentos, "codigo_salida": code,
                         "sha256_salida": sha256_bytes(salida.encode())})
            return {"codigo_salida": code, "salida": salida}

        return [Tool("escribir_archivo", "Escribe un archivo dentro de experiments/.", {"ruta": {"type": "string"}, "contenido": {"type": "string"}}, escribir),
                Tool("leer_archivo", "Lee un archivo de experiments/.", {"ruta": {"type": "string"}}, leer),
                Tool("ejecutar", "Ejecuta un script Python de experiments/ en el sandbox (sin red si sandbox.tipo=docker).",
                     {"script": {"type": "string"}, "argumentos": {"type": "string"}}, ejecutar, ["script"])]

    def run(self, diagnostico: str | None = None, **_):
        prop = self.art_text("art_04_propuesta", 60000)
        user = [f"# art_04_propuesta (hash congelado {self.ctx.state['hash_preregistro']})\n{prop}",
                f"# Tipo de investigación\n{self.cfg.orq.get('tipo_investigacion')}"]
        if diagnostico:
            user.append(f"# Diagnóstico del intento anterior\n{diagnostico}")
        user.append("Implementa y ejecuta el protocolo con las herramientas. Al terminar devuelve SOLO el JSON de art_05_resultados.")
        data = self.ask_json("\n\n".join(user), max_rounds=40, fix=self._fix)
        v, path = self.write("art_05_resultados", data, ["art_04_propuesta"])
        return {"artefactos": [str(path)], "status": data.get("status_ejecucion")}

    def _fix(self, d: dict) -> dict:
        d["hash_propuesta"] = self.ctx.state["hash_preregistro"]
        d.setdefault("entorno", {}).update(entorno())
        d["ejecuciones_sandbox"] = self.ctx.sandbox_runs
        return d


# --- A5b (implementación existente: documentar y evaluar) ------------------------------------
class DocumentadorAgent(Agent):
    output_schema = "art_05_resultados"

    def tools(self):
        return repo_tools(self.ctx, allow_eval=True)

    def run(self, **_):
        repo = self.ctx.repo
        if not repo:
            raise AgentError("A5b requiere existentes.implementacion.repo")
        # 1) ejecución determinista de todos los comandos declarados (evidencia reproducible)
        for i, c in enumerate(repo.comandos):
            if not any(e["indice"] == i for e in repo.ejecuciones):
                self.ctx.trace(self.id, f"ejecutando evaluación {i}: {c['cmd']}")
                repo.run_eval(i)
        ev = [{k: e[k] for k in ("indice", "comando", "descripcion", "codigo_salida", "duracion_s", "sha256_salida")} |
              {"salida_final": e["salida"][-4000:]} for e in repo.ejecuciones]
        extra = self.cfg.get("existentes.resultados_adicionales")
        adicionales = None
        if extra and Path(extra).exists():
            adicionales = json.loads(Path(extra).read_text(encoding="utf-8"))
        user = [f"# art_04_propuesta (preregistro post hoc, hash {self.ctx.state['hash_preregistro']})\n{self.art_text('art_04_propuesta', 40000)}",
                "# Repositorio\n" + self.ctx.repo_brief(45000),
                f"# Ejecuciones de evaluación ya realizadas por SPECTRA\n```json\n{truncate(dumps(ev), 40000)}\n```"]
        if adicionales:
            user.append(f"# Mediciones aportadas por el investigador\n```json\n{truncate(dumps(adicionales), 20000)}\n```")
        user.append("Documenta lo implementado y lo que la evidencia demuestra. NO inventes mediciones: todo número debe "
                    "salir de las ejecuciones, del repositorio o de las mediciones del investigador (indica la fuente). "
                    "Propón mecanismos de evaluación concretos para lo que aún no está medido. "
                    "Devuelve SOLO el JSON de art_05_resultados.")

        def check(d: dict) -> list[str]:
            errs = []
            if not d.get("mecanismos_evaluacion_sugeridos"):
                errs.append("Faltan mecanismos_evaluacion_sugeridos")
            if not d.get("evidencia"):
                errs.append("Falta la lista 'evidencia' con la fuente de cada afirmación")
            return errs

        data = self.ask_json("\n\n".join(user), fix=self._fix, extra_check=check, max_rounds=30)
        v, path = self.write("art_05_resultados", data, ["art_04_propuesta"])
        (self.ctx.project_dir / "logs" / "a05b_ejecuciones.json").write_text(dumps(repo.ejecuciones), encoding="utf-8")
        return {"artefactos": [str(path)], "status": data.get("status_ejecucion"),
                "mecanismos": len(data.get("mecanismos_evaluacion_sugeridos", []))}

    def _fix(self, d: dict) -> dict:
        repo = self.ctx.repo
        g = repo.git_info()
        d["modo"] = "implementacion_existente"
        d["hash_propuesta"] = self.ctx.state["hash_preregistro"]
        d.setdefault("entorno", {}).update(entorno())
        d["codigo"] = {"ruta": str(repo.root), "commit": g.get("commit"), "remoto": g.get("remoto"), "sucio": g.get("sucio")}
        d["ejecuciones"] = [{k: e[k] for k in ("indice", "comando", "codigo_salida", "duracion_s", "sha256_salida")} |
                            {"extracto_salida": e["salida"][-1500:]} for e in repo.ejecuciones]
        d["log_experimento"] = "logs/a05b_ejecuciones.json"
        d.setdefault("corridas", [])
        d.setdefault("baselines", [])
        d.setdefault("pruebas_estadisticas", [])
        d.setdefault("desviaciones", [])
        return d


# --- A6 ------------------------------------------------------------------------------
class MapaAgent(Agent):
    output_schema = "art_06_mapa.graph"

    def run(self, comentarios: str | None = None, **_):
        user = [f"# art_01_ideas\n```json\n{truncate(dumps(self.art('art_01_ideas')), 15000)}\n```",
                f"# art_02_marco\n{self.art_text('art_02_marco', 20000)}",
                f"# art_03 (resumen)\n```json\n{truncate(dumps(compact_lit(self.art('art_03_literatura'), 200)), 15000)}\n```",
                f"# art_04_propuesta\n{self.art_text('art_04_propuesta', 25000)}",
                f"# art_05_resultados\n```json\n{truncate(dumps(self.art('art_05_resultados')), 30000)}\n```"]
        if comentarios:
            user.append(f"# Comentarios del investigador (HITL 2b)\n{comentarios}")
        user.append("Devuelve SOLO el JSON del grafo art_06_mapa.graph.")

        def check(d: dict) -> list[str]:
            ids = {n["id"] for n in d.get("nodos", [])}
            errs = [f"Arista con nodo inexistente: {e}" for e in d.get("aristas", []) if e.get("de") not in ids or e.get("a") not in ids]
            if d.get("raiz") not in ids:
                errs.append("La raíz no es un nodo existente")
            errs += [f"secciones_borrador referencia nodo inexistente {n}" for s in d.get("secciones_borrador", [])
                     for n in s.get("nodos", []) if n not in ids]
            return errs[:20]

        data = self.ask_json("\n\n".join(user), extra_check=check, use_tools=False)
        v, path = self.write("art_06_mapa.graph", data, ["art_01_ideas", "art_02_marco", "art_03_literatura",
                                                         "art_04_propuesta", "art_05_resultados"])
        files = drawio.build(data, self.ctx.store.dir / "mapa_mental.drawio", "tree", "Mapa mental")
        out = [str(path), files["drawio"], files["svg"]]
        img = self.conf.get("imagen") or {}
        if img.get("habilitado"):
            try:
                out += self._image(data, img)
            except Exception as e:
                self.ctx.trace(self.id, f"imagen omitida: {e}")
        return {"artefactos": out}

    def _image(self, g: dict, img: dict) -> list[str]:
        from ..providers import get_provider
        from ..state import Timer
        labels = "; ".join(n["etiqueta"][:40] for n in g["nodos"][:24])
        prompt = ("Ilustración limpia de un mapa mental académico, fondo blanco, estilo diagrama plano, "
                  f"nodo central '{g['nodos'][0]['etiqueta'][:50]}'. Ramas: {labels}. Texto corto y legible.")
        prov = get_provider(self.cfg, img.get("proveedor", "openrouter"))
        self.ctx.costs.check(self.id)
        with Timer() as t:
            r = prov.chat([{"role": "user", "content": prompt}], img["openrouter_id"], modalities=["image", "text"])
        self._account(r, t.dt, img["openrouter_id"])
        if not r.images:
            return []
        p = self.ctx.store.dir / "mapa_mental.png"
        p.write_bytes(r.images[0])
        return [str(p)]


# --- A7 ------------------------------------------------------------------------------
class RedactorAgent(Agent):
    def required_sections(self) -> list[str]:
        mapa = self.art("art_06_mapa.graph") or {}
        secs = [s["seccion"] for s in mapa.get("secciones_borrador", [])]
        for s in ("Limitaciones", "Declaración de uso de IA"):
            if s not in secs:
                secs.append(s)
        return secs

    def ai_declaration_facts(self) -> list[dict]:
        out, st = [], self.ctx.store
        for p in sorted(st.dir.glob("art_*_v*.*")):
            if p.suffix not in (".json", ".md"):
                continue
            art_id = re.sub(r"_v\d+$", "", p.stem)
            try:
                meta, _ = st.read(art_id, int(re.search(r"_v(\d+)$", p.stem).group(1)))
            except Exception:
                continue
            gp = meta.get("generado_por") or {}
            out.append({"artefacto": p.name, "agente": gp.get("agente"), "modelo": gp.get("modelo"), "proveedor": gp.get("proveedor")})
        return out

    def build_user(self, modo: str = "inicial", feedback_art: str | None = None, **_) -> str:
        st = self.ctx.store
        n = (st.latest_version("art_07_borrador") or 0) + 1
        figs = [f"figuras/{f}" for f in ("arquitectura.svg", "mapa_mental.svg") if (st.dir / f).exists()]
        user = [f"# Iteración {n} (modo: {modo})",
                "# Estructura obligatoria (art_06.secciones_borrador + obligatorias)\n" + "\n".join(f"- {s}" for s in self.required_sections()),
                f"# Claves de art_refs.bib (cita SOLO estas, formato [@clave])\n{bib_listing(self.ctx)}",
                "# Figuras disponibles (inclúyelas con ![Leyenda](ruta))\n" + ("\n".join(figs) or "(ninguna)"),
                "# Tablas comparativas obligatorias\n- Al final de «Marco Teórico»: tabla comparativa de los conceptos o "
                "marcos (a partir de art_02).\n- Al final de «Trabajos Relacionados»: tabla comparativa del estado del arte "
                "frente a este trabajo (a partir de art_03.tabla_comparativa_resumen y sus artículos).",
                f"# art_01_ideas\n```json\n{truncate(dumps(self.art('art_01_ideas')), 12000)}\n```",
                f"# art_02_marco\n{self.art_text('art_02_marco', 25000)}",
                f"# art_03 (resumen)\n```json\n{truncate(dumps(compact_lit(self.art('art_03_literatura'))), 25000)}\n```",
                f"# art_04_propuesta (preregistro: {self.ctx.state.get('preregistro_tipo')})\n{self.art_text('art_04_propuesta', 30000)}",
                f"# art_05_resultados (fuente ÚNICA de números)\n```json\n{truncate(dumps(self.art('art_05_resultados')), 45000)}\n```",
                f"# art_06_mapa\n```json\n{truncate(dumps(self.art('art_06_mapa.graph')), 15000)}\n```",
                f"# Hechos para la Declaración de uso de IA (meta.generado_por)\n```json\n{dumps(self.ai_declaration_facts())}\n```"]
        prev = st.latest_version("art_07_borrador")
        if prev:
            user.append(f"# Borrador anterior (art_07_borrador_v{prev})\n{st.read('art_07_borrador')[1]}")
        if feedback_art:
            user.append(f"# Feedback a integrar ({feedback_art}) — atiende TODO lo obligatorio\n{self.art_text(feedback_art, 40000)}")
        user.append("Devuelve SOLO el Markdown completo del artículo (sin front-matter).")
        return "\n\n".join(user)

    def check(self, md: str) -> list[str]:
        md = strip_outer_fence(md)
        errs = [f"Cita inexistente en art_refs.bib: @{k}" for k in check_citations(md, bib_text(self.ctx))["faltantes"]]
        errs += [f"Falta la sección obligatoria: {s}" for s in check_structure(md, self.required_sections())["faltantes"]]
        errs += [f"La sección «{s}» debe terminar con su tabla comparativa (tabla Markdown, ≥ 3 columnas y ≥ 2 filas, "
                 "seguida como mucho de su leyenda)" for s in check_comparative_tables(md)["faltantes"]]
        if not md.lstrip().startswith("# "):
            errs.append("El documento debe empezar con '# Título'")
        return errs

    def run(self, **kw):
        return self.postprocess(self.ask_text(self.build_user(**kw), check=self.check, use_tools=False), **kw)

    def postprocess(self, md: str, modo: str = "inicial", feedback_art: str | None = None, **_) -> dict:
        st = self.ctx.store
        md = strip_outer_fence(md)
        n = (st.latest_version("art_07_borrador") or 0) + 1
        responde = f"{feedback_art}_v{st.latest_version(feedback_art)}" if feedback_art and st.exists(feedback_art) else None
        v, path = self.write("art_07_borrador", md, ["art_01_ideas", "art_02_marco", "art_03_literatura", "art_04_propuesta",
                                                     "art_05_resultados", "art_06_mapa.graph"] + ([feedback_art] if feedback_art else []),
                             extra_meta={"iteracion": n, "responde_a": responde, "modo": modo})
        return {"artefactos": [str(path)], "version": v}


# --- A8 ------------------------------------------------------------------------------
class RevisorAgent(Agent):
    output_schema = "art_08_revision"

    def run(self, **_):
        st = self.ctx.store
        v = st.latest_version("art_07_borrador")
        draft = st.read("art_07_borrador")[1]
        gates = self.ctx.hard_gates(draft)
        user = [f"# Borrador a revisar: art_07_borrador_v{v}\n{draft}",
                f"# Puertas duras (calculadas por SPECTRA; no las modifiques)\n```json\n{truncate(dumps(gates), 15000)}\n```",
                f"# art_05_resultados\n```json\n{truncate(dumps(self.art('art_05_resultados')), 35000)}\n```",
                f"# art_06_mapa (estructura esperada)\n```json\n{truncate(dumps(self.art('art_06_mapa.graph')), 10000)}\n```",
                f"# art_03 (resumen)\n```json\n{truncate(dumps(compact_lit(self.art('art_03_literatura'), 200)), 15000)}\n```",
                f"# Claves bib\n{bib_listing(self.ctx)}",
                "Evalúa con rigor y devuelve SOLO el JSON de art_08_revision."]

        def fix(d: dict) -> dict:
            d["puertas_duras"] = gates
            crit = d.get("criterios") or {}
            if crit and not d.get("score_global"):
                d["score_global"] = round(sum(crit.values()) / len(crit))
            return d

        data = self.ask_json("\n\n".join(user), fix=fix, use_tools=False)
        g = self.cfg.get("puertas", {})
        crit = data.get("criterios") or {}
        duras_ok = all(x.get("ok") for x in gates.values())
        aprueba = duras_ok and data.get("score_global", 0) >= g.get("score_global_min", 85) and \
            (min(crit.values()) if crit else 0) >= g.get("criterio_min", 75)
        data["quality_gate_1"] = {"aprueba": aprueba, "puertas_duras_ok": duras_ok,
                                  "score_global_min": g.get("score_global_min", 85), "criterio_min": g.get("criterio_min", 75)}
        vv, path = self.write("art_08_revision", data, ["art_07_borrador", "art_05_resultados", "art_03_literatura", "art_06_mapa.graph"],
                              extra_meta={"iteracion": v, "responde_a": f"art_07_borrador_v{v}"})
        return {"artefactos": [str(path)], "aprueba": aprueba, "score": data.get("score_global")}


# --- A9 ------------------------------------------------------------------------------
class AsesorAgent(Agent):
    def build_user(self, **_) -> str:
        rub = self.cfg.path(self.conf.get("rubrica", "config/rubrica.yaml"))
        rubrica = rub.read_text(encoding="utf-8") if rub.exists() else "(sin rúbrica)"
        return "\n\n".join([
            f"# Último borrador aprobado por el Revisor\n{self.ctx.store.read('art_07_borrador')[1]}",
            f"# art_08_revision\n```json\n{truncate(dumps(self.art('art_08_revision')), 20000)}\n```",
            f"# art_05_resultados\n```json\n{truncate(dumps(self.art('art_05_resultados')), 30000)}\n```",
            f"# Rúbrica institucional\n```yaml\n{rubrica}\n```",
            f"# Norma de citación: {self.cfg.get('compilacion.csl', 'apa').upper()}",
            "Devuelve SOLO el Markdown de art_09_asesor."])

    def check(self, md: str) -> list[str]:
        return [f"Falta la sección: {s}" for s in check_structure(md, ["Preguntas críticas", "Acciones obligatorias"])["faltantes"]]

    def run(self, **kw):
        return self.postprocess(self.ask_text(self.build_user(**kw), check=self.check, use_tools=False))

    def postprocess(self, md: str, **_) -> dict:
        v, path = self.write("art_09_asesor", strip_outer_fence(md), ["art_07_borrador", "art_08_revision", "art_05_resultados"])
        return {"artefactos": [str(path)]}


# --- A10 ------------------------------------------------------------------------------
DECISIONES = {"accept": "Accept", "minor revision": "Minor Revision", "major revision": "Major Revision", "reject": "Reject"}


def parse_decision(md: str) -> str | None:
    m = re.search(r"\*\*Decisi[oó]n:?\*\*:?\s*([A-Za-z ]+)", md)
    if not m:
        m = re.search(r"Decisi[oó]n\s*:\s*\**\s*([A-Za-z ]+)", md)
    if not m:
        return None
    t = m.group(1).strip().lower()
    for k, v in DECISIONES.items():
        if t.startswith(k):
            return v
    return None


class PeerReviewAgent(Agent):
    def run(self, **_):
        from ..compile import html_text
        paper_dir = self.ctx.paper_dir
        hp = paper_dir / "art_paper.html"
        paper = html_text(hp) if hp.exists() else (paper_dir / "art_paper.md").read_text(encoding="utf-8")
        chk = self.cfg.path(self.conf.get("checklist", "config/checklist_peer_review.yaml"))
        checklist = chk.read_text(encoding="utf-8") if chk.exists() else ""
        # Pasada 1: ciega (solo el paper)
        ciega = self.ask_text(f"# PASADA 1 — CIEGA\nSolo tienes el artículo, como un revisor real.\n\n# Checklist\n```yaml\n{checklist}\n```\n\n"
                              f"# Artículo\n{truncate(paper, 120000)}\n\nEscribe tus comentarios principales, la evaluación por "
                              "criterio (1–5) y una decisión provisional.", use_tools=False)
        (self.ctx.project_dir / "logs" / "a10_pasada_ciega.md").write_text(ciega, encoding="utf-8")
        # Pasada 2: auditoría (paper + preregistro + resultados + código)
        code_info = dumps((self.art("art_05_resultados") or {}).get("codigo"))
        user = (f"# PASADA 2 — AUDITORÍA\n# Comentarios de tu pasada ciega\n{ciega}\n\n"
                f"# Artículo\n{truncate(paper, 100000)}\n\n"
                f"# art_04_propuesta (preregistro {self.ctx.state.get('preregistro_tipo')}, hash {self.ctx.state.get('hash_preregistro')})\n"
                f"{self.art_text('art_04_propuesta', 30000)}\n\n"
                f"# art_05_resultados\n```json\n{truncate(dumps(self.art('art_05_resultados')), 35000)}\n```\n\n"
                f"# art_03 (resumen)\n```json\n{truncate(dumps(compact_lit(self.art('art_03_literatura'), 200)), 15000)}\n```\n\n"
                f"# Código\n{code_info}\n\n"
                "Escribe el dictamen final en el formato de art_10_dictamen (empieza con '# Dictamen de Arbitraje' y "
                "'**Decisión:** Accept | Minor Revision | Major Revision | Reject').")

        def check(md):
            return [] if parse_decision(md) else ["No se encontró la línea '**Decisión:** <Accept|Minor Revision|Major Revision|Reject>'"]

        md = strip_outer_fence(self.ask_text(user, check=check, use_tools=False))
        dec = parse_decision(md)
        v, path = self.write("art_10_dictamen", md, ["art_04_propuesta", "art_05_resultados", "art_03_literatura"],
                             extra_meta={"decision": dec, "ronda": self.ctx.state["rondas_peer_review"] + 1})
        return {"artefactos": [str(path)], "decision": dec}


REGISTRY = {
    "a01_ideas": IdeasAgent, "a02_marco": MarcoAgent, "a03_estado_arte": EstadoArteAgent,
    "a04_arquitectura": PropuestaAgent, "a04b_arquitectura_inversa": PropuestaInversaAgent,
    "a05_implementador": ImplementadorAgent, "a05b_documentador": DocumentadorAgent,
    "a06_mapa_mental": MapaAgent, "a07_redactor": RedactorAgent, "a08_revisor": RevisorAgent,
    "a09_asesor": AsesorAgent, "a10_peer_review": PeerReviewAgent,
}


def make_agent(agent_id: str, ctx) -> Agent:
    conf = ctx.cfg.agent(agent_id)
    if conf.get("clase"):
        from ..util import load_object
        cls = load_object(conf["clase"])
    else:
        cls = REGISTRY.get(agent_id) or REGISTRY.get(conf.get("basado_en", ""))
        if cls is None:
            raise KeyError(f"No hay implementación para {agent_id}; define `clase` o `basado_en` en la configuración")
    return cls(agent_id, ctx)
