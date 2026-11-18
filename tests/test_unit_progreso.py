"""Unitarios de `core/progreso.py`: validaciones de pasos y resultados, tiempos con datos
dañados y reportero que falla."""
from datetime import datetime

import pytest

from core import progreso

AHORA = datetime(2026, 11, 11, 12, 0, 0)


def test_paso_desconocido_es_error():
    """Avanzar a un paso que no existe lanza ValueError."""
    with pytest.raises(ValueError, match="desconocido"):
        progreso.avanzar(progreso.nuevo(AHORA), "inventado", AHORA)


def test_resultado_desconocido_es_error():
    """Terminar con un resultado no válido lanza ValueError."""
    with pytest.raises(ValueError, match="Resultado"):
        progreso.terminar(progreso.nuevo(AHORA), "QUIZAS", AHORA)


def test_paso_con_inicio_ilegible_cierra_sin_duracion():
    """Si el inicio del paso abierto está dañado, se cierra con duración None."""
    prog = {"pasos": [{"paso": "analizar", "inicio": "no es fecha", "fin": None}]}
    res = progreso.terminar(prog, "ERROR", AHORA)
    assert res["pasos"][0]["fin"] == "2026-11-11T12:00:00"
    assert res["pasos"][0]["duracion_s"] is None
    assert res["resultado"] == "ERROR"


def test_sumar_activo_con_valor_previo_danado_arranca_de_cero():
    """Un `activo_s` ilegible se toma como 0 y los segundos negativos no restan."""
    assert progreso.sumar_activo({"activo_s": "x"}, 2.5)["activo_s"] == 2.5
    assert progreso.sumar_activo({"activo_s": 1.0}, -3)["activo_s"] == 1.0


@pytest.mark.asyncio
async def test_reportero_que_falla_no_rompe_el_trabajo(caplog):
    """Si el reportero lanza, el error se registra y `reportar` vuelve normalmente."""
    async def roto(paso, sub):
        raise RuntimeError("canal cerrado")

    with progreso.usar_reportero(roto):
        await progreso.reportar("analizar", "sub")
    assert "No se pudo informar el paso 'analizar'" in caplog.text
