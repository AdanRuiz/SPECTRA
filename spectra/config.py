"""Carga de configuración: global + proyecto, perfiles de modelo y resolución de proveedor."""
from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class ModelChoice:
    agent_id: str
    proveedor: str            # nombre de proveedor (clave de `proveedores`) o "claude_code"
    modelo: str               # id del modelo para ese proveedor
    subagente: str | None = None
    claude_code_modelo: str | None = None
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        if self.proveedor == "claude_code":
            return f"claude_code:{self.subagente}({self.claude_code_modelo})"
        return f"{self.proveedor}:{self.modelo}"


class Config:
    def __init__(self, project_dir: Path, overrides: dict | None = None):
        self.root = ROOT
        self.project_dir = Path(project_dir).resolve()
        load_dotenv(ROOT / ".env")
        load_dotenv(self.project_dir / ".env")
        base = yaml.safe_load((ROOT / "spectra.config.yaml").read_text(encoding="utf-8")) or {}
        proj_file = self.project_dir / "proyecto.yaml"
        proj = yaml.safe_load(proj_file.read_text(encoding="utf-8")) if proj_file.exists() else {}
        self.data = deep_merge(deep_merge(base, proj or {}), overrides or {})

    # --- accesos cómodos -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def orq(self) -> dict:
        return self.data.get("orquestador", {})

    @property
    def perfil(self) -> str:
        return self.orq.get("perfil_modelos", "principal")

    def agent(self, agent_id: str) -> dict:
        a = self.data.get("agentes", {}).get(agent_id)
        if a is None:
            raise KeyError(f"Agente no configurado: {agent_id}")
        return a

    def path(self, rel: str | Path) -> Path:
        """Ruta relativa al proyecto si existe ahí; si no, relativa a la raíz de SPECTRA."""
        p = Path(rel)
        if p.is_absolute():
            return p
        cand = self.project_dir / p
        return cand if cand.exists() else ROOT / p

    def orchestrator_mode(self) -> str:
        mode = self.orq.get("implementacion", self.orq.get("modo", "python"))
        if mode == "openrouter":       # alias de v2
            return "python"
        if mode == "auto":
            in_cc = bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"))
            return "claude_code" if (shutil.which("claude") and in_cc) else "python"
        return mode

    # --- resolución de modelo (§3.1 de la especificación) -------------------
    def resolve_model(self, agent_id: str, perfil: str | None = None, which: str | None = None) -> ModelChoice:
        a = self.agent(agent_id)
        perfil = perfil or self.perfil
        spec = a.get(which) if which else None
        if spec is None:
            spec = a.get(perfil) or a.get("principal") or {}
            if "override" in a:
                spec = deep_merge(spec, a["override"])
        mode = self.orchestrator_mode()
        cc = spec.get("claude_code")
        if mode == "claude_code" and cc and cc.get("subagente"):
            return ModelChoice(agent_id, "claude_code", f"claude-code/{cc.get('modelo', 'sonnet')}",
                               subagente=cc["subagente"], claude_code_modelo=cc.get("modelo"))
        proveedor = spec.get("proveedor", "openrouter")
        modelo = spec.get("modelo") or spec.get("openrouter_id")
        if proveedor == "claude_cli" and cc:
            modelo = spec.get("modelo") or cc.get("modelo")
        if not modelo:
            raise ValueError(f"El agente {agent_id} no tiene modelo para el perfil {perfil}")
        return ModelChoice(agent_id, proveedor, modelo,
                           subagente=(cc or {}).get("subagente"),
                           claude_code_modelo=(cc or {}).get("modelo"),
                           extra={k: v for k, v in spec.items() if k not in ("claude_code", "openrouter_id", "modelo", "proveedor")})

    def provider_conf(self, name: str) -> dict:
        p = self.data.get("proveedores", {}).get(name)
        if p is None:
            raise KeyError(f"Proveedor no configurado: {name}")
        return p
