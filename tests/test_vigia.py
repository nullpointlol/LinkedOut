"""Vigía del portal: si el event loop no avanza en `UMBRAL_CUELGUE_S`, escribe las pilas de todos los hilos
(`faulthandler`) en su log, para saber qué lo trabó la próxima vez. No reinicia ni corta nada."""
import asyncio
import time

import pytest

from core import vigia

pytestmark = pytest.mark.timeout(120)


@pytest.fixture
def rapido(monkeypatch):
    # Holgado para un runner lento: una pausa de la máquina no es un cuelgue; los tests traban bastante más
    monkeypatch.setattr(vigia, "UMBRAL_CUELGUE_S", 1.5)
    monkeypatch.setattr(vigia, "SONDEO_S", 0.05)
    monkeypatch.setattr(vigia, "REPETIR_CADA_S", 30.0)


def _trabar_el_loop_con_sleep(segundos):
    time.sleep(segundos)  # SQLite, un join o un lock: algo síncrono adentro del loop


@pytest.mark.asyncio
async def test_un_loop_trabado_deja_las_pilas_de_todos_los_hilos_en_el_log(rapido, tmp_path):
    log = tmp_path / "vigia.log"
    v = vigia.Vigia(log)
    await v.iniciar()
    try:
        await asyncio.sleep(0.2)
        _trabar_el_loop_con_sleep(4.0)
        await asyncio.sleep(0.2)
    finally:
        await v.detener()
    texto = log.read_text(encoding="utf-8")
    assert "El event loop del portal no avanza" in texto
    assert "_trabar_el_loop_con_sleep" in texto  # la pila del hilo trabado, con la línea que lo traba
    assert texto.count("Thread 0x") >= 2  # todos los hilos, no solo uno


@pytest.mark.asyncio
async def test_sin_cuelgue_no_escribe_nada(rapido, tmp_path, monkeypatch):
    monkeypatch.setattr(vigia, "UMBRAL_CUELGUE_S", 5.0)  # sin cuelgue: un umbral holgado para una máquina lenta
    log = tmp_path / "vigia.log"
    v = vigia.Vigia(log)
    await v.iniciar()
    try:
        for _ in range(10):
            await asyncio.sleep(0.05)
    finally:
        await v.detener()
    assert not log.exists() or "no avanza" not in log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_un_cuelgue_largo_se_vuelca_una_vez_y_el_proceso_sigue(rapido, tmp_path):
    log = tmp_path / "vigia.log"
    v = vigia.Vigia(log)
    await v.iniciar()
    try:
        await asyncio.sleep(0.2)
        _trabar_el_loop_con_sleep(4.0)
        await asyncio.sleep(0.3)
        assert v.vivo  # no reinicia ni corta: sigue vigilando
    finally:
        await v.detener()
    assert log.read_text(encoding="utf-8").count("El event loop del portal no avanza") == 1
    assert not v.vivo


def test_las_reglas_son_constantes_con_nombre():
    assert vigia.UMBRAL_CUELGUE_S == 10
    assert vigia.REPETIR_CADA_S == 60


def test_el_portal_arranca_el_vigia(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")
    with TestClient(server.app):
        assert server.app.state.vigia.vivo
    assert not server.app.state.vigia.vivo


def test_si_no_puede_escribir_el_volcado_lo_avisa_y_sigue(tmp_path, caplog):
    """Un log inalcanzable (su carpeta es un archivo) no tira abajo al vigía: queda en el log del portal."""
    (tmp_path / "ocupado").write_text("no es carpeta", encoding="utf-8")
    v = vigia.Vigia(tmp_path / "ocupado" / "vigia.log")
    with caplog.at_level("ERROR", logger=vigia.logger.name):
        v._volcar(12.0)
    assert "No se pudo escribir el volcado" in caplog.text
