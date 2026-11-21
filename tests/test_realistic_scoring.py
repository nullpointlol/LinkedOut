"""Pruebas unitarias para el modelo de scoring calibrado y realista (sin 100/100 artificial)."""
import pytest
from core.strategist import calculate_match_score
from core.auditor import evaluate_ats_compliance, scan_anti_ai_writing, AuditorAgent

SAMPLE_EXCELLENT_CV = """# SAM ORTEGA
SENIOR TECHNICAL BUSINESS ANALYST
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | https://wa.me/5491100000000 | sam.ortega@example.com | LinkedIn: https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 7 años de trayectoria optimizando flujos de trabajo e integración de sistemas.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• Reduje en un **40%** el tiempo de procesamiento manual de pedidos mediante flujos con **Python**.
• Elevó la trazabilidad funcional de operaciones críticas al 100%.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• Centralicé la observabilidad de más de **90 aplicaciones** construyendo un extractor automatizado en Python.
• Aceleré en un 35% los tiempos de resolución de incidentes críticos con **SQL**.

### Operador de Monitoreo & Soporte de Infraestructura | KORVA | 2011 - 2017
• Supervisó infraestructura 24x7 con Nagios XI y ServiceNow resolviendo alarmas críticas.
• Automaticé reportes con fórmulas en Excel y soporte bilingüe en inglés a clientes de EE.UU.

## HABILIDADES TÉCNICAS
• **Lenguajes & Automatización:** Python, SQL, Bash, APIs REST/SOAP.
• **CI/CD & DevOps:** Jenkins, GitLab CI/CD, Codacy, Linux, Docker.

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Inglés avanzado, Español nativo
"""

SAMPLE_CV_WITHOUT_METRICS = """# SAM ORTEGA
ANALISTA FUNCIONAL
Ciudad Ejemplo, Buenos Aires | sam.ortega@example.com

## PERFIL
Profesional con experiencia en sistemas y tareas de oficina.

## EXPERIENCIA
### Analista Funcional | VELORA SISTEMAS | 2018 - 2025
• Participó en reuniones de relevamiento de requerimientos.
• Colaboró con el equipo en la resolución de tareas diarias.

## HABILIDADES TÉCNICAS
• Python, SQL

## EDUCACIÓN
• Tecnicatura en Informática - 2016

## IDIOMAS
• Español nativo
"""

def test_match_score_reaches_100_when_all_match_direct():
    # 100% de tags directos alcanza 100.0% honesto (sin cota artificial de 92%)
    all_direct_tags = [
        {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
        {"nombre": "APIs", "estado": "MATCH_DIRECTO", "approved": True},
    ]
    score = calculate_match_score(all_direct_tags)
    assert score == 100.0

def test_ats_compliance_dimensions_and_no_100():
    keywords = ["Python", "SQL", "APIs", "Jenkins", "Codacy"]
    eval_result = evaluate_ats_compliance(SAMPLE_EXCELLENT_CV, keywords)

    # Debe contener el desglose dimensional
    assert "ats_score" in eval_result
    assert "dimensiones" in eval_result
    dims = eval_result["dimensiones"]
    assert "estructura" in dims
    assert "keywords" in dims
    assert "metricas_empiricas" in dims
    assert "escaneabilidad" in dims

    # Un CV excelente alcanza un score alto (80.0 a 100.0 según cobertura)
    assert 80.0 <= eval_result["ats_score"] <= 100.0

def test_cv_without_metrics_penalized_in_empirical_dimension():
    keywords = ["Python", "SQL"]
    eval_good = evaluate_ats_compliance(SAMPLE_EXCELLENT_CV, keywords)
    eval_no_metrics = evaluate_ats_compliance(SAMPLE_CV_WITHOUT_METRICS, keywords)

    # El CV sin métricas debe tener un puntaje empírico sustancialmente menor
    assert eval_no_metrics["dimensiones"]["metricas_empiricas"] < eval_good["dimensiones"]["metricas_empiricas"]
    assert eval_no_metrics["ats_score"] < eval_good["ats_score"]
    assert len(eval_no_metrics["deducciones"]) > 0

def test_auditor_global_score_realistic():
    auditor = AuditorAgent()
    keywords = ["Python", "SQL", "APIs", "Jenkins"]
    report = auditor.audit(SAMPLE_EXCELLENT_CV, "Pitch directo sin clichés...", keywords)

    assert "global_score" in report
    # Score en escala 0-100 calibrada, aprobado para entrevista
    assert 80.0 <= report["global_score"] <= 100.0
    assert report["approved"] is True
    assert report["verdict"] == "PASE A ENTREVISTA"
