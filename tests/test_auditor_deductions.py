import pytest
from core.auditor import evaluate_ats_compliance, scan_anti_ai_writing, AuditorAgent


def test_evaluate_ats_compliance_100_points():
    cv = """# JUAN PÉREZ - TECH LEAD & FULL STACK DEVELOPER
juan@example.com | +54 11 1234-5678 | Buenos Aires, Argentina

## PERFIL
Tech Lead con más de 12 años de trayectoria liderando squads de desarrollo y arquitectura de software.

## EXPERIENCIA

### FREELANCE - TECH LEAD & ARCHITECT
01/2025 - Presente
• **Liderazgo Técnico:** Dirigí la arquitectura cloud reduciendo costos operativos un 35% en 6 meses.
• **Microservicios:** Diseñé soluciones en Python y FastAPI atendiendo más de 500k transacciones diarias.

### VELORA SISTEMAS - SR SOFTWARE ENGINEER
03/2018 - 12/2024
• **Optimización:** Rediseñé pipelines CI/CD disminuyendo tiempos de despliegue en un 40% en 15 proyectos.
• **Escalabilidad:** Implementé microservicios escalables para banca regional.

### KORVA - SOFTWARE DEVELOPER
01/2011 - 02/2018
• **Desarrollo:** Desarrollé componentes críticos en backend bajo estándares de alta disponibilidad.

## HABILIDADES TÉCNICAS
• **Lenguajes y Frameworks:** Python, FastAPI, TypeScript, React, SQL.
• **Cloud & DevOps:** Docker, Kubernetes, AWS, CI/CD, Git.

## EDUCACIÓN
• Licenciatura en Sistemas de Información - Universidad de Buenos Aires (2006 - 2011).

## IDIOMAS
• Español: Nativo.
• Inglés: C1 Profesional / Fluido.
"""
    keywords = ["Python", "FastAPI", "Docker", "AWS", "SQL"]
    res = evaluate_ats_compliance(cv, keywords)

    assert res["ats_score"] == 100.0
    dim = res["dimensiones"]
    assert dim["estructura"] == 20.0
    assert dim["keywords"] == 35.0
    assert dim["metricas_empiricas"] == 25.0
    assert dim["escaneabilidad"] == 20.0
    assert res["deducciones"] == []


def test_evaluate_ats_compliance_deductions_when_below_100():
    # CV sin IDIOMAS, con solo 1 métrica, viñeta sin verbo de acción en 1ª persona y extensa, y faltan keywords
    cv = """# JUAN PÉREZ
juan@example.com

## PERFIL
Desarrollador con experiencia en sistemas distribuidos.

## EXPERIENCIA

### FREELANCE
01/2025 - Presente
• Trabajé en el mantenimiento general de la infraestructura con el equipo logrando 10 proyectos.
• Soporte operativo continuo y tareas administrativas generales sin verbos de acción en primera persona extendidas con texto descriptivo de relleno que excede holgadamente el límite de concisión de doscientos cincuenta caracteres para evaluar la deducción del auditor.

## HABILIDADES TÉCNICAS
• Tecnologías: Python, Linux.

## EDUCACIÓN
• Universidad de Buenos Aires.
"""
    keywords = ["Python", "Kubernetes", "AWS", "GraphQL", "Redis"]
    res = evaluate_ats_compliance(cv, keywords)

    assert res["ats_score"] < 100.0
    assert len(res["deducciones"]) > 0

    deducciones_text = " ".join(res["deducciones"])
    # Debe advertir estructura (falta IDIOMAS)
    assert "Estructura" in deducciones_text
    assert "IDIOMAS" in deducciones_text

    # Debe advertir falta de keywords (Kubernetes, AWS, GraphQL, Redis)
    assert "Keywords" in deducciones_text

    # Debe advertir métricas insuficientes (solo 1 detectada)
    assert "Métricas" in deducciones_text

    # Debe advertir escaneabilidad (viñetas sin verbo de acción en 1ª persona o extensas)
    assert "Escaneabilidad" in deducciones_text


def test_scan_anti_ai_writing_detects_ize_spelling():
    bad_spelling_text = (
        "En mi último proyecto, estandarizé las arquitecturas y optimizé los tiempos de respuesta. "
        "Además, automatizé los despliegues con un enfoque exhaustivo."
    )
    scan = scan_anti_ai_writing(bad_spelling_text)

    assert scan["clean"] is False
    assert scan["anti_ai_score"] < 100
    detected_words_lower = [w.lower() for w in scan["detected_words"]]
    assert any("estandarizé" in w for w in detected_words_lower)
    assert any("optimizé" in w for w in detected_words_lower)
    assert any("automatizé" in w for w in detected_words_lower)


def test_audit_deductions_integration():
    cv = """# JUAN PÉREZ - DEVELOPER
juan@example.com

## PERFIL
Estandarizé soluciones de software durante mi carrera.

## EXPERIENCIA
### FREELANCE
01/2025 - Presente
• Desarrollé servicios web en Python.

### VELORA SISTEMAS
03/2018 - 12/2024
• Mantuve sistemas legados.

### KORVA
01/2011 - 02/2018
• Programé módulos en backend.

## HABILIDADES TÉCNICAS
• Python, Git.

## EDUCACIÓN
• Ingeniería.

## IDIOMAS
• Español.
"""
    auditor = AuditorAgent()
    report = auditor.audit(cv, "Pitch de prueba", ["Python", "AWS", "Kubernetes"])

    assert "deducciones" in report
    assert isinstance(report["deducciones"], list)
    assert len(report["deducciones"]) > 0

    # Debe existir deducción por ortografía con -izé y por keywords faltantes
    all_ded = " ".join(report["deducciones"])
    assert "Keywords" in all_ded
    assert "Ortografía" in all_ded or "Redacción" in all_ded

    # Verificar que las deducciones están disponibles en diagnostico_documento
    assert "deducciones" in report["diagnostico_documento"]
    assert "deducciones" in report["evaluacion_profesional"]


def test_audit_deductions_ui_elements():
    with open("static/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    assert "id=\"auditDeductionsContainer\"" in html
    assert "id=\"auditDeductionsList\"" in html
    assert "id=\"auditDeductionsTotal\"" in html

    with open("static/app.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "auditDeductionsContainer" in js
    assert "auditDeductionsList" in js
    assert "audit.deducciones" in js


def test_scan_anti_ai_writing_detects_spanish_ai_cliches():
    from core.auditor import scan_anti_ai_writing

    cliche_text = """
    Analista Funcional especializado en especificación funcional en ecosistemas transaccionales de alta concurrencia.
    Cuento con sólida práctica articulando la comunicación fluida entre negocio y desarrollo sin fricción,
    garantizando la consistencia y continuidad operativa sin afectación operativa.
    """
    res = scan_anti_ai_writing(cliche_text)
    assert not res["clean"], "Debe marcar como no limpio un texto con clichés de IA en español"
    assert res["anti_ai_score"] < 100
    detected_lower = [w.lower() for w in res["detected_words"]]
    assert any("ecosistema" in w for w in detected_lower)
    assert any("articular" in w or "articulando" in w for w in detected_lower)
    assert any("fluida" in w or "comunicación fluida" in w for w in detected_lower)
    assert any("sin fricción" in w for w in detected_lower)
