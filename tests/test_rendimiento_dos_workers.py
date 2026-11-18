"""Contrato C: reservas, controles e historial con proveedores sintéticos.

Plan de prueba: medir primero la base serial; cortar en INVARIANT_CHECK;
exigir después dos reservas productivas, cancelación aislada y cierre drenado.
Los plazos son guardas de bloqueo; las etapas se coordinan con Events.
"""
import asyncio
import itertools
import os
from collections import Counter, defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import database, progreso, radar_store as store
from core.bridge import AntigravityBridge
from core.mailer import GmailConfig
from core.radar import RadarLoop


# Solo una guarda contra bloqueos: las etapas se esperan por condición (Events y `_hasta`). Con la PC cargada
# (suite en paralelo, otras sesiones) el avance puede tardar más de 5 s sin que nada esté mal (12/11).
GUARDA = 60
_URNS = itertools.count(95000)
ENVIO_REAL = server._radar_send
ANALISIS = {
    "puesto": "Analista funcional", "empresa": "Empresa sintética",
    "match_score": 85,
    "requisitos": [{"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True}],
}


def _prohibido(*args, **kwargs):
    raise AssertionError("La prueba intentó usar un proveedor real")


@pytest.fixture(autouse=True)
def _sin_proveedores_reales(monkeypatch):
    monkeypatch.setattr(AntigravityBridge, "run_prompt", AsyncMock(side_effect=_prohibido))
    monkeypatch.setattr(server, "LinkedInHarvester", _prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP", _prohibido)


def _post(estado="GENERANDO", **campos):
    n = next(_URNS)
    pid = store.insert_post_if_new({
        "urn": f"urn:li:activity:{n}", "texto": (
            "Buscamos analista funcional para relevar necesidades de usuarios y documentar "
            "procesos de negocio. La persona trabajará con equipos de desarrollo y soporte "
            "en el análisis de incidentes, consultas SQL y validación de soluciones. Se requiere "
            "experiencia en especificaciones funcionales, seguimiento de requerimientos y "
            "pruebas de aceptación. Ofrecemos una jornada completa con modalidad híbrida, "
            "capacitación y participación en proyectos de mejora continua. Enviar CV al email "
            "de contacto indicando disponibilidad para una entrevista."
        ),
        "autor": "Empresa sintética", "emails": [f"rrhh{n}@example.com"],
    }, "prueba")
    # Un GENERANDO creado así es un encolado automático (1); el backlog se marca explícito (08/11).
    if estado == "GENERANDO":
        campos.setdefault("gen_prioridad", 1)
    store.update_post(pid, estado=estado, triage={"puesto": f"Puesto {pid}"}, **campos)
    return pid


def _listo():
    cv_id = database.create_postulacion(
        "Analista funcional", "Empresa sintética", "Oferta de prueba", status="READY")
    database.update_postulacion(cv_id, cv_markdown="# CV sintético")
    pid = _post("LISTO", postulacion_id=cv_id, email_to=f"rrhh{cv_id}@example.com",
                email_subject="Postulación", email_body="Hola, adjunto mi CV.")
    return pid, cv_id


class PipelineControlado:
    def __init__(self):
        self.llamadas = []
        self.activos = Counter()
        self.maximo = 0
        self.maximo_por_id = Counter()
        self.inicio = defaultdict(asyncio.Event)
        self.liberar = defaultdict(asyncio.Event)
        self.salida = defaultdict(asyncio.Event)
        self.cancelados = []
        self.tareas = []
        self.fallar = set()

    async def __call__(self, post):
        pid = post["id"]
        self.tareas.append(asyncio.current_task())
        self.llamadas.append(pid)
        self.activos[pid] += 1
        self.maximo_por_id[pid] = max(self.maximo_por_id[pid], self.activos[pid])
        self.maximo = max(self.maximo, sum(self.activos.values()))
        try:
            await progreso.reportar("redactar" if pid % 2 else "auditar", sub=f"post {pid}")
            self.inicio[pid].set()
            await self.liberar[pid].wait()
            if pid in self.fallar:
                self.fallar.remove(pid)
                raise RuntimeError("fallo sintético del pipeline")
            return 1000 + pid
        except asyncio.CancelledError:
            self.cancelados.append(pid)
            raise
        finally:
            self.activos[pid] -= 1
            self.salida[pid].set()


async def _compose(post, cv_id):
    return {"to": post["emails"][0], "subject": f"Postulación {post['id']}",
            "body": f"CV {cv_id}", "is_fallback": False}


def _loop(tmp_path, pipeline, *, productivo=False, monkeypatch=None, send=None):
    if productivo:
        monkeypatch.setattr(server, "_radar_pipeline", pipeline)
        monkeypatch.setattr(server, "_radar_compose", _compose)
        monkeypatch.setattr(server, "_radar_send", send or AsyncMock(side_effect=_prohibido))
        monkeypatch.setattr(server, "broadcast_event", AsyncMock())
        monkeypatch.setattr(server, "triage_batch", AsyncMock(side_effect=_prohibido))
        monkeypatch.setattr(server, "_radar_analyze", AsyncMock(side_effect=_prohibido))
        # Producción genera un CV por vez; estos tests ejercitan la capacidad de dos workers.
        monkeypatch.setattr(server, "GENERACIONES_EN_PARALELO", 2)
        loop = server._build_radar_loop()
        loop._debug_dir = tmp_path / "debug"
    else:
        # Base: constructor actual, sin generation_workers ni propiedades nuevas.
        loop = RadarLoop(
            harvester_factory=_prohibido, triage_fn=AsyncMock(side_effect=_prohibido),
            analyze_fn=AsyncMock(side_effect=_prohibido), pipeline_fn=pipeline,
            compose_fn=_compose, send_fn=send or AsyncMock(side_effect=_prohibido),
            broadcast=AsyncMock(), debug_dir=tmp_path / "debug")
    store.update_radar_state(backlog_activo=False)
    return loop


async def _hasta(predicado, mensaje):
    async def esperar():
        while not predicado():
            await asyncio.sleep(0.005)
    try:
        await asyncio.wait_for(esperar(), GUARDA)
    except TimeoutError:
        raise AssertionError(mensaje() if callable(mensaje) else mensaje) from None


async def _vueltas():
    for _ in range(30):
        await asyncio.sleep(0)


async def _dos(loop, pipeline, ids):
    await loop.wake_generation()
    await _hasta(lambda: all(pipeline.inicio[i].is_set() for i in ids),
                 lambda: f"Producción debe reservar dos posts distintos; inició {pipeline.llamadas}")
    assert {i for i, n in pipeline.activos.items() if n} == set(ids)
    assert loop.generando_ids == set(ids)


async def _limpiar(loop, pipeline=None):
    try:
        await asyncio.wait_for(loop.stop_for_shutdown(), GUARDA)
    finally:
        if pipeline:
            for tarea in pipeline.tareas:
                if not tarea.done():
                    tarea.cancel()
            await asyncio.gather(*pipeline.tareas, return_exceptions=True)


async def _base_serial(tmp_path):
    a, b = _post(), _post()
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline)
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.inicio[a].wait(), GUARDA)
        await asyncio.gather(*(loop.wake_generation() for _ in range(8)))
        await _vueltas()
        # invariante: default serial y wakes idempotentes, medidos en la base actual.
        assert pipeline.llamadas == [a]
        assert pipeline.maximo == 1
        assert loop.generando_ahora == a
        pipeline.liberar[a].set()
        pipeline.liberar[b].set()
        await _hasta(lambda: store.get_post(b)["estado"] == "LISTO", "No drenó la cola serial")
        assert pipeline.llamadas == [a, b]
        assert all(store.get_post(i)["aprobado_at"] is None for i in (a, b))
    finally:
        await _limpiar(loop, pipeline)


async def _base_pausa(tmp_path):
    a = _post(gen_prioridad=2)
    b = _post(gen_prioridad=1, intentos=1)
    backlog = _post("ANALIZADA", analisis={"match_score": 90})
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline)
    store.update_radar_state(backlog_activo=True)
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.inicio[a].wait(), GUARDA)
        store.update_radar_state(generacion_pausada=True)
        pipeline.liberar[a].set()
        await _hasta(lambda: store.get_post(a)["estado"] == "LISTO", "La pausa cortó el CV activo")
        await _vueltas()
        # invariante: pausa conserva pendientes, intentos y backlog; no aprueba contenido nuevo.
        assert pipeline.llamadas == [a]
        assert store.get_post(b)["estado"] == "GENERANDO"
        assert store.get_post(b)["intentos"] == 1
        assert store.get_post(backlog)["estado"] == "ANALIZADA"
        assert store.get_post(a)["aprobado_at"] is None
        store.update_radar_state(generacion_pausada=False)
        pipeline.liberar[b].set()
        pipeline.liberar[backlog].set()
        await loop.wake_generation()
        await _hasta(lambda: store.get_post(backlog)["estado"] == "LISTO", "No reanudó el backlog")
        assert pipeline.llamadas == [a, b, backlog]
    finally:
        await _limpiar(loop, pipeline)


async def _base_fallback(tmp_path, monkeypatch):
    """Una generación del CV que falla (desde el maestro, 14/11) corta: el post queda en ERROR con el motivo, sin
    componer el mail ni marcar nada como listo."""
    with monkeypatch.context() as m:
        from core.titulo_cv import DecisionTitulo
        generar = AsyncMock(side_effect=RuntimeError("timeout sintético"))
        completar, compose = AsyncMock(), AsyncMock()
        m.setattr(server.cv_maestro, "FlujoCV", lambda **kw: SimpleNamespace(generar=generar, segundos={}))
        m.setattr(server, "asegurar_anios_por_area", AsyncMock(return_value=[]))
        m.setattr(server, "decidir_titulo", AsyncMock(return_value=DecisionTitulo(
            caso=1, clave=None, titulo="Analista", roles_reales=(), puesto_oferta="Analista")))
        m.setattr(server, "_asegurar_completo", completar)
        m.setattr(server, "compose_message", compose)
        eventos = []

        async def broadcast(tipo, datos):
            eventos.append((tipo, dict(datos)))

        m.setattr(server, "broadcast_event", broadcast)
        pid = _post(analisis=dict(ANALISIS))
        loop = _loop(tmp_path, server._radar_pipeline)
        loop._compose_fn = server._radar_compose
        try:
            await loop.wake_generation()
            await _hasta(lambda: store.get_post(pid)["estado"] == "ERROR", "La falla no cortó")
            generar.assert_awaited_once()
            completar.assert_not_awaited()
            compose.assert_not_awaited()
            assert "timeout sintético" in store.get_post(pid)["error"]  # el motivo exacto llega al Radar
            assert all(t != "complete" and d.get("status") != "READY" for t, d in eventos)
        finally:
            await _limpiar(loop)


def _cuota_controlada(loop):
    reloj = [0.0]
    cuota = {"agotada": True, "renueva": "2026-11-08T10:00:00"}
    loop._reloj = lambda: reloj[0]
    loop._cuota = lambda: dict(cuota)
    loop._plazo_trabajo_s = 10
    loop._tick_plazo_s = 0.001
    return reloj, cuota


async def _espera_cuota_sin_plazo(pipeline, ids, reloj, cuota):
    await _hasta(lambda: all((store.get_post(i)["progreso"] or {}).get("esperando_cuota") for i in ids),
                 "No expuso la espera de cuota de cada trabajo")
    reloj[0] = 1000.0  # Más que el plazo; sigue siendo espera, no trabajo efectivo.
    cuota["renueva"] = "2026-11-09T10:00:00"
    await _hasta(lambda: all((store.get_post(i)["progreso"] or {}).get("cuota_renueva") == cuota["renueva"]
                            for i in ids), "No observó la cuota posterior al avance del reloj")
    # invariante: base actual no carga espera de cuota al plazo ni a los intentos.
    assert pipeline.cancelados == []
    assert all(store.get_post(i)["intentos"] == 0 for i in ids)
    assert all(store.get_post(i)["progreso"]["activo_s"] == 0 for i in ids)
    cuota["agotada"] = False
    await _hasta(lambda: all(not (store.get_post(i)["progreso"] or {}).get("esperando_cuota") for i in ids),
                 "No retomó después de la cuota")


async def _base_cuota(tmp_path):
    pid = _post()
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline)
    reloj, cuota = _cuota_controlada(loop)
    try:
        await loop.wake_generation()
        await asyncio.wait_for(pipeline.inicio[pid].wait(), GUARDA)
        await _espera_cuota_sin_plazo(pipeline, (pid,), reloj, cuota)
        pipeline.liberar[pid].set()
        await _hasta(lambda: store.get_post(pid)["estado"] == "LISTO", "Cuota serial perdió el trabajo")
        # invariante: esperar cuota no dispara reintentos físicos.
        assert pipeline.llamadas == [pid]
    finally:
        await _limpiar(loop, pipeline)


async def _envio_seguro(tmp_path, monkeypatch, loop=None):
    """Mailer y worker reales; SMTP/PDF sintéticos locales, sin probar paginado."""
    with monkeypatch.context() as m:
        eventos, entregas = [], []
        destino_actual = {}
        config = GmailConfig("candidato@example.com", "clave-sintetica")
        m.setattr(server, "load_gmail_config", lambda: config)
        m.setattr("core.radar_api.load_gmail_config", lambda: config)
        m.setattr(server, "load_candidate_profile", lambda: {"nombre": "Candidato sintético"})

        def pdf(*args, **kwargs):
            ruta = tmp_path / "adjunto-sintetico.pdf"
            ruta.write_bytes(b"%PDF-1.4\nprueba de transporte")
            return ruta

        m.setattr(server, "compile_markdown_to_pdf", pdf)

        class SMTP:
            def __init__(self, *a, **k):
                pass

            def login(self, *a):
                eventos.append("login")
                if destino_actual["cambio"] == "revocar":
                    store.update_post(destino_actual["post"], aprobado_at=None)
                elif destino_actual["cambio"] == "cv":
                    database.update_postulacion(destino_actual["cv"], cv_markdown="# CV cambiado durante login")

            def send_message(self, mensaje):
                eventos.append("entrega")
                entregas.append(mensaje)

            def quit(self):
                return 221, b"bye"

            def close(self):
                pass

        m.setattr("core.mailer.smtplib.SMTP_SSL", SMTP)
        enviar_real = server.send_application_email

        def enviar(*args, **kwargs):
            validar_real = kwargs["before_send"]

            def validar():
                eventos.append("validacion")
                validar_real()

            kwargs["before_send"] = validar
            return enviar_real(*args, **kwargs)

        m.setattr(server, "send_application_email", enviar)
        propio = loop is None
        if propio:
            loop = _loop(tmp_path, AsyncMock(side_effect=_prohibido), send=ENVIO_REAL)
        m.setattr(loop, "_send_fn", ENVIO_REAL)
        m.setattr(server, "radar_loop", loop)
        client = TestClient(server.app)  # Sin lifespan: no inicia workers ni navegador.
        try:
            pid, _ = _listo()
            enviados_base = store.get_radar_state()["contadores_dia"]["enviados"]
            for payload, codigo in (({}, 400), ({"updated_at": "2000-01-01 00:00:00"}, 409)):
                respuesta = client.post(f"/api/radar/posts/{pid}/approve", json=payload)
                # invariante: aprobar sin versión o con versión vieja no autoriza ningún envío.
                assert respuesta.status_code == codigo
                assert store.get_post(pid)["aprobado_at"] is None
            assert await loop._procesar_envio(pid) is False
            assert entregas == [] and eventos == []
            assert store.get_post(pid)["sent_at"] is None
            assert store.get_post(pid)["message_id"] is None
            assert store.get_radar_state()["contadores_dia"]["enviados"] == enviados_base
            for cambio in ("revocar", "cv", "valido"):
                pid, cv_id = _listo()
                destino_actual.update(post=pid, cv=cv_id, cambio=cambio)
                store.update_post(pid, aprobado_at="2026-11-07 10:00:00")
                eventos.clear()
                entregas.clear()
                resultado = await loop._procesar_envio(pid)
                post = store.get_post(pid)
                # invariante: login < revalidación < entrega; cambios en login entregan cero.
                assert eventos[:2] == ["login", "validacion"]
                if cambio == "valido":
                    assert resultado is True
                    assert eventos == ["login", "validacion", "entrega"]
                    assert len(entregas) == 1
                    assert post["estado"] == "ENVIADO" and post["sent_at"] and post["message_id"]
                    assert store.get_radar_state()["contadores_dia"]["enviados"] == enviados_base + 1
                else:
                    assert resultado is False and entregas == []
                    assert post["estado"] == "LISTO" and post["aprobado_at"] is None
                    assert post["sent_at"] is None and post["message_id"] is None
                    assert store.get_radar_state()["contadores_dia"]["enviados"] == enviados_base
        finally:
            client.close()
            if propio:
                await _limpiar(loop)


@pytest.mark.asyncio
async def test_invariantes_pausa_fallback_y_envio(tmp_path, monkeypatch):
    await _base_pausa(tmp_path)
    await _base_fallback(tmp_path, monkeypatch)
    await _envio_seguro(tmp_path, monkeypatch)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: los mismos controles de envío siguen vigentes con dos CV retenidos.
    a, b = _post(), _post()
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline, productivo=True, monkeypatch=monkeypatch)
    try:
        await _dos(loop, pipeline, (a, b))
        await _envio_seguro(tmp_path, monkeypatch, loop)
        assert loop.generando_ids == {a, b}
        assert pipeline.cancelados == []
    finally:
        await _limpiar(loop, pipeline)


@pytest.mark.asyncio
async def test_dos_reservas_y_wakes_sin_duplicacion(tmp_path, monkeypatch):
    await _base_serial(tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: prioridad explícita antes del backlog; éste respeta score descendente.
    tercero = _post(gen_prioridad=1)
    a, b = _post(gen_prioridad=2), _post(gen_prioridad=2)
    bajo = _post("ANALIZADA", analisis={"match_score": 80})
    alto = _post("ANALIZADA", analisis={"match_score": 95})
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline, productivo=True, monkeypatch=monkeypatch)
    store.update_radar_state(backlog_activo=True)
    try:
        await asyncio.gather(*(loop.wake_generation() for _ in range(12)))
        await _dos(loop, pipeline, (a, b))
        assert store.get_post(a)["progreso"]["sub"] == f"post {a}"
        assert store.get_post(b)["progreso"]["sub"] == f"post {b}"
        representante = loop.generando_ahora
        copia = loop.generando_ids
        copia.clear()
        assert loop.generando_ids == {a, b}
        await asyncio.gather(*(loop.wake_generation() for _ in range(12)))
        await _vueltas()
        assert loop.generando_ahora == representante and representante in {a, b}
        assert pipeline.llamadas == [a, b]
        assert not pipeline.inicio[tercero].is_set()
        pipeline.liberar[a].set()
        await asyncio.wait_for(pipeline.inicio[tercero].wait(), GUARDA)
        assert pipeline.llamadas == [a, b, tercero]
        pipeline.liberar[tercero].set()
        await asyncio.wait_for(pipeline.inicio[alto].wait(), GUARDA)
        assert pipeline.llamadas == [a, b, tercero, alto]
        pipeline.liberar[alto].set()
        await asyncio.wait_for(pipeline.inicio[bajo].wait(), GUARDA)
        pipeline.liberar[bajo].set()
        pipeline.liberar[b].set()
        await _hasta(lambda: all(store.get_post(i)["estado"] == "LISTO"
                                for i in (a, b, tercero, alto, bajo)), "Se perdió un post reservado")
        assert pipeline.llamadas == [a, b, tercero, alto, bajo]
        assert pipeline.maximo == 2
        assert all(n == 1 for n in pipeline.maximo_por_id.values())
        assert all(store.get_post(i)["aprobado_at"] is None for i in pipeline.llamadas)
    finally:
        await _limpiar(loop, pipeline)


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["pausa", "cuota", "descartar", "shutdown", "stop"])
async def test_pausa_cancelacion_y_stop_con_dos(tmp_path, monkeypatch, control):
    await _base_pausa(tmp_path)
    if control == "cuota":
        await _base_cuota(tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    a, b, c = _post(), _post(), _post()
    backlog = _post("ANALIZADA", analisis={"match_score": 85})
    pipeline = PipelineControlado()
    loop = _loop(tmp_path, pipeline, productivo=True, monkeypatch=monkeypatch)
    store.update_radar_state(backlog_activo=True)
    if control == "cuota":
        reloj, cuota = _cuota_controlada(loop)
    try:
        await _dos(loop, pipeline, (a, b))
        if control == "cuota":
            await _espera_cuota_sin_plazo(pipeline, (a, b), reloj, cuota)
            assert pipeline.llamadas == [a, b]
            for i in (a, b, c, backlog):
                pipeline.liberar[i].set()
            await _hasta(lambda: all(store.get_post(i)["estado"] == "LISTO" for i in (a, b, c, backlog)),
                         "Cuota con dos trabajos perdió una reserva")
            assert pipeline.llamadas == [a, b, c, backlog]
            assert pipeline.cancelados == []
        elif control == "pausa":
            store.update_radar_state(generacion_pausada=True)
            pipeline.liberar[a].set()
            pipeline.liberar[b].set()
            await _hasta(lambda: all(store.get_post(i)["estado"] == "LISTO" for i in (a, b)),
                         "La pausa interrumpió trabajos activos")
            await asyncio.gather(*(loop.wake_generation() for _ in range(5)))
            await _vueltas()
            assert pipeline.llamadas == [a, b] and pipeline.cancelados == []
            assert store.get_post(c)["estado"] == "GENERANDO"
            assert store.get_post(backlog)["estado"] == "ANALIZADA"
            store.update_radar_state(generacion_pausada=False)
            pipeline.liberar[c].set()
            pipeline.liberar[backlog].set()
            await loop.wake_generation()
            await _hasta(lambda: store.get_post(backlog)["estado"] == "LISTO", "No reanudó dos plazas")
            assert pipeline.llamadas == [a, b, c, backlog]
        elif control == "descartar":
            store.update_post(a, estado="DESCARTADO", aprobado_at=None)
            assert await loop.cancelar_generacion(a) is True
            await asyncio.wait_for(pipeline.inicio[c].wait(), GUARDA)
            assert pipeline.cancelados == [a]
            assert pipeline.activos[b] == 1 and not pipeline.salida[b].is_set()
            assert await loop.cancelar_generacion(a) is False
            assert await loop.cancelar_generacion(backlog) is False
            for i in (b, c, backlog):
                pipeline.liberar[i].set()
            await _hasta(lambda: store.get_post(backlog)["estado"] == "LISTO", "Cancelar frenó al hermano")
            assert store.get_post(a)["estado"] == "DESCARTADO"
            assert all(store.get_post(i)["estado"] == "LISTO" for i in (b, c, backlog))
            assert pipeline.llamadas == [a, b, c, backlog]
        else:
            originales = list(pipeline.tareas)
            # La pausa permite inspeccionar el retorno de stop sin su reanudación inmediata.
            if control == "stop":
                store.update_radar_state(generacion_pausada=True, desired_running=True)
                await asyncio.wait_for(loop.stop(), GUARDA)
            else:
                store.update_radar_state(desired_running=True)
                await asyncio.wait_for(loop.stop_for_shutdown(), GUARDA)
            assert all(t.done() for t in originales)
            assert set(pipeline.cancelados) == {a, b}
            assert loop.generando_ids == set() and loop.etapas_generacion == {}
            assert all(store.get_post(i)["estado"] == "GENERANDO" for i in (a, b, c))
            assert all(store.get_post(i)["progreso"] is None for i in (a, b))
            assert all(store.get_post(i)["intentos"] == 0 for i in (a, b, c))
            assert store.get_radar_state()["desired_running"] is (control == "shutdown")
            await loop.wake_generation()
            await _vueltas()
            assert pipeline.llamadas == [a, b]
            # shutdown no relanza; stop sí conserva el wake/reanudación con Radar detenido.
            store.update_radar_state(desired_running=False, generacion_pausada=False)
            for i in (a, b, c, backlog):
                pipeline.liberar[i].set()
            if control == "shutdown":
                await loop.resume_if_needed()
            else:
                await loop.wake_generation()
            await _hasta(lambda: store.get_post(backlog)["estado"] == "LISTO", "El cierre perdió reservas")
            assert Counter(pipeline.llamadas) == Counter({a: 2, b: 2, c: 1, backlog: 1})
        assert pipeline.maximo == 2
        assert all(n == 1 for n in pipeline.maximo_por_id.values())
    finally:
        await _limpiar(loop, pipeline)


def test_historial_expone_dos_y_excluye_activos(monkeypatch):
    a, b = _post(gen_prioridad=2), _post(gen_prioridad=2)
    c, d = _post(gen_prioridad=1), _post(gen_prioridad=0)
    prog_a = {"inicio": "2026-11-07T10:00:00", "paso": "redactar", "pasos": []}
    prog_b = {"inicio": "2026-11-07T10:01:00", "paso": "auditar", "pasos": []}
    store.update_post(a, progreso=prog_a)
    store.update_post(b, progreso=prog_b)
    antiguo = SimpleNamespace(generando_ahora=a, en_vuelo=set())
    monkeypatch.setattr(server, "radar_loop", antiguo)
    client = TestClient(server.app)
    try:
        respuesta = client.get("/api/radar/procesos")
        data = respuesta.json()
        # invariante: JSON y consumidor escalar heredados mantienen su contrato.
        assert respuesta.status_code == 200
        assert set(data) == {"en_curso", "cola_generacion", "cola_analisis", "backlog", "colas", "errores", "terminados", "topes", "cuota", "agy"}
        assert 1 <= data["agy"]["limite"] <= 2
        assert [p["post_id"] for p in data["en_curso"]] == [a]
        assert [p["post_id"] for p in data["cola_generacion"]] == [b, c, d]
        assert data["en_curso"][0]["progreso"] == prog_a
        if os.environ.get("INVARIANT_CHECK") == "1":
            return

        # defecto: colección completa, progreso por post y ninguna reserva en cola.
        antiguo.generando_ids = {a, b}
        respuesta = client.get("/api/radar/procesos")
        assert respuesta.status_code == 200
        data = respuesta.json()
        activos = {p["post_id"]: p for p in data["en_curso"]}
        assert set(activos) == {a, b}, "El historial debe listar ambas generaciones activas"
        assert activos[a]["progreso"] == prog_a and activos[b]["progreso"] == prog_b
        assert all(p["tipo"] == "generacion" and p["posicion"] is None for p in activos.values())
        assert [p["post_id"] for p in data["cola_generacion"]] == [c, d]
        assert [p["posicion"] for p in data["cola_generacion"]] == [1, 2]
    finally:
        client.close()


@pytest.mark.asyncio
async def test_error_reinicio_y_backlog_no_pierden_reserva(tmp_path, monkeypatch):
    # invariante: error de pipeline serial no pierde el siguiente post.
    a, b = _post(), _post()
    base = PipelineControlado()
    base.fallar.add(a)
    base.liberar[a].set()
    base.liberar[b].set()
    loop = _loop(tmp_path, base)
    try:
        await loop.wake_generation()
        await _hasta(lambda: store.get_post(b)["estado"] == "LISTO", "Error serial perdió la cola")
        assert base.llamadas == [a, b]
        assert store.get_post(a)["estado"] == "ERROR" and store.get_post(a)["intentos"] == 1
        assert "fallo sintético" in store.get_post(a)["error"]
    finally:
        await _limpiar(loop, base)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # La falla anterior queda fuera de este backlog sintético.
    store.update_post(a, estado="DESCARTADO")
    a, b, c = _post(), _post(), _post()
    bajo = _post("ANALIZADA", analisis={"match_score": 80})
    alto = _post("ANALIZADA", analisis={"match_score": 95})
    pipeline = PipelineControlado()
    pipeline.fallar.add(a)
    loop = _loop(tmp_path, pipeline, productivo=True, monkeypatch=monkeypatch)
    store.update_radar_state(backlog_activo=True)
    monkeypatch.setattr("core.radar.REINICIO_WORKER_S", 0)
    emitir_real = loop._emitir_post
    fallo = asyncio.Event()

    async def emitir(pid):
        if pid == a and store.get_post(a)["estado"] == "ERROR" and not fallo.is_set():
            fallo.set()
            raise RuntimeError("fallo inesperado publicando el error")
        await emitir_real(pid)

    monkeypatch.setattr(loop, "_emitir_post", emitir)
    try:
        await _dos(loop, pipeline, (a, b))
        pipeline.liberar[a].set()
        await asyncio.wait_for(fallo.wait(), GUARDA)
        # defecto: supervisión automática sin un wake de rescate, reserva exclusiva y backlog ordenado.
        await asyncio.wait_for(pipeline.inicio[c].wait(), GUARDA)
        assert store.get_post(b)["estado"] == "GENERANDO"
        for i in (b, c, alto, bajo):
            pipeline.liberar[i].set()
        await _hasta(lambda: all(store.get_post(i)["estado"] == "LISTO" for i in (b, c, alto, bajo)),
                     "La supervisión dejó una reserva o el backlog varados")
        assert store.get_post(a)["estado"] == "ERROR" and store.get_post(a)["intentos"] == 1
        assert pipeline.llamadas.index(c) < pipeline.llamadas.index(alto) < pipeline.llamadas.index(bajo)
        assert all(n == 1 for n in pipeline.maximo_por_id.values())
        assert pipeline.maximo == 2
        await loop.retry(a)
        await _hasta(lambda: store.get_post(a)["estado"] == "LISTO", "No pudo reintentar la reserva fallida")
        assert pipeline.llamadas.count(a) == 2
        await _hasta(lambda: not loop.generando_ids, "No liberó las reservas terminadas")
        assert loop.generando_ids == set()
    finally:
        await _limpiar(loop, pipeline)
