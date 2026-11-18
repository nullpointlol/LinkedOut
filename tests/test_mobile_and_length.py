"""Pruebas unitarias para el límite estricto de 2500 caracteres, condensación y mobile layout."""
import pytest
from core.writer import WriterAgent, ensure_cv_length
from core.auditor import AuditorAgent, evaluate_ats_compliance
from fastapi.testclient import TestClient
from server import app

SAMPLE_LONG_CV = """# SAM ORTEGA
SENIOR TECHNICAL BUSINESS ANALYST
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | https://wa.me/5491100000000 | sam.ortega@example.com | LinkedIn: https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 7 años de trayectoria optimizando flujos de trabajo, pipelines de CI/CD e integración de sistemas complejos en corporaciones multinacionales y empresas de alta demanda operativa, liderando iniciativas de transformación tecnológica y reducción sistemática de errores productivos mediante arquitecturas robustas y enfoques basados en datos comprobables.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el tiempo de procesamiento manual de pedidos mediante el diseño e implementación de flujos automatizados con **Python** y modelos de lenguaje a gran escala, integrando múltiples fuentes de datos distribuidas con validaciones avanzadas de consistencia funcional y trazabilidad operativa.
• **Garantía de Calidad & QA:** Elevó la trazabilidad funcional de operaciones críticas al **100%** articulando requerimientos técnicos y casos de prueba automatizados entre negocio y desarrollo con matrices de cobertura exhaustiva.
• **Optimización de Despliegues:** Disminuyó en un **30%** la fricción en pases a producción mediante pipelines de integración continua en **GitLab CI**.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad & Calidad de Código:** Centralizó la observabilidad de más de **90 aplicaciones** construyendo un extractor automatizado en **Python** para la API de **Codacy** y tableros en **PowerBI**, reduciendo la deuda técnica en un **25%** a nivel corporativo.
• **Gestión de Incidentes Críticos:** Aceleró en un **35%** los tiempos de resolución de incidentes productivos de severidad alta analizando métricas de rendimiento con **PagerDuty** y ejecutando diagnósticos directos en **SQL** relacional.
• **Integración de Datos & Middleware:** Automatizó la conciliación masiva de datos entre sistemas legados y **ESB** mediante scripts **SQL** y pruebas de servicios con **Insomnia**, eliminando semanas de labor manual operativa redundante.
• **Capacitación Técnica:** Entrenó a más de **30 analistas juniors** en metodologías ágiles y gestión de incidentes en entornos de alta disponibilidad.

## HABILIDADES TÉCNICAS
• **Metodologías & Análisis:** Relevamiento funcional, UML, BPMN, Historias de Usuario, Jira, Scrum, Kanban.
• **Lenguajes & Automatización:** Python, SQL, Bash, APIs REST/SOAP, Microservicios.
• **CI/CD, Calidad & DevOps:** Jenkins, GitLab CI/CD, Codacy, Linux, Docker, Bash scripting.
• **Datos & Observabilidad:** PowerBI, PagerDuty, pgAdmin, DBeaver, Prometheus.

## EDUCACIÓN
• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)
• Bachiller en Ciencias Naturales - EET N°4 (2008)

## IDIOMAS
• Inglés: Avanzado escrito y lectura, intermedio oral.
• Español: Nativo.
"""

def test_ensure_cv_length_trims_overlong_cv():
    # SAMPLE_LONG_CV supera o se acerca a 2500
    long_text = SAMPLE_LONG_CV + "\n" + ("• **Logro Extra:** Información complementaria que excede el tamaño ideal para lectura en 15 segundos.\n" * 10)
    assert len(long_text) > 2800

    condensed = ensure_cv_length(long_text, max_chars=2500)
    assert len(condensed) <= 2500
    # Debe conservar las secciones canónicas
    for sec in ["PERFIL", "EXPERIENCIA", "HABILIDADES TÉCNICAS", "EDUCACIÓN", "IDIOMAS"]:
        assert sec in condensed
    # Debe conservar el nombre y contacto
    assert "SAM ORTEGA" in condensed
    assert "Ciudad Ejemplo" in condensed

def test_writer_agent_fallback_length_is_balanced():
    writer = WriterAgent()
    fallback = writer._deterministic_fallback(
        puesto="Senior Technical Business Analyst",
        empresa="Mercado Libre",
        analysis={"dolores_empresa": ["Optimizar procesos"]},
        profile=writer.load_candidate_profile()
    )
    cv_md = fallback["cv_markdown"]
    assert len(cv_md) <= 3500
    assert len(cv_md) >= 1500

def test_auditor_compliance_reports_length_within_limit():
    keywords = ["Python", "SQL", "Jenkins", "Codacy"]
    result = evaluate_ats_compliance(SAMPLE_LONG_CV, keywords)

    assert "longitud" in result
    assert "caracteres" in result["longitud"]
    assert "lectura_estimada_min" in result["longitud"]

def test_auditor_remediate_enforces_clean_language():
    auditor = AuditorAgent()
    keywords = ["Python", "SQL"]
    long_dirty_cv = SAMPLE_LONG_CV + "\n" + ("• **Logro de relleno:** Tarea con palabras fetiche de IA como delve into the pivotal tapestry.\n" * 12)
    assert len(long_dirty_cv) > 3000

    clean_cv, clean_report = auditor.remediate_deterministic(long_dirty_cv, keywords)
    assert "delve" not in clean_cv.lower()
    assert "pivotal" not in clean_cv.lower()

def test_api_regenerate_and_recalculate_endpoints(tmp_path, monkeypatch):
    from core.database import init_db
    test_db = tmp_path / "test_regen.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)

    async def dummy_run_prompt(self, prompt, json_mode=True, timeout_seconds=180):
        if "Estratega" in prompt:
            return {
                "empresa": "TechCorp",
                "puesto": "Analista Técnico",
                "dolores_empresa": ["Optimización"],
                "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}],
                "match_score": 85.0
            }
        elif "Redactor" in prompt:
            return {"cv_markdown": "# SAM ORTEGA\n## PERFIL\nBreve\n## EXPERIENCIA\n• Logro\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Tec\n## IDIOMAS\n• ES", "pitch": "P1\n\nP2\n\nP3"}
        elif "Auditor" in prompt:
            return {"cv_markdown": "# SAM ORTEGA\n## PERFIL\nBreve\n## EXPERIENCIA\n• Logro\n## HABILIDADES TÉCNICAS\n• Python\n## EDUCACIÓN\n• Tec\n## IDIOMAS\n• ES", "pitch": "P1\n\nP2\n\nP3"}
        return {}

    monkeypatch.setattr("core.bridge.AntigravityBridge.run_prompt", dummy_run_prompt)

    client = TestClient(app)
    from core.database import create_postulacion
    oid = create_postulacion("Senior Analyst", "Test Inc", "Requerimientos: Python, SQL")

    # 1. Regenerar completo
    regen_res = client.post(f"/api/ofertas/{oid}/regenerate")
    assert regen_res.status_code == 200
    assert regen_res.json()["status"] == "REGENERATING"

    # 2. Recalcular CV con tags actuales
    recalc_res = client.post(f"/api/ofertas/{oid}/recalculate-cv")
    assert recalc_res.status_code == 200
    assert recalc_res.json()["status"] == "RECALCULATING_CV"

    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass

def test_html_mobile_scroll_and_minimalist_elements():
    from pathlib import Path
    html_path = Path("static/index.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    # 1. Debe incluir botón de regeneración accesible
    assert "regenerarPostulacionActual" in content or "regenerar" in content.lower()

    # 2. Debe incluir soporte de scroll táctil móvil (-webkit-overflow-scrolling o touch-action)
    assert "-webkit-overflow-scrolling" in content or "touch-action" in content

    # 3. Debe incluir badge o indicador de longitud de caracteres
    assert "charCount" in content or "cvCharCount" in content or "caracteres" in content.lower()

    # 4. Panel izquierdo con listado de ofertas y sin el incómodo select superior
    assert "listaOfertasCards" in content
    assert "ofertaSelect" not in content

    # 5. Panel central dividido horizontalmente en dos subpaneles
    assert "subpanel-vacante" in content
    assert "subpanel-tags" in content


def test_app_js_left_panel_and_tabs():
    from pathlib import Path
    js_path = Path("static/app.js")
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")

    # No debe contener el selector select eliminado
    assert "ofertaSelect" not in content

    # Debe gestionar la lista vertical de ofertas y su contador
    assert "listaOfertasCards" in content
    assert "contadorOfertasBadge" in content
    assert "renderizarListaOfertas" in content

    # Debe manejar los 4 tabs de navegación móvil fluida
    assert "'ofertas'" in content
    assert "'vacante'" in content



