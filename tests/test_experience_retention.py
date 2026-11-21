"""Tests para garantizar la retención innegociable de experiencias laborales (Anti-Gaps)."""
import pytest
from core.writer import WriterAgent, ensure_cv_length
from core.auditor import AuditorAgent, evaluate_ats_compliance
from core.prompts_evaluacion import WRITER_PROMPT, AUDITOR_PROMPT


def test_ensure_cv_length_never_drops_any_company():
    """ensure_cv_length debe podar manteniendo siempre las 3 empresas (Freelance, VELORA SISTEMAS, KORVA)."""
    # CV largo con viñetas extensas que supera ampliamente 2500 caracteres (~2900 chars)
    long_cv = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | https://wa.me/5491100000000 | sam.ortega@example.com | LinkedIn: https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 12 años de trayectoria optimizando flujos de trabajo, pipelines de CI/CD e integración de sistemas complejos en telecomunicaciones, banca e industria corporativa. Especializado en transformar requerimientos de negocio en soluciones técnicas medibles con Python, APIs y observabilidad avanzada.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el tiempo de procesamiento manual implementando flujos completos con Python, modelos de IA y APIs REST complejas de integración empresarial.
• **Garantía de Calidad & QA:** Elevó la trazabilidad de operaciones críticas al **100%** coordinando casos de prueba funcionales y matrices de regresión exhaustivas con los equipos de desarrollo.
• **Relevamiento y Alcance:** Lideró workshops con stakeholders clave para definir historias de usuario detalladas y criterios de aceptación técnicos para plataformas digitales mobile-first.
• **Moderación Inteligente:** Diseñó reglas de negocio y validaciones funcionales para sistemas de geolocalización de alta disponibilidad.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad & Calidad:** Centralizó la observabilidad de más de **90 aplicaciones** construyendo un extractor automatizado en Python para Codacy y tableros interactivos en PowerBI reduciendo la deuda técnica.
• **Gestión de Incidentes Críticos:** Aceleró en un **35%** los tiempos de resolución de incidentes de severidad alta mediante diagnósticos directos en SQL y análisis de rendimiento con PagerDuty.
• **Integración de Middleware:** Automatizó la conciliación masiva de datos entre sistemas legados y ESB mediante scripts SQL y pruebas Insomnia avanzadas en ambientes productivos.
• **Soporte Productivo N3:** Coordinó guardias activas 24x7 resolviendo caídas operativas en aplicaciones transaccionales de telecomunicaciones de alta concurrencia.
• **Capacitaciones Técnicas:** Lideró capacitaciones operativas en bases de datos relacionales y APIs para unificar criterios de resolución en el equipo.

### Operador de Monitoreo & Soporte de Infraestructura | KORVA | 2011 - 2017
• **Monitoreo Corporativo 24x7:** Supervisó infraestructuras críticas corporativas mediante Nagios XI, OTRS y ServiceNow, gestionando alarmas de alta severidad y disponibilidad continua de servidores.
• **Escalamiento Técnico & Triaging:** Coordinó con equipos de DBA, Storage y Windows el diagnóstico y mitigación de eventos de degradación operativa según matrices de severidad.
• **Soporte Bilingüe & Automatización:** Diseñó macros en Excel para generación automática de tickets y reportes de continuidad, brindando atención telefónica técnica en inglés a clientes corporativos de EE.UU.
• **Gestión de Eventos Masivos:** Mantuve la operatividad durante caídas generales de enlace garantizando la trazabilidad de tickets y timelines operativas.

## HABILIDADES TÉCNICAS
• **Metodologías & Análisis:** Relevamiento funcional, Scrum, Kanban, UML, BPMN, Historias de Usuario, Jira.
• **Lenguajes & Automatización:** Python, SQL, Bash, APIs REST/SOAP, Shell Scripting.
• **CI/CD, Calidad & DevOps:** Jenkins, GitLab CI/CD, Codacy, Linux, Docker.
• **Datos & Observabilidad:** PowerBI, PagerDuty, pgAdmin, DBeaver, ServiceNow, Nagios.

## EDUCACIÓN
• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)
• Bachiller en Ciencias Naturales - EET N°4 (2008)

## IDIOMAS
• Inglés: Avanzado escrito y lectura, intermedio oral (experiencia en soporte directo a clientes de EE.UU.).
• Español: Nativo.
"""
    assert len(long_cv) > 2500

    trimmed = ensure_cv_length(long_cv, max_chars=2500)

    assert len(trimmed) <= 2500
    trimmed_upper = trimmed.upper()
    assert "FREELANCE" in trimmed_upper, "Freelance fue podado indebidamente"
    assert "VELORA SISTEMAS" in trimmed_upper, "VELORA SISTEMAS fue podado indebidamente"
    assert "KORVA" in trimmed_upper, "KORVA fue podado indebidamente (gap crítico generado)"

    # Asegurar que cada empresa conserve viñetas
    for company in ["FREELANCE", "VELORA SISTEMAS", "KORVA"]:
        lines = trimmed.splitlines()
        found = False
        bullets = 0
        for l in lines:
            if company in l.upper():
                found = True
            elif found and l.strip().startswith(("•", "*", "-")):
                bullets += 1
            elif found and l.strip().startswith(("### ", "## ")):
                break
        assert bullets >= 1, f"La empresa {company} quedó sin viñetas tras la reducción de longitud"


def test_auditor_rejects_cv_when_company_is_missing():
    """El auditor debe rechazar (approved=False) cualquier CV al que le falte alguna empresa canónica."""
    cv_sin_korva = """# SAM ORTEGA
ANALISTA TÉCNICO
Ciudad Ejemplo | +54911 | email@test.test | linkedin.com

## PERFIL
Analista Técnico Funcional con 7 años de experiencia en procesos y CI/CD con métricas de 40% y 90 aplicaciones.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el procesamiento manual con **Python**.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• **Observabilidad & Calidad:** Centralizó más de **90 aplicaciones** con **Python** y **Codacy**.

## HABILIDADES TÉCNICAS
• **Herramientas:** Python, SQL, Jenkins, Codacy.

## EDUCACIÓN
• Tecnicatura en Informática (2012 - 2015)

## IDIOMAS
• Español: Nativo.
"""
    auditor = AuditorAgent()
    keywords = ["Python", "SQL", "Jenkins", "Codacy"]
    report = auditor.audit(cv_sin_korva, "Pitch de prueba...", keywords)

    assert report["approved"] is False, "El auditor aprobó un CV al que le falta KORVA"
    assert report["verdict"] == "REQUIERE REMEDIACIÓN"
    assert report["ats"]["has_all_experiences"] is False
    assert "KORVA" in report["ats"]["missing_experiences"]


def test_writer_prompt_mandates_full_career_history():
    """Valida que WRITER_PROMPT instruya la inclusión obligatoria de todas las empresas del historial."""
    prompt_upper = WRITER_PROMPT.upper()
    assert "RETENCIÓN OBLIGATORIA DEL HISTORIAL LABORAL" in prompt_upper
    assert "TODAS LAS EMPRESAS" in prompt_upper
    assert "LAGUNAS" in prompt_upper or "GAPS" in prompt_upper or "OMITIR" in prompt_upper
