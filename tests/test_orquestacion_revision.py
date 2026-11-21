"""Regresiones del cierre S4: procesos falsos y datos aislados."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from core import bridge, radar_store as store
from core.prioridades import prioridad_rol
from tests.test_radar_workers import FIXED_NOW, TEXTO_SUFICIENTE, FakePipeline, make_radar, wait_until
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI


@pytest.mark.asyncio
async def test_503_textual_activa_freno_sin_repetir(monkeypatch, tmp_path):
    exe = tmp_path / 'falso.exe'
    exe.write_text('sin ejecución', encoding='utf-8')
    calls = []

    async def crear(*args, **kwargs):
        calls.append(args)
        async def communicate():
            return b'503 Service Unavailable', b''
        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(bridge, '_crear_proceso', crear)
    monkeypatch.setattr(bridge, '_cerrar_proceso', lambda proc: None)
    clock = 1_900_000_000.0
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(time=lambda: clock, monotonic=lambda: clock))
    bridge.CUOTA.restablecida()
    store.update_radar_state(modo_serie=False, agy_serie_hasta=None, agy_historial=[])
    with bridge.etapa('ocr') as telemetry:
        with pytest.raises(RuntimeError, match='503'):
            await asyncio.wait_for(bridge.AntigravityBridge(agy_path=exe).run_prompt('OCR', json_mode=False), 60)
    state = bridge.estado_admision()
    assert len(calls) == 1 and telemetry['llamadas'] == 1
    assert state['limite'] == 1 and state['serie_hasta'] == clock + 900
    assert [e['motivo'] for e in state['historial']] == ['Saturación AGY (503)']
    assert bridge.CUOTA.estado()['agotada'] is False


def test_texto_normal_que_menciona_503_no_es_error():
    assert bridge.texto_de_error('Gestioné incidentes de HTTP 503 en producción.', '', 0, False) == ''


@pytest.mark.parametrize('role,group', [
    ('Ingeniero civil', 'c'), ('Arquitecto de obra', 'c'), ('Personal de seguridad', 'c'),
    ('Security Guard', 'c'), ('Técnico informático', 'b'), ('Ingeniero de sistemas', 'b'),
    ('Software Engineer', 'b'), ('Arquitecto de software', 'b'),
    ('Analista de seguridad informática', 'b'), ('Operador NOC', 'a'), ('Soporte N2', 'a'),
])
def test_roles_con_contexto_informatico(role, group):
    assert prioridad_rol(role) == group


def post(i, *, state='OFERTA', role='QA', email=False, origin='busqueda', priority=0):
    pid = store.insert_post_if_new({'urn': f'urn:revision:{i}', 'texto': TEXTO_SUFICIENTE,
        'emails': ['rrhh@example.test'] if email else []}, 'prueba')
    store.update_post(pid, estado=state, fuente=origin, prioridad=priority,
        created_at=(FIXED_NOW + timedelta(seconds=i)).isoformat(),
        triage={'puesto': role}, analisis={'puesto': role, 'match_score': 90})
    return pid


@pytest.mark.asyncio
async def test_503_no_reinicia_cv_desde_supervisor_ni_backlog(tmp_path):
    calls = []
    async def pipeline(p):
        calls.append(p['id'])
        raise RuntimeError('503 Service Unavailable')
    radar = make_radar(tmp_path, pipeline=pipeline, umbral=70)
    pid = post(1, state='ANALIZADA', role='Business Analyst')
    store.update_radar_state(backlog_activo=True, generacion_pausada=False,
                             envios_pausados=True, analisis_pausado=True)
    try:
        await radar.wake_generation()
        await wait_until(lambda: radar._gen_task.done())
        assert calls == [pid]
        failed = store.get_post(pid)
        assert failed['estado'] == 'ERROR' and failed['intentos'] == 1
        assert '503' in failed['error'] and failed['aprobado_at'] is None
        assert store.backlog_pendiente() == []
        await radar.wake_generation()
        await wait_until(lambda: radar._gen_task.done())
        assert calls == [pid], 'Un nuevo wake no debe reiniciar el CV'
    finally:
        await radar.stop_for_shutdown()


def test_error_503_guardado_no_vuelve_al_backlog():
    pid = post(1, state='ERROR')
    store.update_post(pid, intentos=1, error='Falló la generación: 503 Service Unavailable')
    assert store.backlog_pendiente() == []
    # Los errores comunes mantienen el reintento previo.
    store.update_post(pid, error='Falló la generación: timeout')
    assert [p['id'] for p in store.backlog_pendiente()] == [pid]


@pytest.mark.asyncio
async def test_reintento_explicito_de_saturacion_conserva_el_flujo_cv(tmp_path):
    pipeline = FakePipeline()
    radar = make_radar(tmp_path, pipeline=pipeline)
    pid = post(1, state='ERROR')
    store.update_post(pid, error='Generación detenida por saturación AGY (503): sin capacidad')
    store.update_radar_state(analisis_pausado=True, generacion_pausada=False, envios_pausados=True)
    try:
        await radar.retry(pid)
        assert store.get_post(pid)['estado'] in ('GENERANDO', 'LISTO')
        await wait_until(lambda: store.get_post(pid)['estado'] == 'LISTO')
        assert pipeline.calls == [pid] and store.get_post(pid)['aprobado_at'] is None
    finally:
        await radar.stop_for_shutdown()


def test_panel_publica_orden_real_del_scheduler(monkeypatch, tmp_path):
    radar = make_radar(tmp_path)
    store.update_radar_state(analisis_pausado=False)
    ids = [post(1, role='Vendedor', email=True), post(2, role='QA'),
           post(3, role='Soporte N2', email=True), post(4, role='QA', email=True),
           post(5, role='Vendedor', origin='feed'), post(6, role='QA', priority=1)]
    monkeypatch.setattr(server, 'radar_loop', radar)
    client = TestClient(server.app)
    try:
        response = client.get('/api/radar/procesos')
        assert response.status_code == 200
        visible = [p['post_id'] for p in response.json()['cola_analisis']]
        selected = []
        for _ in ids:
            action, batch = radar._siguiente_trabajo(True)
            assert action == 'analizar' and len(batch) == 1
            selected.append(batch[0]['id'])
            radar._en_vuelo.add(batch[0]['id'])
        assert set(selected) == set(ids)
        assert visible == selected
    finally:
        client.close()
