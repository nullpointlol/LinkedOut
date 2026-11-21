"""Tests del RadarLoop (Task 7): ciclo de lectura, triage, ritmo, topes y estados.

Sin navegador ni LLM reales: harvester falso, triage falso, `sleep` falso que registra
las duraciones y cede el control, reloj inyectable y `rng=random.Random(0)`.
"""
import asyncio
import time
import random
from datetime import date, datetime, timedelta

import pytest

from core.linkedin_harvester import HarvestResult
from core.radar import DEFAULT_QUERIES, RadarLoop
from core.radar_store import (
    get_post,
    get_radar_state,
    insert_post_if_new,
    list_posts,
    update_post,
    update_radar_state,
)
from core.triage import TriageError


# ----------------------------------------------------------------- dobles de prueba

TEXTO_SUFICIENTE = (
    "Buscamos un Analista Funcional con amplia experiencia en relevamiento de procesos "
    "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
    "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
    "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
    "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
)


def raw(n: int, emails=("rrhh@empresa.test",), texto: str = None) -> dict:
    # Texto distinto por post: el radar no guarda dos veces la misma oferta republicada
    texto = f"{TEXTO_SUFICIENTE} (aviso {n})" if texto is None else texto
    return {
        "urn": f"urn:li:activity:{n}",
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
        "autor": f"Autor {n}",
        "cargo_autor": "HR",
        "texto": texto,
        "fecha_rel": "3 h",
        "emails": list(emails),
    }


class FakeHarvester:
    """Respuestas por consulta: lista (se consume en orden), HarvestResult fijo o excepción."""

    def __init__(self, responses=None, logged_in_seq=None):
        self.responses = dict(responses or {})
        self.logged_in_seq = list(logged_in_seq or [])
        self.calls = []
        self.show_calls = 0
        self.close_calls = 0
        self.login_checks = 0
        self.dumps = []
        self.conocidos = []
        self.params = []

    async def open(self):
        pass

    async def harvest(self, query, max_posts=30, max_scrolls=8, conocidos=None, paciencia=1):
        self.calls.append(query)
        self.params.append((query, max_posts, max_scrolls, paciencia))
        self.conocidos.append(conocidos)
        r = self.responses.get(query, HarvestResult())
        if isinstance(r, list):
            r = r.pop(0) if r else HarvestResult()
        if isinstance(r, BaseException):
            raise r
        return r

    async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
        self.calls.append("feed")
        self.params.append(("feed", max_posts, max_scrolls, None))
        self.conocidos.append(conocidos)
        r = self.responses.get("feed", HarvestResult())
        if isinstance(r, list):
            r = r.pop(0) if r else HarvestResult()
        if isinstance(r, BaseException):
            raise r
        return r

    async def check_logged_in(self):
        self.login_checks += 1
        return self.logged_in_seq.pop(0) if self.logged_in_seq else True

    async def show(self):
        self.show_calls += 1

    async def dump_debug(self, target_dir):
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "page.html").write_text("<html></html>", encoding="utf-8")
        self.dumps.append(target_dir)

    async def close(self):
        self.close_calls += 1


class FakeSleep:
    """Registra duraciones y cede; las esperas >= `block_at` no terminan nunca (como una pausa real)."""

    def __init__(self, block_at=None):
        self.calls = []
        self.block_at = block_at

    async def __call__(self, seconds):
        self.calls.append(seconds)
        if self.block_at is not None and seconds >= self.block_at:
            await asyncio.Event().wait()
        await asyncio.sleep(0)


class FakeTriage:
    def __init__(self, es_busqueda=True, error=None, alcance="ARGENTINA",
                 canal="email", destino_canal=None):
        self.canal = canal
        self.destino_canal = destino_canal
        self.es_busqueda = es_busqueda
        self.alcance = alcance
        self.error = error
        self.calls = []

    async def __call__(self, posts):
        self.calls.append([p["id"] for p in posts])
        if self.error is not None:
            raise self.error
        return {
            p["id"]: {
                "es_busqueda_laboral": self.es_busqueda,
                "puesto": "Analista Funcional",
                "empresa": "Empresa",
                "ubicacion_modalidad": "Remoto",
                "motivo": "es una oferta" if self.es_busqueda else "no es una oferta",
                "email_destino": (p.get("emails") or [""])[0],
                "instrucciones_postulacion": "",
                "alcance": self.alcance,
                "pais": "",
                "alcance_motivo": f"alcance {self.alcance}",
                "canal": self.canal,
                "destino_canal": self.destino_canal,
            }
            for p in posts
        }


ANALISIS_OK = {
    "puesto": "Analista Funcional", "empresa": "Acme", "match_score": 72.5,
    "dolores_empresa": ["Procesos manuales"],
    "requisitos": [{"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO",
                    "justificacion": "7 años", "equivalencia": None, "approved": True}],
}


class FakeAnalyze:
    """Análisis de la oferta falso: registra los ids; `error` (o una lista de errores/None por
    llamada) hace fallar."""

    def __init__(self, error=None, gate=None, score=70):
        self.error = error
        self.gate = gate
        self.score = score
        self.calls = []

    async def __call__(self, post):
        self.calls.append(post["id"])
        if self.gate is not None:
            await self.gate.wait()
        error = self.error.pop(0) if isinstance(self.error, list) and self.error else (
            None if isinstance(self.error, list) else self.error)
        if error is not None:
            raise error
        return dict(ANALISIS_OK, match_score=self.score)


class Recorder:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def __call__(self, name, payload):
        self.events.append((name, payload))
        if self.fail:
            raise RuntimeError("cliente SSE caído")

    def estados(self):
        return [p["estado"] for n, p in self.events if n == "radar_status"]


FIXED_NOW = datetime(2026, 11, 1, 10, 0, 0)


def make_radar(tmp_path, harvester=None, triage=None, sleep=None, now=None,
               queries=("q1", "q2"), broadcast=None, analyze=None, umbral=100, queries_en=()):
    harvester = harvester or FakeHarvester()
    created = []

    def factory():
        created.append(harvester)
        return harvester

    async def _unused(*a, **k):
        raise AssertionError("no se usa en Task 7")

    radar = RadarLoop(
        harvester_factory=factory,
        triage_fn=triage or FakeTriage(),
        analyze_fn=analyze or FakeAnalyze(),
        pipeline_fn=_unused,
        compose_fn=_unused,
        send_fn=_unused,
        broadcast=broadcast or Recorder(),
        sleep=sleep or FakeSleep(),
        rng=random.Random(0),
        # `queries_en` (heredado de los tests viejos) ya no distingue nada: se suman al final, en orden
        queries=list(queries) + [q for q in queries_en if q not in queries],
        debug_dir=tmp_path / "radar_debug",
        now=now or (lambda: FIXED_NOW),
    )
    radar._test_created = created
    # El análisis falso queda en 70: estas pruebas de análisis no encolan CVs.
    update_radar_state(umbral_match=umbral)
    return radar


async def wait_until(pred, tries=5000, segundos=60):
    """Espera la condición: primero `tries` vueltas del event loop y después, si hace falta (un hilo de SQLite
    con la PC cargada), de a 10 ms hasta `segundos`. Es solo una guarda contra bloqueos."""
    limite = time.monotonic() + segundos
    vueltas = 0
    while not pred():
        if time.monotonic() > limite:
            raise AssertionError("la condición no se cumplió")
        await asyncio.sleep(0 if vueltas < tries else 0.01)
        vueltas += 1


# ----------------------------------------------------------------------- tests

def test_default_queries_spanish_first_then_english():
    """Las búsquedas son las activas de busquedas.json (fixture): primero español, después inglés."""
    assert DEFAULT_QUERIES == [
        '"cv a"', '("cv a" OR "enviar cv") (frontend OR "desarrollador web")',
        '"cv a" ((desarrollo AND web) OR fullstack) Argentina',
        '("send your cv" OR "send your resume") (Argentina OR LATAM)', '"night shift" (Argentina OR LATAM)']
    assert '"cv a" pausada' not in DEFAULT_QUERIES  # la inactiva no se lee


@pytest.mark.asyncio
async def test_cycle_dedups_spanish_posts_across_queries(tmp_path):
    h = FakeHarvester({
        "q1": HarvestResult(posts=[raw(1), raw(2, emails=())]),
        "q2": HarvestResult(posts=[raw(1)]),
    })
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        assert len(list_posts()) == 2  # en español entra también el que no tiene email
        c = get_radar_state()["contadores_dia"]
        assert c["leidos_busquedas"] == 2 and c["con_email"] == 2  # raw(1) releído no cuenta dos veces
        assert {p["query"] for p in list_posts()} == {"q1"}
    finally:
        await radar.stop()




















@pytest.mark.asyncio
async def test_login_needed_sets_esperando_login_and_resumes(tmp_path):
    h = FakeHarvester(
        {"q1": [HarvestResult(needs_login=True), HarvestResult(posts=[raw(1)])],
         "q2": HarvestResult(posts=[raw(2)])},
        logged_in_seq=[False, True],
    )
    rec = Recorder()
    sleep = FakeSleep()
    radar = make_radar(tmp_path, h, sleep=sleep, broadcast=rec)
    try:
        await radar.run_cycle()
        estados = rec.estados()
        i = estados.index("ESPERANDO_LOGIN")
        assert "CORRIENDO" in estados[i + 1:]
        assert h.show_calls == 1
        assert sleep.calls.count(30) == 2 and h.login_checks == 2
        assert h.calls == ["feed", "q1", "q1", "q2"]  # la consulta interrumpida se reintenta
        assert len(list_posts()) == 2
        assert get_radar_state()["estado"] == "CORRIENDO"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_stop_while_waiting_login_is_immediate(tmp_path):
    h = FakeHarvester({"feed": HarvestResult(needs_login=True)})
    radar = make_radar(tmp_path, h, sleep=FakeSleep(block_at=30))
    await radar.start()
    await wait_until(lambda: h.show_calls == 1)
    await asyncio.wait_for(radar.stop(), 60)
    assert get_radar_state()["estado"] == "DETENIDO"
    assert h.login_checks == 0 and not radar.is_running


@pytest.mark.asyncio
async def test_two_empty_cycles_set_error_and_dump_debug(tmp_path):
    h = FakeHarvester()
    radar = make_radar(tmp_path, h)
    debug_dir = tmp_path / "radar_debug"
    try:
        await radar.run_cycle()
        s = get_radar_state()
        # Cero resultados en una búsqueda ya es un error; los selectores rotos, a los dos ciclos
        assert s["ciclos_vacios_consecutivos"] == 1
        assert s["estado"] == "ERROR" and "selectores" not in s["estado_detalle"]
        await radar.run_cycle()
        s = get_radar_state()
        assert s["ciclos_vacios_consecutivos"] == 2
        assert s["estado"] == "ERROR" and "selectores" in s["estado_detalle"]
        assert [d for d in debug_dir.iterdir() if d.is_dir()]

        h.responses["q1"] = HarvestResult(posts=[raw(1)])
        h.responses["q2"] = HarvestResult(posts=[raw(2)])
        await radar.run_cycle()
        s = get_radar_state()
        assert s["ciclos_vacios_consecutivos"] == 0
        assert s["estado"] != "ERROR"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_harvester_exception_records_error_and_continues(tmp_path):
    h = FakeHarvester({"q1": RuntimeError("boom selector"), "q2": HarvestResult(posts=[raw(2)])})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1", "q2"]
        assert len(list_posts()) == 1
        s = get_radar_state()
        assert s["estado"] == "ERROR" and "boom selector" in s["estado_detalle"]
        assert s["ciclos_vacios_consecutivos"] == 0
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_loop_survives_errors_and_keeps_error_visible_during_pause(tmp_path):
    h = FakeHarvester({"q1": RuntimeError("boom"), "q2": RuntimeError("boom")})
    sleep = FakeSleep(block_at=600)
    radar = make_radar(tmp_path, h, sleep=sleep)
    try:
        await radar.start()
        await wait_until(lambda: sleep.calls and sleep.calls[-1] >= 600)
        s = get_radar_state()
        assert radar.is_running
        assert s["estado"] == "ERROR" and "boom" in s["estado_detalle"]
        assert s["next_cycle_at"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_unexpected_cycle_exception_does_not_kill_loop(tmp_path):
    async def triage(posts, perfil):
        return {}

    radar = make_radar(tmp_path, FakeHarvester({"q1": HarvestResult(posts=[raw(1)])}),
                       triage=triage, sleep=FakeSleep(block_at=600))

    def explota():
        raise RuntimeError("DB ilegible")

    radar._posts_busquedas_hoy = explota
    try:
        await radar.start()
        await wait_until(lambda: radar._sleep.calls and radar._sleep.calls[-1] >= 600)
        assert radar.is_running
        assert get_radar_state()["estado"] in ("ERROR", "PAUSA")
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_broadcast_failures_never_break_the_cycle(tmp_path):
    radar = make_radar(tmp_path, FakeHarvester({"q1": HarvestResult(posts=[raw(1)])}),
                       broadcast=Recorder(fail=True))
    try:
        await radar.run_cycle()
        await radar.procesar_pendientes()
        assert list_posts()[0]["estado"] == "ANALIZADA"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_pacing_between_queries_and_cycles(tmp_path):
    h = FakeHarvester({q: HarvestResult(posts=[raw(i)]) for i, q in enumerate(["q1", "q2", "q3"])})
    sleep = FakeSleep(block_at=600)
    radar = make_radar(tmp_path, h, sleep=sleep, queries=("q1", "q2", "q3"))
    try:
        await radar.start()
        await wait_until(lambda: len(sleep.calls) >= 4)
        # feed -> q1 -> q2 -> q3: tres esperas entre lecturas y luego la pausa del ciclo
        entre_consultas, pausa = sleep.calls[:3], sleep.calls[3]
        assert all(180 <= s <= 360 for s in entre_consultas)
        assert 1500 <= pausa <= 2100  # 25–35 min
        s = get_radar_state()
        assert s["estado"] == "PAUSA"
        esperado = (FIXED_NOW + timedelta(seconds=pausa)).strftime("%Y-%m-%d %H:%M:%S")
        assert s["next_cycle_at"] == esperado
        assert h.calls == ["feed", "q1", "q2", "q3"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_stop_during_pause_is_immediate(tmp_path):
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1)]), "q2": HarvestResult(posts=[raw(2)])})
    sleep = FakeSleep(block_at=600)
    radar = make_radar(tmp_path, h, sleep=sleep)
    await radar.start()
    await wait_until(lambda: sleep.calls and sleep.calls[-1] >= 600)
    assert get_radar_state()["estado"] == "PAUSA"
    await asyncio.wait_for(radar.stop(), 60)
    s = get_radar_state()
    assert s["estado"] == "DETENIDO"
    assert s["desired_running"] is False
    assert h.close_calls == 1
    assert not radar.is_running


@pytest.mark.asyncio
async def test_stop_for_shutdown_keeps_desired_running(tmp_path):
    h = FakeHarvester()
    rec = Recorder()
    radar = make_radar(tmp_path, h, sleep=FakeSleep(block_at=600), broadcast=rec)
    await radar.start()
    await wait_until(lambda: radar._sleep.calls and radar._sleep.calls[-1] >= 600)
    await asyncio.wait_for(radar.stop_for_shutdown(), 60)
    s = get_radar_state()
    assert s["estado"] == "DETENIDO" and s["desired_running"] is True
    assert rec.estados()[-1] == "DETENIDO"
    assert h.close_calls == 1 and not radar.is_running


@pytest.mark.asyncio
async def test_restart_after_stop_creates_new_harvester(tmp_path):
    radar = make_radar(tmp_path, sleep=FakeSleep(block_at=600))
    await radar.start()
    await wait_until(lambda: len(radar._test_created) == 1 and radar._sleep.calls)
    await radar.stop()
    await radar.start()
    await wait_until(lambda: len(radar._test_created) == 2)
    assert radar.is_running and get_radar_state()["desired_running"] is True
    await radar.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path):
    radar = make_radar(tmp_path, sleep=FakeSleep(block_at=600))
    try:
        await radar.start()
        task = radar._task
        await radar.start()
        assert radar._task is task
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_show_browser_opens_harvester_and_shows(tmp_path):
    h = FakeHarvester()
    radar = make_radar(tmp_path, h)
    try:
        await radar.show_browser()
        assert h.show_calls == 1 and len(radar._test_created) == 1
    finally:
        await radar.stop()
    assert h.close_calls == 1


@pytest.mark.asyncio
async def test_resume_if_needed_starts_when_desired_running(tmp_path):
    update_radar_state(desired_running=True)
    pid = insert_post_if_new(raw(1), "q0")
    update_post(pid, estado="ENVIANDO")
    radar = make_radar(tmp_path, sleep=FakeSleep(block_at=600))
    try:
        await radar.resume_if_needed()
        assert radar.is_running
        assert get_post(pid)["estado"] == "ERROR"  # reconcile_on_startup
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_resume_if_needed_stays_stopped_when_not_desired(tmp_path):
    update_radar_state(desired_running=False, estado="PAUSA")
    radar = make_radar(tmp_path)
    await radar.resume_if_needed()
    assert not radar.is_running
    assert get_radar_state()["estado"] == "DETENIDO"


@pytest.mark.asyncio
async def test_counters_reset_when_day_changes_mid_run(tmp_path):
    ayer = (date.today() - timedelta(days=1)).isoformat()
    update_radar_state(contadores_dia={"leidos_busquedas": 250, "con_email": 40, "match": 9},
                       fecha_contadores=ayer)
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1), raw(2, emails=())])})
    radar = make_radar(tmp_path, h, queries=(), queries_en=("q1",))
    try:
        await radar.run_cycle()
        c = get_radar_state()["contadores_dia"]
        assert c["leidos_busquedas"] == 2 and c["con_email"] == 2  # en cualquier idioma, con o sin email
    finally:
        await radar.stop()


# --- Alcance geográfico: solo Argentina y exterior compatible generan CV ---

@pytest.mark.asyncio
@pytest.mark.parametrize("alcance,estado", [
    ("ARGENTINA", "GENERANDO"),
    ("EXTERIOR_COMPATIBLE", "GENERANDO"),
    ("EXTERIOR", "DESCARTADO"),
    ("DESCONOCIDO", "REVISAR"),
])








# ------------------------------------------------------------- feed de inicio (Mi feed)

def raw_feed(n: int, texto: str = None, **extra) -> dict:
    texto = f"{TEXTO_SUFICIENTE} (aviso feed {n}), escribinos por WhatsApp" if texto is None else texto
    d = raw(n, emails=(), texto=texto)
    d.update(extra)
    return d


@pytest.mark.asyncio
async def test_cycle_reads_feed_before_first_query_with_pause_between(tmp_path):
    h = FakeHarvester()
    sleep = FakeSleep()
    radar = make_radar(tmp_path, h, sleep=sleep, queries=("q1", "q2"))
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1", "q2"]
        # 20-60 s tanto entre el feed y la primera consulta como entre consultas
        assert len(sleep.calls) == 2 and all(180 <= s <= 360 for s in sleep.calls)
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_post_without_email_but_job_text_is_saved_with_fuente_feed(tmp_path):
    h = FakeHarvester({"feed": HarvestResult(posts=[raw_feed(1)])})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        posts = list_posts()
        assert len(posts) == 1
        assert posts[0]["fuente"] == "feed" and posts[0]["query"] == "feed"
        assert get_radar_state()["contadores_dia"]["con_email"] == 1
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_radar_post_event_carries_fuente_and_canal_of_feed_post(tmp_path):
    rec = Recorder()
    h = FakeHarvester({"feed": HarvestResult(posts=[raw_feed(1)])})
    radar = make_radar(tmp_path, h, broadcast=rec)
    try:
        await radar.run_cycle()
        eventos = [p for n, p in rec.events if n == "radar_post"]
        assert eventos and all(e["fuente"] == "feed" for e in eventos)
        canal = list_posts()[0].get("canal") or "email"
        assert all(e["canal"] == canal for e in eventos)
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_has_no_keyword_filter_every_post_with_text_goes_to_triage(tmp_path):
    # Pedido del usuario: el feed no se filtra por palabras; el triage decide qué es una búsqueda
    h = FakeHarvester({"feed": HarvestResult(posts=[
        raw_feed(1, texto="Qué lindo día para salir a correr por el parque"),
        raw_feed(2, texto="¡Abrimos estas oportunidades para sumar talento! Ingeniero de datos"),
        raw_feed(3, texto=""),
    ])})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        assert sorted(p["urn"] for p in list_posts()) == ["urn:li:activity:1", "urn:li:activity:2"]
        assert get_radar_state()["contadores_dia"]["leidos_feed"] == 3
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_english_search_post_without_email_is_saved_too(tmp_path):
    """sin «solo con email» en inglés; se guarda todo y el triage borra lo que no es oferta."""
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1, emails=())])})
    radar = make_radar(tmp_path, h, queries=(), queries_en=("q1",))
    try:
        await radar.run_cycle()
        assert len(list_posts()) == 1
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_search_post_is_saved_with_fuente_busqueda(tmp_path):
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1)])})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        assert list_posts()[0]["fuente"] == "busqueda"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_needs_login_sets_esperando_login_and_retries_feed(tmp_path):
    h = FakeHarvester(
        {"feed": [HarvestResult(needs_login=True), HarvestResult(posts=[raw_feed(1)])]},
        logged_in_seq=[False, True],
    )
    rec = Recorder()
    radar = make_radar(tmp_path, h, broadcast=rec, queries=("q1",))
    try:
        await radar.run_cycle()
        estados = rec.estados()
        assert "ESPERANDO_LOGIN" in estados
        assert "CORRIENDO" in estados[estados.index("ESPERANDO_LOGIN") + 1:]
        assert h.show_calls == 1 and h.login_checks == 2
        assert h.calls == ["feed", "feed", "q1"]
        assert len(list_posts()) == 1
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_exception_records_error_and_continues_with_queries(tmp_path):
    h = FakeHarvester({"feed": RuntimeError("feed roto"), "q1": HarvestResult(posts=[raw(1)])})
    radar = make_radar(tmp_path, h, queries=("q1",))
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1"]
        assert len(list_posts()) == 1
        s = get_radar_state()
        assert s["estado"] == "ERROR" and "feed roto" in s["estado_detalle"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_has_no_daily_cap(tmp_path):
    h = FakeHarvester({"feed": HarvestResult(posts=[raw_feed(1), raw_feed(2), raw_feed(3)]),
                       "q1": HarvestResult(posts=[raw(9)])})
    radar = make_radar(tmp_path, h, queries=(), queries_en=("q1",))
    try:
        await radar.run_cycle()
        # El feed no tiene tope diario ni cuenta para el tope de posts de búsquedas
        assert h.calls == ["feed", "q1"]
        c = get_radar_state()["contadores_dia"]
        assert c["leidos_feed"] == 3 and c["leidos_busquedas"] == 1
        assert len(list_posts()) == 4
    finally:
        await radar.stop()






@pytest.mark.asyncio
async def test_feed_post_with_email_but_no_job_keywords_is_saved(tmp_path):
    posts = [
        raw_feed(1, texto="Necesitamos un analista funcional. Escribinos a rrhh@acme.test",
                 emails=["rrhh@acme.test"]),
        raw_feed(2, texto="Sumamos gente a nuestro equipo, mail rrhh@acme.test", emails=["rrhh@acme.test"]),
        raw_feed(3, texto="Qué lindo día para salir a correr"),  # sin filtro: también entra
    ]
    h = FakeHarvester({"feed": HarvestResult(posts=posts)})
    radar = make_radar(tmp_path, h, queries=())
    try:
        await radar.run_cycle()
        guardados = list_posts()
        assert len(guardados) == 3
        assert all(p["fuente"] == "feed" for p in guardados)
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_empty_search_queries_flag_broken_selectors_even_if_feed_works(tmp_path):
    h = FakeHarvester({"feed": HarvestResult(posts=[raw_feed(1)])})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        assert get_radar_state()["ciclos_vacios_consecutivos"] == 1
        h.responses["feed"] = HarvestResult(posts=[raw_feed(2)])
        await radar.run_cycle()
        s = get_radar_state()
        assert s["ciclos_vacios_consecutivos"] == 2
        assert s["estado"] == "ERROR" and "selectores" in s["estado_detalle"]
        assert [d for d in (tmp_path / "radar_debug").iterdir() if d.is_dir()]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_empty_and_all_queries_failing_is_not_counted_as_empty_cycle(tmp_path):
    h = FakeHarvester({"q1": RuntimeError("boom"), "q2": RuntimeError("boom")})
    radar = make_radar(tmp_path, h)
    try:
        await radar.run_cycle()
        await radar.run_cycle()
        assert get_radar_state()["ciclos_vacios_consecutivos"] == 0
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_feed_empty_two_cycles_in_a_row_logs_warning_without_state_change(tmp_path, caplog):
    h = FakeHarvester({
        "q1": HarvestResult(posts=[raw(1)]),
        "feed": [HarvestResult(), HarvestResult(), HarvestResult(posts=[raw_feed(5)]), HarvestResult()],
    })
    radar = make_radar(tmp_path, h, queries=("q1",))
    caplog.set_level("WARNING", logger="core.radar")
    try:
        await radar.run_cycle()
        assert not [r for r in caplog.records if "feed de inicio" in r.getMessage()]
        await radar.run_cycle()
        avisos = [r for r in caplog.records if "feed de inicio" in r.getMessage()]
        assert len(avisos) == 1 and avisos[0].levelname == "WARNING" and "2 ciclos" in avisos[0].getMessage()
        assert get_radar_state()["estado"] != "ERROR"
        caplog.clear()
        await radar.run_cycle()   # el feed vuelve a traer posts: se reinicia la cuenta
        await radar.run_cycle()
        assert not [r for r in caplog.records if "feed de inicio" in r.getMessage()]
    finally:
        await radar.stop()


# ------------------------------------------------- tope diario: solo posts nuevos, repartido

@pytest.mark.asyncio
async def test_rereading_known_posts_does_not_consume_the_daily_cap(tmp_path):
    h = FakeHarvester({"feed": [HarvestResult(posts=[raw_feed(1), raw_feed(2)]),
                                HarvestResult(posts=[raw_feed(1), raw_feed(2), raw_feed(3)])]})
    radar = make_radar(tmp_path, h, queries=())
    try:
        await radar.run_cycle()
        await radar.run_cycle()
        assert get_radar_state()["contadores_dia"]["leidos_feed"] == 3
        # La segunda lectura le pasa al harvester los urns ya vistos para que corte antes
        assert h.conocidos[1] == {"urn:li:activity:1", "urn:li:activity:2"}
    finally:
        await radar.stop()


# ------------------------------------------------- circuito: captura → clasificación → análisis

@pytest.mark.asyncio
async def test_cycle_only_captures_and_never_classifies_inline(tmp_path):
    triage = FakeTriage()
    radar = make_radar(tmp_path, FakeHarvester({"q1": HarvestResult(posts=[raw(1)])}), triage=triage)
    try:
        await radar.run_cycle()
        assert [p["estado"] for p in list_posts()] == ["NUEVO"]
        assert triage.calls == []  # se clasifica en el worker de procesamiento, no en el ciclo
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_non_offer_posts_are_deleted_not_discarded(tmp_path):
    rec = Recorder()
    pid = insert_post_if_new(raw_feed(1, texto="Qué lindo día"), "feed", fuente="feed")
    radar = make_radar(tmp_path, triage=FakeTriage(es_busqueda=False), broadcast=rec)
    try:
        await radar.procesar_pendientes()
        assert list_posts() == [] and get_post(pid) is None
        assert ("radar_post_removed", {"id": pid}) in rec.events
    finally:
        await radar.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("alcance", ["ARGENTINA", "EXTERIOR_COMPATIBLE", "DESCONOCIDO"])
async def test_offer_is_analyzed_and_cv_waits_when_backlog_is_paused(tmp_path, alcance):
    update_radar_state(backlog_activo=False)
    pid = insert_post_if_new(raw(1), "q1")
    analyze = FakeAnalyze(score=70)
    radar = make_radar(tmp_path, triage=FakeTriage(alcance=alcance), analyze=analyze)
    try:
        await radar.procesar_pendientes()
        post = get_post(pid)
        assert post["estado"] == "ANALIZADA"
        assert post["analisis"]["match_score"] == 70 and post["triage"]["alcance"] == alcance
        assert analyze.calls == [pid]
        assert not radar._generation_event.is_set()  # el CV lo pide el usuario
        assert get_radar_state()["contadores_dia"]["match"] == 1
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_exterior_offer_is_discarded_and_not_analyzed(tmp_path):
    pid = insert_post_if_new(raw(1), "q1")
    analyze = FakeAnalyze()
    radar = make_radar(tmp_path, triage=FakeTriage(alcance="EXTERIOR"), analyze=analyze)
    try:
        await radar.procesar_pendientes()
        assert get_post(pid)["estado"] == "DESCARTADO" and analyze.calls == []
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_classification_uses_batches_of_15(tmp_path):
    for n in range(23):
        insert_post_if_new(raw(n), "q0")
    triage = FakeTriage(alcance="EXTERIOR")
    radar = make_radar(tmp_path, triage=triage)
    try:
        await radar.procesar_pendientes()
        assert [len(b) for b in triage.calls] == [15, 8]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_failed_batch_is_split_in_halves_not_post_by_post(tmp_path):
    ids = [insert_post_if_new(raw(n), "q0") for n in range(1, 5)]
    malo = ids[1]
    calls = []

    async def triage(posts):
        calls.append([p["id"] for p in posts])
        if any(p["id"] == malo for p in posts):
            raise TriageError("La llamada a Antigravity CLI excedió el tiempo límite")
        return await FakeTriage(alcance="EXTERIOR")(posts)

    radar = make_radar(tmp_path, triage=triage)
    try:
        await radar.procesar_pendientes()
        # [1,2,3,4] falla → mitades [1,2] y [3,4]; [1,2] falla → [1] y [2]; el malo solo, hasta ERROR
        assert [len(c) for c in calls[:5]] == [4, 2, 2, 1, 1]
        assert get_post(malo)["estado"] == "ERROR" and get_post(malo)["intentos"] == 3
        assert all(get_post(i)["estado"] == "DESCARTADO" for i in ids if i != malo)
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_halves_of_a_failed_batch_wait_behind_priorities_and_my_feed(tmp_path):
    globales = [insert_post_if_new(raw(n), "q1") for n in range(1, 5)]
    orden = []
    fallo = {"activo": True}

    async def triage(posts):
        orden.append(("clasificar", sorted(p["id"] for p in posts)))
        if fallo["activo"] and len(posts) == 4:
            fallo["activo"] = False
            # Mientras el lote fallaba, llegó un post de Mi feed
            insert_post_if_new(raw_feed(9), "feed", fuente="feed")
            raise TriageError("timeout")
        return await FakeTriage(alcance="EXTERIOR")(posts)

    radar = make_radar(tmp_path, triage=triage)
    try:
        await radar.procesar_pendientes()
        feed_id = list_posts(fuente="feed")[0]["id"]
        assert orden[0] == ("clasificar", sorted(globales))
        assert orden[1] == ("clasificar", [feed_id])  # Mi feed antes que las mitades del Feed general
        assert [len(ids) for _, ids in orden[2:]] == [2, 2]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_classification_failure_three_times_goes_error(tmp_path):
    pid = insert_post_if_new(raw(1), "q0")
    triage = FakeTriage(error=TriageError("respuesta malformada"))
    radar = make_radar(tmp_path, triage=triage)
    try:
        await radar.procesar_pendientes()
        p = get_post(pid)
        assert p["estado"] == "ERROR" and p["intentos"] == 3 and "respuesta malformada" in p["error"]
        assert len(triage.calls) == 3
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_missing_classification_result_counts_as_failure(tmp_path):
    pid = insert_post_if_new(raw(1), "q0")
    calls = []

    async def triage(posts):
        calls.append(1)
        return {}

    radar = make_radar(tmp_path, triage=triage)
    try:
        await radar._clasificar([get_post(pid)])
        p = get_post(pid)
        assert p["estado"] == "NUEVO" and p["intentos"] == 1
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_my_feed_is_classified_and_analyzed_before_the_general_feed(tmp_path):
    texto_largo_1 = (
        "Buscamos un Analista Funcional Sr para liderar el relevamiento y diseño de soluciones "
        "en proyectos bancarios y fintech de alta escala. Requisitos: 4 años de experiencia en "
        "análisis funcional, metodologías ágiles Scrum, redacción de historias de usuario, diagramas "
        "UML y consultas SQL. Modalidad de trabajo híbrida en CABA o remoto desde cualquier punto de Argentina. "
        "Se ofrecen excelentes condiciones salariales, prepaga de primer nivel y capacitaciones constantes."
    )
    texto_largo_2 = (
        "Oportunidad para Analista Funcional Semi Senior en empresa de desarrollo de software. "
        "Buscamos profesionales con experiencia en contacto con clientes, especificación de requerimientos "
        "técnicos y funcionales, pruebas de aceptación y coordinación con equipos de desarrollo y testing. "
        "Manejo de Jira, Confluence y bases de datos relacionales. Modalidad remota en Argentina con horario flexible. "
        "Interesados postularse indicando remuneración pretendida y disponibilidad para comenzar."
    )
    global_id = insert_post_if_new(raw(1, texto=texto_largo_1), "q1")
    feed_id = insert_post_if_new(raw_feed(2, texto=texto_largo_2), "feed", fuente="feed")
    orden = []
    triage, analyze = FakeTriage(), FakeAnalyze()

    async def triage_reg(posts):
        orden.append(("clasificar", [p["id"] for p in posts]))
        return await triage(posts)

    async def analyze_reg(post):
        orden.append(("analizar", post["id"]))
        return await analyze(post)

    radar = make_radar(tmp_path, triage=triage_reg, analyze=analyze_reg)
    try:
        await radar.procesar_pendientes()
        assert orden == [("clasificar", [feed_id]), ("analizar", feed_id),
                         ("clasificar", [global_id]), ("analizar", global_id)]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_analizar_button_puts_the_post_first(tmp_path):
    viejo = insert_post_if_new(raw_feed(1), "feed", fuente="feed")
    nuevo = insert_post_if_new(raw(2), "q1")
    for pid in (viejo, nuevo):
        update_post(pid, estado="OFERTA", triage={"es_busqueda_laboral": True, "alcance": "ARGENTINA", "puesto": "Analista Funcional"})
    analyze = FakeAnalyze()
    radar = make_radar(tmp_path, analyze=analyze)
    try:
        await radar.prioritize(nuevo)
        assert get_post(nuevo)["prioridad"] == 1
        await radar.procesar_pendientes()
        assert analyze.calls == [nuevo, viejo]
        assert get_post(nuevo)["prioridad"] == 0  # analizado: deja de ser prioritario
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_analysis_failure_retries_then_error_and_analizar_requeues(tmp_path):
    pid = insert_post_if_new(raw(1), "q1")
    update_post(pid, estado="OFERTA", triage={"es_busqueda_laboral": True, "alcance": "ARGENTINA", "puesto": "Analista Funcional"})
    analyze = FakeAnalyze(error=RuntimeError("el modelo no respondió un análisis válido"))
    radar = make_radar(tmp_path, analyze=analyze)
    try:
        await radar.procesar_pendientes()
        p = get_post(pid)
        assert p["estado"] == "ERROR" and p["intentos"] == 3 and "análisis" in p["error"]
        analyze.error = None
        await radar.prioritize(pid)
        await radar.procesar_pendientes()
        assert get_post(pid)["estado"] == "ANALIZADA"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_analizar_on_unclassified_post_classifies_then_analyzes(tmp_path):
    pid = insert_post_if_new(raw(1), "q1")
    update_post(pid, estado="ERROR", intentos=3, error="Triage falló")
    radar = make_radar(tmp_path)
    try:
        await radar.prioritize(pid)
        assert get_post(pid)["estado"] == "NUEVO"
        await radar.procesar_pendientes(todo=False)
        assert get_post(pid)["estado"] == "ANALIZADA"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_stopped_radar_processes_only_what_the_user_prioritized(tmp_path):
    pedido = insert_post_if_new(raw(1), "q1")
    otro = insert_post_if_new(raw(2), "q1")
    radar = make_radar(tmp_path)
    try:
        await radar.prioritize(pedido)  # radar detenido: el worker igual arranca para este post
        await wait_until(lambda: get_post(pedido)["estado"] == "ANALIZADA")
        await wait_until(lambda: radar._proc_task is None or radar._proc_task.done())
        assert get_post(otro)["estado"] == "NUEVO"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_running_radar_classifies_while_reading(tmp_path):
    h = FakeHarvester({"feed": HarvestResult(posts=[raw_feed(1)])})
    radar = make_radar(tmp_path, h, sleep=FakeSleep(block_at=600), queries=())
    try:
        await radar.start()
        await wait_until(lambda: [p["estado"] for p in list_posts()] == ["ANALIZADA"])
        assert radar._proc_task is not None and not radar._proc_task.done()  # espera más trabajo
    finally:
        await radar.stop()
    assert radar._proc_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["OFERTA", "ANALIZADA", "NUEVO", "ERROR", "DESCARTADO"])
async def test_generate_cv_is_the_users_decision(tmp_path, estado):
    pid = insert_post_if_new(raw(1), "q1")
    update_post(pid, estado=estado)
    radar = make_radar(tmp_path)
    try:
        await radar.generate_cv(pid)
        assert get_post(pid)["estado"] == "GENERANDO"
        assert radar._generation_event.is_set()
    finally:
        await radar.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["GENERANDO", "LISTO", "ENVIANDO", "ENVIADO", "ANALIZANDO"])
async def test_generate_cv_refused_when_it_has_cv_or_is_being_analyzed(tmp_path, estado):
    pid = insert_post_if_new(raw(1), "q1")
    update_post(pid, estado=estado)
    radar = make_radar(tmp_path)
    try:
        with pytest.raises(ValueError):
            await radar.generate_cv(pid)
        if estado not in ("ANALIZANDO", "LISTO"):  # una LISTO sin aprobar sí se puede volver a analizar
            with pytest.raises(ValueError):
                await radar.prioritize(pid)
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_classification_persists_canal_and_destination(tmp_path):
    pid = insert_post_if_new(raw_feed(1, whatsapp=["5491155550000"]), "feed", fuente="feed")
    radar = make_radar(tmp_path, triage=FakeTriage(canal="whatsapp", destino_canal="5491155550000"))
    try:
        await radar.procesar_pendientes()
        p = get_post(pid)
        assert p["canal"] == "whatsapp" and p["destino_canal"] == "5491155550000"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_radar_post_event_carries_analysis_match_score(tmp_path):
    rec = Recorder()
    insert_post_if_new(raw(1), "q1")
    radar = make_radar(tmp_path, broadcast=rec, analyze=FakeAnalyze(score=70))
    try:
        await radar.procesar_pendientes()
        ultimo = [p for n, p in rec.events if n == "radar_post"][-1]
        assert ultimo["estado"] == "ANALIZADA" and ultimo["match_score"] == 70
        assert ultimo["puesto"] == "Analista Funcional"
        assert set(ultimo) == {"id", "estado", "autor", "puesto", "match_score", "fuente", "canal"}
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_stop_returns_an_interrupted_analysis_to_the_queue(tmp_path):
    pid = insert_post_if_new(raw_feed(1), "feed", fuente="feed")
    gate = asyncio.Event()
    analyze = FakeAnalyze(gate=gate)  # nunca termina
    radar = make_radar(tmp_path, analyze=analyze, sleep=FakeSleep(block_at=600), queries=())
    try:
        await radar.start()
        await wait_until(lambda: get_post(pid)["estado"] == "ANALIZANDO")
    finally:
        await radar.stop()
    assert get_post(pid)["estado"] == "OFERTA"


# ------------------------------------------------- procesamiento en paralelo

@pytest.mark.asyncio
async def test_up_to_3_jobs_run_in_parallel_and_no_post_is_taken_twice(tmp_path):
    import os
    texto_largo = (
        "Buscamos un Analista Funcional con amplia experiencia en relevamiento de procesos "
        "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
        "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
        "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
        "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
    )
    # Aísla la concurrencia de análisis: el backlog activo puede avanzar a GENERANDO/LISTO.
    update_radar_state(backlog_activo=False)
    ids = [insert_post_if_new(raw(n, texto=f"{texto_largo} (aviso {n})"), "q1") for n in range(1, 6)]
    for pid in ids:
        update_post(pid, estado="OFERTA", prioridad=1,
                    triage={"es_busqueda_laboral": True, "alcance": "ARGENTINA", "puesto": "Analista Funcional"})
    gate = asyncio.Event()
    analyze = FakeAnalyze(gate=gate)
    radar = make_radar(tmp_path, analyze=analyze)
    try:
        radar._wake_processing()
        await wait_until(lambda: len(analyze.calls) >= 1)
        for _ in range(50):
            await asyncio.sleep(0)
        # invariante: ningún post se procesa dos veces a la vez (reservas atómicas)
        assert len(set(analyze.calls)) == len(analyze.calls)
        if os.environ.get("INVARIANT_CHECK") == "1":
            gate.set()
            return
        # defecto: con CONCURRENCIA_PROCESO=1 solo 1 análisis corre a la vez (hoy corren hasta 3)
        assert len(analyze.calls) == 1
        gate.set()
        await wait_until(lambda: all(get_post(i)["estado"] in ("ANALIZADA", "GENERANDO") for i in ids))
        assert sorted(analyze.calls) == sorted(ids)  # cada post una sola vez
    finally:
        gate.set()
        await radar.stop()


@pytest.mark.asyncio
async def test_stop_cancels_parallel_jobs_and_returns_analyses_to_the_queue(tmp_path):
    ids = [insert_post_if_new(raw(n), "q1") for n in range(1, 3)]
    for pid in ids:
        update_post(pid, estado="OFERTA", prioridad=1,
                    triage={"es_busqueda_laboral": True, "alcance": "ARGENTINA", "puesto": "Analista Funcional"})
    analyze = FakeAnalyze(gate=asyncio.Event())  # nunca termina
    radar = make_radar(tmp_path, analyze=analyze)
    radar._wake_processing()
    await wait_until(lambda: len(analyze.calls) >= 1)
    await radar.stop()
    assert all(get_post(i)["estado"] == "OFERTA" for i in ids)


@pytest.mark.asyncio
async def test_el_largo_de_cada_lectura_y_las_pausas_son_del_perfil(tmp_path, monkeypatch):
    """feed y búsquedas leen con el largo del perfil (uno solo para toda búsqueda, sin idioma)."""
    from tests import reglas_prueba
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(
        busquedas_por_hora=10**6, busquedas_por_dia=10**6, feed_posts=7, feed_scrolls=3, busqueda_posts=11,
        busqueda_scrolls=4, busqueda_pantallas_sin_nuevos=1, pausa_entre_vueltas_s=(60.0, 120.0)))
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=("q1",), queries_en=("en1",))
    try:
        await radar.run_cycle()
        assert ("feed", 7, 3, None) in h.params
        assert ("q1", 11, 4, 1) in h.params and ("en1", 11, 4, 1) in h.params
        assert all(60.0 <= radar._segundos_pausa() <= 120.0 for _ in range(20))
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_same_offer_reposted_with_another_urn_is_not_saved_twice(tmp_path):
    texto = "NUEVA BUSQUEDA LABORAL Para importante industria plástica VENDEDOR TECNICO. Enviar CV a rrhh@x.test"
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1, texto=texto), raw(2, texto=texto + "  "),
                                                   raw(3, texto="Otra búsqueda: Analista. rrhh@x.test")])})
    radar = make_radar(tmp_path, h, queries=("q1",))
    try:
        await radar.run_cycle()
        assert sorted(p["urn"] for p in list_posts()) == ["urn:li:activity:1", "urn:li:activity:3"]
    finally:
        await radar.stop()


# ----------------------------------------------------------------------- reparto de la cuota y prefiltro

from core import radar_store  # noqa: E402

@pytest.mark.asyncio
async def test_clearly_foreign_post_is_discarded_without_ai(tmp_path):
    texto = "We are hiring a Data Analyst in Pune! Send your CV to hr@acme.test.in #PuneJobs"
    h = FakeHarvester({"q1": HarvestResult(posts=[raw(1, texto=texto), raw(2)])})
    triage = FakeTriage()
    radar = make_radar(tmp_path, h, triage=triage, queries=("q1",))
    await radar.run_cycle()
    estados = {p["urn"]: p for p in radar_store.list_posts()}
    descartado = estados["urn:li:activity:1"]
    assert descartado["estado"] == "DESCARTADO" and descartado["triage"]["motivo_descarte"] == "prefiltro"
    assert estados["urn:li:activity:2"]["estado"] == "NUEVO"


@pytest.mark.asyncio
async def test_no_oferta_que_habla_de_una_busqueda_se_descarta_no_se_borra(tmp_path):
    """el clasificador borró ofertas reales; si el post habla de una búsqueda, queda DESCARTADO."""
    rec = Recorder()
    pid = insert_post_if_new(raw_feed(1, texto="¡BUSCAMOS ANALISTA FUNCIONAL! Sumate a nuestro equipo"), "feed",
                             fuente="feed")
    radar = make_radar(tmp_path, triage=FakeTriage(es_busqueda=False), broadcast=rec)
    try:
        await radar.procesar_pendientes()
        post = get_post(pid)
        assert post is not None and post["estado"] == "DESCARTADO"
        assert post["triage"]["motivo"] == "El clasificador no lo vio como oferta"
        assert ("radar_post_removed", {"id": pid}) not in rec.events
    finally:
        await radar.stop()


# ----------------------------------------------------------------------- español a fondo, inglés igual

@pytest.mark.asyncio
async def test_toda_busqueda_lee_a_fondo_y_guarda_todo_post_con_texto(tmp_path):
    from tests.reglas_prueba import REALES
    posts = [raw(n) for n in range(1, 31)] + [raw(n, emails=()) for n in range(31, 61)]
    h = FakeHarvester({"en1": HarvestResult(posts=posts)})
    radar = make_radar(tmp_path, h, queries=(), queries_en=("en1",))
    await radar.run_cycle()
    assert ("en1", REALES.busqueda_posts, REALES.busqueda_scrolls, REALES.busqueda_pantallas_sin_nuevos) in h.params
    # Sin pedido de email ni corte por idioma: entran los 60 (el triage decide qué es oferta)
    assert len(list_posts()) == 60
    assert get_radar_state()["contadores_dia"]["leidos_busquedas"] == 60


@pytest.mark.asyncio
async def test_processing_follows_the_profile_order_without_language(tmp_path):
    """priorizado, feed y después las búsquedas en el orden del perfil, sin distinguir idioma."""
    en_id = insert_post_if_new(raw(1, emails=("a@x.test",)), "en1")
    es_id = insert_post_if_new(raw(2, emails=("b@x.test",)), "q1")
    viejo_id = insert_post_if_new(raw(3, emails=("c@x.test",)), "ya no está en el perfil")
    orden = []
    triage, analyze = FakeTriage(), FakeAnalyze()

    async def triage_reg(posts):
        orden.append(("clasificar", [p["id"] for p in posts]))
        return await triage(posts)

    async def analyze_reg(post):
        orden.append(("analizar", post["id"]))
        return await analyze(post)

    # «en1» va primero en el perfil: se procesa antes aunque sea en inglés
    radar = make_radar(tmp_path, triage=triage_reg, analyze=analyze_reg, queries=("en1", "q1"))
    try:
        await radar.procesar_pendientes()
        assert orden == [("clasificar", [en_id]), ("analizar", en_id),
                         ("clasificar", [es_id]), ("analizar", es_id),
                         ("clasificar", [viejo_id]), ("analizar", viejo_id)]
    finally:
        await radar.stop()




@pytest.mark.asyncio
async def test_analizar_una_lista_sin_aprobar_conserva_su_cv_y_rehace_el_mensaje(tmp_path):
    pid = insert_post_if_new(raw(1), "q1")
    update_post(pid, estado="LISTO", triage={"puesto": "Analista"}, postulacion_id=7,
                email_subject="Analista", email_body="Hola")
    radar = make_radar(tmp_path)
    try:
        await radar.prioritize(pid)
        post = get_post(pid)
        assert post["estado"] == "OFERTA" and post["postulacion_id"] == 7
        assert post["email_subject"] == "" and post["email_body"] == ""  # la cola rehace el mensaje
        update_post(pid, estado="LISTO", aprobado_at="2026-11-21 10:00:00")
        with pytest.raises(ValueError):
            await radar.prioritize(pid)
    finally:
        await radar.stop()
