import pytest
from core.auditor import evaluate_ats_compliance


def test_auditor_recognizes_qualitative_functional_achievements_without_percentages():
    """CV con logros cualitativos empíricos y alcance de procesos (sin números ni porcentajes)
    debe recibir puntaje alto (>= 18.0 o 25.0) en métricas_empíricas sin castigo draconiano.
    """
    cv = """# CARLOS LÓPEZ - ANALISTA FUNCIONAL SR
carlos@example.com | +54 11 0000-0000 | Buenos Aires, Argentina

## PERFIL
Analista Funcional Senior especializado en relevamiento de procesos y diseño de soluciones.

## EXPERIENCIA

### EMPRESA ALPHA - SR FUNCTIONAL ANALYST
01/2022 - Presente
• **Relevamiento & Alcance:** Lideré el relevamiento de requerimientos y la definición de historias de usuario con criterios de aceptación detallados.
• **Calidad & Testing:** Confeccioné matrices de prueba y coordiné la ejecución de casos de prueba en ambientes de certificación.
• **Gobernanza de Datos:** Ejecuté consultas SQL para asegurar la consistencia de datos en sistemas transaccionales y modelos relacionales.

### EMPRESA BETA - ANALISTA FUNCIONAL
03/2018 - 12/2021
• **Procesos de Negocio:** Mapeé reglas de negocio complejas y coordiné pases a producción en procesos críticos.

## HABILIDADES TÉCNICAS
• **Análisis & Testing:** Historias de Usuario, Criterios de Aceptación, SQL, Jira, Testing.
• **Metodologías:** Scrum, Relevamiento Funcional, Modelos Relacionales.

## EDUCACIÓN
• Licenciatura en Sistemas - Universidad Nacional (2012 - 2017).

## IDIOMAS
• Español: Nativo.
• Inglés: B2 Profesional.
"""
    keywords = ["SQL", "Historias de Usuario", "Jira", "Scrum", "Testing"]
    res = evaluate_ats_compliance(cv, keywords, expected_companies=["EMPRESA ALPHA", "EMPRESA BETA"])

    metricas_score = res["dimensiones"]["metricas_empiricas"]
    assert metricas_score >= 18.0, f"Esperado >= 18.0, obtenido {metricas_score}"
    assert metricas_score == 25.0
    deducciones_metricas = [d for d in res["deducciones"] if "Métricas" in d or "Impacto" in d]
    assert len(deducciones_metricas) == 0


def test_auditor_still_recognizes_quantitative_metrics():
    """CV con métricas cuantitativas tradicionales (40%, 90 aplicaciones, 500 usuarios)
    debe mantener 25.0 puntos en métricas empíricas.
    """
    cv = """# MARÍA GÓMEZ - TECH LEAD
maria@example.com | +54 11 1111-2222 | Buenos Aires, Argentina

## PERFIL
Tech Lead orientada a escalabilidad y optimización de plataformas de alto tráfico.

## EXPERIENCIA

### TECH CORP - TECH LEAD
01/2022 - Presente
• **Optimización:** Optimicé la latencia de servicios en un 40% mediante refactorización de consultas.
• **Gestión de Proyectos:** Coordiné la entrega exitosa de más de 90 aplicaciones de software.
• **Escalabilidad:** Diseñé la arquitectura backend soportando más de 500 usuarios concurrentes.

## HABILIDADES TÉCNICAS
• **Backend:** Python, FastAPI, PostgreSQL, Docker.
• **Cloud:** AWS, Kubernetes, CI/CD, Git.

## EDUCACIÓN
• Ingeniería en Informática - Universidad Tecnológica (2014 - 2019).

## IDIOMAS
• Español: Nativo.
• Inglés: C1 Avanzado.
"""
    keywords = ["Python", "FastAPI", "AWS", "PostgreSQL", "Docker"]
    res = evaluate_ats_compliance(cv, keywords, expected_companies=["TECH CORP"])

    metricas_score = res["dimensiones"]["metricas_empiricas"]
    assert metricas_score == 25.0
    deducciones_metricas = [d for d in res["deducciones"] if "Métricas" in d or "Impacto" in d]
    assert len(deducciones_metricas) == 0


def test_auditor_penalizes_cv_with_no_metrics_and_no_scope_evidence():
    """CV sin métricas cuantitativas y sin evidencias de alcance o procesos debe recibir 0.0 pts."""
    cv = """# PEDRO PICAPIEDRA - ASISTENTE
pedro@example.com | +54 11 3333-4444 | Buenos Aires, Argentina

## PERFIL
Profesional de soporte general en tareas administrativas.

## EXPERIENCIA

### OFICINA GENERAL - ASISTENTE
01/2023 - Presente
• **Colaboración:** Colaboré en reuniones de equipo y redacté minutas.
• **Operaciones:** Hice tareas asignadas diarias y envié correos generales.

## HABILIDADES TÉCNICAS
• **Herramientas:** Correo electrónico, Procesador de texto, Planillas.
• **Generales:** Comunicación, Organización, Trabajo en equipo.

## EDUCACIÓN
• Bachiller - Colegio Secundario (2015 - 2020).

## IDIOMAS
• Español: Nativo.
"""
    keywords = ["Correo electrónico", "Organización", "Planillas"]
    res = evaluate_ats_compliance(cv, keywords, expected_companies=["OFICINA GENERAL"])

    metricas_score = res["dimensiones"]["metricas_empiricas"]
    assert metricas_score == 0.0
    deducciones_metricas = [d for d in res["deducciones"] if "Métricas" in d or "Impacto" in d]
    assert len(deducciones_metricas) == 1
    assert "-25.0 pts" in deducciones_metricas[0]
