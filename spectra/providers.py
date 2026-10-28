"""Proveedores de LLM intercambiables: openai_compat (OpenRouter, LM Studio...) y claude_cli."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)       # tokens_entrada, tokens_salida, usd
    model: str = ""
    raw: Any = None
    images: list[bytes] = field(default_factory=list)


class ProviderError(RuntimeError):
    pass


class OpenAICompatProvider:
    def __init__(self, name: str, conf: dict):
        self.name = name
        self.base_url = conf["base_url"].rstrip("/")
        self.key = os.environ.get(conf.get("api_key_env", ""), "") or "no-key"
        self.headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                        **(conf.get("cabeceras") or {})}
        self.timeout = conf.get("timeout_s", 900)
        self.is_openrouter = "openrouter.ai" in self.base_url

    def chat(self, messages: list[dict], model: str, *, tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False, modalities: list[str] | None = None) -> LLMResponse:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        if modalities:
            body["modalities"] = modalities
        if self.is_openrouter:
            body["usage"] = {"include": True}
        last = None
        for intento in range(5):
            try:
                r = requests.post(f"{self.base_url}/chat/completions", headers=self.headers,
                                  data=json.dumps(body), timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(3 * (intento + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504, 520, 524):
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                time.sleep(5 * (intento + 1))
                continue
            if r.status_code == 400 and json_mode and "response_format" in body:
                body.pop("response_format")          # modelo sin modo JSON: reintenta sin él
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{self.name} {model}: HTTP {r.status_code}: {r.text[:800]}")
            data = r.json()
            if "error" in data:
                last = str(data["error"])[:500]
                time.sleep(3 * (intento + 1))
                continue
            if not data.get("choices"):
                last = f"respuesta sin choices: {str(data)[:300]}"
                time.sleep(3)
                continue
            msg = data["choices"][0].get("message", {}) or {}
            u = data.get("usage") or {}
            usage = {"tokens_entrada": u.get("prompt_tokens", 0), "tokens_salida": u.get("completion_tokens", 0),
                     "usd": float(u.get("cost") or 0.0)}
            images = []
            for im in msg.get("images") or []:
                url = (im.get("image_url") or {}).get("url", "")
                if url.startswith("data:") and "," in url:
                    images.append(base64.b64decode(url.split(",", 1)[1]))
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            return LLMResponse(content=content, tool_calls=msg.get("tool_calls") or [], usage=usage,
                               model=data.get("model", model), raw=data, images=images)
        raise ProviderError(f"{self.name} {model}: sin respuesta válida tras reintentos ({last})")


class ClaudeCLIProvider:
    """Ejecuta `claude -p` (sesión local de Claude Code). Sin tool calling propio: el agente
    recibe todo el contexto en el prompt. Útil para usar Claude sin OpenRouter."""

    def __init__(self, name: str, conf: dict):
        self.name = name
        self.bin = conf.get("binario", "claude")
        if not shutil.which(self.bin):
            raise ProviderError(f"No se encontró el binario {self.bin}")

    def chat(self, messages: list[dict], model: str, *, tools=None, temperature=None, max_tokens=None,
             json_mode: bool = False, modalities=None) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages
                            if m["role"] != "system" and isinstance(m.get("content"), str))
        cmd = [self.bin, "-p", "--output-format", "json", "--model", model]
        if system:
            cmd += ["--append-system-prompt", system]
        r = subprocess.run(cmd, input=convo, capture_output=True, text=True, encoding="utf-8", timeout=1800)
        if r.returncode != 0:
            raise ProviderError(f"claude -p falló: {r.stderr[:500]}")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return LLMResponse(content=r.stdout, model=model)
        u = data.get("usage") or {}
        return LLMResponse(content=data.get("result", ""), model=model, raw=data,
                           usage={"tokens_entrada": u.get("input_tokens", 0),
                                  "tokens_salida": u.get("output_tokens", 0),
                                  "usd": float(data.get("total_cost_usd") or 0.0)})


_CACHE: dict[str, Any] = {}


def get_provider(cfg, name: str):
    if name in _CACHE:
        return _CACHE[name]
    conf = cfg.provider_conf(name)
    tipo = conf.get("tipo", "openai_compat")
    if tipo == "openai_compat":
        p = OpenAICompatProvider(name, conf)
    elif tipo == "claude_cli":
        p = ClaudeCLIProvider(name, conf)
    elif ":" in tipo:                       # proveedor propio: "modulo:Clase"
        from .util import load_object
        p = load_object(tipo)(name, conf)
    else:
        raise ProviderError(f"Tipo de proveedor desconocido: {tipo}")
    _CACHE[name] = p
    return p
