"""Pruebas offline del núcleo determinista de SPECTRA (sin LLM ni red)."""
import json
import shutil
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spectra import drawio, gates  # noqa: E402
from spectra.artifacts import ArtifactStore, content_hash  # noqa: E402
from spectra.bib import build_bib, cite_label, parse_bib, parse_marco_refs  # noqa: E402
from spectra.compile import Citer, md_to_html, md_to_latex  # noqa: E402
from spectra.config import Config  # noqa: E402
from spectra.util import extract_fenced, extract_json, make_bib_key  # noqa: E402

GRAPH = {"raiz": "n0", "nodos": [
    {"id": "n0", "etiqueta": "Problema: sincronía DJ-luces", "tipo": "problema"},
    {"id": "n1", "etiqueta": "H1: latencia < 10 ms", "tipo": "hipotesis", "origen": "art_01#H1"},
    {"id": "n2", "etiqueta": "Reloj compartido", "tipo": "concepto"},
    {"id": "n3", "etiqueta": "Ableton Link", "tipo": "trabajo"},
    {"id": "n4", "etiqueta": "57 pruebas pasan", "tipo": "resultado"}],
    "aristas": [{"de": "n0", "a": "n1", "etiqueta": "se aborda con"}, {"de": "n1", "a": "n2"},
                {"de": "n2", "a": "n3"}, {"de": "n1", "a": "n4", "etiqueta": "evidencia"}],
    "secciones_borrador": [{"seccion": "Introducción", "nodos": ["n0", "n1"]}]}

ARQ = {"grupos": [{"id": "g1", "etiqueta": "Emisor"}, {"id": "g2", "etiqueta": "Receptor"}],
       "nodos": [{"id": "a", "etiqueta": "Mixxx", "tipo": "externo", "grupo": "g1"},
                 {"id": "b", "etiqueta": "libvertexav emitter", "tipo": "componente", "grupo": "g1"},
                 {"id": "c", "etiqueta": "Plugin QLC+", "tipo": "componente", "grupo": "g2"},
                 {"id": "d", "etiqueta": "Sin grupo", "tipo": "datos"}],
       "aristas": [{"de": "a", "a": "b"}, {"de": "b", "a": "c", "etiqueta": "UDP/WebSocket"}, {"de": "c", "a": "d"}]}


def test_drawio_tree_and_layered_are_valid_xml(tmp_path):
    for g, lay in ((GRAPH, "tree"), (ARQ, "layered")):
        res = drawio.build(g, tmp_path / f"{lay}.drawio", lay)
        root = ET.fromstring(Path(res["drawio"]).read_text(encoding="utf-8"))
        assert root.tag == "mxfile"
        cells = list(root.iter("mxCell"))
        assert sum(1 for c in cells if c.get("vertex") == "1") >= len(g["nodos"])
        ET.fromstring(Path(res["svg"]).read_text(encoding="utf-8"))


def test_drawio_roundtrip(tmp_path):
    drawio.build(ARQ, tmp_path / "a.drawio", "layered")
    g = drawio.from_drawio(tmp_path / "a.drawio")
    assert {n["id"] for n in g["nodos"]} == {"a", "b", "c", "d"}
    assert {n["id"]: n.get("grupo") for n in g["nodos"]}["b"] == "g1"
    assert {(e["de"], e["a"]) for e in g["aristas"]} == {("a", "b"), ("b", "c"), ("c", "d")}


def test_artifact_versioning_and_hash(tmp_path):
    st = ArtifactStore(tmp_path)
    v1, p1 = st.write("art_01_ideas", {"titulo_propuesto": "x"}, agente="a01", proveedor="openrouter", modelo="m")
    v2, p2 = st.write("art_01_ideas", {"titulo_propuesto": "y"}, agente="a01", proveedor="openrouter", modelo="m")
    assert (v1, v2) == (1, 2) and p1.exists() and p2.exists()
    meta, data = st.read("art_01_ideas")
    assert data["titulo_propuesto"] == "y" and meta["sha256"] == content_hash("art_01_ideas", data)
    v, p = st.write("art_07_borrador", "---\niteracion: 1\n---\n# T\n## Resumen\nHola", agente="a07", proveedor="p", modelo="m")
    meta, body = st.read("art_07_borrador")
    assert meta["iteracion"] == 1 and body.startswith("# T")


def test_citations_gate():
    bib = "@article{smith2020a,\n title={A}}\n@misc{ruiz2026vertexav, title={B}}"
    md = "Texto [@smith2020a, p. 3; @ruiz2026vertexav] y @falta2021x. correo a@b.com `@code`"
    r = gates.check_citations(md, bib)
    assert r["faltantes"] == ["falta2021x"] and not r["ok"]


def test_numbers_gate_strict_results():
    art05 = {"resumen": {"lat": {"mediana_ms": 1.04}}, "pruebas": 57, "accuracy": 0.962}
    strict = gates.numbers_in(art05)
    allp = strict | gates.numbers_in("puerto 9470")
    md = ("## Metodología\nUsa el puerto 9470 y 9999.\n## Resultados\nPasaron 57 pruebas; mediana 1,04 ms; "
          "precisión 96.2 %; error 3.7 ms. En 2026 se probó H1 [@x, p. 77].\n")
    r = gates.check_numbers(md, strict, allp, {"enteros_ignorados_max": 12})
    bad = {(d["texto"], d["seccion"]) for d in r["discrepancias"]}
    assert ("9999", "Metodología") in bad and ("3.7", "Resultados") in bad
    assert not any(t in ("57", "1,04", "96.2 %", "9470", "2026", "77") for t, _ in bad)
    r2 = gates.check_numbers("## Metodología\nEl 2026-10-28 a las 16:17:54Z.\n", strict, allp, {})
    assert r2["ok"]


def test_structure_and_similarity():
    md = "# T\n## Resumen\n## Introducción\n## Limitaciones\n"
    assert gates.check_structure(md, ["Resumen", "Limitaciones"])["ok"]
    assert gates.check_structure(md, ["Declaración de uso de IA"])["faltantes"] == ["Declaración de uso de IA"]
    src = "the quick brown fox jumps over the lazy dog near the river bank today"
    r = gates.check_similarity("Intro. " + src, {"s": src}, n=8, umbral=0.15)
    assert r["valor"] > 0.5 and not r["ok"]


def test_comparative_tables_gate():
    tabla = "| Concepto | Definición | Fuente |\n|---|---|---|\n| A | x | [@a] |\n| B | y | [@b] |\n"
    ok = f"# T\n## Marco Teórico\nTexto.\n\n{tabla}\nTabla 1. Comparativa.\n## Trabajos Relacionados\nTexto.\n{tabla}\n## Metodología\n"
    assert gates.check_comparative_tables(ok)["ok"]
    sin = "# T\n## Marco Teórico\nTexto.\n" + tabla + "\nUn párrafo largo después de la tabla. " * 20 + "\n## Trabajos Relacionados\nSolo texto.\n"
    assert gates.check_comparative_tables(sin)["faltantes"] == ["Marco Teórico", "Trabajos Relacionados"]


def test_bib_parse_format_and_build_offline():
    bib = '@article{lamport1978time,\n  title={Time, clocks, and the ordering of events},\n  author={Lamport, Leslie},\n  year={1978},\n  journal={CACM}, doi={10.1145/359545.359563}\n}'
    p = parse_bib(bib)
    assert p["lamport1978time"]["year"] == "1978" and cite_label(p["lamport1978time"]) == "Lamport, 1978"
    md = "## Referencias usadas\n- clave: ruiz2026x · doi: — · archivo: spec.md · tipo: documento_proyecto\n"
    assert parse_marco_refs(md)[0]["clave"] == "ruiz2026x"
    art03 = {"articulos": [{"clave_bib": "mills1991ntp", "id": {"doi": None, "arxiv": None, "url": "https://x"},
                            "titulo": "Internet time synchronization", "autores": ["David L. Mills"], "anio": 1991}]}
    text, informe, sin = build_bib(art03, md, {"ruiz2026x": {"titulo": "Spec", "autores": ["Adan Ruiz"], "anio": 2026,
                                                             "tipo": "documento_proyecto"}}, online=False)
    keys = gates.bib_keys(text)
    assert {"mills1991ntp", "ruiz2026x"} <= keys and not sin


def test_compile_internal(tmp_path):
    bib = "@misc{ruiz2026x, title={Spec}, author={Ruiz, Adan}, year={2026}}"
    md = "# Título\n## Resumen\nTexto [@ruiz2026x] con **negrita** y $x^2$.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- uno\n- dos\n"
    h = md_to_html(md, bib, "apa", tmp_path)
    assert "(Ruiz, 2026)" in h and "<table>" in h and "Referencias" in h
    h2 = md_to_html("| a | b |\n|---|---|\n| mediana de \\|e\\| | 2 |\n", bib, "apa", tmp_path)
    assert "<td>mediana de |e|</td>" in h2
    tex = md_to_latex(md, Citer(parse_bib(bib)), "", "art_refs.bib")
    assert "\\parencite{ruiz2026x}" in tex and "\\begin{itemize}" in tex


def test_util_json_and_keys():
    assert extract_json("bla ```json\n{\"a\": 1}\n``` fin") == {"a": 1}
    assert extract_json("prefacio {\"a\": {\"b\": \"}\"}} sufijo") == {"a": {"b": "}"}}
    g, rest = extract_fenced("# P\n```json arquitectura\n{\"nodos\": []}\n```\nfin", "arquitectura")
    assert json.loads(g) == {"nodos": []} and "arquitectura" not in rest
    assert make_bib_key(["Leslie Lamport"], 1978, "Time, clocks and the ordering") == "lamport1978time"


def _project(tmp_path, extra: str = "") -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "proyecto.yaml").write_text("proyecto: {tema: t}\nhitl: {modo: archivo}\n" + extra, encoding="utf-8")
    return d


def test_skip_logic_and_presets(tmp_path):
    from spectra.orchestrator import Orchestrator
    d = _project(tmp_path, "flujo: {preset: completo}\n")
    o = Orchestrator(Config(d))
    assert not o.should_skip("S01_IDEAS")
    o.store.write("art_01_ideas", {"titulo_propuesto": "x"}, agente="humano", proveedor="humano", modelo="-")
    assert o.should_skip("S01_IDEAS")
    o.st["visitados"] = ["S01_IDEAS"]
    assert not o.should_skip("S01_IDEAS")          # ya visitado: no se salta al volver
    d2 = tmp_path / "p2"
    d2.mkdir()
    (d2 / "proyecto.yaml").write_text("existentes: {implementacion: {repo: .}}\n", encoding="utf-8")
    o2 = Orchestrator(Config(d2))
    assert o2.preset == "implementacion_existente"
    assert o2.agent_for("S05_IMPL") == "a05b_documentador" and o2.agent_for("S04_PROPUESTA") == "a04b_arquitectura_inversa"


def test_agent_override_and_orchestrator_swap(tmp_path):
    d = _project(tmp_path, "agentes:\n  a07_redactor:\n    override: {proveedor: lmstudio, modelo: local-model}\n"
                           "orquestador: {implementacion: python}\n")
    cfg = Config(d)
    mc = cfg.resolve_model("a07_redactor")
    assert (mc.proveedor, mc.modelo) == ("lmstudio", "local-model")
    cfg2 = Config(d, {"orquestador": {"implementacion": "claude_code"}})
    assert cfg2.resolve_model("a07_redactor").proveedor == "claude_code"
    assert cfg2.resolve_model("a08_revisor").proveedor == "openrouter"   # no-Claude sigue por OpenRouter


def test_saltar_requires_artifacts(tmp_path):
    from spectra.orchestrator import Orchestrator
    d = _project(tmp_path, "flujo: {preset: solo_redaccion}\n")
    o = Orchestrator(Config(d))
    with pytest.raises(RuntimeError):
        o.should_skip("S05_IMPL")


def test_docx_zotero_fields(tmp_path):
    import shutil as _sh
    import zipfile
    from spectra.docx_export import build_doc_json, export_docx, latex_runs
    bib = "@misc{ruiz2026x, title={Spec}, author={Ruiz, Adan}, year={2026}, doi={—}}"
    md = "# Título\n## Resumen\nTexto [@ruiz2026x, p. 3] y $rtt_{\min} \le 2$.\n\n| a | b | c |\n|---|---|---|\n| 1 | \|e\| | 3 |\n"
    d = build_doc_json(md, bib, tmp_path, tmp_path, zotero=False)
    assert d["citations"][0]["code"].startswith("ADDIN ZOTERO_ITEM CSL_CITATION")
    assert '"locator": "3"' in d["citations"][0]["code"] and "DOI" not in d["citations"][0]["code"]
    assert any(r.get("subScript") and r["text"] == "min" for r in latex_runs(r"rtt_{\min}"))
    if not _sh.which("node"):
        return
    r = export_docx(md, bib, tmp_path / "p.docx", tmp_path, zotero=False)
    assert "error" not in r, r
    xml = zipfile.ZipFile(r["docx"]).read("word/document.xml").decode("utf-8")
    assert "ZOTERO_ITEM CSL_CITATION" in xml and "ZOTERO_BIBL" in xml and "§§" not in xml
    assert "ZOTERO_PREF_1" in zipfile.ZipFile(r["docx"]).read("docProps/custom.xml").decode("utf-8")


def test_family_heterogeneity_enforced(tmp_path):
    from spectra.orchestrator import Orchestrator
    d = _project(tmp_path, "agentes:\n  a08_revisor:\n    override: {openrouter_id: anthropic/claude-haiku-5.5}\n")
    cfg = Config(d)
    assert any("a08_revisor" in v and "anthropic" in v for v in cfg.family_violations())
    o = Orchestrator(cfg)
    o.run(max_pasos=1)
    assert "Heterogeneidad" in o.st["estado_detalle"]          # se detiene antes de llamar a ningún modelo
    (tmp_path / "ok").mkdir()
    assert Config(_project(tmp_path / "ok", "")).family_violations() == []


def test_preregistration_detects_in_place_edit(tmp_path):
    from spectra.orchestrator import Orchestrator
    o = Orchestrator(Config(_project(tmp_path, "")))
    v, p = o.store.write("art_04_propuesta", "# Propuesta\n## Protocolo experimental\nH1: x < 10 ms", agente="a", proveedor="p", modelo="m")
    o._freeze()
    assert o._check_hash()
    p.write_text(p.read_text(encoding="utf-8").replace("x < 10 ms", "x < 20 ms"), encoding="utf-8")   # edición a mano
    assert not o._check_hash() and o.st["preregistro_violado"]


def test_hitl_minutes_recorded(tmp_path):
    from spectra.hitl import record_decision
    from spectra.state import State
    st = State(tmp_path)
    st["hitl"]["H2"] = {"pendiente": True, "ts": "2026-10-28T10:00:00Z", "opciones": ["aprobar", "cambios", "rechazar"]}
    record_decision(st, "H2", "aprobar")
    assert st["hitl"]["H2"]["solicitado"] == "2026-10-28T10:00:00Z" and st["hitl"]["H2"]["minutos_espera"] > 0


def test_d1_verifies_non_a3_entries(monkeypatch):
    from spectra import bib as B
    monkeypatch.setattr(B.academic, "verify", lambda a, email="": {"ok": False, "fuente": "crossref", "motivo": "metadatos no coinciden",
                                                                  "coincide_titulo": False, "coincide_autor": True, "coincide_anio": True})
    monkeypatch.setattr(B.academic, "crossref_bibtex", lambda doi: None)
    md = "## Referencias usadas\n- clave: falso2020x · doi: 10.1/abc · archivo: — · tipo: ficha_resumen\n"
    text, informe, sin = B.build_bib({"articulos": []}, md, {"falso2020x": {"titulo": "Otro", "autores": ["A B"], "anio": 2020}})
    assert "falso2020x" not in gates.bib_keys(text) and any("falso2020x" in s for s in sin)
