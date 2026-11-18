"""El motor lee los datos del candidato solo de Mi perfil (creador, 13/11). Sin el dato, no inventa.

Candidato inventado: una domadora de leones con stack de lotes en Cobol.
"""
import json
import os
from unittest.mock import AsyncMock

import pytest
from docx import Document

from core import afinidad_reglas, descartes, docx_generator, paths, prioridades, titulo_cv
from core.docx_generator import compile_markdown_to_docx
from core.prompts_evaluacion import prompt_estratega
from core.strategist import StrategistAgent
from core.writer import WriterAgent, synthesize_multichannel_pitches

STACK = {
    "coincidencias": [{"nombre": "Cobol", "palabras": ["cobol"], "justificacion": "Lotes nocturnos en Cobol"}],
    "transferibles": [{"nombre": "Fortran", "palabras": ["fortran"], "equivalente": "Lotes en Cobol"}],
    "habilidades_base": {"gestion": ["Tablero de pista"], "tecnicas": ["Cobol", "JCL"]},
}
ROLES = {
    "roles_objetivo": ["domador de leones", "=equilibrista"],
    "areas": [
        {"area": "circo", "titulos": ["domador", "domadora"], "sinonimos": ["\\bpista\\b"]},
        {"area": "magia", "titulos": ["mago", "maga"], "perfil_actual": False},
    ],
}
PERSONALES = "Ana Prueba\nPuesto: Domadora de Leones\n"
EXPERIENCIA = ("## Circo Ejemplo\n### Domadora de Leones | 2015 - 2025\n• Rutinas con leones.\n"
               "### Maga de Escena | 2010 - 2015\n• Trucos de cartas.\n")


@pytest.fixture
def perfil(tmp_path, monkeypatch):
    """Carpeta del candidato vacía; cada test escribe lo que necesita."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    base = tmp_path / "candidato"
    base.mkdir()

    def escribir(nombre, dato):
        ruta = base / nombre
        ruta.write_text(dato if isinstance(dato, str) else json.dumps(dato), encoding="utf-8")
        st = ruta.stat()
        os.utime(ruta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return escribir


# ---------------------------------------------------------------- rol por defecto

def test_rol_por_defecto_sale_del_puesto(perfil, tmp_path, monkeypatch):
    perfil("personales.txt", PERSONALES)
    pitches = synthesize_multichannel_pitches(pitch="", puesto=None, empresa="Acme")
    assert "Domadora de Leones" in json.dumps(pitches, ensure_ascii=False)
    monkeypatch.setattr(docx_generator, "EXPORTS_DIR", tmp_path)
    destino = compile_markdown_to_docx("# Ana Prueba\n\nTexto.", candidate_name="Ana Prueba")
    assert Document(destino).core_properties.title == "CV - Ana Prueba - Domadora de Leones"


def test_sin_puesto_no_inventa_rol(perfil, tmp_path, monkeypatch):
    perfil("personales.txt", "Ana Prueba\n")
    monkeypatch.setattr(docx_generator, "EXPORTS_DIR", tmp_path)
    destino = compile_markdown_to_docx("# Ana Prueba\n\nTexto.", candidate_name="Ana Prueba")
    assert Document(destino).core_properties.title == "CV - Ana Prueba"
    fallback = WriterAgent(bridge=AsyncMock())._deterministic_fallback(
        "", "Acme", {"requisitos": []}, {"experiencia": EXPERIENCIA, "personales": "Ana Prueba\n"})
    assert "Analista" not in fallback["cv_markdown"]


# ---------------------------------------------------------------- stack

OFERTA = ("Analista de lotes\nRequisitos:\n• Experiencia en Cobol para lotes\n• Conocimientos de Fortran\n"
          "• Coordinar equipos de pista\n")


def _fallback():
    return StrategistAgent(bridge=AsyncMock(), expander=AsyncMock())._deterministic_fallback(OFERTA, "", [])


def test_fallback_estratega_usa_el_stack(perfil):
    perfil("stack.json", STACK)
    requisitos = {r["nombre"]: r for r in _fallback()["requisitos"]}
    cobol = next(r for n, r in requisitos.items() if "Cobol" in n)
    assert cobol["estado"] == "MATCH_DIRECTO" and cobol["justificacion"] == "Lotes nocturnos en Cobol"
    fortran = next(r for n, r in requisitos.items() if "Fortran" in n)
    assert fortran["estado"] == "TRANSFERIBLE" and fortran["equivalencia"] == "Lotes en Cobol"


def test_fallback_estratega_sin_stack_no_valida_nada(perfil):
    requisitos = _fallback()["requisitos"]
    assert requisitos and all(r["estado"] == "BRECHA_REAL" for r in requisitos)
    assert not any("20" in (r.get("justificacion") or "") for r in requisitos)


def test_habilidades_del_cv_de_respaldo(perfil):
    perfil("personales.txt", PERSONALES)
    perfil("stack.json", STACK)
    agente = WriterAgent(bridge=AsyncMock())
    perfil_dict = {"experiencia": EXPERIENCIA, "personales": PERSONALES}
    cv = agente._deterministic_fallback("Domadora", "Acme", {"requisitos": []}, perfil_dict)["cv_markdown"]
    assert "JCL" in cv and "Tablero de pista" in cv
    perfil("stack.json", {})
    cv = agente._deterministic_fallback("Domadora", "Acme", {"requisitos": []}, perfil_dict)["cv_markdown"]
    assert "JCL" not in cv
    assert ":** ." not in cv


# ---------------------------------------------------------------- roles y áreas

def test_grupo_a_sale_de_los_roles_objetivo(perfil):
    assert prioridades.prioridad_rol("Domador de Leones Sr") == "c"
    perfil("roles_objetivo.json", ROLES)
    assert prioridades.prioridad_rol("Domador de Leones Sr") == "a"
    assert prioridades.prioridad_rol("Equilibrista") == "a"
    assert prioridades.prioridad_rol("Equilibrista junior") == "c"
    assert prioridades.prioridad_rol("Analista funcional") == "c"
    assert prioridades.prioridad_rol("Desarrollador Java") == "b"


def test_prompt_del_estratega_lista_los_roles(perfil):
    assert 'no uses "A"' in prompt_estratega()
    perfil("roles_objetivo.json", ROLES)
    prompt = prompt_estratega()
    assert "domador de leones, equilibrista" in prompt and "[ROLES_OBJETIVO]" not in prompt


@pytest.mark.asyncio
async def test_area_fuera_del_perfil_es_caso_3(perfil):
    perfil("roles_objetivo.json", ROLES)
    areas = [{"area": "circo", "anios": "10"}, {"area": "magia", "anios": "5"}]
    d = await titulo_cv.decidir_titulo({"titulo": "Mago de Escena"}, experiencia=EXPERIENCIA, personales=PERSONALES,
                                       anios_por_area=areas, bridge=AsyncMock(), sin_ia=True)
    assert d.caso == 3 and d.clave == "magia"
    assert all("MAGA" not in r.upper() for r in d.roles_reales)
    d = await titulo_cv.decidir_titulo({"titulo": "Domadora de Leones"}, experiencia=EXPERIENCIA, personales=PERSONALES,
                                       anios_por_area=areas, bridge=AsyncMock(), sin_ia=True)
    assert d.caso == 1 and d.clave == "circo"


@pytest.mark.asyncio
async def test_sin_areas_no_hay_clave(perfil):
    d = await titulo_cv.decidir_titulo({"titulo": "Domadora"}, experiencia=EXPERIENCIA, personales=PERSONALES,
                                       anios_por_area=[{"area": "circo", "anios": "10"}], bridge=AsyncMock(), sin_ia=True)
    assert d.clave is None and d.caso == 3


def test_sinonimos_de_area_del_perfil(perfil):
    areas = [{"area": "circo", "anios": "10"}]
    assert afinidad_reglas._buscar_areas_coincidentes("Trabajo en la pista central", areas) == []
    perfil("roles_objetivo.json", ROLES)
    assert afinidad_reglas._buscar_areas_coincidentes("Trabajo en la pista central", areas)[0][1] == 10


# ---------------------------------------------------------------- zona

RESTRICCIONES = {"presencial_hibrido_permitido": "Pueblo Ejemplo", "remoto_desde": "Argentina"}


def _req(texto):
    return {"nombre": texto, "texto": texto, "tipo": "REQUISITO_EXCLUYENTE", "estado": "BRECHA_REAL"}


ANALISIS = {"requisitos": [_req("Presencial en Córdoba")]}


def test_zona_sale_de_las_localidades_permitidas():
    con_zona = {**RESTRICCIONES, "localidades_permitidas": ["pueblo ejemplo"]}
    assert [m["categoria"] for m in descartes.motivos_descarte(ANALISIS, con_zona)] == ["UBICACION"]
    local = {"requisitos": [_req("Presencial en Córdoba o en Pueblo Ejemplo")]}
    assert descartes.motivos_descarte(local, con_zona) == []
    # Sin localidades no se descarta por ubicación: el código no supone ninguna zona.
    assert descartes.motivos_descarte(ANALISIS, RESTRICCIONES) == []


@pytest.mark.parametrize("valor", ["amba", [""], [3], ["x"] * 201])
def test_localidades_invalidas(valor):
    assert not descartes._validas({"localidades_permitidas": valor})
