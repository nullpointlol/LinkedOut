"""Roles y áreas de un perfil de TI genérico (análisis, soporte, operaciones) para los tests de título y de CV que
los necesitan. El candidato de los fixtures (`tests/fixtures/candidato`) es una desarrolladora web: estos tests
prueban la lógica del título con otra familia de puestos y la traen consigo.

    def test_x(roles_ti): ...   # candidato_datos.roles() devuelve ROLES_TI
"""
import pytest

from core import candidato_datos

ROLES_TI = {
    "roles_objetivo": [
        "analisis funcional",
        "analista funcional",
        "tecnico funcional",
        "business analyst",
        "analista de negocio",
        "incident manager",
        "gestion de incidentes",
        "analista de monitoreo",
        "monitoring analyst",
        "monitoring",
        "=operations",
        "it operations",
        "ti operations",
        "technical operations",
        "tecnica operations",
        "tecnicas operations",
        "noc operations",
        "network operations",
        "data center operations",
        "command center operations",
        "operaciones it",
        "operaciones ti",
        "operaciones de it",
        "operaciones de ti",
        "operaciones tecnologicas",
        "operador de monitoreo",
        "operador noc",
        "operador de data center",
        "noc operator",
        "data center operator",
        "monitoring operator",
        "technical analyst",
        "analista tecnico",
        "noc",
        "data center",
        "command center",
        "centro de computos",
        "asistente virtual",
        "chatbot",
        "voicebot",
        "soporte",
        "help desk",
        "service desk"
    ],
    "areas": [
        {
            "area": "funcional",
            "titulos": [
                "funcional",
                "business analyst",
                "analista de negocio",
                "analista de negocios",
                "analista de sistemas",
                "technical analyst",
                "business technical analyst",
                "consultor técnico",
                "consultor funcional"
            ],
            "sinonimos": [
                "\\bba\\b",
                "\\bbusiness analyst\\b",
                "\\banalisis funcional\\b",
                "\\bfuncional\\b"
            ],
            "nombres": [
                "funcional"
            ],
            "perfil_actual": True
        },
        {
            "area": "soporte",
            "titulos": [
                "soporte",
                "support",
                "help desk",
                "mesa de ayuda",
                "service desk",
                "incidentes",
                "técnico de sistemas",
                "microinformática"
            ],
            "sinonimos": [
                "\\bhelp desk\\b",
                "\\bservice desk\\b",
                "\\bmesa de ayuda\\b",
                "\\bsoporte\\b"
            ],
            "nombres": [
                "soporte",
                "incidentes"
            ],
            "perfil_actual": True
        },
        {
            "area": "monitoreo",
            "titulos": [
                "monitoreo",
                "monitoring",
                "noc",
                "command center",
                "itsm",
                "it operations",
                "operaciones it"
            ],
            "sinonimos": [
                "\\bitsm\\b",
                "\\bnoc\\b",
                "\\bobservabilidad\\b",
                "\\bmonitoreo\\b"
            ],
            "nombres": [
                "monitoreo",
                "itsm"
            ],
            "perfil_actual": True
        },
        {
            "area": "devops",
            "titulos": [
                "devops",
                "sre",
                "site reliability",
                "cloud",
                "infraestructura",
                "platform engineer"
            ],
            "sinonimos": [
                "\\bsre\\b",
                "\\bsite reliability\\b",
                "\\bcloud\\b",
                "\\binfraestructura\\b",
                "\\bdevops\\b"
            ],
            "nombres": [
                "devops"
            ],
            "perfil_actual": True
        },
        {
            "area": "producto",
            "titulos": [
                "product owner",
                "product manager",
                "producto"
            ],
            "sinonimos": [
                "\\bproduct owner\\b",
                "\\bproduct manager\\b",
                "\\bmanager\\b",
                "\\bpm\\b",
                "\\bproducto\\b"
            ],
            "nombres": [
                "producto"
            ],
            "perfil_actual": True
        },
        {
            "area": "desarrollo",
            "titulos": [
                "developer",
                "desarrollador",
                "desarrolladora",
                "programador",
                "programadora",
                "software engineer",
                "backend",
                "frontend",
                "full stack"
            ],
            "sinonimos": [
                "\\bdeveloper\\b",
                "\\bprogramacion\\b",
                "\\bsoftware\\b",
                "\\bdesarrollo\\b"
            ],
            "nombres": [
                "desarrollo"
            ],
            "perfil_actual": False
        }
    ]
}


@pytest.fixture
def roles_ti(monkeypatch):
    normalizado = candidato_datos._roles_normalizado(ROLES_TI)
    monkeypatch.setattr(candidato_datos, "roles", lambda: normalizado)
    return normalizado
