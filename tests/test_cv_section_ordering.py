"""Tests para el orden canónico estricto de secciones y preservación de Educación e Idiomas en ensure_cv_length."""
import pytest
from core.writer import ensure_cv_length


def test_ensure_cv_length_preserves_perfil_above_experiencia():
    """Probar con un markdown que tenga Perfil arriba de Experiencia y verificar que ensure_cv_length preserva Perfil antes de Experiencia."""
    cv_sample = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires | +54 9 11 0000-0000 | sam.ortega@example.com | LinkedIn: https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 12 años optimizando procesos e integración de sistemas complejos. Especializado en traducir reglas de negocio en soluciones técnicas medibles con Python y APIs.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• Redujo en un 40% el tiempo de procesamiento manual implementando flujos con Python.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• Centralizó la observabilidad de más de 90 aplicaciones construyendo un extractor automatizado en Python.

### Operador de Monitoreo & Soporte de Infraestructura | KORVA | 2011 - 2017
• Supervisó infraestructuras críticas corporativas 24x7 mediante ServiceNow y Nagios XI.

## HABILIDADES TÉCNICAS
• **Desarrollo & Automatización:** Python, SQL, APIs REST, GitLab CI/CD.
• **Gestión Funcional:** Scrum, Jira, BPMN, Historias de Usuario.

## EDUCACIÓN
• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)
• Bachiller en Ciencias Naturales - EET N°4 (2008)

## IDIOMAS
• Inglés: Avanzado escrito y lectura, intermedio oral.
• Español: Nativo.
"""
    # Test normal and compression
    out = ensure_cv_length(cv_sample, max_chars=1200)
    assert "## PERFIL" in out
    assert "## EXPERIENCIA" in out
    idx_perfil = out.index("## PERFIL")
    idx_exp = out.index("## EXPERIENCIA")
    assert idx_perfil < idx_exp, "## PERFIL debe estar estrictamente antes de ## EXPERIENCIA"
    assert "Analista Técnico Funcional con más de 12 años" in out

    # Test inverted input (Experiencia before Perfil) to verify canonical enforcement
    inverted_cv = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires | +54 9 11 0000-0000 | sam.ortega@example.com

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• Redujo en un 40% el tiempo manual.

### Analista Técnico Funcional y Soporte | VELORA SISTEMAS | 2018 - 2025
• Centralizó observabilidad de 90 aplicaciones.

### Operador de Monitoreo & Soporte de Infraestructura | KORVA | 2011 - 2017
• Monitoreo 24x7 con ServiceNow.

## PERFIL
Analista Técnico Funcional con 12 años de trayectoria transformando requerimientos en código.

## HABILIDADES TÉCNICAS
• Python, SQL, REST APIs.
• Scrum, Jira, BPMN.

## EDUCACIÓN
• Tecnicatura en Informática (2012 - 2015)

## IDIOMAS
• Español: Nativo.
• Inglés: Avanzado.
"""
    fixed = ensure_cv_length(inverted_cv, max_chars=5000)
    assert fixed.index("## PERFIL") < fixed.index("## EXPERIENCIA"), (
        "## PERFIL debe ser reubicado antes de ## EXPERIENCIA incluso si el input los tenía invertidos"
    )


def test_ensure_cv_length_never_drops_educacion_or_idiomas_bullets():
    """Probar con viñetas reales de Educación e Idiomas y verificar que bajo compresión/recorte NUNCA se eliminan."""
    long_cv = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | sam.ortega@example.com | LinkedIn: https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL
Analista Técnico Funcional con más de 12 años de trayectoria optimizando flujos de trabajo, pipelines de CI/CD e integración de sistemas complejos en telecomunicaciones, banca e industria corporativa. Especializado en transformar requerimientos de negocio en soluciones técnicas medibles con Python, APIs y observabilidad avanzada.

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• **Automatización de Procesos:** Redujo en un **40%** el tiempo de procesamiento manual implementando flujos completos con Python, modelos de IA y APIs REST complejas de integración empresarial en entornos productivos distribuidos.
• **Garantía de Calidad & QA:** Elevó la trazabilidad de operaciones críticas al **100%** coordinando casos de prueba funcionales y matrices de regresión exhaustivas con los equipos de desarrollo e infraestructura.
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

## EDUCACIÓN
• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)
• Bachiller en Ciencias Naturales - EET N°4 (2008)

## IDIOMAS
• Inglés: Avanzado escrito y lectura, intermedio oral (experiencia en soporte directo a clientes corporativos de EE.UU.).
• Español: Nativo.
"""
    # Forzamos compresión agresiva (ej. max_chars=2200) donde la máquina de estados
    # debe podar viñetas de experiencia pero NUNCA vaciar ni podar educación ni idiomas
    trimmed = ensure_cv_length(long_cv, max_chars=2200)

    assert len(trimmed) <= 2200
    assert "## EDUCACIÓN" in trimmed
    assert "• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)" in trimmed
    assert "• Bachiller en Ciencias Naturales - EET N°4 (2008)" in trimmed

    assert "## IDIOMAS" in trimmed
    assert "• Inglés: Avanzado" in trimmed
    assert "• Español: Nativo" in trimmed


def test_ensure_cv_length_canonical_section_order():
    """Verificar que el orden final de encabezados de nivel 2 es exactamente ['## PERFIL', '## EXPERIENCIA', '## HABILIDADES TÉCNICAS', '## EDUCACIÓN', '## IDIOMAS']."""
    scrambled_cv = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires | +54 9 11 0000-0000 | sam.ortega@example.com

## IDIOMAS
• Inglés: B2 profesional.
• Español: Nativo.

## EDUCACIÓN
• Tecnicatura en Informática - Instituto de Ejemplo (2012 - 2015)
• Bachiller - EET N°4 (2008)

## EXPERIENCIA
### Analista Funcional Técnico | Freelance | 2025 - 2026
• Automatización de flujos con Python reduciendo 40% de tiempo.
### Analista Técnico Funcional | VELORA SISTEMAS | 2018 - 2025
• Centralización de Codacy en 90 aplicaciones.
### Operador de Monitoreo | KORVA | 2011 - 2017
• Monitoreo 24x7 con Nagios y ServiceNow.

## PERFIL
Analista Técnico con 12 años integrando sistemas y optimizando procesos con Python y SQL.

## HABILIDADES TÉCNICAS
• Desarrollo: Python, SQL, REST APIs.
• Metodologías: Scrum, Jira, BPMN.
"""
    result = ensure_cv_length(scrambled_cv, max_chars=4000)

    h2_headers = [line.strip() for line in result.splitlines() if line.strip().startswith("## ")]
    expected_order = [
        "## PERFIL",
        "## EXPERIENCIA",
        "## HABILIDADES TÉCNICAS",
        "## EDUCACIÓN",
        "## IDIOMAS",
    ]
    assert h2_headers == expected_order, f"El orden de secciones obtenido fue {h2_headers}, se esperaba {expected_order}"

    # Header de Sam Ortega debe ser el inicio del documento antes de cualquier ##
    assert result.strip().startswith("# SAM ORTEGA")


def test_ensure_cv_length_populates_empty_educacion_and_idiomas():
    """Verificar que si un CV tiene los encabezados de Educación e Idiomas vacíos, ensure_cv_length los puebla con los datos canónicos."""
    empty_sections_cv = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires | sam.ortega@example.com

## PERFIL
Analista Técnico Funcional con trayectoria en Python y SQL.

## EXPERIENCIA
### FREELANCE
01/2025 - Presente
• Automatización de procesos.

### VELORA SISTEMAS
03/2018 - 12/2024
• Gestión de observabilidad.

### KORVA
01/2011 - 02/2018
• Monitoreo 24x7.

## HABILIDADES TÉCNICAS
• Python, SQL, Docker.

## EDUCACIÓN

## IDIOMAS
"""
    result = ensure_cv_length(empty_sections_cv)

    assert "## EDUCACIÓN" in result
    assert "Tecnicatura en Programación" in result
    assert "## IDIOMAS" in result
    assert "Español: Nativo" in result
    assert "Inglés:" in result
