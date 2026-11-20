"""Colas dinámicas de generación: prioritaria ≤ 5 días (grupo a/b), de fondo hasta 21,
orden recalculado en cada turno y nada se marca GENERANDO antes de que le toque."""
from datetime import datetime, timedelta, timezone

import pytest

from core import colas, radar, radar_store as store
from tests.test_radar_workers import FakePipeline, crear_post, esperar_workers, finalizar, make_radar
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI

AHORA = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
TEXTO = "Buscamos Analista Funcional. " + "Detalle de la oferta. " * 30


def post(pid, dias=0.0, puesto="Analista Funcional", score=80, email=True, fecha_rel="", **extra):
    leida = AHORA - timedelta(days=dias)
    p = {"id": pid, "texto": TEXTO, "triage": {"puesto": puesto}, "analisis": {"match_score": score},
         "created_at": leida.strftime("%Y-%m-%d %H:%M:%S"), "fecha_rel": fecha_rel, "estado": "ANALIZADA",
         "emails": ["rrhh@example.com"] if email else [], "links": [] if email else ["https://example.com/job"]}
    p.update(extra)
    return p


@pytest.mark.parametrize("rel,horas", [("3 h", 3), ("2 días", 48), ("1 día", 24), ("1 sem", 168), ("45 min", 0.75),
                                       ("2d", 48), ("1w", 168), ("", 0), ("hace un rato", 0)])
def test_publicada_resta_la_fecha_relativa(rel, horas):
    p = post(1, fecha_rel=rel)
    assert colas.edad_dias(p, AHORA) == pytest.approx(horas / 24)


def test_niveles():
    assert colas.nivel(post(1, dias=0.5), AHORA) == colas.PRIORITARIA
    assert colas.nivel(post(2, dias=14), AHORA) == colas.PRIORITARIA
    assert colas.nivel(post(3, dias=14.1), AHORA) == colas.FONDO         # pasó la ventana de 14 días
    assert colas.nivel(post(4, puesto="Repositor"), AHORA) == colas.FONDO  # grupo c
    assert colas.nivel(post(5, dias=21), AHORA) == colas.FONDO
    assert colas.nivel(post(6, dias=21.1), AHORA) == colas.FONDO          # sin tope de antigüedad
    assert colas.nivel(post(7, score=70), AHORA) == colas.FONDO           # sin umbral de afinidad
    assert colas.nivel(post(11, analisis={"match_score": 90, "oferta_cerrada": True}), AHORA) == colas.FONDO
    assert colas.nivel(post(8, fecha_rel="1 sem"), AHORA) == colas.PRIORITARIA  # publicada hace 7 días
    assert colas.nivel(post(10, dias=10, fecha_rel="1 sem"), AHORA) == colas.FONDO  # 17 días
    assert colas.nivel(post(9, score=10, dias=40, gen_prioridad=2), AHORA) == colas.PRIORITARIA  # forzada


def test_orden_prioritaria_reciente_email_grupo_score():
    posts = [
        post(1, dias=2, score=99),                       # más vieja, aunque mejor score
        post(2, dias=0.2, score=75, email=False),        # hoy, solo link
        post(3, dias=0.3, score=72, puesto="Desarrollador"),  # hoy, email, grupo b
        post(4, dias=0.4, score=71),                     # hoy, email, grupo a
        post(5, dias=0.1, score=90),                     # hoy, email, grupo a, más score
    ]
    assert colas.nivel(posts[2], AHORA) == colas.PRIORITARIA, "Desarrollador es grupo b"
    orden = [p["id"] for p in colas.armar_colas(posts, AHORA)[colas.PRIORITARIA]]
    assert orden == [5, 4, 3, 2, 1]


def test_orden_fondo_reciente_email_score():
    posts = [post(1, dias=18, score=95), post(2, dias=16, score=72, email=False), post(3, dias=16, score=71),
             post(4, dias=16.5, score=99, puesto="Repositor"), post(5, dias=30)]
    armadas = colas.armar_colas(posts, AHORA)
    assert armadas[colas.PRIORITARIA] == []
    assert [p["id"] for p in armadas[colas.FONDO]] == [4, 3, 2, 1, 5]


def test_a_la_de_fondo_entra_todo_lo_que_no_es_prioritario():
    """con orden, pero entra absolutamente todo."""
    posts = [
        post(1, dias=1, score=90),                                         # prioritaria
        post(2, dias=1, score=40),                                         # baja afinidad
        post(3, dias=0.1, score=20),                                       # baja afinidad, más nueva
        post(4, dias=60, score=65),                                        # vieja, baja afinidad
        post(5, dias=1, score=95, analisis={"match_score": 95, "oferta_cerrada": True}),  # cerrada
        post(6, dias=1, score=85, texto="Buscamos CV", email=False),       # captura incompleta (sin email)
        post(7, dias=30, score=80),                                        # > 70 pero vieja
        post(8, dias=2, score=88, puesto="Repositor"),                     # > 70, grupo c
        post(9, analisis={}),                                              # sin afinidad
    ]
    armadas = colas.armar_colas(posts, AHORA)
    assert [p["id"] for p in armadas[colas.PRIORITARIA]] == [1]
    # > 70 por antigüedad; después más afinidad; al final cerradas o incompletas
    assert [p["id"] for p in armadas[colas.FONDO]] == [8, 7, 4, 2, 3, 9, 5, 6]
    assert len(armadas[colas.PRIORITARIA]) + len(armadas[colas.FONDO]) == len(posts)


def test_candidatas_incluye_toda_analizada_sin_mensaje():
    baja = _crear(1, score=30)
    _crear(2, score=90, email_body="ya armado")
    assert [p["id"] for p in store.candidatas_cola()] == [baja]


def _crear(n, **campos):
    return crear_post(n, "ANALIZADA", analisis={"match_score": campos.pop("score", 80)}, **campos)


@pytest.mark.asyncio
async def test_analizada_no_se_encola_fija_y_la_mejor_pasa_adelante(tmp_path):
    vieja = _crear(1, score=95, fecha_rel="3 días")
    loop = make_radar(tmp_path)
    loop.wake_generation = lambda: None

    async def nada():
        return None
    loop.wake_generation = nada
    assert await loop.generar_automaticos([vieja]) == []
    assert store.get_post(vieja)["estado"] == "ANALIZADA"
    # Llega una más reciente antes de que empiece la generación: pasa adelante
    nueva = _crear(2, score=75, fecha_rel="2 h")
    elegido = loop._siguiente_generacion()
    assert elegido["id"] == nueva and elegido["gen_prioridad"] == radar.PRIORIDAD_AUTO
    assert store.get_post(vieja)["estado"] == "ANALIZADA"


@pytest.mark.asyncio
async def test_fondo_solo_sin_prioritarias_y_con_fondo_activo(tmp_path):
    fondo = _crear(1, fecha_rel="16 días")
    prioritaria = _crear(2, fecha_rel="1 día")
    store.update_radar_state(backlog_activo=False)
    loop = make_radar(tmp_path)
    assert loop._siguiente_generacion()["id"] == prioritaria
    assert loop._siguiente_generacion()["id"] == prioritaria  # ya GENERANDO: se retoma, no se duplica
    store.update_post(prioritaria, estado="LISTO")
    assert loop._siguiente_generacion() is None               # fondo apagado
    store.update_radar_state(backlog_activo=True)
    elegido = loop._siguiente_generacion()
    assert elegido["id"] == fondo and elegido["gen_prioridad"] == radar.PRIORIDAD_BACKLOG


def test_pausa_frena_las_dos_colas(tmp_path):
    _crear(1, fecha_rel="1 h")
    _crear(2, fecha_rel="16 días")
    store.update_radar_state(generacion_pausada=True, backlog_activo=True)
    assert make_radar(tmp_path)._siguiente_generacion() is None
    assert store.tomar_candidata(1, colas.PRIORITARIA) is False
    assert all(p["estado"] == "ANALIZADA" for p in store.list_posts())


def test_tomar_candidata_revalida_el_nivel():
    pid = _crear(1, fecha_rel="1 h")
    assert store.tomar_candidata(pid, colas.FONDO) is False  # es prioritaria, no de fondo
    assert store.tomar_candidata(pid, colas.PRIORITARIA) is True
    assert store.get_post(pid)["estado"] == "GENERANDO"


@pytest.mark.asyncio
async def test_worker_genera_en_orden_dinamico(tmp_path):
    a = _crear(1, score=72, fecha_rel="4 días")
    b = _crear(2, score=90, fecha_rel="1 h")
    c = _crear(3, score=85, fecha_rel="16 días")
    store.update_radar_state(backlog_activo=True)
    pipeline = FakePipeline()
    loop = make_radar(tmp_path, pipeline=pipeline)
    try:
        await loop.wake_generation()
        await esperar_workers(loop)
        assert pipeline.calls == [b, a, c]
    finally:
        await finalizar(loop)


def test_panel_de_procesos_muestra_las_proximas_tres():
    from core.procesos import armar_procesos
    prios = [_crear(n, score=70 + n, fecha_rel="1 h") for n in range(1, 6)]
    fondo = _crear(9, fecha_rel="16 días")
    store.update_radar_state(backlog_activo=False)
    c = armar_procesos(None)["colas"]
    assert c["dias_prioritaria"] == 14 and "dias_fondo" not in c
    assert c["prioritaria"]["total"] == 5
    assert [i["post_id"] for i in c["prioritaria"]["proximas"]] == prios[::-1][:3]  # más score primero
    assert [i["posicion"] for i in c["prioritaria"]["proximas"]] == [1, 2, 3]
    assert c["fondo"] == {"activo": False, "total": 1, "proximas": [c["fondo"]["proximas"][0]]}
    assert c["fondo"]["proximas"][0]["post_id"] == fondo and c["fondo"]["proximas"][0]["canal"] == "email"


def test_api_lista_trae_el_lugar_en_la_cola():
    from fastapi.testclient import TestClient
    import server
    pid = _crear(1, fecha_rel="1 h")
    otro = crear_post(2, "LISTO")
    posts = {p["id"]: p for p in TestClient(server.app).get("/api/radar/posts").json()}
    assert posts[pid]["cola"] == {"nivel": "prioritaria", "posicion": 1, "total": 1}
    assert posts[otro]["cola"] is None


def test_tarjeta_muestra_prioritaria_o_de_fondo():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "radar.js").read_text(encoding="utf-8")
    assert "(prio ? 'Prioritaria, ' : 'De fondo, ') + post.cola.posicion + '.º'" in js
    assert "function renderColas(" in js and "renderColas(lista, data.colas" in js


def test_panel_de_procesos_sin_secciones_confusas():
    """una fila por control con su estado, sin «Completar mensajes» ni «Cambios de modo»."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    js = (raiz / "static" / "radar.js").read_text(encoding="utf-8")
    html = (raiz / "static" / "index.html").read_text(encoding="utf-8")
    for viejo in ("Completar mensajes", "Cambios de modo", "Cola de CVs", "CV y mensajes"):
        assert viejo not in js and viejo not in html, viejo
    # Nombres de la hoja rediseñada: antes «Análisis de ofertas», «Cola prioritaria» y
    # «Cola de fondo»
    for fila in ("Analizar ofertas", "CV y mail urgentes", "CV y mail del resto"):
        assert fila in html
    assert 'id="radarGeneracionEstado"' in html and 'id="radarAnalisisEstado"' in html
    orden = [js.index(x) for x in ("renderColas(lista, data.colas", "'Por clasificar o analizar'",
                                   "renderErrores(lista, data.errores)", "'Últimos terminados'")]
    assert orden == sorted(orden)


def test_index_sin_cache():
    from fastapi.testclient import TestClient
    import server
    r = TestClient(server.app).get("/")
    assert r.headers.get("cache-control") == "no-cache"
