"""Perfil verificable del historial, sin datos derivados de una oferta."""
import os
import re
from datetime import date

import pytest

from core import profile_parser
from core.prompts_evaluacion import ATS_CRITERIA


PERSONALES = """# ANA PRUEBA
Buenos Aires | ana@example.com | https://linkedin.com/in/ana-prueba

## EDUCACIÓN
Licenciatura en Sistemas

## IDIOMAS
Inglés avanzado
"""


def _invariantes(exp_text, empresas):
    perfil = profile_parser.parse_candidate_profile(exp_text, PERSONALES)
    # invariante: todas las claves anteriores conservan sus valores exactos.
    anteriores = {
        "nombre": "ANA PRUEBA",
        "contacto": "Buenos Aires | ana@example.com | https://linkedin.com/in/ana-prueba",
        "empresas": empresas,
        "educacion": ["Licenciatura en Sistemas"],
        "idiomas": ["Inglés avanzado"],
    }
    assert {clave: perfil[clave] for clave in anteriores} == anteriores
    assert "# EXPERIENCIA" in exp_text
    for rango in ("2018-2025", "01/2018 - 01/2025"):
        assert re.fullmatch(ATS_CRITERIA["date_format_regex"], rango)
    return perfil


def test_rol_y_siete_anios_de_historial(tmp_path):
    exp = "# EXPERIENCIA\n## Empresa Uno\n### Analista de Datos | 01/2018 - 01/2025\n"
    perfil = _invariantes(exp, ["Empresa Uno"])
    (tmp_path / "experiencia.txt").write_text(exp, encoding="utf-8")
    (tmp_path / "personales.txt").write_text(PERSONALES, encoding="utf-8")
    # invariante: la carga conserva el mismo perfil y no modifica las fuentes.
    assert profile_parser.load_candidate_profile(tmp_path) == perfil
    assert (tmp_path / "experiencia.txt").read_text(encoding="utf-8") == exp
    assert (tmp_path / "personales.txt").read_text(encoding="utf-8") == PERSONALES
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert perfil["rol"] == "Analista de Datos"
    assert perfil["anios_experiencia"] == 7
    assert profile_parser.load_candidate_profile(tmp_path)["anios_experiencia"] == 7


@pytest.mark.parametrize("rangos, esperado", [
    (["01/2018 - 01/2023", "01/2020 - 01/2025"], 7),
    (["01/2020 - 01/2025", "01/2018 - 01/2023"], 7),
    (["01/2018 - 01/2020", "01/2022 - 01/2025"], 5),
    (["01/2018 - 01/2020", "01/2020 - 01/2025"], 7),
    (["01/2018 - 01/2025", "01/2018 - 01/2025"], 7),
    (["01/2020 - 08/2020", "01/2022 - 08/2022"], 1),
    (["01/2020 - 12/2020"], 0),
])
def test_solapamientos_y_huecos(rangos, esperado):
    exp = "# EXPERIENCIA\n## Empresa Uno\n" + "\n".join(
        f"### Analista | {rango}" for rango in rangos)
    _invariantes(exp, ["Empresa Uno"])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert profile_parser.calcular_anios_experiencia(exp, date(2026, 11, 7)) == esperado


@pytest.mark.parametrize("presente", ["Presente", "Actualidad", "Present", "Current", "CURRENT"])
def test_presente_con_fecha_inyectada(presente):
    exp = f"# EXPERIENCIA\n## Empresa Uno\n### Analista | 11/2018 - {presente}\n"
    _invariantes(exp, ["Empresa Uno"])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    calcular = profile_parser.calcular_anios_experiencia
    assert calcular(exp, date(2025, 11, 1)) == 7
    assert calcular(exp, date(2025, 11, 30)) == 7
    assert calcular(exp, date(2025, 10, 31)) == 6


@pytest.mark.parametrize("contenido", [
    "",
    "## Empresa Uno\nSin fechas ni título profesional.",
    "## Empresa Uno\n2018, 2025.",
    # Los extremos mixtos no respaldan una duración mensual completa.
    "## Empresa Uno\n### Analista | 01/2018 - 2025",
    "## Empresa Uno\n### Analista | 2018 - 01/2025",
    "## Empresa Uno\n### Analista | 01/2018 - 01/2020\n### Analista | 2022-2025",
    "## Empresa Uno\n### Analista | 01/2025 - 01/2018",
    "## Empresa Uno\n### Analista | 13/2018 - 01/2025",
    "## Empresa Uno\n### Analista | 01/2018 - 00/2025",
    "## Empresa Uno\n### Analista | 01/2018 - 01/2099",
    "## Empresa Uno\n### Analista | 01/2098 - Presente",
    "## EDUCACIÓN\n### Licenciada | 01/2010 - 01/2020",
    "EDUCACIÓN\nLicenciada | 01/2010 - 01/2020",
    "## EDUCATION\n### Degree | 01/2010 - 01/2020",
])
def test_faltantes_y_educacion_no_inventan(contenido):
    exp = "# EXPERIENCIA\n" + contenido
    empresas = (["Empresa Uno"] if "## Empresa Uno" in contenido else
                ["EDUCATION"] if "## EDUCATION" in contenido else [])
    perfil = _invariantes(exp, empresas)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert profile_parser.calcular_anios_experiencia(exp, date(2026, 11, 7)) is None
    assert perfil["anios_experiencia"] is None
    if "### Analista |" not in contenido:
        assert perfil["rol"] == ""


@pytest.mark.parametrize("historial,esperado", [
    ("2018-2025", 7),
    ("2011–2017\n2018–2026", 15),
    ("2018–2025\n2020–2026", 8),
    ("Más de 7 años de experiencia.\n2011–2017\n2018–2026", 15),  # 08/11: una frase suelta nunca da la cifra; mandan las fechas
])
def test_q2_historial_anual_y_declaracion_explicita(historial, esperado):
    exp = "# EXPERIENCIA\n## Empresa Uno\n### Analista | " + historial
    perfil = _invariantes(exp, ["Empresa Uno"])
    assert profile_parser.calcular_anios_experiencia(exp, date(2026, 11, 7)) == esperado
    assert perfil["anios_experiencia"] == esperado


@pytest.mark.parametrize("titulo", ["EDUCACIÓN", "EDUCACION", "EDUCATION", "FORMACIÓN ACADÉMICA"])
def test_educacion_no_suma_y_experiencia_posterior_se_recupera(titulo):
    exp = (f"# EXPERIENCIA\n## {titulo}\n"
           "### Licenciada | 01/2000 - 01/2010\n"
           "#### Materias\n01/2010 - 01/2015\n"
           "## Empresa Uno\n### Analista | 01/2018 – 01/2025\n")
    empresas = ([titulo] if titulo in ("EDUCATION", "FORMACIÓN ACADÉMICA") else []) + ["Empresa Uno"]
    perfil = _invariantes(exp, empresas)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert perfil["rol"] == "Analista"
    assert perfil["anios_experiencia"] == 7


def test_sin_rol_explicito_no_usa_empresa_y_sin_archivos_no_inventa(tmp_path):
    exp = "# EXPERIENCIA\n## Empresa Uno | 01/2018 - 01/2025\nAnalista sin cabecera.\n"
    perfil = _invariantes(exp, ["Empresa Uno"])
    # invariante: valores previos del perfil vacío, medidos en el parser original.
    vacio = profile_parser.load_candidate_profile(tmp_path)
    assert {k: vacio[k] for k in ("nombre", "contacto", "empresas", "educacion", "idiomas")} == {
        "nombre": "Candidato", "contacto": "", "empresas": [], "educacion": [], "idiomas": []}
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert perfil["rol"] == ""
    assert perfil["anios_experiencia"] == 7
    assert vacio["rol"] == ""
    assert vacio["anios_experiencia"] is None
