"""Cobertura de `core/radar_api.py` (11/11): éxito, error y efecto en la base de cada endpoint.

Solo están acá los casos que ningún otro test cubría (la matriz completa, con los tests existentes,
la tiene quien despachó este trabajo). Reglas de aislamiento:
- La base y DATA_DIR son temporales (`tests/conftest.py`); acá se siembra y se lee con SQL directo.
- El Radar es un doble (`AsyncMock(spec=RadarLoop)`): nunca se arranca el real ni se abre Edge.
- Gmail se simula y `smtplib.SMTP_SSL` explota si alguien lo llama: ningún test puede mandar un mail.
- `TestClient(server.app)` sin `with`: no corre el lifespan.

Al final, los casos que daban 500 ante una entrada inválida (ya corregidos): un id fuera del rango de SQLite,
un requisito sin estado marcado como eliminatorio y un análisis con entradas que no son requisitos.
"""
import itertools
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import server
from core import acceso_remoto, database, radar_store
from core import aprendizajes as aprendizajes_mod
from core.mailer import GmailConfig
from core.radar import RadarLoop
from core.radar_api import SOLO_LOCAL

_URN = itertools.count(1)
ID_FUERA_DE_RANGO = 10**20  # más grande que un INTEGER de SQLite (64 bits)


# ----------------------------------------------------------------- fixtures y helpers

@pytest.fixture(autouse=True)
def sin_smtp(monkeypatch):
    """Si algún camino llegara a enviar, el test falla en vez de abrir una conexión real."""
    def _prohibido(*args, **kwargs):
        raise AssertionError("Un test intentó abrir SMTP real")
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _prohibido)


@pytest.fixture(autouse=True)
def aprendizajes_aislados(tmp_path, monkeypatch):
    monkeypatch.setattr(aprendizajes_mod, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")


@pytest.fixture
def debug_dir(tmp_path):
    d = tmp_path / "radar_debug"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def radar(monkeypatch, debug_dir):
    stub = AsyncMock(spec=RadarLoop)
    stub.debug_dir = debug_dir
    stub.generando_ahora = None
    stub.en_vuelo = set()
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def gmail(monkeypatch):
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: GmailConfig("yo@example.com", "x"))


@pytest.fixture
def client(radar):
    return TestClient(server.app)


@pytest.fixture
def clave_celular(tmp_path, monkeypatch):
    monkeypatch.setattr(acceso_remoto, "ARCHIVO_CLAVE", tmp_path / "acceso_celular.key")
    monkeypatch.setattr(acceso_remoto, "ip_lan", lambda: "192.168.1.50")
    return acceso_remoto.clave()


def _desde_afuera() -> TestClient:
    """Un cliente de la red local que no es la PC ni un celular emparejado."""
    return TestClient(server.app, base_url="http://localhost:8000", client=("192.168.1.50", 51000))


def _sql(consulta: str, parametros=()) -> list:
    conn = database.get_db_connection()
    try:
        with conn:
            filas = conn.execute(consulta, parametros).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


def _foto() -> dict:
    """Todas las filas de todas las tablas de la base temporal, para comparar antes y después.

    Las tablas vacías no cuentan: algunos módulos crean su tabla recién al primer uso, y eso no es un dato."""
    tablas = [f["name"] for f in _sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    foto = {t: sorted(json.dumps(f, sort_keys=True, default=str) for f in _sql(f'SELECT * FROM "{t}"'))
            for t in tablas if t != "sqlite_sequence"}
    return {t: filas for t, filas in foto.items() if filas}


def _estado_radar() -> dict:
    return _sql("SELECT * FROM radar_state WHERE id = 1")[0]


def _sembrar_post(estado="LISTO", **campos) -> int:
    """Inserta un post con SQL directo (sin pasar por la API ni por `radar_store`)."""
    n = next(_URN)
    fila = dict(
        urn=f"urn:li:activity:{n}", url=f"https://www.linkedin.com/feed/update/urn:li:activity:{n}/",
        autor=f"Autor {n}", texto="Buscamos analista funcional. Enviá tu CV a rrhh@empresa.test",
        query='"cv a"', estado=estado,
        triage=json.dumps({"es_busqueda_laboral": True, "puesto": "Analista Funcional", "empresa": "Acme",
                           "motivo": "encaja"}),
        email_to="rrhh@empresa.test", email_subject="Postulación – Analista Funcional",
        email_body="Hola, adjunto mi CV.",
    )
    fila.update({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in campos.items()})
    columnas = ", ".join(fila)
    marcas = ", ".join("?" * len(fila))
    conn = database.get_db_connection()
    try:
        radar_store.init_radar_tables(conn)
        with conn:
            return conn.execute(f"INSERT INTO linkedin_posts ({columnas}) VALUES ({marcas})",
                                tuple(fila.values())).lastrowid
    finally:
        conn.close()


def _fila_post(post_id: int) -> dict:
    return _sql("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,))[0]


def _sembrar_lectura(query='"cv a"', fuente="busqueda", motivo_fin="fin") -> int:
    ahora = datetime.now().isoformat()
    _estado_radar()  # garantiza las tablas del Radar
    conn = database.get_db_connection()
    try:
        with conn:
            return conn.execute(
                "INSERT INTO radar_lecturas (query, fuente, inicio, fin, scrolls, motivo_fin, pantallas, "
                "parseados, guardados, destinos) VALUES (?, ?, ?, ?, 2, ?, ?, 4, 1, ?)",
                (query, fuente, ahora, ahora, motivo_fin, json.dumps([{"tarjetas_dom": 4}]),
                 json.dumps({"urn:li:activity:7": "guardado"}))).lastrowid
    finally:
        conn.close()


def _sembrar_revision(campo="remuneración", valor="[a completar]", post_id=None) -> int:
    from core import revision_store
    conn = database.get_db_connection()
    try:
        revision_store.init_tabla(conn)
        with conn:
            return conn.execute(
                "INSERT INTO revision_pendiente (post_id, tipo, campo, valor_usado) VALUES (?, 'placeholder', ?, ?)",
                (post_id, campo, valor)).lastrowid
    finally:
        conn.close()


def _assert_403(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"].startswith(SOLO_LOCAL)


# ----------------------------------------------------------------- ciclo de vida

def test_start_persiste_desired_running_en_la_base(client, radar):
    _sql("UPDATE radar_state SET desired_running = 0 WHERE id = 1")
    r = client.post("/api/radar/start")
    assert r.status_code == 200, r.text
    assert _estado_radar()["desired_running"] == 1
    radar.start.assert_awaited_once()


def test_stop_persiste_desired_running_en_la_base(client, radar):
    _sql("UPDATE radar_state SET desired_running = 1 WHERE id = 1")
    r = client.post("/api/radar/stop")
    assert r.status_code == 200, r.text
    assert _estado_radar()["desired_running"] == 0
    radar.stop.assert_awaited_once()


@pytest.mark.parametrize("ruta", [
    "/api/radar/stop", "/api/radar/reintentar", "/api/radar/levantar-bloqueo", "/api/radar/resume-sends",
    "/api/radar/agy/reintentar", "/api/radar/reprocesar", "/api/radar/rescate", "/api/radar/regenerar-listos",
])
def test_post_desde_afuera_da_403_sin_tocar_la_base_ni_el_radar(radar, ruta):
    """Las rutas sin cuerpo ni parámetro: el único error que define el código es la guarda de solo-local."""
    _sql("UPDATE radar_state SET desired_running = 1 WHERE id = 1")
    _sembrar_post("LISTO")
    _sembrar_lectura(motivo_fin="bloqueo")
    antes = _foto()
    _assert_403(_desde_afuera().post(ruta, json={}))
    assert _foto() == antes
    assert radar.mock_calls == []


def test_status_con_metodo_invalido_da_405(client):
    assert client.post("/api/radar/status").status_code == 405


def test_status_refleja_el_estado_sembrado_y_no_toca_la_base(client):
    _sql("UPDATE radar_state SET umbral_match = 63, envios_pausados = 1, tope_envios_dia = 17 WHERE id = 1")
    _sembrar_revision()
    antes = _foto()
    data = client.get("/api/radar/status").json()
    assert (data["umbral_match"], data["envios_pausados"], data["tope_envios_dia"]) == (63, True, 17)
    assert data["revision_pendientes"] == 1
    assert _foto() == antes


def test_levantar_bloqueo_actualiza_solo_las_lecturas_bloqueadas_que_no_son_del_feed(client):
    bloqueada = _sembrar_lectura(fuente="busqueda", motivo_fin="bloqueo")
    del_feed = _sembrar_lectura(fuente="feed", motivo_fin="bloqueo")
    normal = _sembrar_lectura(fuente="busqueda", motivo_fin="fin")
    r = client.post("/api/radar/levantar-bloqueo")
    assert r.status_code == 200 and r.json()["levantadas"] == 1
    motivos = {f["id"]: f["motivo_fin"] for f in _sql("SELECT id, motivo_fin FROM radar_lecturas")}
    assert motivos == {bloqueada: "bloqueo_levantado", del_feed: "bloqueo", normal: "fin"}


def test_captura_bloqueo_no_toca_la_base(client, debug_dir):
    (debug_dir / "bloqueo").mkdir()
    (debug_dir / "bloqueo" / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _sembrar_post("LISTO")
    antes = _foto()
    r = client.get("/api/radar/captura-bloqueo")
    assert r.status_code == 200 and r.content == b"\x89PNG\r\n\x1a\n"
    assert _foto() == antes


# ----------------------------------------------------------------- lecturas y procesos

@pytest.mark.parametrize("limite", ["-1", "muchas"])
def test_lecturas_con_limite_invalido_da_422(client, limite):
    assert client.get("/api/radar/lecturas", params={"limite": limite}).status_code == 422


def test_lecturas_refleja_la_fila_sembrada_y_no_toca_la_base(client):
    lectura = _sembrar_lectura(query='"cv a" funcional')
    _sembrar_lectura(query="otra")
    antes = _foto()
    filas = client.get("/api/radar/lecturas", params={"query": '"cv a" funcional'}).json()
    assert [f["id"] for f in filas] == [lectura]
    assert filas[0]["destinos"] == {"urn:li:activity:7": "guardado"}
    assert filas[0]["pantallas"] == [{"tarjetas_dom": 4}]
    assert _foto() == antes


def test_procesos_con_metodo_invalido_da_405(client):
    assert client.post("/api/radar/procesos").status_code == 405


def test_procesos_refleja_el_estado_sembrado_y_no_toca_la_base(client, monkeypatch):
    # Los topes son del perfil (reglas_radar.json, 12/11), no de radar_state
    import core.reglas_radar
    from tests.reglas_prueba import reglas
    monkeypatch.setattr(core.reglas_radar, "leer_reglas", lambda: reglas(tope_envios_dia=33))
    _sembrar_post("ANALIZADA", analisis={"match_score": 80})
    antes = _foto()
    r = client.get("/api/radar/procesos")
    assert r.status_code == 200, r.text
    topes = r.json()["topes"]
    assert topes["tope_envios"] == 33 and "tope_leidos" not in topes  # sin tope de posts (12/11)
    assert _foto() == antes


# ----------------------------------------------------------------- pausas y config

@pytest.mark.parametrize("ruta, cuerpo", [
    ("/api/radar/backlog", {"activo": "quizás"}),
    ("/api/radar/backlog", {}),
    ("/api/radar/generacion", {"pausada": "quizás"}),
    ("/api/radar/generacion", {}),
    ("/api/radar/analisis", {"pausada": "quizás"}),
    ("/api/radar/analisis", {}),
])
def test_pausas_con_cuerpo_invalido_dan_422_sin_tocar_la_base(client, radar, ruta, cuerpo):
    antes = _foto()
    assert client.post(ruta, json=cuerpo).status_code == 422
    assert _foto() == antes
    radar.wake_generation.assert_not_awaited()
    radar.wake_processing.assert_not_awaited()


def test_backlog_persiste_en_la_base(client, radar):
    assert client.post("/api/radar/backlog", json={"activo": False}).status_code == 200
    assert _estado_radar()["backlog_activo"] == 0
    assert client.post("/api/radar/backlog", json={"activo": True}).status_code == 200
    assert _estado_radar()["backlog_activo"] == 1


def test_analisis_persiste_en_la_base_y_despierta_al_reanudar(client, radar):
    assert client.post("/api/radar/analisis", json={"pausada": True}).status_code == 200
    assert _estado_radar()["analisis_pausado"] == 1
    radar.wake_processing.assert_not_awaited()
    assert client.post("/api/radar/analisis", json={"pausada": False}).status_code == 200
    assert _estado_radar()["analisis_pausado"] == 0
    radar.wake_processing.assert_awaited_once()


# ----------------------------------------------------------------- revisión pendiente

def test_revision_con_parametro_invalido_da_422(client):
    assert client.get("/api/radar/revision", params={"todos": "quizás"}).status_code == 422


def test_revision_refleja_la_fila_sembrada_y_no_toca_la_base(client):
    post = _sembrar_post("LISTO")
    item = _sembrar_revision(campo="sueldo", valor="[sueldo]", post_id=post)
    antes = _foto()
    data = client.get("/api/radar/revision").json()
    assert data["pendientes"] == 1
    assert [(i["id"], i["valor_usado"], i["puesto"], i["empresa"]) for i in data["items"]] == [
        (item, "[sueldo]", "Analista Funcional", "Acme")]
    assert _foto() == antes


def test_revision_revisado_marca_solo_ese_item_en_la_base(client):
    uno = _sembrar_revision(campo="a")
    otro = _sembrar_revision(campo="b")
    assert client.post(f"/api/radar/revision/{uno}/revisado").json() == {"pendientes": 1}
    revisados = {f["id"]: f["revisado"] for f in _sql("SELECT id, revisado FROM revision_pendiente")}
    assert revisados == {uno: 1, otro: 0}


# ----------------------------------------------------------------- acceso desde el celular

def test_acceso_celular_devuelve_la_clave_guardada_y_no_toca_la_base(client, clave_celular):
    antes = _foto()
    r = client.get("/api/radar/acceso-celular")
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(f"/api/radar/emparejar?clave={clave_celular}")
    assert acceso_remoto.clave(crear=False) == clave_celular
    assert _foto() == antes


def test_regenerar_acceso_cambia_la_clave_y_no_toca_la_base(client, clave_celular):
    """La clave vive en un archivo de DATA_DIR, no en la base: se verifica el archivo y que la base no cambie."""
    antes = _foto()
    r = client.post("/api/radar/acceso-celular/regenerar")
    assert r.status_code == 200, r.text
    nueva = acceso_remoto.clave(crear=False)
    assert nueva and nueva != clave_celular
    assert not acceso_remoto.es_valida(clave_celular)
    assert r.json()["url"].endswith(f"clave={nueva}")
    assert _foto() == antes


def test_emparejar_setea_la_cookie_y_no_toca_la_base(client, clave_celular):
    antes = _foto()
    r = client.get("/api/radar/emparejar", params={"clave": clave_celular}, follow_redirects=False)
    assert r.status_code == 303
    assert f"{acceso_remoto.COOKIE}={clave_celular}" in r.headers["set-cookie"]
    assert acceso_remoto.clave(crear=False) == clave_celular
    assert _foto() == antes


# ----------------------------------------------------------------- rutas que delegan en el Radar

def _preparar_delegacion(radar, monkeypatch, ruta):
    """Siembra lo que necesita cada ruta y devuelve (url, método del radar que tiene que llamarse)."""
    async def _sondeo():
        return {"agotada": False}
    monkeypatch.setattr("core.radar_api.sondear_cuota", _sondeo)
    radar.reprocesar.return_value = {"triage": [1], "analisis": [], "generacion": []}
    radar.rescatar_capturas.return_value = {"revisados": 0, "rescatados": 0, "siguen_incompletos": 0}
    if ruta == "retry":
        return f"/api/radar/posts/{_sembrar_post('ERROR', error='falló')}/retry", radar.retry
    if ruta in ("analizar", "generar-cv"):
        pid = _sembrar_post("ANALIZADA")
        return f"/api/radar/posts/{pid}/{ruta}", radar.prioritize if ruta == "analizar" else radar.generate_cv
    metodos = {"show-browser": radar.show_browser, "resume-sends": radar.resume_sends,
               "reprocesar": radar.reprocesar, "rescate": radar.rescatar_capturas, "agy/reintentar": None}
    return f"/api/radar/{ruta}", metodos[ruta]


@pytest.mark.parametrize("ruta", [
    "show-browser", "resume-sends", "reprocesar", "rescate", "agy/reintentar", "analizar", "generar-cv", "retry",
])
def test_rutas_que_delegan_no_tocan_la_base(client, radar, monkeypatch, ruta):
    """Estas rutas no escriben: le piden el trabajo al Radar (acá un doble) y la base queda igual."""
    url, metodo = _preparar_delegacion(radar, monkeypatch, ruta)
    antes = _foto()
    r = client.post(url)
    assert r.status_code == 200, r.text
    assert _foto() == antes
    if metodo is not None:
        metodo.assert_awaited_once()


# ----------------------------------------------------------------- posts

def test_posts_refleja_la_fila_sembrada_y_no_toca_la_base(client):
    listo = _sembrar_post("LISTO", analisis={"match_score": 77.5, "puesto": "Analista Sr"})
    _sembrar_post("DESCARTADO")
    antes = _foto()
    posts = client.get("/api/radar/posts", params={"estado": "LISTO"}).json()
    assert [(p["id"], p["puesto"], p["match_score"], p["empresa"]) for p in posts] == [
        (listo, "Analista Sr", 77.5, "Acme")]
    assert _foto() == antes


def test_detalle_de_post_inexistente_da_404(client):
    assert client.get("/api/radar/posts/999999").status_code == 404


def test_detalle_refleja_la_fila_sembrada_y_no_toca_la_base(client):
    postulacion = database.create_postulacion("Analista", "Acme", "texto", origen="linkedin", status="READY")
    pid = _sembrar_post("LISTO", postulacion_id=postulacion, email_body="Cuerpo sembrado")
    antes = _foto()
    d = client.get(f"/api/radar/posts/{pid}").json()
    assert d["email_body"] == "Cuerpo sembrado"
    assert d["postulacion"]["id"] == postulacion and d["postulacion"]["status"] == "READY"
    assert _foto() == antes


def test_approve_all_escribe_aprobado_at_solo_en_los_confirmados(client, radar, gmail):
    confirmado = _sembrar_post("LISTO", email_to="uno@empresa.test")
    no_confirmado = _sembrar_post("LISTO", email_to="dos@empresa.test")
    visto = _fila_post(confirmado)["updated_at"]
    r = client.post("/api/radar/approve-all", json={"ids": [confirmado], "updated_at": {str(confirmado): visto}})
    assert r.status_code == 200, r.text
    assert r.json()["encolados"] == [confirmado]
    assert _fila_post(confirmado)["aprobado_at"]
    assert _fila_post(no_confirmado)["aprobado_at"] is None
    radar.enqueue_send.assert_awaited_once_with(confirmado)


# ----------------------------------------------------------------- entradas inválidas que daban 500

@pytest.mark.parametrize("metodo, url, cuerpo", [
    ("get", f"/api/radar/posts/{ID_FUERA_DE_RANGO}", None),
    ("post", f"/api/radar/posts/{ID_FUERA_DE_RANGO}/discard", None),
    ("post", f"/api/radar/revision/{ID_FUERA_DE_RANGO}/revisado", None),
    ("post", "/api/radar/approve-all", {"ids": [ID_FUERA_DE_RANGO], "updated_at": {}}),
    ("get", f"/api/radar/lecturas?limite={ID_FUERA_DE_RANGO}", None),
])
def test_id_fuera_de_rango_no_da_500(radar, gmail, metodo, url, cuerpo):
    c = TestClient(server.app, raise_server_exceptions=False)
    r = getattr(c, metodo)(url, **({} if cuerpo is None else {"json": cuerpo}))
    assert r.status_code < 500, r.text


def test_requisito_eliminatorio_sin_estado_no_da_500(radar):
    pid = _sembrar_post("ANALIZADA", analisis={"match_score": 40, "requisitos": [{"nombre": "SQL"}]})
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "SQL", "eliminatorio": True})
    assert r.status_code < 500, r.text


def test_requisito_con_entrada_basura_en_el_analisis_no_da_500(radar):
    pid = _sembrar_post("ANALIZADA", analisis={"match_score": 40, "requisitos": [
        {"nombre": "SQL", "estado": "BRECHA_REAL"}, "basura"]})
    c = TestClient(server.app, raise_server_exceptions=False)
    r = c.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "SQL"})
    assert r.status_code < 500, r.text
