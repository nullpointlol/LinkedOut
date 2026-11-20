"""las habilidades van por transferencia de conocimientos. Las de la oferta del mismo mundo
que la base entran; rubro, formación y años nunca son habilidades (casos 871 y 1746)."""
import inspect

import pytest

from core import auditor, prompts_evaluacion
from core.cv_guardas import es_requisito_no_habilidad, quitar_requisitos_de_habilidades
from core.email_composer import build_composer_prompt


@pytest.mark.parametrize("nombre", ["Mercado de capitales o fintech", "Formación en Sistemas o afines",
                                    "Experiencia previa mayor a 3 años", "Modalidad híbrida en Vicente López"])
def test_no_son_habilidades(nombre):
    assert es_requisito_no_habilidad(nombre)


@pytest.mark.parametrize("nombre", ["Docker", "Cloud AWS o GCP", "Ciberseguridad", "Inteligencia Artificial generativa",
                                    "Metodologías ágiles y gestión incidencias", "SQL y Postman para APIs",
                                    "Experiencia en análisis funcional y QA"])
def test_transferibles_y_propias_siguen(nombre):
    assert not es_requisito_no_habilidad(nombre)


def test_keywords_del_cv_sin_rubro_formacion_ni_anios():
    import server
    claves = server._keywords_del_cv(["SQL y Postman para APIs", "Mercado de capitales o fintech",
                                      "Formación en Sistemas o afines", "Docker", "Experiencia previa mayor a 3 años"], {})
    assert claves == ["SQL y Postman para APIs", "Docker"]


def test_guarda_saca_rubro_y_formacion_y_deja_transferibles():
    cv = ("## HABILIDADES TÉCNICAS\n"
          "• **Análisis funcional y testing:** historias de usuario, pruebas de API, mercado de capitales o fintech, documentación.\n"
          "• **Metodologías:** Scrum, Kanban, Git, formación en sistemas o afines.\n"
          "• **DevOps:** Docker, Cloud AWS o GCP, Jenkins, Linux.\n")
    limpio, cambios = quitar_requisitos_de_habilidades(cv)
    assert "fintech" not in limpio and "afines" not in limpio
    assert "Docker, Cloud AWS o GCP, Jenkins, Linux" in limpio and "Scrum, Kanban, Git" in limpio
    assert len(cambios) == 2


def test_remediacion_aplica_la_guarda_y_pide_transferencia():
    fuente = inspect.getsource(auditor)
    assert "quitar_requisitos_de_habilidades(patched_cv)" in fuente
    assert "DEBEN ser integradas" not in fuente and "transferencia de conocimientos" in fuente
    assert "transferencia de conocimientos" in prompts_evaluacion._COMPLETO_HABILIDADES


def test_mail_no_pega_tareas_cortas_a_los_anios():
    prompt = build_composer_prompt({"texto": "Buscamos analista", "emails": ["a@b.test"]}, {"pitch": ""},
                                   {"rol": "Analista", "anios_por_area": [{"area": "soporte", "anios": "7"}]}, voz={})
    assert "no va pegada a los +N años" in prompt
