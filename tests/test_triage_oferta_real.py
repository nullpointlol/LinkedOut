"""Solo es oferta un post con puesto concreto y forma de postularse (creador, 08/11).

Ejemplo sintético del caso real: un post de cultura («así es trabajar en…») que se clasificó como oferta.
"""
import pytest

from core import triage

CULTURA = {"id": 1, "texto": "En Empresa Prueba celebramos 10 años. Así es trabajar con nosotros: equipos ágiles, "
                             "aprendizaje continuo y mucho café. ¡Estamos creciendo! #cultura", "emails": []}
OFERTA = {"id": 2, "texto": "Buscamos Analista Funcional para banca. Enviá tu CV a rrhh@example.com",
          "emails": ["rrhh@example.com"]}


class Bridge:
    def __init__(self, filas):
        self.filas, self.prompts = filas, []

    async def run_prompt(self, prompt, **kw):
        self.prompts.append(prompt)
        return {"r": self.filas}


@pytest.mark.asyncio
async def test_oferta_sin_puesto_no_es_oferta():
    b = Bridge([[1, 1, "AR", "Argentina", "otro", "", ""], [2, 1, "AR", "Argentina", "email", "Analista Funcional", ""]])
    r = await triage.triage_batch([CULTURA, OFERTA], bridge=b)
    assert r[1]["es_busqueda_laboral"] is False
    assert r[2]["es_busqueda_laboral"] is True and r[2]["email_destino"] == "rrhh@example.com"


def test_prompt_no_exige_forma_de_postularse():
    """Creador, 08/11: exigir puesto Y forma de postularse borró ofertas reales («¡Buscamos Analista Funcional!»)."""
    p = triage.build_triage_prompt([CULTURA, OFERTA])
    assert "LAS DOS condiciones" not in p
    assert "aunque no diga cómo postularse" in p and "Ante la duda, 1" in p
