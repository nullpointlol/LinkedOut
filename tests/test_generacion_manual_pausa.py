"""Pedir CV + mail a mano funciona siempre: la pausa solo frena la generación automática.

SQLite temporal (conftest), IA y envío simulados: nunca se lee LinkedIn ni sale un mail.
"""
import asyncio
import sqlite3

import pytest

from core import database
from core import radar_store as store
from tests.test_radar_workers import (
    FakeCompose, FakePipeline, FakeSend, crear_post, esperar_workers, finalizar, make_radar, wait_until,
)
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI

ANALISIS_ELEGIBLE = {"match_score": 78}


def _listo_sin_aprobar(pid):
    post = store.get_post(pid)
    assert post["estado"] == "LISTO", post
    assert post["postulacion_id"] == 42
    assert post["email_subject"] and post["email_body"].startswith("Hola")
    # Generar no es enviar: queda esperando la aprobación del usuario.
    assert post["aprobado_at"] is None and post["sent_at"] is None
    return post


@pytest.mark.asyncio
async def test_pedido_manual_se_genera_con_pausa_y_ninguna_automatica_se_toma(tmp_path):
    manual = crear_post(1, "ANALIZADA", analisis=ANALISIS_ELEGIBLE)
    candidata = crear_post(2, "ANALIZADA", analisis=ANALISIS_ELEGIBLE)
    tomada_antes = crear_post(3, gen_prioridad=1)  # automática ya en GENERANDO antes de pausar
    pipeline, compose, send = FakePipeline(), FakeCompose(), FakeSend()
    loop = make_radar(tmp_path, pipeline=pipeline, compose=compose, send=send)
    store.update_radar_state(generacion_pausada=True)
    try:
        await loop.generate_cv(manual)
        assert store.get_post(manual)["gen_manual"] == 1
        await wait_until(lambda: store.get_post(manual)["estado"] == "LISTO")
        _listo_sin_aprobar(manual)
        await asyncio.sleep(0.05)
        assert pipeline.calls == [manual]
        assert compose.calls == [(manual, 42)]
        assert store.get_post(candidata)["estado"] == "ANALIZADA"
        assert store.get_post(tomada_antes)["estado"] == "GENERANDO"
        assert loop._siguiente_generacion() is None
        assert send.calls == []
        assert store.get_radar_state()["generacion_pausada"] is True
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_al_reanudar_la_automatica_vuelve_igual_que_antes(tmp_path):
    manual = crear_post(1, "ANALIZADA", analisis=ANALISIS_ELEGIBLE)
    tomada_antes = crear_post(2, gen_prioridad=1)
    candidata = crear_post(3, "ANALIZADA", analisis=ANALISIS_ELEGIBLE)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    store.update_radar_state(generacion_pausada=True)
    try:
        await loop.generate_cv(manual)
        await wait_until(lambda: store.get_post(manual)["estado"] == "LISTO")
        store.update_radar_state(generacion_pausada=False)
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [manual, tomada_antes, candidata]
        for pid in (tomada_antes, candidata):
            post = _listo_sin_aprobar(pid)
            assert post["gen_manual"] == 0  # automáticas: así queda registrado en la base
        assert store.get_post(manual)["gen_manual"] == 1
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_pedido_manual_cortado_por_reinicio_se_retoma_con_pausa(tmp_path):
    manual = crear_post(1, gen_prioridad=2, gen_manual=1)  # quedó GENERANDO al cortarse el servidor
    automatica = crear_post(2, gen_prioridad=1)
    store.update_radar_state(generacion_pausada=True)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline)
    try:
        await loop.resume_if_needed()
        await wait_until(lambda: store.get_post(manual)["estado"] == "LISTO")
        _listo_sin_aprobar(manual)
        await asyncio.sleep(0.05)
        assert pipeline.calls == [manual]
        assert store.get_post(automatica)["estado"] == "GENERANDO"
        assert not loop.is_running
    finally:
        await finalizar(loop)


def _quitar_columna_gen_manual():
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("ALTER TABLE linkedin_posts DROP COLUMN gen_manual")


@pytest.mark.asyncio
async def test_generando_manual_de_antes_del_arreglo_se_toma_con_pausa(tmp_path):
    """Caso del post 6941: pedido a mano con la pausa puesta antes de este arreglo. Quedó GENERANDO
    con gen_prioridad 2 y sin la columna nueva; al migrar se marca manual y se genera solo."""
    previo = crear_post(1, gen_prioridad=2)
    automatica = crear_post(2, gen_prioridad=1)
    analizada = crear_post(3, "ANALIZADA", gen_prioridad=2, analisis=ANALISIS_ELEGIBLE)
    _quitar_columna_gen_manual()
    assert store.get_post(previo)["gen_manual"] == 1
    assert store.get_post(automatica)["gen_manual"] == 0
    assert store.get_post(analizada)["gen_manual"] == 0
    store.update_radar_state(generacion_pausada=True)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline)
    try:
        await loop.resume_if_needed()
        await wait_until(lambda: store.get_post(previo)["estado"] == "LISTO")
        _listo_sin_aprobar(previo)
        await asyncio.sleep(0.05)
        assert pipeline.calls == [previo]
        assert store.get_post(automatica)["estado"] == "GENERANDO"
        assert store.get_post(analizada)["estado"] == "ANALIZADA"
    finally:
        await finalizar(loop)


def test_migracion_marca_una_sola_vez_al_crear_la_columna():
    """Con la columna ya creada, un GENERANDO con gen_prioridad 2 (p. ej. de reprocesar) no pasa a manual."""
    pid = crear_post(1, gen_prioridad=2)
    for _ in range(2):
        assert store.get_post(pid)["gen_manual"] == 0


@pytest.mark.asyncio
async def test_lo_no_pedido_por_oferta_no_queda_marcado_manual(tmp_path):
    """Reprocesar, reintentar y la toma automática ponen gen_manual en 0: un 1 viejo no se cuela en la pausa."""
    listo = crear_post(1, "LISTO", postulacion_id=7, email_subject="a", email_body="b", gen_manual=1)
    error = crear_post(2, "ERROR", error="Falló la generación: x", gen_manual=1)
    loop = make_radar(tmp_path)
    store.update_radar_state(generacion_pausada=True)
    try:
        hechos = await loop.reprocesar()
        assert listo in hechos["mensaje"] and error in hechos["reintento"]
        for pid in (listo, error):
            post = store.get_post(pid)
            assert post["estado"] == "GENERANDO" and post["gen_manual"] == 0
        assert loop._siguiente_generacion() is None
    finally:
        await finalizar(loop)


def test_tomas_automaticas_registran_gen_manual_cero():
    a = crear_post(1, "ANALIZADA", analisis=ANALISIS_ELEGIBLE, gen_manual=1)
    b = crear_post(2, "ANALIZADA", analisis=ANALISIS_ELEGIBLE, gen_manual=1)
    assert store.tomar_automatico(a)
    assert store.tomar_candidata(b, "prioritaria")
    assert store.get_post(a)["gen_manual"] == 0
    assert store.get_post(b)["gen_manual"] == 0


# ------------------------------------------------- postulación vinculada sin CV listo (post 6941)

class PipelineQueReusa:
    """Como `_radar_pipeline`: rehace el CV sobre la misma fila no READY y la deja READY con CV."""

    def __init__(self):
        self.calls = []

    async def __call__(self, post):
        self.calls.append(post["id"])
        pid = store.get_post(post["id"]).get("postulacion_id") or self.fila
        database.update_postulacion(pid, status="READY", cv_markdown="# CV rehecho")
        store.update_post(post["id"], postulacion_id=pid)
        return pid


class ComposeConGuarda:
    """Como `_radar_compose`: solo redacta sobre una postulación READY con CV; si no, desvincula y falla."""

    def __init__(self):
        self.calls = []

    async def __call__(self, post, postulacion_id):
        self.calls.append((post["id"], postulacion_id))
        fila = database.get_postulacion(postulacion_id)
        if not fila or fila.get("status") != "READY" or not (fila.get("cv_markdown") or "").strip():
            store.update_post(post["id"], postulacion_id=None)
            raise RuntimeError(f"La postulación {postulacion_id} no tiene un CV listo; se regenerará")
        return {"to": "rrhh@empresa.test", "subject": "Postulación – Analista Funcional", "body": "Hola, adjunto mi CV."}


def _postulacion(status, cv=""):
    pid = database.create_postulacion(titulo="Analista Funcional", empresa="Acme", contenido="x", url="",
                                      origen="linkedin")
    database.update_postulacion(pid, status=status, cv_markdown=cv)
    return pid


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cv", [("ERROR", "# CV a medio redactar"), ("PENDING", ""), ("READY", "")])
async def test_pedido_manual_con_postulacion_sin_cv_listo_rehace_el_cv_y_despues_el_mail(tmp_path, status, cv):
    fila = _postulacion(status, cv)
    pid = crear_post(1, "ERROR", postulacion_id=fila, error="Falló la generación: corte", intentos=1,
                     analisis=ANALISIS_ELEGIBLE)
    pipeline, compose = PipelineQueReusa(), ComposeConGuarda()
    pipeline.fila = fila
    loop = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    store.update_radar_state(generacion_pausada=True)
    try:
        await loop.generate_cv(pid)
        await wait_until(lambda: store.get_post(pid)["estado"] in ("LISTO", "ERROR"))
        post = store.get_post(pid)
        assert post["estado"] == "LISTO", post["error"]
        assert pipeline.calls == [pid], "nunca salta directo al mensaje"
        assert compose.calls == [(pid, fila)]
        assert post["postulacion_id"] == fila and post["aprobado_at"] is None
        assert database.get_postulacion(fila)["cv_markdown"] == "# CV rehecho"
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_pedido_manual_con_cv_listo_reusa_el_cv_y_solo_redacta_el_mail(tmp_path):
    fila = _postulacion("READY", "# CV bueno")
    pid = crear_post(1, "ERROR", postulacion_id=fila, error="Falló la generación: mail", intentos=1)
    pipeline, compose = PipelineQueReusa(), ComposeConGuarda()
    loop = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    try:
        await loop.generate_cv(pid)
        await wait_until(lambda: store.get_post(pid)["estado"] in ("LISTO", "ERROR"))
        assert store.get_post(pid)["estado"] == "LISTO"
        assert pipeline.calls == [] and compose.calls == [(pid, fila)]
        assert database.get_postulacion(fila)["cv_markdown"] == "# CV bueno"
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_generacion_manual_cortada_a_mitad_del_cv_se_retoma_entera_con_pausa(tmp_path):
    """Reinicio del portal con el CV a medio redactar: la postulación quedó PROCESSING y el post GENERANDO."""
    fila = _postulacion("PROCESSING", "# CV a medio")
    pid = crear_post(1, postulacion_id=fila, gen_prioridad=2, gen_manual=1, analisis=ANALISIS_ELEGIBLE)
    pipeline, compose = PipelineQueReusa(), ComposeConGuarda()
    pipeline.fila = fila
    store.update_radar_state(generacion_pausada=True)
    loop = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    try:
        await loop.resume_if_needed()
        await wait_until(lambda: store.get_post(pid)["estado"] in ("LISTO", "ERROR"))
        post = store.get_post(pid)
        assert post["estado"] == "LISTO", post["error"]
        assert pipeline.calls == [pid] and compose.calls == [(pid, fila)]
        assert database.get_postulacion(fila)["status"] == "READY"
    finally:
        await finalizar(loop)


# ------------------------------------------------- oferta cargada a mano (fuente manual, post 7657)

TEXTO_MANUAL = ("Buscamos Analista Funcional para proyecto bancario. Requisitos: SQL, relevamiento, historias de "
                "usuario y Jira. Modalidad híbrida en CABA. Enviar CV a rrhh@acme.test")


@pytest.mark.asyncio
async def test_oferta_manual_analizada_se_genera_con_pausa_y_las_del_radar_no(tmp_path):
    manual = store.insert_post_manual(TEXTO_MANUAL, titulo="Analista Funcional", empresa="Acme")
    store.update_post(manual, estado="ANALIZADA", triage={"oferta": 1, "puesto": "Analista Funcional",
                                                          "canal": "email"}, analisis=ANALISIS_ELEGIBLE)
    radar = crear_post(1, "ANALIZADA", analisis=ANALISIS_ELEGIBLE)
    tomada = crear_post(2, gen_prioridad=1)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline)
    store.update_radar_state(generacion_pausada=True)
    try:
        await loop.wake_processing()  # lo que corre tras analizarla
        await wait_until(lambda: store.get_post(manual)["estado"] in ("LISTO", "ERROR"))
        _listo_sin_aprobar(manual)
        assert store.get_post(manual)["gen_manual"] == 1
        await asyncio.sleep(0.05)
        assert pipeline.calls == [manual]
        assert store.get_post(radar)["estado"] == "ANALIZADA"
        assert store.get_post(tomada)["estado"] == "GENERANDO"
    finally:
        await finalizar(loop)


@pytest.mark.asyncio
async def test_oferta_manual_ya_trabada_en_generando_se_toma_con_pausa(tmp_path):
    """Como el 7657: quedó GENERANDO con gen_manual 0 antes del arreglo; se retoma al arrancar."""
    manual = store.insert_post_manual(TEXTO_MANUAL, titulo="Analista Funcional", empresa="Acme")
    store.update_post(manual, estado="GENERANDO", gen_prioridad=2, gen_manual=0,
                      triage={"oferta": 1, "puesto": "Analista Funcional", "canal": "email"})
    automatica = crear_post(1, gen_prioridad=2, gen_manual=0)
    pipeline = FakePipeline()
    store.update_radar_state(generacion_pausada=True)
    loop = make_radar(tmp_path, pipeline=pipeline)
    try:
        await loop.resume_if_needed()
        await wait_until(lambda: store.get_post(manual)["estado"] in ("LISTO", "ERROR"))
        _listo_sin_aprobar(manual)
        await asyncio.sleep(0.05)
        assert pipeline.calls == [manual]
        assert store.get_post(automatica)["estado"] == "GENERANDO"
    finally:
        await finalizar(loop)
