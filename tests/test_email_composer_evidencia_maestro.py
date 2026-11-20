"""El mail dice qué términos concretos acepta su control: el pedido y la corrección los listan."""
from core import email_composer as ec

EVIDENCIA = ["Gestioné unos 70 tickets mensuales en Acme.", "Ordené los proyectos SoapUI del equipo."]


def test_los_terminos_que_acepta_el_control_se_listan_tal_cual():
    assert ec.terminos_concretos_visibles(EVIDENCIA) == ["70", "SoapUI"]


def test_la_correccion_dice_que_terminos_faltan():
    problemas = ec.problemas_de_tono("Hola, me interesa el puesto. Saludos.", evidencia=EVIDENCIA)
    assert any("(70, SoapUI)" in p for p in problemas)
    assert not any("término concreto" in p for p in ec.problemas_de_tono("Hola, usé SoapUI. Saludos.", evidencia=EVIDENCIA))


def test_el_pedido_lista_los_terminos(monkeypatch):
    post = {"id": 1, "texto": "Buscamos analista.", "autor": "Ana", "triage": {"puesto": "Analista"}}
    postulacion = {"cv_markdown": "## EXPERIENCIA\n• Gestioné unos 70 tickets mensuales en Acme.\n", "pitch": "",
                   "titulo": "Analista", "empresa": "Initech"}
    prompt = ec.build_composer_prompt(post, postulacion, {"rol": "Analista", "nombre": "Sam Ortega"}, voz={})
    assert "DATO CONCRETO OBLIGATORIO" in prompt and "70" in prompt.split("DATO CONCRETO OBLIGATORIO")[1][:200]
