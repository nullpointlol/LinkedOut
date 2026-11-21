import pytest
from core.auditor import scan_anti_ai_writing


def test_scan_anti_ai_writing_detects_peacock_and_puffery_phrases():
    puffery_text = """
    Cuento con una sólida trayectoria en el sector. Poseo un marcado interés en retail.
    Demostré un vasto conocimiento en arquitecturas complejas y una destacada labor.
    """
    res = scan_anti_ai_writing(puffery_text)
    assert not res["clean"]
    assert res["anti_ai_score"] < 100
    detected_lower = [w.lower() for w in res["detected_words"]]
    assert any("sólida trayectoria" in w for w in detected_lower)
    assert any("marcado interés" in w for w in detected_lower)
    assert any("vasto conocimiento" in w for w in detected_lower)
    assert any("destacada labor" in w for w in detected_lower)


def test_scan_anti_ai_writing_detects_robotic_syntactic_symmetry():
    symmetric_cv = """
    ## EXPERIENCIA
    ### Empresa Uno
    • **Gestión:** Traduje requerimientos operativos mediante VS Code, estableciendo criterios de aceptación.
    • **Pruebas:** Coordiné pruebas funcionales integrales mediante Insomnia, asegurando entregas sin defectos.
    • **Sistemas:** Gestioné incidencias críticas mediante DBeaver, auditando transacciones masivas.
    • **Integración:** Diseñé pruebas de integración mediante Postman, validando contratos de datos.
    • **Automatización:** Implementé scripts automáticos mediante Python, reduciendo tiempos operativos.
    """
    res = scan_anti_ai_writing(symmetric_cv)
    assert not res["clean"]
    assert any("simetría" in w.lower() or "gerundio" in w.lower() for w in res["detected_words"])


def test_scan_anti_ai_writing_preserves_genuine_technical_domain_terms():
    domain_text = """
    ## EXPERIENCIA
    ### Empresa Real
    • **Datos:** Mantuve la consistencia de datos en bases PostgreSQL bajo alta concurrencia.
    • **Backend:** Diseñé sistemas transaccionales con RabbitMQ y Python para conciliación bancaria.
    • **Calidad:** Participé en la auditoría de procesos de despliegue continuo con equipos DevOps.
    """
    res = scan_anti_ai_writing(domain_text)
    detected_lower = [w.lower() for w in res["detected_words"]]
    assert not any("consistencia de datos" in w for w in detected_lower)
    assert not any("sistemas transaccionales" in w for w in detected_lower)
    assert not any("auditoría de procesos" in w for w in detected_lower)


def test_scan_anti_ai_writing_allows_diverse_bullets_without_symmetry():
    diverse_cv = """
    ## EXPERIENCIA
    ### Empresa Tech
    • **Liderazgo:** Dirigí un squad de 6 ingenieros para migrar servicios a Kubernetes en 3 meses.
    • **Rendimiento:** Rediseñé consultas SQL lentas con índices compuestos, reduciendo tiempos un 40%.
    • **Infraestructura:** Desplegué arquitecturas Terraform en AWS sin interrupción de servicio.
    • **Monitoreo:** Centralicé métricas en Datadog y configuré alertas PagerDuty para guardias rotativas.
    • **Documentación:** Escribí manuales técnicos y especificaciones de arquitectura en Confluence.
    """
    res = scan_anti_ai_writing(diverse_cv)
    detected_lower = [w.lower() for w in res["detected_words"]]
    assert not any("simetría" in w for w in detected_lower)
