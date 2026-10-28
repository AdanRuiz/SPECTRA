"""Human-in-the-Loop: terminal | archivo | auto."""
from __future__ import annotations

import sys
from pathlib import Path

from .artifacts import now_iso

OPCIONES_DEF = {"aprobar": "Aprobar y continuar", "cambios": "Pedir cambios (con comentario)", "rechazar": "Rechazar"}


class HITL:
    def __init__(self, ctx):
        self.ctx = ctx
        self.mode = ctx.cfg.get("hitl.modo", "archivo")
        self.dir = ctx.project_dir / "state" / "hitl"
        self.dir.mkdir(parents=True, exist_ok=True)

    def ask(self, h: str, titulo: str, resumen: str, opciones: dict | None = None) -> dict | None:
        """Devuelve {decision, comentario} o None si queda pendiente (modo archivo)."""
        st = self.ctx.state
        opciones = opciones or OPCIONES_DEF
        dec = st["hitl"].get(h)
        if dec and not dec.get("pendiente"):
            st["hitl"].pop(h)
            st.data.setdefault("hitl_historial", []).append({"hitl": h, **dec})
            st.save()
            return dec
        if self.mode == "auto":
            d = {"decision": "aprobar", "comentario": "aprobación automática (hitl.modo=auto)", "ts": now_iso(), "por": "auto"}
            st.data.setdefault("hitl_historial", []).append({"hitl": h, **d})
            st.save()
            return d
        if self.mode == "terminal" and sys.stdin and sys.stdin.isatty():
            print(f"\n{'=' * 78}\n[HITL {h}] {titulo}\n{'=' * 78}\n{resumen}\n")
            keys = list(opciones)
            for i, k in enumerate(keys, 1):
                print(f"  {i}) {k}: {opciones[k]}")
            while True:
                r = input("Opción: ").strip()
                if r.isdigit() and 1 <= int(r) <= len(keys):
                    k = keys[int(r) - 1]
                    break
                if r in keys:
                    k = r
                    break
            com = input("Comentario (opcional): ").strip() if k != "aprobar" or True else ""
            d = {"decision": k, "comentario": com, "ts": now_iso(), "por": "terminal"}
            st.data.setdefault("hitl_historial", []).append({"hitl": h, **d})
            st.save()
            return d
        # modo archivo
        f = self.dir / f"{h}.md"
        ops = "\n".join(f"- `{k}`: {v}" for k, v in opciones.items())
        proj = self.ctx.project_dir
        f.write_text(f"# HITL {h}: {titulo}\n\n{resumen}\n\n## Opciones\n{ops}\n\n## Cómo responder\n"
                     f"```\npython -m spectra approve {h} -p {proj}\n"
                     f"python -m spectra decide {h} <opcion> -c \"comentario\" -p {proj}\n"
                     f"python -m spectra edit {h} <artefacto> <archivo_editado> -p {proj}\n"
                     f"python -m spectra resume -p {proj}\n```\n", encoding="utf-8")
        st["hitl"][h] = {"pendiente": True, "titulo": titulo, "archivo": str(f), "opciones": list(opciones), "ts": now_iso()}
        st.save()
        print(f"[SPECTRA] HITL {h} pendiente: {titulo}\n          Revisa {f}")
        return None


def record_decision(state, h: str, decision: str, comentario: str = "", por: str = "humano") -> None:
    pend = state["hitl"].get(h) or {}
    ops = pend.get("opciones")
    if ops and decision not in ops:
        raise ValueError(f"Opción inválida para {h}: {decision}. Válidas: {ops}")
    state["hitl"][h] = {"decision": decision, "comentario": comentario, "ts": now_iso(), "por": por}
    state.save()
