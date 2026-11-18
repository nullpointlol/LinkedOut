"""Tests para Task 2: Motor de Priorización Dinámica por Dolores en el Redactor."""
import pytest
from core.prompts_evaluacion import WRITER_PROMPT
from core.writer import synthesize_multichannel_pitches, ensure_cv_length, WriterAgent


def test_writer_prompt_has_no_hardcoded_candidate_data():
    """Verifica que en WRITER_PROMPT no existen datos fijos de ningún candidato ni empresas específicas."""
    prompt = WRITER_PROMPT
    assert "SAM ORTEGA" not in prompt
    assert "VELORA SISTEMAS" not in prompt
    assert "KORVA" not in prompt
    assert "0000-0000" not in prompt
    assert "sam.ortega" not in prompt
    assert "Instituto de Ejemplo" not in prompt
    assert "EEM" not in prompt
    assert "Sam lideró" not in prompt
    # Debe contener la jerarquía universal de relevancia por dolores
    assert "JERARQUÍA UNIVERSAL DE RELEVANCIA POR DOLORES" in prompt
    assert "MÁXIMA PRIORIDAD" in prompt


def test_writer_agent_synthesize_multichannel_pitches_dynamic():
    """Verifica que synthesize_multichannel_pitches con un perfil dinámico genera asunto, firma e inmail con el candidato."""
    dynamic_profile = {
        "nombre": "Juan Pérez",
        "contacto": "Córdoba, Argentina | +54 9 351 123-4567 | juan.perez@example.com | LinkedIn: https://linkedin.com/in/persona-ficticia-02",
    }
    pitches = synthesize_multichannel_pitches(
        pitch="",
        puesto="Arquitecto Cloud",
        empresa="Global Corp",
        dolores=["Alta latencia en microservicios", "Falta de escalabilidad en Kubernetes"],
        profile=dynamic_profile,
    )
    email = pitches["email"]
    inmail = pitches["inmail"]
    web_ats = pitches["web_ats"]

    # Asunto formal de email
    assert "Asunto: Postulación Arquitecto Cloud - Juan Pérez" in email
    assert "Juan Pérez" in email
    assert "juan.perez@example.com" in email
    assert "Sam Ortega" not in email
    assert "0000-0000" not in email

    # InMail < 300 caracteres con el candidato
    assert len(inmail) <= 300
    assert "Juan Pérez" in inmail
    assert "Sam Ortega" not in inmail
    assert "12+ años" not in inmail  # No fixed years for Sam

    # Web/ATS no debe tener datos fijos de empresas de Sam
    assert "VELORA SISTEMAS" not in web_ats
    assert "KORVA" not in web_ats


def test_ensure_cv_length_agnostic_companies():
    """Pasa un CV con 3 empresas arbitrarias con 6 viñetas cada una, y verifica que tras ensure_cv_length,
    se reduce respetando el largo, mantiene el orden original y no inyecta empresas ni colegios de Sam Ortega.
    """
    long_cv = """# MARÍA GONZÁLEZ
LÍDER TÉCNICA DE DESARROLLO
Rosario, Argentina | maria.gonzalez@example.com

## PERFIL
Líder técnica con amplia trayectoria en arquitectura distribuida, microservicios y escalabilidad de sistemas críticos.

## EXPERIENCIA
### Líder Técnica | Empresa Alpha | 2023 - 2026
• **Arquitectura Cloud:** Diseñé soluciones en AWS con Terraform reduciendo tiempos de despliegue en un 40% a través de pipelines automatizados.
• **Microservicios Go:** Implementé arquitectura de microservicios en Go procesando más de 500k transacciones diarias sin interrupciones.
• **Optimización de Consultas:** Optimicé queries complejas en PostgreSQL reduciendo el tiempo de respuesta p95 en 300ms.
• **Liderazgo Técnico:** Coordiné un equipo de 8 ingenieros de backend implementando ceremonias ágiles y revisiones de código exhaustivas.
• **Monitoreo & Métricas:** Implementé observabilidad completa con Prometheus y Grafana alertando cuellos de botella en tiempo real.
• **Seguridad & Auth:** Diseñé el flujo de autenticación JWT y OAuth2 para todas las APIs públicas de la plataforma.

### Ingeniera de Software Senior | Empresa Beta | 2020 - 2023
• **Desarrollo Backend:** Desarrollé APIs RESTful de alta concurrencia con Python y FastAPI manteniendo disponibilidad del 99.9%.
• **Automatización CI/CD:** Diseñé pipelines automatizados en GitLab CI para testing unitario y análisis estático con Codacy.
• **Refactorización de Deuda:** Reduje la deuda técnica en un 35% refactorizando servicios monolíticos hacia componentes desacoplados.
• **Pruebas de Carga:** Ejecuté tests de carga con k6 identificando cuellos de botella antes de picos estacionales de tráfico.
• **Integración de Terceros:** Coordiné integraciones con pasarelas de pago y proveedores logísticos externos con tolerancia a fallos.
• **Mentoring:** Guié a 4 desarrolladores junior en buenas prácticas de Clean Code y patrones de diseño orientados a dominio.

### Desarrolladora Fullstack | Empresa Gamma | 2017 - 2020
• **Desarrollo Web:** Construí interfaces reactivas con Vue.js y TypeScript consumiendo endpoints REST optimizados.
• **Bases de Datos:** Diseñé esquemas relacionales en MySQL normalizados y migraciones versionadas sin tiempo de inactividad.
• **Integración Continua:** Participé en el mantenimiento de servidores de integración continua en Jenkins reduciendo fallos de compilación.
• **Soporte Productivo:** Atendí incidencias de segundo nivel en entornos productivos diagnosticando logs y trazas de error.
• **Documentación Técnica:** Redacté especificaciones OpenAPI y documentación de arquitectura para facilitar la adopción interna.
• **Automatización de Tareas:** Desarrollé scripts en Bash y Python para backups automáticos y sincronización periódica de datos.

## HABILIDADES TÉCNICAS
• **Lenguajes & Backend:** Python, Go, TypeScript, FastAPI, PostgreSQL, AWS.
• **DevOps & Metodologías:** Docker, Kubernetes, GitLab CI, Scrum, Terraform.

## EDUCACIÓN
• Licenciatura en Ciencias de la Computación - Universidad Nacional de Rosario (2012 - 2017)

## IDIOMAS
• Español: Nativo.
• Inglés: Avanzado (C1).
"""
    assert len(long_cv) > 2800

    reduced = ensure_cv_length(long_cv, max_chars=2600)

    assert len(reduced) <= 2600
    # Mantiene el orden original Alpha -> Beta -> Gamma
    idx_alpha = reduced.find("Empresa Alpha")
    idx_beta = reduced.find("Empresa Beta")
    idx_gamma = reduced.find("Empresa Gamma")
    assert idx_alpha != -1, "Empresa Alpha debe estar presente"
    assert idx_beta != -1, "Empresa Beta debe estar presente"
    assert idx_gamma != -1, "Empresa Gamma debe estar presente"
    assert idx_alpha < idx_beta < idx_gamma, "El orden cronológico Alpha -> Beta -> Gamma debe preservarse"

    # No debe inyectar datos fijos de Sam Ortega
    assert "VELORA SISTEMAS" not in reduced
    assert "KORVA" not in reduced
    assert "Instituto de Ejemplo" not in reduced
    assert "EEM" not in reduced
    assert "Licenciatura en Ciencias de la Computación" in reduced


def test_writer_prompt_contains_pain_prioritization_directives():
    """Verifica que el prompt estructurado contiene las instrucciones de priorizar según los dolores de la vacante."""
    prompt = WRITER_PROMPT
    assert "DOLORES CLAVE DE LA EMPRESA" in prompt
    assert "MÁXIMA PRIORIDAD" in prompt
    assert "PRIORIDAD MEDIA" in prompt
    assert "PRIORIDAD BAJA" in prompt
    assert "RETENCIÓN OBLIGATORIA DEL HISTORIAL LABORAL" in prompt
    assert "CERO GAPS O LAGUNAS" in prompt
