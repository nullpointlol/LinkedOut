"""Perfil en 3ª persona, años en singular y voz de las viñetas (tickets del dev 1112)."""
import pytest

from core.auditor import scan_anti_ai_writing
from core.cv_guardas import _RE_ANIOS, bloque_prompt, corregir_anios


@pytest.mark.parametrize("texto", ["con 1 year in DevOps", "con 1 año en DevOps", "for 1 year", "un año de soporte"])
def test_un_anio_en_singular_cuenta_como_anios(texto):
    assert _RE_ANIOS.search(texto)


def test_el_pedido_escribe_un_anio_en_singular():
    texto = bloque_prompt([{"area": "DevOps", "anios": "1"}, {"area": "soporte", "anios": "más de 6"}])
    assert "DevOps: 1 año;" in texto and "soporte: más de 6 años" in texto and "1 años" not in texto


def test_corregir_anios_respeta_el_singular():
    linea, cambios = corregir_anios("DevOps engineer with 3 years in DevOps.", [{"area": "DevOps", "anios": "1"}])
    assert linea == "DevOps engineer with 1 year in DevOps." and cambios


def _cv(perfil, vineta):
    return f"# ANA PRUEBA\n\n## PERFIL\n{perfil}\n\n## EXPERIENCIA\n### Acme\nAnalista | 2018 - 2025\n• {vineta}\n"


def test_otro_sujeto_en_el_medio_de_una_vineta_no_es_error_de_voz():
    # V27: la herramienta es la que «estandarizó», la viñeta abre en 1ª persona
    cv = _cv("Analista de monitoreo con 6 años de experiencia en NOC.",
             "Armé en Excel una herramienta que generaba el ticket y estandarizó el circuito de alertas.")
    assert not any("inconsistencia de voz" in d for d in scan_anti_ai_writing(cv)["detected_words"])


def test_una_vineta_que_abre_en_tercera_persona_si_es_error_de_voz():
    cv = _cv("Analista de monitoreo con 6 años de experiencia en NOC.", "Estandarizó el circuito de alertas.")
    assert any("inconsistencia de voz" in d for d in scan_anti_ai_writing(cv)["detected_words"])


@pytest.mark.parametrize("perfil", [
    "Profesional con experiencia en atención al público y armado de pedidos.",
    "Analista funcional con 7 años de experiencia en sistemas de facturación.",
    "Con más de 6 años de experiencia en monitoreo de infraestructura.",
])
def test_la_apertura_pedida_no_es_formulaica(perfil):
    assert not any("apertura formulaica" in d for d in scan_anti_ai_writing(_cv(perfil, "Armé reportes."))["detected_words"])


def test_cuento_con_sigue_siendo_formulaica():
    cv = _cv("Cuento con experiencia en atención al público.", "Armé reportes.")
    assert any("apertura formulaica" in d for d in scan_anti_ai_writing(cv)["detected_words"])
