from pathlib import Path
from bs4 import BeautifulSoup
import subprocess
import json
import tempfile
import os

def run_node_script(script_content: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script_content)
        temp_name = f.name
    try:
        res = subprocess.run(["node", temp_name], capture_output=True, text=True, check=True)
        return json.loads(res.stdout.strip())
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def test_audit_verdict_in_index_html_is_neutral_by_default():
    """index.html must not hardcode 'PASE A ENTREVISTA' or green styling in auditVerdict by default."""
    html_path = Path("static/index.html")
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    
    verdict_el = soup.find(id="auditVerdict")
    assert verdict_el is not None, "#auditVerdict element must exist in index.html"
    
    text = verdict_el.get_text().strip()
    classes = verdict_el.get("class", [])
    
    assert text != "PASE A ENTREVISTA", "Initial template must not claim 'PASE A ENTREVISTA' before analysis is loaded"
    assert "text-emerald-400" not in classes, "Initial template must not use emerald green badge by default"
    assert "PENDIENTE" in text.upper() or "--" in text, f"Expected pending verdict in initial template, got: {text}"


def test_limpiar_detalle_oferta_uses_neutral_placeholders_not_fake_metrics():
    """limpiarDetalleOferta must use neutral '--%' placeholders, not fake 0% ATS or fake 100% Anti-AI."""
    js_path = Path("static/app.js")
    code = js_path.read_text(encoding="utf-8")
    
    # Locate limpiarDetalleOferta function
    assert "function limpiarDetalleOferta()" in code
    fn_body = code.split("function limpiarDetalleOferta()")[1].split("resetBotonDescartar()")[0]
    
    assert "atsScore.textContent = '0%';" not in fn_body, "atsScore in limpiarDetalleOferta should be '--%', not fake '0%'"
    assert "aiScore.textContent = '100%';" not in fn_body, "aiScore in limpiarDetalleOferta should be '--%', not fake '100%'"
    assert "kwPctEl.textContent = '0%';" not in fn_body, "kwPctEl in limpiarDetalleOferta should be '--%', not fake '0%'"
    assert "afinidadEl.textContent = '0%';" not in fn_body, "afinidadEl in limpiarDetalleOferta should be '--%', not fake '0%'"


def test_render_oferta_does_not_crash_on_undefined_score_and_renders_audit():
    """renderOferta must execute without throwing ReferenceError: score is not defined,
    and properly populate all audit DOM elements from real offer data.
    """
    js_code = Path("static/app.js").read_text(encoding="utf-8")
    
    node_test_script = f"""
    // Mock minimal browser environment
    const elements = {{}};
    function getOrCreateEl(id) {{
      if (!elements[id]) {{
        elements[id] = {{
          id: id,
          textContent: '',
          innerHTML: '',
          className: '',
          classList: {{
            add: (c) => {{}},
            remove: (c) => {{}},
            contains: (c) => false
          }},
          style: {{}},
          value: '',
          setAttribute: () => {{}},
          appendChild: () => {{}}
        }};
      }}
      return elements[id];
    }}

    global.document = {{
      getElementById: (id) => getOrCreateEl(id),
      addEventListener: () => {{}},
      removeEventListener: () => {{}},
      createElement: (tag) => ({{
        className: '',
        textContent: '',
        appendChild: () => {{}}
      }})
    }};
    global.window = {{
      innerWidth: 1200,
      addEventListener: () => {{}},
      removeEventListener: () => {{}}
    }};
    global.marked = {{
      parse: (txt) => txt
    }};
    global.escapeHtml = (s) => s;
    global.formatearDescripcionVacante = () => '';
    global.resetBotonDescartar = () => {{}};
    global.renderizarListaTags = () => {{}};
    global.renderMatrizOferta = () => {{}};
    global.updatePitchView = () => {{}};
    global.renderAuditFindings = () => {{}};
    global.actualizarScoreVisual = () => {{}};
    global.formatearDiasTranscurridos = () => null;

    // Load app.js functions
    {js_code}

    // Test payload with real audit data (like ID 5)
    const testOferta = {{
      id: 5,
      titulo: 'Analista Funcional IA',
      empresa: 'NIZTECH',
      status: 'READY',
      match_score: 93.8,
      cv_markdown: '# CV Test',
      reporte_auditoria: {{
        verdict: 'PASE A ENTREVISTA',
        ats_score: 100.0,
        anti_ai_score: 100,
        deducciones: [],
        ats: {{
          ats_score: 100.0,
          keyword_coverage: 1.0,
          found_keywords: ['SQL', 'Jira', 'APIs'],
          missing_keywords: []
        }},
        anti_ai: {{
          clean: true,
          detected_words: [],
          anti_ai_score: 100
        }}
      }}
    }};

    try {{
      renderOferta(testOferta);
      console.log(JSON.stringify({{
        success: true,
        auditVerdict: elements['auditVerdict']?.textContent,
        auditAfinidadScore: elements['auditAfinidadScore']?.textContent,
        atsScoreValue: elements['atsScoreValue']?.textContent,
        aiScoreValue: elements['aiScoreValue']?.textContent,
        keywordCoveragePct: elements['keywordCoveragePct']?.textContent
      }}));
    }} catch (err) {{
      console.log(JSON.stringify({{
        success: false,
        error: err.message,
        stack: err.stack
      }}));
    }}
    """
    
    out = run_node_script(node_test_script)
    assert out["success"] is True, f"renderOferta crashed with error: {out.get('error')}"
    assert out["auditAfinidadScore"] == "94%"
    assert out["atsScoreValue"] == "100%"
    assert out["aiScoreValue"] == "100%"
    assert out["keywordCoveragePct"] == "100%"
    assert out["auditVerdict"] == "PASE A ENTREVISTA"


def test_render_oferta_pending_audit_displays_clean_pending_state():
    """When an offer has no audit report yet, renderOferta must show pending status and '--%', not fake scores."""
    js_code = Path("static/app.js").read_text(encoding="utf-8")
    
    node_test_script = f"""
    const elements = {{}};
    function getOrCreateEl(id) {{
      if (!elements[id]) {{
        elements[id] = {{
          id: id,
          textContent: '',
          innerHTML: '',
          className: '',
          classList: {{
            add: (c) => {{}},
            remove: (c) => {{}},
            contains: (c) => false
          }},
          style: {{}},
          value: '',
          setAttribute: () => {{}},
          appendChild: () => {{}}
        }};
      }}
      return elements[id];
    }}

    global.document = {{
      getElementById: (id) => getOrCreateEl(id),
      addEventListener: () => {{}},
      removeEventListener: () => {{}},
      createElement: (tag) => ({{
        className: '',
        textContent: '',
        appendChild: () => {{}}
      }})
    }};
    global.window = {{ innerWidth: 1200,
      addEventListener: () => {{}},
      removeEventListener: () => {{}} }};
    global.marked = {{ parse: (txt) => txt }};
    global.escapeHtml = (s) => s;
    global.formatearDescripcionVacante = () => '';
    global.resetBotonDescartar = () => {{}};
    global.renderizarListaTags = () => {{}};
    global.renderMatrizOferta = () => {{}};
    global.updatePitchView = () => {{}};
    global.renderAuditFindings = () => {{}};
    global.actualizarScoreVisual = () => {{}};
    global.formatearDiasTranscurridos = () => null;

    {js_code}

    const pendingOferta = {{
      id: 99,
      titulo: 'Oferta Reciente',
      empresa: 'Tech Corp',
      status: 'PROCESSING',
      match_score: 75.0,
      cv_markdown: '',
      reporte_auditoria: null
    }};

    try {{
      renderOferta(pendingOferta);
      console.log(JSON.stringify({{
        success: true,
        auditVerdict: elements['auditVerdict']?.textContent,
        atsScoreValue: elements['atsScoreValue']?.textContent,
        aiScoreValue: elements['aiScoreValue']?.textContent,
        keywordCoveragePct: elements['keywordCoveragePct']?.textContent
      }}));
    }} catch (err) {{
      console.log(JSON.stringify({{
        success: false,
        error: err.message
      }}));
    }}
    """
    
    out = run_node_script(node_test_script)
    assert out["success"] is True, f"renderOferta crashed on pending offer: {out.get('error')}"
    assert "PENDIENTE" in out["auditVerdict"].upper()
    assert out["atsScoreValue"] == "--%", f"atsScoreValue should be '--%' for pending offer, got {out['atsScoreValue']}"
    assert out["aiScoreValue"] == "--%", f"aiScoreValue should be '--%' for pending offer, got {out['aiScoreValue']}"
    assert out["keywordCoveragePct"] == "--%", f"keywordCoveragePct should be '--%' for pending offer, got {out['keywordCoveragePct']}"
