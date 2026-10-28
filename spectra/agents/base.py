"""Agente base: prompt canónico + proveedor resuelto + herramientas + validación + costos."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..artifacts import split_front_matter
from ..config import ModelChoice
from ..gates import validate_json
from ..providers import LLMResponse, get_provider
from ..state import Timer
from ..util import dumps, extract_json, truncate


class AgentError(RuntimeError):
    pass


class NeedsClaudeCode(RuntimeError):
    """El agente está asignado a un subagente local de Claude Code: la skill /spectra debe ejecutarlo."""


class Tool:
    def __init__(self, name: str, description: str, params: dict, fn: Callable[..., Any], required: list[str] | None = None):
        self.name, self.fn = name, fn
        self.schema = {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": params, "required": required or list(params)}}}


class Agent:
    """Subclases: definen `inputs()`, `run()` y opcionalmente `tools()`."""
    output_schema: str | None = None

    def __init__(self, agent_id: str, ctx):
        self.id = agent_id
        self.ctx = ctx
        self.cfg = ctx.cfg
        self.conf = self.cfg.agent(agent_id)
        prompt_path = self.cfg.path(self.conf.get("prompt", f"agents/specs/{agent_id}.md"))
        text = Path(prompt_path).read_text(encoding="utf-8")
        self.prompt_meta, self.prompt = split_front_matter(text)
        self.version_prompt = f"{Path(prompt_path).stem}@{self.prompt_meta.get('version', '1.0')}"
        self.usage = {"tokens_entrada": 0, "tokens_salida": 0, "usd": 0.0}
        self.model: ModelChoice = self.cfg.resolve_model(agent_id)

    # --- utilidades ---------------------------------------------------------------
    def system_prompt(self) -> str:
        common = (self.cfg.path("agents/specs/_comun.md").read_text(encoding="utf-8")
                  if self.cfg.path("agents/specs/_comun.md").exists() else "")
        idioma = self.cfg.orq.get("idioma", "es")
        return f"{split_front_matter(common)[1]}\n\n{self.prompt}\n\nIdioma de salida: {idioma}."

    def tools(self) -> list[Tool]:
        return []

    def _enabled_tools(self) -> list[Tool]:
        allowed = self.conf.get("herramientas")
        ts = self.tools()
        return [t for t in ts if allowed is None or t.name in allowed or t.name.startswith("_")]

    def entradas_refs(self, ids: list[str]) -> list[dict]:
        st = self.ctx.store
        return [st.ref(a) for a in ids if st.exists(a)]

    def art(self, art_id: str) -> Any:
        """Contenido del último artefacto (dict JSON o cuerpo Markdown)."""
        if not self.ctx.store.exists(art_id):
            return None
        return self.ctx.store.read(art_id)[1]

    def art_text(self, art_id: str, limit: int = 60000) -> str:
        if not self.ctx.store.exists(art_id):
            return f"(no existe {art_id})"
        return truncate(self.ctx.store.read_text(art_id), limit)

    # --- llamada al modelo -------------------------------------------------------------
    def _provider(self):
        if self.model.proveedor == "claude_code":
            raise NeedsClaudeCode(f"{self.id} -> subagente {self.model.subagente}")
        return get_provider(self.cfg, self.model.proveedor)

    def _account(self, r: LLMResponse, dt: float, modelo: str | None = None):
        for k in ("tokens_entrada", "tokens_salida"):
            self.usage[k] += int(r.usage.get(k) or 0)
        self.usage["usd"] = round(self.usage["usd"] + float(r.usage.get("usd") or 0.0), 6)
        self.ctx.costs.record(self.id, modelo or self.model.modelo, self.model.proveedor, r.usage, dt)

    def chat(self, messages: list[dict], *, json_mode: bool = False, use_tools: bool = True,
             max_rounds: int = 16, model: str | None = None, **kw) -> str:
        prov = self._provider()
        tools = self._enabled_tools() if use_tools else []
        tmap = {t.name: t for t in tools}
        msgs = list(messages)
        temp = self.conf.get("temperatura")
        max_tokens = self.conf.get("max_tokens", 16000)
        for ronda in range(max_rounds + 1):
            self.ctx.costs.check(self.id)
            final = ronda == max_rounds
            with Timer() as t:
                r = prov.chat(msgs, model or self.model.modelo, tools=[x.schema for x in tools] if tools and not final else None,
                              temperature=temp, max_tokens=max_tokens, json_mode=json_mode and (not tools or final), **kw)
            self._account(r, t.dt, model)
            if r.tool_calls and tools and not final:
                msgs.append({"role": "assistant", "content": r.content or "", "tool_calls": r.tool_calls})
                for tc in r.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        out = tmap[name].fn(**args) if name in tmap else f"ERROR: herramienta desconocida {name}"
                    except Exception as e:      # el error vuelve al modelo, no rompe el agente
                        out = f"ERROR: {type(e).__name__}: {e}"
                    out_s = out if isinstance(out, str) else dumps(out)
                    self.ctx.trace(self.id, f"tool {name}({truncate(json.dumps(args, ensure_ascii=False), 200)}) -> {len(out_s)} chars")
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "name": name, "content": truncate(out_s, 24000)})
                if ronda == max_rounds - 1:
                    msgs.append({"role": "user", "content": "Límite de herramientas alcanzado. Entrega AHORA la salida final completa."})
                continue
            if not (r.content or "").strip():
                msgs.append({"role": "user", "content": "Tu respuesta llegó vacía. Entrega la salida final completa."})
                continue
            return r.content
        raise AgentError(f"{self.id}: sin salida final")

    def ask_json(self, user: str, schema: str | None = None, fix: Callable[[dict], dict] | None = None,
                 extra_check: Callable[[dict], list[str]] | None = None, **kw) -> dict:
        """Llama al modelo, extrae JSON, valida contra el esquema y reintenta con los errores."""
        msgs = [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": user}]
        schema = schema or self.output_schema
        last_err: list[str] = []
        for intento in range(int(self.cfg.orq.get("max_reintentos_agente", 2)) + 1):
            content = self.chat(msgs, json_mode=True, **kw)
            try:
                data = extract_json(content)
            except ValueError as e:
                last_err = [str(e)]
            else:
                if fix:
                    data = fix(data)
                errs = validate_json(data, self.cfg.path(f"schemas/{schema}.schema.json")) if schema else []
                if extra_check:
                    errs += extra_check(data)
                if not errs:
                    return data
                last_err = errs
            self.ctx.trace(self.id, f"salida inválida (intento {intento + 1}): {last_err[:5]}")
            msgs = msgs[:2] + [{"role": "assistant", "content": truncate(content, 30000)},
                               {"role": "user", "content": "La salida no es válida. Corrige estos errores y devuelve el JSON COMPLETO:\n- "
                                + "\n- ".join(last_err[:20])}]
            kw["use_tools"] = False
        raise AgentError(f"{self.id}: salida inválida tras reintentos: {last_err[:5]}")

    def ask_text(self, user: str, check: Callable[[str], list[str]] | None = None, **kw) -> str:
        msgs = [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": user}]
        last: list[str] = []
        for intento in range(int(self.cfg.orq.get("max_reintentos_agente", 2)) + 1):
            content = self.chat(msgs, **kw)
            errs = check(content) if check else []
            if not errs:
                return content
            last = errs
            self.ctx.trace(self.id, f"salida rechazada (intento {intento + 1}): {errs[:5]}")
            msgs = msgs[:2] + [{"role": "assistant", "content": content},
                               {"role": "user", "content": "Corrige estos problemas y devuelve el documento COMPLETO corregido:\n- "
                                + "\n- ".join(errs[:30])}]
            kw["use_tools"] = False
        raise AgentError(f"{self.id}: salida rechazada tras reintentos: {last[:5]}")

    def write(self, art_id: str, content: Any, entradas: list[str], extra_meta: dict | None = None):
        return self.ctx.store.write(art_id, content, agente=self.id, proveedor=self.model.proveedor,
                                    modelo=self.model.modelo, version_prompt=self.version_prompt,
                                    entradas=self.entradas_refs(entradas), costo=dict(self.usage), extra_meta=extra_meta)

    def run(self, **kw) -> dict:
        raise NotImplementedError
