"""Tests de blindaje contra el límite OpenXML de 255 caracteres en metadatos DOCX."""
import io
import docx
from pathlib import Path
from fastapi.testclient import TestClient

from core.docx_generator import compile_markdown_to_docx
from server import app
from core.database import create_postulacion, update_postulacion

SAMPLE_CV = """# SAM ORTEGA
ANALISTA TÉCNICO FUNCIONAL
Ciudad Ejemplo, Buenos Aires | +54 9 11 0000-0000 | sam.ortega@example.com | https://linkedin.com/in/sam-ortega

## PERFIL
Analista Técnico Funcional con sólida experiencia en integración de sistemas y gestión de incidentes.

## EXPERIENCIA
### Analista Técnico Funcional | Freelance | 2025 - 2026
• **Integración:** Desarrollo y mantenimiento de microservicios con Python y Docker.

## HABILIDADES TÉCNICAS
• Python, FastAPI, Docker, PostgreSQL, Kubernetes.
"""

LONG_KEYWORDS = [
    "Arquitectura de Microservicios Distribuidos en la Nube",
    "Gestión Integral del Ciclo de Vida de Incidentes Críticos",
    "Automatización de Pruebas de Rendimiento y Carga Continua",
    "Administración Avanzada de Bases de Datos Relacionales PostgreSQL",
    "Desarrollo e Integración de APIs RESTful Empresariales",
    "Monitoreo Proactivo de Aplicaciones con PagerDuty y Datadog",
    "Diseño de Pipelines de Integración y Entrega Continua CI/CD con GitLab",
    "Orquestación de Contenedores con Kubernetes y Docker Compose",
    "Análisis Funcional y Especificación Formal de Requerimientos Técnicos",
    "Liderazgo de Ceremonias Ágiles bajo Marcos de Trabajo Scrum y Kanban",
    "Resolución Quirúrgica de Problemas Complejos en Ambientes Productivos",
    "Implementación de Estrategias de Seguridad OWASP y Cifrado de Datos",
    "Modelado de Procesos de Negocio con Notación Estándar BPMN 2.0",
    "Auditoría Estática de Código y Gestión de Calidad con Codacy",
    "Configuración y Administración de Repositorios de Código Git",
    "Gestión de Configuración y Despliegues Automatizados con Ansible",
    "Migración de Sistemas Monolíticos a Arquitecturas Distribuidas",
    "Optimización de Consultas SQL Complejas y Ajuste Fino de Índices",
    "Elaboración de Documentación Técnica y Manuales de Arquitectura",
    "Gestión de Tickets y Flujos Operativos de Soporte en Jira Service Management",
    "Coordinación Interdisciplinaria entre Equipos de IT y Negocio",
    "Supervisión de Ambientes de Testing, Homologación y Producción",
    "Monitoreo y Alertas en Tiempo Real con Prometheus y Grafana",
    "Desarrollo de Scripts de Automatización en Bash y Python",
    "Validación de Integridad de Datos y Procesamiento Batch ETL",
    "Administración de Servidores Linux Debian y Red Hat Enterprise",
    "Control de Versiones y Políticas de Ramificación GitFlow",
    "Evaluación de Riesgos Operativos y Planes de Continuidad de Negocio",
    "Comunicación Asertiva con Stakeholders Ejecutivos y Clientes",
    "Ingeniería de Software Orientada a Dominio y Clean Architecture"
]


def test_compile_docx_with_long_keywords_list_does_not_crash(tmp_path):
    """Compilar un CV pasando una lista de 30 keywords que totalicen > 600 caracteres.
    
    Verificar que no se lanza ValueError, que doc.core_properties.keywords tiene longitud <= 255,
    y que el archivo DOCX se genera y es legible por Document().
    """
    assert sum(len(k) for k in LONG_KEYWORDS) > 600

    output_file = "test_long_keywords.docx"
    docx_path = compile_markdown_to_docx(
        SAMPLE_CV,
        target_role="Líder Técnico Funcional y Arquitectura de Integraciones",
        output_filename=output_file,
        keywords=LONG_KEYWORDS
    )

    assert docx_path.exists()
    doc = docx.Document(str(docx_path))

    # Core properties no deben exceder el límite estricto de 255 caracteres
    assert doc.core_properties.keywords is not None
    assert len(doc.core_properties.keywords) <= 255
    assert len(doc.core_properties.keywords) <= 250
    assert len(doc.core_properties.title) <= 255
    assert len(doc.core_properties.subject) <= 255
    assert len(doc.core_properties.author) <= 255


def test_compile_docx_retains_full_keywords_in_footer_paragraph(tmp_path):
    """Verificar que el párrafo del apéndice ATS al pie contiene las keywords completas (100% íntegro)."""
    output_file = "test_footer_full_keywords.docx"
    docx_path = compile_markdown_to_docx(
        SAMPLE_CV,
        target_role="Analista Técnico",
        output_filename=output_file,
        keywords=LONG_KEYWORDS
    )

    doc = docx.Document(str(docx_path))
    ats_paragraph = None
    for p in reversed(doc.paragraphs):
        if any(kw in p.text for kw in LONG_KEYWORDS[:3]):
            ats_paragraph = p
            break

    assert ats_paragraph is not None, "El documento DOCX debe contener el párrafo de micro-indexación ATS al pie"

    # Verificar que el footer contiene todas las keywords sin truncar
    footer_text = ats_paragraph.text
    for kw in LONG_KEYWORDS:
        assert kw in footer_text, f"La keyword '{kw}' debe estar presente en el footer ATS íntegro"


def test_api_export_docx_endpoint_with_many_keywords():
    """Usar TestClient para llamar al endpoint /api/ofertas/{id}/export/docx en una postulación
    con decenas de keywords y verificar respuesta HTTP 200 con encabezado attachment; filename=...
    """
    postulacion_id = create_postulacion(
        titulo="Arquitecto de Soluciones y Líder Técnico de Integraciones",
        empresa="Enterprise Solutions Corp",
        contenido="Buscamos especialista con amplia experiencia en arquitectura e integraciones.",
        url="https://example.com/job/enterprise-arch"
    )

    update_postulacion(
        postulacion_id=postulacion_id,
        cv_markdown=SAMPLE_CV,
        requisitos=[{"nombre": kw, "estado": "MATCH_DIRECTO", "approved": True} for kw in LONG_KEYWORDS]
    )

    client = TestClient(app)
    response = client.get(f"/api/ofertas/{postulacion_id}/export/docx")

    assert response.status_code == 200
    content_disp = response.headers.get("content-disposition", "")
    assert "attachment; filename=" in content_disp
    assert content_disp.endswith('.docx"') or content_disp.endswith('.docx')

    # Verificar validez del binario devuelto
    doc = docx.Document(io.BytesIO(response.content))
    assert doc.core_properties.keywords is not None
    assert len(doc.core_properties.keywords) <= 255
    assert len(doc.core_properties.keywords) <= 250

    # Verificar que el apéndice ATS en el binario exportado contiene el 100% de las keywords
    ats_paragraph = None
    for p in reversed(doc.paragraphs):
        if any(kw in p.text for kw in LONG_KEYWORDS[:3]):
            ats_paragraph = p
            break

    assert ats_paragraph is not None
    for kw in LONG_KEYWORDS:
        assert kw in ats_paragraph.text
