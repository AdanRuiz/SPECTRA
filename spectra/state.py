"""Estado persistente del pipeline (state/pipeline_state.json) y registro de costos."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .artifacts import now_iso


class BudgetExceeded(RuntimeError):
    pass


class State:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "state" / "pipeline_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "estado": None,           # estado actual de la máquina
                "estado_detalle": "",
                "historial": [],          # [{estado, resultado, ts, nota}]
                "iteracion_revision": 0,
                "rondas_peer_review": 0,
                "reintentos": {},
                "hash_preregistro": None,
                "preregistro_tipo": None,  # prospectivo | post_hoc
                "hitl": {},               # H -> {decision, comentario, ts}
                "saltados": [],
                "gasto_usd": 0.0,
                "gasto_por_agente": {},
                "creado": now_iso(),
            }
            self.save()

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def __setitem__(self, k: str, v: Any) -> None:
        self.data[k] = v

    def get(self, k: str, d: Any = None) -> Any:
        return self.data.get(k, d)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def log(self, estado: str, resultado: str, nota: str = "") -> None:
        self.data["historial"].append({"estado": estado, "resultado": resultado, "ts": now_iso(), "nota": nota})
        self.save()

    def set_state(self, estado: str, detalle: str = "") -> None:
        self.data["estado"] = estado
        self.data["estado_detalle"] = detalle
        self.save()


class CostLog:
    """logs/costos.jsonl + control de presupuesto (§8)."""

    def __init__(self, project_dir: Path, state: State, cfg):
        self.file = Path(project_dir) / "logs" / "costos.jsonl"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.state = state
        self.total = float(cfg.orq.get("presupuesto_usd_total", 30))
        self.per_agent = float(cfg.orq.get("presupuesto_usd_por_agente", 6))
        self.warn = float(cfg.orq.get("aviso_presupuesto", 0.8))
        self._warned = False

    def check(self, agent_id: str) -> None:
        gasto = self.state["gasto_usd"]
        if gasto >= self.total:
            raise BudgetExceeded(f"Presupuesto total agotado: {gasto:.2f} / {self.total:.2f} USD")
        ga = self.state["gasto_por_agente"].get(agent_id, 0.0)
        if ga >= self.per_agent:
            raise BudgetExceeded(f"Presupuesto del agente {agent_id} agotado: {ga:.2f} / {self.per_agent:.2f} USD")
        if gasto >= self.warn * self.total and not self._warned:
            self._warned = True
            print(f"[SPECTRA] Aviso: gasto {gasto:.2f} USD = {100 * gasto / self.total:.0f}% del presupuesto")

    _lock = threading.Lock()

    def record(self, agent_id: str, modelo: str, proveedor: str, usage: dict, dur_s: float) -> None:
        with self._lock:
            self._record(agent_id, modelo, proveedor, usage, dur_s)

    def _record(self, agent_id: str, modelo: str, proveedor: str, usage: dict, dur_s: float) -> None:
        usd = float(usage.get("usd") or 0.0)
        self.state["gasto_usd"] = round(self.state["gasto_usd"] + usd, 6)
        pa = self.state["gasto_por_agente"]
        pa[agent_id] = round(pa.get(agent_id, 0.0) + usd, 6)
        self.state.save()
        line = {"ts": now_iso(), "agente": agent_id, "modelo": modelo, "proveedor": proveedor,
                "tokens_entrada": usage.get("tokens_entrada", 0), "tokens_salida": usage.get("tokens_salida", 0),
                "usd": usd, "duracion_s": round(dur_s, 2)}
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


class Timer:
    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *a):
        self.dt = time.time() - self.t0
