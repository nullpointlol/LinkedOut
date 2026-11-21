"""Tests de los workers del RadarLoop (Task 8): generación secuencial y envío aprobado.

Sin pipeline, composer ni SMTP reales: fakes que registran llamadas, `sleep` falso que
registra duraciones y cede el control, reloj fijo y `rng=random.Random(0)`. Cada test
detiene el radar y drena los workers al final para no dejar tareas pendientes.
"""
import asyncio
import random
from datetime import date, datetime, timedelta

import pytest

from core.linkedin_harvester import HarvestResult
from core import radar_store
from core.mailer import MailerAmbiguousError, MailerAuthError, MailerError, MailerTemporaryError
from core.radar import RadarLoop
from core.radar_store import (
    get_post,
    get_radar_state,
    insert_post_if_new,
    list_posts,
    update_post,
    update_radar_state,
)
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI


FIXED_NOW = datetime(2026, 11, 1, 10, 0, 0)
APROBADO = "2026-11-01 09:00:00"
WORKER_TIMEOUT = 60  # Guarda ante bloqueos; la sincronización no depende de esta duración.


# ----------------------------------------------------------------- dobles de prueba

TEXTO_SUFICIENTE = (
    "Buscamos un Analista Funcional con experiencia comprobable en relevamiento de procesos "
    "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
    "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
    "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
    "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
)


def raw(n: int, emails=("rrhh@empresa.test",), texto: str = None) -> dict:
    return {
        "urn": f"urn:li:activity:{n}",
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
        "autor": f"Autor {n}",
        "cargo_autor": "HR",
        "texto": f"{TEXTO_SUFICIENTE} (aviso {n})" if texto is None else texto,
        "fecha_rel": "3 h",
        "emails": list(emails),
    }


TRIAGE_OK = {
    "es_busqueda_laboral": True,
    "puesto": "Analista Funcional",
    "empresa": "Empresa",
    "motivo": "es una oferta",
    "alcance": "ARGENTINA",
}


def crear_post(n: int, estado: str = "GENERANDO", **campos) -> int:
    pid = insert_post_if_new(raw(n), "q0")
    update_post(pid, estado=estado, triage=dict(TRIAGE_OK), **campos)
    return pid


def crear_aprobado(n: int, **campos) -> int:
    datos = dict(
        postulacion_id=100 + n,
        email_to=f"rrhh{n}@empresa.test",
        email_subject="Postulación – Analista Funcional",
        email_body="Hola, adjunto mi CV.",
        aprobado_at=APROBADO,
    )
    datos.update(campos)
    return crear_post(n, "LISTO", **datos)


class FakeHarvester:
    def __init__(self, responses=None, log=None, close_gate=None):
        self.responses = dict(responses or {})
        self.log = log if log is not None else []
        self.close_gate = close_gate
        self.show_calls = 0
        self.close_calls = 0

    async def harvest(self, query, max_posts=30, max_scrolls=8, conocidos=None, paciencia=1):
        r = self.responses.pop(query, HarvestResult())
        return r

    async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
        return self.responses.pop("feed", HarvestResult())

    async def check_logged_in(self):
        return True

    async def show(self):
        self.show_calls += 1
        self.log.append(("show", id(self)))

    async def dump_debug(self, target_dir):
        pass

    async def close(self):
        self.log.append(("close_start", id(self)))
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.close_calls += 1
        self.log.append(("close_end", id(self)))


class FakeSleep:
    """Registra duraciones (también en un log compartido) y cede; las >= `block_at` no terminan."""

    def __init__(self, block_at=None, log=None):
        self.calls = []
        self.block_at = block_at
        self.log = log

    async def __call__(self, seconds):
        self.calls.append(seconds)
        if self.log is not None:
            self.log.append(("sleep", seconds))
        if self.block_at is not None and seconds >= self.block_at:
            await asyncio.Event().wait()
        await asyncio.sleep(0)


class FakePipeline:
    def __init__(self, fail_ids=(), gate=None):
        self.calls = []
        self.started = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.fail_ids = set(fail_ids)
        self.gate = gate

    async def __call__(self, post):
        self.calls.append(post["id"])
        self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            for _ in range(3):
                await asyncio.sleep(0)
            if post["id"] in self.fail_ids:
                raise RuntimeError("el pipeline quedó en estado FAILED")
            return 42
        finally:
            self.active -= 1


class FakeCompose:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    async def __call__(self, post, postulacion_id):
        self.calls.append((post["id"], postulacion_id))
        await asyncio.sleep(0)
        if self.result is not None:
            return dict(self.result)
        return {
            "to": (post.get("emails") or [""])[0],
            "subject": "Postulación – Analista Funcional",
            "body": f"Hola, soy candidato para el post {post['id']}.",
            "is_fallback": False,
        }


class FakeSend:
    """`behaviour`: lista de excepciones/None consumida en orden (None = éxito); luego éxito."""

    def __init__(self, behaviour=None, log=None, gate=None):
        self.behaviour = list(behaviour or [])
        self.calls = []
        self.estados_al_enviar = []
        self.log = log
        self.gate = gate

    async def __call__(self, post):
        self.calls.append(post["id"])
        self.estados_al_enviar.append(get_post(post["id"])["estado"])
        if self.log is not None:
            self.log.append(("send", post["id"]))
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(0)
        if self.behaviour:
            b = self.behaviour.pop(0)
            if b is not None:
                raise b
        return f"<msg-{post['id']}@test>"


class Recorder:
    def __init__(self):
        self.events = []

    async def __call__(self, name, payload):
        self.events.append((name, payload))


async def triage_ok(posts):
    return {p["id"]: dict(TRIAGE_OK) for p in posts}


async def analyze_ok(post):
    return {"puesto": "Analista Funcional", "empresa": "Empresa", "match_score": 80.0, "requisitos": []}


def make_radar(tmp_path, *, pipeline=None, compose=None, send=None, sleep=None,
               harvester=None, harvester_factory=None, triage=None, broadcast=None,
               queries=("q1",), umbral=100):
    harvester = harvester or FakeHarvester()
    created = []

    def factory():
        h = harvester_factory() if harvester_factory else harvester
        created.append(h)
        return h

    radar = RadarLoop(
        harvester_factory=factory,
        triage_fn=triage or triage_ok,
        analyze_fn=analyze_ok,
        pipeline_fn=pipeline or FakePipeline(),
        compose_fn=compose or FakeCompose(),
        send_fn=send or FakeSend(),
        broadcast=broadcast or Recorder(),
        sleep=sleep or FakeSleep(),
        rng=random.Random(0),
        queries=list(queries),
        debug_dir=tmp_path / "radar_debug",
        now=lambda: FIXED_NOW,
    )
    radar._test_created = created
    # Umbral 100 por defecto: el análisis falso (72.5) no dispara el CV automático salvo que el test lo pida
    update_radar_state(umbral_match=umbral)
    return radar


async def wait_until(pred, timeout=WORKER_TIMEOUT):
    """Espera la condición, con un plazo de guarda independiente de las vueltas del loop."""
    async def poll():
        while not pred():
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(poll(), timeout)
    except TimeoutError as exc:
        raise AssertionError("la condición no se cumplió") from exc


async def esperar_tarea(task, timeout=WORKER_TIMEOUT):
    if task is not None and not task.done():
        await asyncio.wait_for(asyncio.shield(task), timeout)


async def finalizar(radar):
    """Detiene el radar y drena los workers que sigan vivos (sin tareas pendientes)."""
    await asyncio.wait_for(radar.stop_for_shutdown(), WORKER_TIMEOUT)
    await esperar_tarea(radar._send_task)
    await esperar_tarea(radar._gen_task)


async def esperar_workers(radar):
    """Con el radar detenido, los workers terminan solos cuando no queda trabajo."""
    await esperar_tarea(radar._gen_task)
    await esperar_tarea(radar._send_task)


# ------------------------------------------------------------------ generación

@pytest.mark.asyncio
async def test_generation_worker_moves_generando_to_listo(tmp_path):
    pid = crear_post(1)
    pipeline, compose, rec = FakePipeline(), FakeCompose(), Recorder()
    radar = make_radar(tmp_path, pipeline=pipeline, compose=compose, broadcast=rec)
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        post = get_post(pid)
        assert post["estado"] == "LISTO"
        assert post["postulacion_id"] == 42
        assert post["email_to"] == "rrhh@empresa.test"
        assert post["email_subject"] == "Postulación – Analista Funcional"
        assert post["email_body"].startswith("Hola")
        assert post["aprobado_at"] is None and post["error"] is None
        assert get_radar_state()["contadores_dia"]["listos"] == 1
        assert pipeline.calls == [pid] and compose.calls == [(pid, 42)]
        eventos = [p for n, p in rec.events if n == "radar_post" and p["id"] == pid]
        assert eventos[-1]["estado"] == "LISTO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_stage_is_tracked_while_generating(tmp_path):
    """El estado de la cola muestra la etapa del post en curso, y la borra al terminar."""
    pid = crear_post(1)
    vistas = []

    class Pipeline(FakePipeline):
        async def __call__(self, post):
            vistas.append(dict(radar.etapas_generacion))
            return await super().__call__(post)

    radar = make_radar(tmp_path, pipeline=Pipeline())
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        assert vistas == [{pid: "Preparando la postulación"}]
        assert radar.etapas_generacion == {}
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_is_sequential(tmp_path):
    ids = [crear_post(n) for n in (3, 1, 2)]
    pipeline = FakePipeline()
    radar = make_radar(tmp_path, pipeline=pipeline)
    try:
        await radar.wake_generation()
        await radar.wake_generation()  # despertar dos veces no crea un segundo worker
        await esperar_workers(radar)
        assert pipeline.max_active == 1
        assert pipeline.calls == sorted(ids)
        assert all(get_post(i)["estado"] == "LISTO" for i in ids)
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_failure_sets_error_and_continues(tmp_path):
    p1, p2 = crear_post(1), crear_post(2)
    radar = make_radar(tmp_path, pipeline=FakePipeline(fail_ids={p1}))
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        assert get_post(p1)["estado"] == "ERROR"
        assert "FAILED" in get_post(p1)["error"]
        assert get_post(p2)["estado"] == "LISTO"
        assert get_radar_state()["contadores_dia"]["listos"] == 1
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_incomplete_compose_result_is_error(tmp_path):
    pid = crear_post(1)
    compose = FakeCompose(result={"to": "a@b.test", "subject": "", "body": "x", "is_fallback": True})
    radar = make_radar(tmp_path, compose=compose)
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        post = get_post(pid)
        assert post["estado"] == "ERROR" and post["error"]
        # el CV ya se generó: el reintento solo recompone el email
        assert post["postulacion_id"] == 42
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
@pytest.mark.parametrize("canal,destino", [("whatsapp", "https://wa.me/5491155550000"), ("dm", ""), ("otro", "")])
async def test_generation_non_email_channel_accepts_message_without_subject(tmp_path, canal, destino):
    pid = crear_post(1, canal=canal, destino_canal=destino)
    compose = FakeCompose(result={"to": destino, "subject": "", "body": "Hola, me interesa.", "is_fallback": False})
    radar = make_radar(tmp_path, compose=compose)
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        post = get_post(pid)
        assert post["estado"] == "LISTO" and post["error"] is None
        assert post["email_subject"] == "" and post["email_body"] == "Hola, me interesa."
        assert post["email_to"] == destino and post["aprobado_at"] is None
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_non_email_channel_still_requires_body(tmp_path):
    pid = crear_post(1, canal="whatsapp", destino_canal="https://wa.me/549")
    compose = FakeCompose(result={"to": "", "subject": "", "body": "  ", "is_fallback": True})
    radar = make_radar(tmp_path, compose=compose)
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        assert get_post(pid)["estado"] == "ERROR"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_regenerating_existing_postulacion_skips_pipeline(tmp_path):
    # Regenerar sin pedir CV nuevo: la API deja `postulacion_id` → solo compose_fn
    pid = crear_post(1, postulacion_id=7, email_subject="viejo", email_body="viejo",
                     aprobado_at=APROBADO)
    pipeline, compose = FakePipeline(), FakeCompose()
    radar = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    try:
        await radar.wake_generation()
        await esperar_workers(radar)
        post = get_post(pid)
        assert pipeline.calls == []
        assert compose.calls == [(pid, 7)]
        assert post["estado"] == "LISTO" and post["postulacion_id"] == 7
        assert post["email_subject"] == "Postulación – Analista Funcional"
        assert post["aprobado_at"] is None  # contenido nuevo: el usuario vuelve a aprobar
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generation_does_not_overwrite_post_changed_meanwhile(tmp_path):
    gate = asyncio.Event()
    pid = crear_post(1)
    radar = make_radar(tmp_path, pipeline=FakePipeline(gate=gate))
    try:
        await radar.wake_generation()
        await wait_until(lambda: radar._pipeline_fn.calls)
        update_post(pid, estado="DESCARTADO")  # el usuario lo descartó mientras se generaba
        gate.set()
        await esperar_workers(radar)
        assert get_post(pid)["estado"] == "DESCARTADO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_generando_requeued_on_resume(tmp_path):
    pid = crear_post(1)
    pipeline = FakePipeline()
    radar = make_radar(tmp_path, pipeline=pipeline)
    try:
        await radar.resume_if_needed()
        await esperar_workers(radar)
        assert pipeline.calls == [pid]
        assert get_post(pid)["estado"] == "LISTO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_offer_waits_analyzed_until_the_user_asks_for_the_cv(tmp_path):
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1)])})
    pipeline = FakePipeline()
    analizada = asyncio.Event()
    continuar = asyncio.Event()

    class BroadcastAnalizada(Recorder):
        async def __call__(self, name, payload):
            await super().__call__(name, payload)
            if name == "radar_post" and payload["estado"] == "ANALIZADA":
                analizada.set()
                await continuar.wait()

    radar = make_radar(tmp_path, harvester=h, pipeline=pipeline,
                       broadcast=BroadcastAnalizada(), sleep=FakeSleep(block_at=600))
    try:
        await radar.start()
        await asyncio.wait_for(analizada.wait(), WORKER_TIMEOUT)
        assert [p["estado"] for p in list_posts()] == ["ANALIZADA"]
        assert pipeline.calls == []  # afinidad bajo el umbral: el CV espera el botón
        pid = list_posts()[0]["id"]
        await radar.generate_cv(pid)
        continuar.set()
        await wait_until(lambda: get_post(pid)["estado"] == "LISTO")
        assert radar.is_running
        assert radar._gen_task is not None and not radar._gen_task.done()  # espera el próximo wake
    finally:
        continuar.set()
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_cancels_generation_in_progress(tmp_path):
    pid = crear_post(1)
    pipeline = FakePipeline(gate=asyncio.Event())  # nunca termina
    radar = make_radar(tmp_path, pipeline=pipeline, sleep=FakeSleep(block_at=600))
    await radar.start()
    await wait_until(lambda: pipeline.calls)
    gen = radar._gen_task
    await finalizar(radar)
    assert gen.cancelled() or gen.done()
    assert get_post(pid)["estado"] == "GENERANDO"  # se retoma en el próximo arranque


# ---------------------------------------------------------------------- envío

@pytest.mark.asyncio
async def test_send_worker_sends_and_marks_enviado(tmp_path):
    pid = crear_aprobado(1)
    send, rec = FakeSend(), Recorder()
    radar = make_radar(tmp_path, send=send, broadcast=rec)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == [pid]
        assert send.estados_al_enviar == ["ENVIANDO"]  # marcado antes de llamar a send_fn
        post = get_post(pid)
        assert post["estado"] == "ENVIADO"
        assert post["message_id"] == f"<msg-{pid}@test>"
        assert post["sent_at"] == "2026-11-01 10:00:00"
        assert get_radar_state()["contadores_dia"]["enviados"] == 1
        estados = [p["estado"] for n, p in rec.events if n == "radar_post" and p["id"] == pid]
        assert estados[-2:] == ["ENVIANDO", "ENVIADO"]
        assert not radar.is_running  # se envió con el radar detenido
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_enqueue_send_requires_listo_and_approval(tmp_path):
    sin_aprobar = crear_aprobado(1, aprobado_at=None)
    generando = crear_post(2)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        with pytest.raises(ValueError):
            await radar.enqueue_send(sin_aprobar)
        with pytest.raises(ValueError):
            await radar.enqueue_send(generando)
        with pytest.raises(ValueError):
            await radar.enqueue_send(9999)
        await esperar_workers(radar)
        assert send.calls == []
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_send_rechecks_approval_right_before_sending(tmp_path):
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        update_post(p2, aprobado_at=None)  # se retiró la aprobación antes de su turno
        await esperar_workers(radar)
        assert send.calls == [p1]
        assert get_post(p2)["estado"] == "LISTO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_enqueue_same_post_twice_sends_once(tmp_path):
    pid = crear_aprobado(1)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(pid)
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == [pid]
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
@pytest.mark.parametrize("campo", ["email_to", "email_body"])
async def test_send_refuses_empty_recipient_or_body(tmp_path, campo):
    pid = crear_aprobado(1, **{campo: "   "})
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == []
        post = get_post(pid)
        assert post["estado"] == "ERROR" and post["error"]
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_sends_are_spaced_30_to_90_seconds(tmp_path):
    log = []
    ids = [crear_aprobado(n) for n in (1, 2, 3)]
    sleep, send = FakeSleep(log=log), FakeSend(log=log)
    radar = make_radar(tmp_path, send=send, sleep=sleep)
    try:
        for pid in ids:
            await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == ids
        assert len(sleep.calls) >= 2
        assert all(30 <= s <= 90 for s in sleep.calls)
        envios = [i for i, (k, _) in enumerate(log) if k == "send"]
        for a, b in zip(envios, envios[1:]):
            assert any(k == "sleep" for k, _ in log[a + 1:b])  # espera entre cada par de envíos
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_daily_send_cap_defers(tmp_path, monkeypatch):
    from tests import reglas_prueba
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(tope_envios_dia=1))
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await esperar_workers(radar)
        assert send.calls == [p1]
        assert get_post(p1)["estado"] == "ENVIADO"
        post2 = get_post(p2)
        assert post2["estado"] == "LISTO" and post2["aprobado_at"] == APROBADO
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_deferred_sends_go_out_after_day_change(tmp_path):
    ayer = (date.today() - timedelta(days=1)).isoformat()
    update_radar_state(tope_envios_dia=1, contadores_dia={"enviados": 1}, fecha_contadores=ayer)
    pid = crear_aprobado(1)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.resume_if_needed()  # reencola los LISTO aprobados aunque el radar no corra
        await esperar_workers(radar)
        assert send.calls == [pid]
        assert get_radar_state()["contadores_dia"]["enviados"] == 1
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_auth_error_pauses_all_sends(tmp_path):
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send, rec = FakeSend([MailerAuthError("535 credenciales inválidas")]), Recorder()
    radar = make_radar(tmp_path, send=send, broadcast=rec)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await esperar_workers(radar)
        assert send.calls == [p1]
        assert get_radar_state()["envios_pausados"] is True
        for pid in (p1, p2):
            post = get_post(pid)
            assert post["estado"] == "LISTO" and post["aprobado_at"] == APROBADO
        assert "credenciales" in get_post(p1)["error"]
        assert any(n == "radar_status" and p["envios_pausados"] for n, p in rec.events)
        # mientras sigan pausados, encolar no envía nada
        await radar.enqueue_send(p2)
        await esperar_workers(radar)
        assert send.calls == [p1]
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_temporary_error_retries_with_backoff_then_error(tmp_path):
    pid = crear_aprobado(1)
    errores = [MailerTemporaryError("421 servicio no disponible")] * 4
    send, sleep = FakeSend(errores), FakeSleep()
    radar = make_radar(tmp_path, send=send, sleep=sleep)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert sleep.calls == [30, 120, 300]
        assert len(send.calls) == 4  # intento original + 3 reintentos
        post = get_post(pid)
        assert post["estado"] == "ERROR" and "421" in post["error"]
        assert post["aprobado_at"] is None  # reintentar exige aprobar de nuevo
        assert get_radar_state()["contadores_dia"]["enviados"] == 0
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_temporary_error_then_success_is_sent(tmp_path):
    pid = crear_aprobado(1)
    send, sleep = FakeSend([MailerTemporaryError("timeout")]), FakeSleep()
    radar = make_radar(tmp_path, send=send, sleep=sleep)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert len(send.calls) == 2 and sleep.calls[0] == 30
        post = get_post(pid)
        assert post["estado"] == "ENVIADO" and post["error"] is None
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [MailerError("553 destinatario rechazado"), RuntimeError("pdf roto")])
async def test_other_send_errors_are_not_retried(tmp_path, exc):
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send, sleep = FakeSend([exc]), FakeSleep()
    radar = make_radar(tmp_path, send=send, sleep=sleep)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await esperar_workers(radar)
        assert send.calls == [p1, p2]  # sin reintento de p1; el siguiente sí sale
        assert get_post(p1)["estado"] == "ERROR" and str(exc) in get_post(p1)["error"]
        assert get_post(p1)["aprobado_at"] is None
        assert get_post(p2)["estado"] == "ENVIADO"
        assert all(30 <= s <= 90 for s in sleep.calls)  # ninguna espera de backoff
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_ambiguous_send_error_is_error_without_retry_and_unapproved(tmp_path):
    """Corte durante la transmisión: puede haber salido, así que nunca se reintenta solo."""
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send, sleep = FakeSend([MailerAmbiguousError("Conexión SMTP caída durante el envío")]), FakeSleep()
    radar = make_radar(tmp_path, send=send, sleep=sleep)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await esperar_workers(radar)
        assert send.calls == [p1, p2]  # p1 una sola vez
        post = get_post(p1)
        assert post["estado"] == "ERROR"
        assert post["error"].startswith("Envío incierto: verificá en Gmail (Enviados) antes de reintentar")
        assert post["aprobado_at"] is None
        assert all(30 <= s <= 90 for s in sleep.calls)  # sin backoff
        assert get_radar_state()["contadores_dia"]["enviados"] == 1
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_missing_recipient_error_clears_approval(tmp_path):
    pid = crear_aprobado(1)
    radar = make_radar(tmp_path)
    try:
        await radar.enqueue_send(pid)
        update_post(pid, email_to="")  # cambió entre el encolado y el envío
        await esperar_workers(radar)
        post = get_post(pid)
        assert post["estado"] == "ERROR" and post["aprobado_at"] is None
    finally:
        await finalizar(radar)


def test_claim_for_sending_is_conditional():
    listo = crear_aprobado(1)
    sin_aprobar = crear_aprobado(2, aprobado_at=None)
    descartado = crear_aprobado(3)
    update_post(descartado, estado="DESCARTADO")
    assert radar_store.claim_for_sending(listo) is True
    assert get_post(listo)["estado"] == "ENVIANDO"
    assert radar_store.claim_for_sending(listo) is False  # ya no está LISTO
    assert radar_store.claim_for_sending(sin_aprobar) is False
    assert get_post(sin_aprobar)["estado"] == "LISTO"
    assert radar_store.claim_for_sending(descartado) is False
    assert get_post(descartado)["estado"] == "DESCARTADO"


@pytest.mark.asyncio
async def test_send_skipped_when_conditional_transition_loses_race(tmp_path, monkeypatch):
    """Si el UPDATE condicional no toma el post (cambió en el medio), send_fn nunca se llama."""
    pid = crear_aprobado(1)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)

    def perdio_carrera(post_id, db_path=None):
        update_post(post_id, aprobado_at=None)  # p. ej. regenerado justo antes
        return False

    monkeypatch.setattr(radar_store, "claim_for_sending", perdio_carrera)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == []
        assert get_post(pid)["estado"] == "LISTO"
    finally:
        await finalizar(radar)


def test_reconcile_on_startup_clears_approval():
    pid = crear_aprobado(1)
    update_post(pid, estado="ENVIANDO")
    radar_store.reconcile_on_startup()
    post = get_post(pid)
    assert post["estado"] == "ERROR" and post["aprobado_at"] is None
    assert post["error"] == radar_store.ERROR_ENVIO_INTERRUMPIDO


@pytest.mark.asyncio
async def test_resume_sends_requeues_approved(tmp_path):
    update_radar_state(envios_pausados=True)
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.resume_if_needed()  # pausados: no reencola
        await esperar_workers(radar)
        assert send.calls == []
        await radar.resume_sends()
        await esperar_workers(radar)
        assert get_radar_state()["envios_pausados"] is False
        assert sorted(send.calls) == [p1, p2]
        assert all(get_post(p)["estado"] == "ENVIADO" for p in (p1, p2))
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_start_launches_three_workers_and_stop_cleans_up(tmp_path):
    radar = make_radar(tmp_path, sleep=FakeSleep(block_at=600))
    await radar.start()
    harvest, proc, gen, send = radar._task, radar._proc_task, radar._gen_task, radar._send_task
    assert all(t is not None and not t.done() for t in (harvest, proc, gen, send))
    await finalizar(radar)
    assert harvest.done() and proc.done() and gen.done() and send.done()


@pytest.mark.asyncio
async def test_stop_lets_inflight_send_finish(tmp_path):
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    gate = asyncio.Event()
    send = FakeSend(gate=gate)
    radar = make_radar(tmp_path, send=send, sleep=FakeSleep(block_at=600))
    try:
        await radar.start()
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await wait_until(lambda: send.calls)
        await asyncio.wait_for(radar.stop(), WORKER_TIMEOUT)  # no espera ni cancela el envío en curso
        assert get_radar_state()["estado"] == "DETENIDO"
        assert get_post(p1)["estado"] == "ENVIANDO"
        gate.set()
        await esperar_workers(radar)
        # el aprobado que seguía en cola también sale con el radar detenido
        assert get_post(p1)["estado"] == "ENVIADO" and get_post(p2)["estado"] == "ENVIADO"
    finally:
        gate.set()
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_for_shutdown_waits_inflight_send_and_leaves_rest_queued(tmp_path):
    p1, p2 = crear_aprobado(1), crear_aprobado(2)
    gate = asyncio.Event()
    send = FakeSend(gate=gate)
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(p1)
        await radar.enqueue_send(p2)
        await wait_until(lambda: send.calls)
        apagado = asyncio.create_task(radar.stop_for_shutdown())
        await wait_until(radar._send_stop_event.is_set)
        assert not apagado.done()  # no corta un send_fn a la mitad
        gate.set()
        await asyncio.wait_for(apagado, WORKER_TIMEOUT)
        assert radar._send_task is None or radar._send_task.done()
        assert get_post(p1)["estado"] == "ENVIADO"
        post2 = get_post(p2)
        assert post2["estado"] == "LISTO" and post2["aprobado_at"] == APROBADO
        assert send.calls == [p1]
    finally:
        gate.set()
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_for_shutdown_before_send_fn_does_not_start_a_send(tmp_path):
    pid = crear_aprobado(1)
    gate = asyncio.Event()

    class BroadcastLento(Recorder):
        async def __call__(self, name, payload):
            await super().__call__(name, payload)
            if name == "radar_post" and payload["estado"] == "ENVIANDO":
                await gate.wait()

    send = FakeSend()
    radar = make_radar(tmp_path, send=send, broadcast=BroadcastLento())
    try:
        await radar.enqueue_send(pid)
        await wait_until(lambda: get_post(pid)["estado"] == "ENVIANDO")
        apagado = asyncio.create_task(radar.stop_for_shutdown())
        await wait_until(radar._send_stop_event.is_set)
        gate.set()
        await asyncio.wait_for(apagado, WORKER_TIMEOUT)
        assert send.calls == []
        post = get_post(pid)
        assert post["estado"] == "LISTO" and post["aprobado_at"] == APROBADO
    finally:
        gate.set()
        await finalizar(radar)


@pytest.mark.asyncio
async def test_stop_for_shutdown_during_backoff_returns_post_to_listo(tmp_path):
    pid = crear_aprobado(1)
    send = FakeSend([MailerTemporaryError("timeout")])
    radar = make_radar(tmp_path, send=send, sleep=FakeSleep(block_at=30))
    try:
        await radar.enqueue_send(pid)
        await wait_until(lambda: radar._sleep.calls)
        await asyncio.wait_for(radar.stop_for_shutdown(), WORKER_TIMEOUT)
        post = get_post(pid)
        assert post["estado"] == "LISTO" and post["aprobado_at"] == APROBADO
        assert send.calls == [pid]
    finally:
        await finalizar(radar)


# ---------------------------------------------------------------------- retry

@pytest.mark.asyncio
async def test_retry_transitions(tmp_path):
    sin_triage = insert_post_if_new(raw(1), "q0")
    update_post(sin_triage, estado="ERROR", intentos=3, error="triage falló")
    sin_email = crear_post(2, "ERROR", intentos=1, error="pipeline falló", postulacion_id=5)
    con_email = crear_aprobado(3, error="envío interrumpido")
    update_post(con_email, estado="ERROR")
    pipeline, compose = FakePipeline(), FakeCompose()
    radar = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    try:
        await radar.retry(sin_triage)
        p = get_post(sin_triage)
        assert p["estado"] == "NUEVO" and p["intentos"] == 0 and p["error"] is None

        await radar.retry(con_email)
        p = get_post(con_email)
        assert p["estado"] == "LISTO" and p["aprobado_at"] is None
        assert p["intentos"] == 0 and p["error"] is None

        await radar.retry(sin_email)
        await esperar_workers(radar)
        p = get_post(sin_email)
        assert p["estado"] == "LISTO" and p["intentos"] == 0 and p["error"] is None
        assert compose.calls == [(sin_email, 5)] and pipeline.calls == []

        with pytest.raises(ValueError):
            await radar.retry(con_email)  # ya no está en ERROR
        with pytest.raises(ValueError):
            await radar.retry(9999)
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_retry_non_email_channel_with_message_goes_to_listo_without_regenerating(tmp_path):
    """Canal no email: el mensaje generado es `email_body` (no lleva asunto)."""
    con_mensaje = crear_post(1, "ERROR", canal="whatsapp", destino_canal="5491155555555",
                             postulacion_id=5, email_subject="", email_body="Hola, te escribo por la búsqueda")
    sin_mensaje = crear_post(2, "ERROR", canal="link", destino_canal="https://x.org/j",
                             postulacion_id=6, email_subject="Asunto viejo", email_body="")
    pipeline, compose = FakePipeline(), FakeCompose()
    radar = make_radar(tmp_path, pipeline=pipeline, compose=compose)
    try:
        await radar.retry(con_mensaje)
        p = get_post(con_mensaje)
        assert p["estado"] == "LISTO" and p["email_body"] == "Hola, te escribo por la búsqueda"

        await radar.retry(sin_mensaje)
        await esperar_workers(radar)
        assert compose.calls == [(sin_mensaje, 6)] and pipeline.calls == []
    finally:
        await finalizar(radar)


# --------------------------------------------------------- correcciones de Task 7

@pytest.mark.asyncio
async def test_show_browser_during_stop_never_overlaps_two_browsers(tmp_path):
    log = []
    gate = asyncio.Event()
    harvesters = []

    def nuevo():
        h = FakeHarvester(log=log, close_gate=gate if not harvesters else None)
        harvesters.append(h)
        return h

    radar = make_radar(tmp_path, harvester_factory=nuevo, sleep=FakeSleep(block_at=600))
    try:
        await radar.start()
        await wait_until(lambda: radar._sleep.calls and radar._sleep.calls[-1] >= 600)
        parada = asyncio.create_task(radar.stop())
        await wait_until(lambda: ("close_start", id(harvesters[0])) in log)
        mostrando = asyncio.Event()

        async def mostrar_browser():
            mostrando.set()
            await radar.show_browser()

        mostrar = asyncio.create_task(mostrar_browser())
        await asyncio.wait_for(mostrando.wait(), WORKER_TIMEOUT)
        assert len(harvesters) == 1 and not mostrar.done()  # espera a que termine stop()
        gate.set()
        await asyncio.wait_for(parada, WORKER_TIMEOUT)
        await asyncio.wait_for(mostrar, WORKER_TIMEOUT)
        assert len(harvesters) == 2
        assert log.index(("close_end", id(harvesters[0]))) < log.index(("show", id(harvesters[1])))
    finally:
        gate.set()
        await finalizar(radar)
    assert harvesters[1].close_calls == 1


@pytest.mark.asyncio
async def test_show_browser_after_server_shutdown_does_not_open_browser(tmp_path):
    radar = make_radar(tmp_path)
    try:
        await radar.stop_for_shutdown()
        await radar.show_browser()
        assert radar._test_created == []
    finally:
        await finalizar(radar)




# ----------------------------------------------------------------- canales sin email nunca salen por Gmail

@pytest.mark.asyncio
@pytest.mark.parametrize("canal", ["whatsapp", "link", "dm", "otro"])
async def test_resume_does_not_send_approved_non_email_post(tmp_path, canal):
    pid = crear_aprobado(1, canal=canal, destino_canal="https://wa.me/549")
    mail = crear_aprobado(2)
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.resume_if_needed()
        await esperar_workers(radar)
        assert send.calls == [mail]
        post = get_post(pid)
        assert post["estado"] == "LISTO" and post["aprobado_at"] is None
        assert canal in post["error"] and "Ya me postulé" in post["error"]
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_procesar_envio_refuses_non_email_before_claiming(tmp_path):
    pid = crear_aprobado(1, canal="whatsapp")
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        assert await radar._procesar_envio(pid) is False
        assert send.calls == []
        post = get_post(pid)
        assert post["estado"] == "LISTO" and post["aprobado_at"] is None
        assert "Ya me postulé" in post["error"]
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_enqueue_send_rejects_non_email_channel(tmp_path):
    pid = crear_aprobado(1, canal="link")
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        with pytest.raises(ValueError, match="link"):
            await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == []
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_legacy_empty_canal_is_still_sent_by_email(tmp_path):
    pid = crear_aprobado(1, canal="")
    send = FakeSend()
    radar = make_radar(tmp_path, send=send)
    try:
        await radar.enqueue_send(pid)
        await esperar_workers(radar)
        assert send.calls == [pid] and get_post(pid)["estado"] == "ENVIADO"
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_offer_over_threshold_generates_its_cv_alone(tmp_path):
    texto_suficiente = (
        "Buscamos un Analista Funcional con experiencia comprobable en relevamiento de procesos "
        "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
        "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
        "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
        "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
    )
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1, texto=texto_suficiente)])})
    pipeline = FakePipeline()
    radar = make_radar(tmp_path, harvester=h, pipeline=pipeline, sleep=FakeSleep(block_at=600), umbral=70)
    try:
        await radar.start()
        await wait_until(lambda: [p["estado"] for p in list_posts()] == ["LISTO"])
        assert len(pipeline.calls) == 1
        assert list_posts()[0]["aprobado_at"] is None  # se genera solo, pero nada sale sin aprobar
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
async def test_el_umbral_del_panel_decide_que_entra_a_las_colas(tmp_path):
    from core.prioridades import supera_umbral
    abierta = crear_post(1, estado="ANALIZADA", analisis={"match_score": 85})
    cerrada = crear_post(2, estado="ANALIZADA", analisis={"match_score": 95, "oferta_cerrada": True})
    baja = crear_post(3, estado="ANALIZADA", analisis={"match_score": 40})
    radar = make_radar(tmp_path, sleep=FakeSleep(block_at=600), umbral=70)
    try:
        from core import colas
        from core.radar_store import candidatas_cola
        assert await radar.generar_automaticos() == []
        armadas = colas.armar_colas(candidatas_cola())
        assert [p["id"] for p in armadas["prioritaria"]] == [abierta]
        assert baja not in [p["id"] for p in armadas["fondo"]]
        update_radar_state(umbral_match=90)
        armadas = colas.armar_colas(candidatas_cola())
        assert armadas["prioritaria"] == [] and [p["id"] for p in armadas["fondo"]] == [cerrada]
        assert get_post(cerrada)["estado"] == "ANALIZADA" and get_post(baja)["estado"] == "ANALIZADA"
    finally:
        await finalizar(radar)
    analisis = {"analisis": {"match_score": "80"}}
    assert supera_umbral(analisis, 79) and not supera_umbral(analisis, 80)
    assert not supera_umbral({"analisis": {"match_score": None}}, 80)


# ---------------------------------------------------------------------- reprocesar

@pytest.mark.asyncio
async def test_reprocesar_requeues_everything_but_discarded_sent_and_approved(tmp_path):
    error_con_mail = crear_aprobado(1, aprobado_at=None, error="Falló la generación: timeout")
    update_post(error_con_mail, estado="ERROR")
    listo = crear_aprobado(2, aprobado_at=None)
    aprobado = crear_aprobado(3)
    analizada = crear_post(4, "ANALIZADA", analisis={"match_score": 40})
    descartado = crear_post(5, "DESCARTADO", analisis={"match_score": 90})
    enviado = crear_aprobado(6)
    update_post(enviado, estado="ENVIADO")
    compose = FakeCompose()
    radar = make_radar(tmp_path, compose=compose)
    try:
        hechos = await radar.reprocesar()
        assert hechos == {"reintento": [error_con_mail], "mensaje": [listo, error_con_mail],
                          "analisis": [analizada]}
        await esperar_workers(radar)
        assert sorted(c[0] for c in compose.calls) == sorted([listo, error_con_mail])
        assert get_post(aprobado)["estado"] == "LISTO" and get_post(aprobado)["aprobado_at"] == APROBADO
        assert get_post(descartado)["estado"] == "DESCARTADO"
        assert get_post(enviado)["estado"] == "ENVIADO"
        assert get_post(analizada)["estado"] != "ANALIZADA" or get_post(analizada)["intentos"] == 0
    finally:
        await finalizar(radar)


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["NUEVO", "OFERTA", "ANALIZADA", "GENERANDO"])
@pytest.mark.parametrize("servidor", [False, True])
async def test_trabajo_cargado_durante_cierre_no_queda_varado(tmp_path, estado, servidor):
    gate = asyncio.Event()
    log = []
    harvester = FakeHarvester(log=log, close_gate=gate)
    radar = make_radar(tmp_path, harvester=harvester)
    radar._harvester = harvester  # Navegador falso ya existente; no se arranca el Radar.
    cierre = asyncio.create_task(radar.stop_for_shutdown() if servidor else radar.stop())
    try:
        await wait_until(lambda: ("close_start", id(harvester)) in log)
        pid = radar_store.insert_post_manual(TEXTO_SUFICIENTE)
        update_post(pid, estado=estado, triage=dict(TRIAGE_OK),
                    analisis={"match_score": 80, "puesto": "Analista"})
        if estado == "GENERANDO":
            update_post(pid, fuente="busqueda")
            await radar.wake_generation()
        else:
            await radar.wake_processing()
        assert radar._proc_task is None and radar._gen_task is None
        gate.set()
        await asyncio.wait_for(cierre, WORKER_TIMEOUT)
        if servidor:
            await esperar_workers(radar)
            assert get_post(pid)["estado"] != "LISTO"
            assert radar._proc_task is None and radar._gen_task is None
        else:
            assert any(t is not None for t in (radar._proc_task, radar._gen_task))
            await wait_until(lambda: get_post(pid)["estado"] == "LISTO")
            assert get_post(pid)["aprobado_at"] is None
        assert radar._test_created == []
    finally:
        gate.set()
        await cierre
        await radar.stop_for_shutdown()


@pytest.mark.asyncio
async def test_stop_conserva_particiones_de_reintento(tmp_path):
    lotes = []

    async def clasificar(posts):
        lotes.append([p["id"] for p in posts])
        return {p["id"]: {"es_busqueda_laboral": False} for p in posts}

    radar = make_radar(tmp_path, triage=clasificar)
    ids = [radar_store.insert_post_manual(f"Oferta manual {i}") for i in range(6)]
    for index, pid in enumerate(ids):
        update_post(pid, created_at=(FIXED_NOW + timedelta(seconds=index)).isoformat())
    radar._mitades = [ids[:2], ids[2:4]]
    try:
        await radar.stop()
        await wait_until(lambda: all(get_post(pid)["estado"] != "NUEVO" for pid in ids))
        assert lotes == [ids[:2], ids[2:4], ids[4:][::-1]]
    finally:
        await radar.stop_for_shutdown()
