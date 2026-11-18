"""Contrato HTTP de las rutas de `server.py` que ningún otro test cubría del todo (inventario del 11/11).

Cada ruta tiene que tener tres pruebas: éxito, error y efecto en la base. Acá van solo las que faltaban;
la matriz completa (con los tests que ya existían en otros archivos) está en la entrega de la sesión.
Todo corre con la base SQLite temporal y el DATA_DIR temporal de `conftest.py`, sin lifespan, sin Radar
(doble con `spec=RadarLoop`), sin IA (el bridge falla si alguien lo llama), sin red y sin SMTP.
Datos ficticios: Alex Rivera y Acme.
"""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import core.database
import server
from core import aprendizajes, docx_generator, pdf_generator, radar_store
from core.bridge import AntigravityBridge
from core.expander import CapabilityExpanderAgent
from core.radar import RadarLoop

MODELOS = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]

CV_ALEX = """# ALEX RIVERA
ANALISTA FUNCIONAL QA
Villa Test, Buenos Aires | +54 9 11 5555-0100 | alex.rivera@example.com | https://linkedin.com/in/alex-rivera

## PERFIL
Analista funcional con experiencia en QA y procesos.

## EXPERIENCIA
### Analista Funcional | Acme | 2018 - 2025
• Coordinó pruebas funcionales de punta a punta.

## HABILIDADES TÉCNICAS
• SQL, Jira, Python.
"""

# Rutas con `{postulacion_id}` (método, ruta con «{id}», cuerpo mínimo válido)
RUTAS_CON_ID = [
    ("GET", "/api/ofertas/{id}", None),
    ("DELETE", "/api/ofertas/{id}", None),
    ("POST", "/api/ofertas/{id}/lifecycle", {"estado_postulacion": "POSTULADA"}),
    ("POST", "/api/ofertas/{id}/tags/SQL/cycle-state", None),
    ("POST", "/api/ofertas/{id}/toggle-tag", {"tag_index": 0}),
    ("POST", "/api/ofertas/{id}/regenerate", None),
    ("POST", "/api/ofertas/{id}/recalculate-cv", None),
    ("POST", "/api/ofertas/{id}/remediar", None),
    ("GET", "/api/ofertas/{id}/export/docx", None),
    ("GET", "/api/ofertas/{id}/export/pdf", None),
]
IDS_RUTAS = [f"{m} {r}" for m, r, _ in RUTAS_CON_ID]


# ------------------------------------------------------------------ utilidades

def foto_base() -> str:
    """Volcado completo de la base temporal: si una GET no escribe, queda idéntico."""
    conn = sqlite3.connect(str(core.database.DB_PATH))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def sembrar(**campos) -> int:
    """Inserta una postulación con SQL directo (sin pasar por las funciones que se prueban)."""
    fila = {"titulo": "Analista QA", "empresa": "Acme", "contenido_oferta": "Buscamos analista QA con SQL.",
            "status": "READY", "origen": "manual", **campos}
    valores = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fila.values()]
    conn = sqlite3.connect(str(core.database.DB_PATH))
    try:
        with conn:
            cur = conn.execute(
                f"INSERT INTO postulaciones ({', '.join(fila)}) VALUES ({', '.join('?' * len(fila))})", valores)
        return cur.lastrowid
    finally:
        conn.close()


def fila(pid: int) -> dict:
    """La fila tal como quedó en la base (JSON decodificado)."""
    conn = sqlite3.connect(str(core.database.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM postulaciones WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, str) and v[:1] in "[{":
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


def pedir(client, metodo, ruta, cuerpo=None):
    kwargs = {"json": cuerpo} if cuerpo is not None else {}
    return client.request(metodo, ruta, **kwargs)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Cliente sin lifespan con todo lo externo reemplazado por dobles."""
    radar = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", radar)
    tareas = {}
    for nombre in ("run_pipeline_task", "recalculate_cv_task", "remediate_cv_task"):
        tareas[nombre] = AsyncMock()
        monkeypatch.setattr(server, nombre, tareas[nombre])
    monkeypatch.setattr(server, "scrape_job_url", AsyncMock(side_effect=AssertionError("sin red en los tests")))
    ia = AsyncMock(side_effect=AssertionError("los tests no llaman a la IA real"))
    monkeypatch.setattr(AntigravityBridge, "run_prompt", ia)
    # Lista de modelos fija: `get_model_info` no ejecuta `agy models`
    monkeypatch.setattr(AntigravityBridge, "_cached_available_models", list(MODELOS))
    monkeypatch.setattr(AntigravityBridge, "_cached_models_details",
                        [{"id": m, "display_name": m, "effort": m.rsplit("-", 1)[1]} for m in MODELOS])
    # El modelo activo es estado de clase: se restaura al terminar
    monkeypatch.setattr(AntigravityBridge, "active_model", AntigravityBridge.active_model)
    monkeypatch.setattr(AntigravityBridge, "active_effort", AntigravityBridge.active_effort)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", tmp_path / "aprendizajes_candidato.json")
    monkeypatch.setattr(docx_generator, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(pdf_generator, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(server, "subscribers", [])
    client = TestClient(server.app, raise_server_exceptions=False)
    yield SimpleNamespace(client=client, radar=radar, tareas=tareas, ia=ia, tmp=tmp_path)
    client.close()
    ia.assert_not_called()


# ------------------------------------------------------------------ errores comunes a las rutas con id

@pytest.mark.parametrize("metodo,ruta,cuerpo", RUTAS_CON_ID, ids=IDS_RUTAS)
def test_id_no_numerico_da_422_sin_tocar_la_base(entorno, metodo, ruta, cuerpo):
    """Un id que no es entero lo rechaza FastAPI con 422 antes de llegar al código."""
    sembrar()
    antes = foto_base()
    r = pedir(entorno.client, metodo, ruta.replace("{id}", "abc"), cuerpo)
    assert r.status_code == 422, r.text
    assert foto_base() == antes


@pytest.mark.parametrize("metodo,ruta,cuerpo", RUTAS_CON_ID, ids=IDS_RUTAS)
def test_id_fuera_de_rango_sqlite_no_da_500(entorno, metodo, ruta, cuerpo):
    """Un id enorme es una postulación inexistente: tiene que dar 404 (o 422), nunca 500."""
    r = pedir(entorno.client, metodo, ruta.replace("{id}", str(10**30)), cuerpo)
    assert r.status_code in (404, 422), r.text


# ------------------------------------------------------------------ ingesta manual

def test_crear_oferta_sin_contenido_da_422(entorno):
    """`contenido` es obligatorio y tiene que ser texto: sin él no se crea ningún post."""
    for cuerpo in ({}, {"titulo": "QA", "empresa": "Acme"}, {"contenido": 123}, {"contenido": None}):
        assert entorno.client.post("/api/ofertas", json=cuerpo).status_code == 422
    assert radar_store.list_posts() == []
    entorno.radar.wake_processing.assert_not_awaited()


@pytest.mark.parametrize("contenido", ["", "   "])
def test_crear_oferta_con_contenido_vacio_se_rechaza(entorno, contenido):
    """Una oferta sin texto ni URL no tiene nada que analizar: se rechaza con 400 o 422."""
    r = entorno.client.post("/api/ofertas", json={"contenido": contenido})
    assert r.status_code in (400, 422), r.text
    assert radar_store.list_posts() == []


def test_crear_oferta_url_sin_url_da_422(entorno):
    """El cuerpo de `/api/ofertas/url` exige `url` de texto."""
    for cuerpo in ({}, {"url": None}, {"url": ["https://acme.test/1"]}):
        assert entorno.client.post("/api/ofertas/url", json=cuerpo).status_code == 422
    assert radar_store.list_posts() == []
    entorno.radar.wake_processing.assert_not_awaited()


def test_batch_con_urls_que_no_son_lista_da_422(entorno):
    """`urls` tiene que ser una lista de textos."""
    for cuerpo in ({}, {"urls": "https://acme.test/1"}, {"urls": [1, 2]}):
        assert entorno.client.post("/api/ofertas/batch", json=cuerpo).status_code == 422
    assert radar_store.list_posts() == []


# ------------------------------------------------------------------ listado y detalle

def test_listar_ofertas_refleja_la_fila_sembrada_y_no_escribe(entorno):
    """GET /api/ofertas devuelve la fila tal como está en la base (más `contenido_limpio`) y no la toca."""
    pid = sembrar(titulo="Analista QA Sr", empresa="Acme", estado_postulacion="EN_ENTREVISTA", match_score=77.0,
                  contenido_oferta="Buscamos analista QA con SQL y Jira.")
    sembrar(titulo="Otra del Radar", origen="linkedin")
    antes = foto_base()
    r = entorno.client.get("/api/ofertas?filtro_lifecycle=EN_PROCESO")
    assert r.status_code == 200
    ofertas = r.json()
    assert [o["id"] for o in ofertas] == [pid]
    assert ofertas[0]["titulo"] == "Analista QA Sr" and ofertas[0]["match_score"] == 77.0
    assert "SQL y Jira" in ofertas[0]["contenido_limpio"]
    assert foto_base() == antes


def test_detalle_de_oferta_inexistente_da_404(entorno):
    """El detalle de un id que no existe responde 404 con el mensaje de siempre."""
    antes = foto_base()
    r = entorno.client.get("/api/ofertas/424242")
    assert r.status_code == 404
    assert r.json() == {"detail": "Oferta no encontrada"}
    assert foto_base() == antes


def test_detalle_refleja_la_fila_sembrada_y_no_escribe(entorno):
    """El detalle completa campos derivados en la respuesta, pero no los guarda."""
    requisitos = [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
                  {"nombre": "Jira", "estado": "BRECHA_REAL", "approved": False}]
    pid = sembrar(titulo="Analista QA", empresa="Acme", requisitos=requisitos, cv_markdown=CV_ALEX,
                  notas_seguimiento="Llamar a Acme el lunes")
    antes = foto_base()
    r = entorno.client.get(f"/api/ofertas/{pid}")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == pid and d["empresa"] == "Acme" and d["cv_markdown"] == CV_ALEX
    assert d["notas_seguimiento"] == "Llamar a Acme el lunes"
    assert [q["nombre"] for q in d["requisitos"]] == ["SQL", "Jira"]
    assert d["analisis"]["requisitos"] == d["requisitos"]
    assert not d["pitches_multicanal"]  # sin pitches multicanal (CV desde el maestro, 14/11)
    assert foto_base() == antes


# ------------------------------------------------------------------ ciclo de vida

def test_lifecycle_sin_estado_da_422(entorno):
    """El cuerpo exige `estado_postulacion` de texto."""
    pid = sembrar()
    antes = foto_base()
    for cuerpo in ({}, {"estado_postulacion": None}, {"notas_seguimiento": "x"}):
        assert entorno.client.post(f"/api/ofertas/{pid}/lifecycle", json=cuerpo).status_code == 422
    assert foto_base() == antes


def test_lifecycle_escribe_la_fila_y_solo_esa(entorno):
    """El cambio de estado queda en la fila (normalizado a mayúsculas) y no toca otras postulaciones."""
    pid = sembrar()
    otra = sembrar(titulo="Otra de Acme")
    fila_otra = fila(otra)
    r = entorno.client.post(f"/api/ofertas/{pid}/lifecycle", json={
        "estado_postulacion": " en_entrevista ", "fecha_postulacion": "2026-11-01 10:00:00",
        "link_postulacion": "https://acme.test/empleos/1", "notas_seguimiento": "Entrevista con Acme",
        "contacto_recruiter": "Alex Rivera (RR. HH. ficticio)"})
    assert r.status_code == 200, r.text
    f = fila(pid)
    assert f["estado_postulacion"] == "EN_ENTREVISTA"
    assert f["fecha_postulacion"] == "2026-11-01 10:00:00"
    assert f["link_postulacion"] == "https://acme.test/empleos/1"
    assert f["notas_seguimiento"] == "Entrevista con Acme"
    assert f["contacto_recruiter"] == "Alex Rivera (RR. HH. ficticio)"
    assert fila(otra) == fila_otra


# ------------------------------------------------------------------ tags

def test_cycle_state_de_oferta_inexistente_da_404(entorno):
    """Ciclar un tag de una postulación que no existe da 404 y no registra aprendizaje."""
    r = entorno.client.post("/api/ofertas/424242/tags/SQL/cycle-state")
    assert r.status_code == 404
    assert aprendizajes.obtener_aprendizajes() == {}


def test_cycle_state_guarda_analisis_puntaje_y_aprendizaje(entorno):
    """Brecha → transferible: el análisis, los requisitos y el puntaje quedan en la fila."""
    requisitos = [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
                  {"nombre": "Jira", "estado": "BRECHA_REAL", "approved": False}]
    pid = sembrar(requisitos=requisitos, match_score=10.0,
                  analisis={"coincidencias": ["SQL"], "transferibles": [], "brechas": ["Jira"]})
    r = entorno.client.post(f"/api/ofertas/{pid}/tags/jira/cycle-state")
    assert r.status_code == 200, r.text
    assert r.json()["anterior_estado"] == "BRECHA_REAL" and r.json()["nuevo_estado"] == "TRANSFERIBLE"
    f = fila(pid)
    assert f["analisis"]["brechas"] == [] and f["analisis"]["transferibles"] == ["Jira"]
    assert [(q["nombre"], q["estado"], q["approved"]) for q in f["requisitos"]] == [
        ("SQL", "MATCH_DIRECTO", True), ("Jira", "TRANSFERIBLE", True)]
    assert f["match_score"] == r.json()["match_score"] != 10.0
    assert aprendizajes.obtener_aprendizajes()["jira"]["estado"] == "TRANSFERIBLE"


def test_cycle_state_con_tag_en_blanco_se_rechaza_sin_escribir(entorno):
    """Un tag vacío no es un requisito: se rechaza con 4xx y la fila queda como estaba."""
    pid = sembrar(analisis={"coincidencias": ["SQL"], "transferibles": [], "brechas": []})
    antes = foto_base()
    r = entorno.client.post(f"/api/ofertas/{pid}/tags/%20/cycle-state")
    assert r.status_code in (400, 404, 422), r.text
    assert foto_base() == antes


def test_toggle_tag_de_oferta_inexistente_da_404(entorno):
    """Sin postulación no hay tag que activar."""
    r = entorno.client.post("/api/ofertas/424242/toggle-tag", json={"tag_index": 0})
    assert r.status_code == 404
    assert r.json() == {"detail": "Oferta o tag inválido"}


def test_toggle_tag_con_cuerpo_invalido_da_422(entorno):
    """`tag_index` es obligatorio y entero."""
    pid = sembrar(requisitos=[{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True}])
    antes = foto_base()
    for cuerpo in ({}, {"tag_index": "uno"}, {"tag_index": None}):
        assert entorno.client.post(f"/api/ofertas/{pid}/toggle-tag", json=cuerpo).status_code == 422
    assert foto_base() == antes


def test_toggle_tag_invierte_approved_y_recalcula_en_la_base(entorno):
    """El tag elegido cambia `approved` en la fila y el puntaje guardado se recalcula."""
    requisitos = [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
                  {"nombre": "Jira", "estado": "TRANSFERIBLE", "approved": True}]
    pid = sembrar(requisitos=requisitos, match_score=0.0, analisis={})
    r = entorno.client.post(f"/api/ofertas/{pid}/toggle-tag", json={"tag_index": 1})
    assert r.status_code == 200, r.text
    f = fila(pid)
    assert [q["approved"] for q in f["requisitos"]] == [True, False]
    assert f["match_score"] == r.json()["match_score"] == f["analisis"]["match_score"]
    assert f["analisis"]["requisitos"] == f["requisitos"]


@pytest.mark.parametrize("indice", [5, -1])
def test_toggle_tag_con_indice_fuera_de_rango_da_404_sin_escribir(entorno, indice):
    """Un índice que no corresponde a ningún requisito es un tag inválido: 404 y la fila no cambia."""
    pid = sembrar(requisitos=[{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True}], match_score=3.0)
    antes = foto_base()
    r = entorno.client.post(f"/api/ofertas/{pid}/toggle-tag", json={"tag_index": indice})
    assert r.status_code == 404, r.text
    assert foto_base() == antes


# ------------------------------------------------------------------ regenerar, recalcular, remediar

@pytest.mark.parametrize("ruta,tarea", [
    ("regenerate", "run_pipeline_task"), ("recalculate-cv", "recalculate_cv_task"), ("remediar", "remediate_cv_task"),
])
def test_tareas_de_ia_con_oferta_inexistente_dan_404_sin_lanzar_nada(entorno, ruta, tarea):
    """Sin postulación no se lanza ninguna tarea en segundo plano."""
    r = entorno.client.post(f"/api/ofertas/424242/{ruta}")
    assert r.status_code == 404
    assert r.json() == {"detail": "Oferta no encontrada"}
    entorno.tareas[tarea].assert_not_awaited()


@pytest.mark.parametrize("ruta,tarea,status", [
    ("regenerate", "run_pipeline_task", "REGENERATING"), ("remediar", "remediate_cv_task", "REMEDIATING"),
])
def test_tareas_de_ia_marcan_processing_y_lanzan_su_tarea(entorno, ruta, tarea, status):
    """La fila pasa a PROCESSING antes de responder y la tarea (doble) recibe el id; las demás no corren."""
    pid = sembrar(status="READY", cv_markdown=CV_ALEX)
    otra = sembrar(status="READY")
    r = entorno.client.post(f"/api/ofertas/{pid}/{ruta}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == status and r.json()["id"] == pid
    assert fila(pid)["status"] == "PROCESSING"
    assert fila(otra)["status"] == "READY"
    entorno.tareas[tarea].assert_awaited_once_with(pid)
    for nombre, doble in entorno.tareas.items():
        if nombre != tarea:
            doble.assert_not_awaited()


def test_recalcular_cv_con_formato_que_no_es_texto_da_422(entorno):
    """`formato` tiene que ser texto: un número no llega al código ni cambia la fila."""
    pid = sembrar()
    antes = foto_base()
    assert entorno.client.post(f"/api/ofertas/{pid}/recalculate-cv", json={"formato": 1}).status_code == 422
    assert foto_base() == antes
    entorno.tareas["recalculate_cv_task"].assert_not_awaited()


# ------------------------------------------------------------------ exportar

@pytest.mark.parametrize("formato", ["docx", "pdf"])
def test_exportar_sin_cv_da_404(entorno, formato):
    """Sin postulación o sin CV generado no hay nada que exportar."""
    pid = sembrar(cv_markdown="")
    for ruta in (f"/api/ofertas/424242/export/{formato}", f"/api/ofertas/{pid}/export/{formato}"):
        r = entorno.client.get(ruta)
        assert r.status_code == 404
        assert r.json() == {"detail": "CV no disponible para exportar"}


@pytest.mark.parametrize("formato,funcion,tipo", [
    ("docx", "compile_markdown_to_docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("pdf", "compile_markdown_to_pdf", "application/pdf"),
])
def test_exportar_usa_el_cv_sembrado_y_no_escribe_la_base(entorno, monkeypatch, formato, funcion, tipo):
    """El archivo sale del CV guardado, con el puesto del CV y las palabras clave aprobadas; la base no cambia."""
    capturado = {}

    def compilar(markdown, target_role=None, output_filename=None, keywords=None):
        capturado.update(markdown=markdown, rol=target_role, archivo=output_filename, keywords=keywords)
        destino = entorno.tmp / output_filename
        destino.write_bytes(b"contenido de prueba")
        return destino

    monkeypatch.setattr(server, funcion, compilar)
    requisitos = [{"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True},
                  {"nombre": "Kubernetes", "estado": "BRECHA_REAL", "approved": False}]
    pid = sembrar(titulo="Convocatoria abierta Acme", cv_markdown=CV_ALEX, requisitos=requisitos)
    antes = foto_base()
    r = entorno.client.get(f"/api/ofertas/{pid}/export/{formato}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(tipo)
    assert r.content == b"contenido de prueba"
    assert capturado["markdown"] == CV_ALEX
    assert capturado["rol"] == "ANALISTA FUNCIONAL QA"
    assert capturado["archivo"] in r.headers["content-disposition"]
    assert "SQL" in capturado["keywords"] and "Kubernetes" not in capturado["keywords"]
    assert foto_base() == antes


# ------------------------------------------------------------------ modelo

def test_model_info_refleja_el_modelo_activo_y_no_escribe(entorno, monkeypatch):
    """GET /api/model-info muestra el modelo activo del bridge y la lista disponible; no toca la base."""
    monkeypatch.setattr(AntigravityBridge, "active_model", "gemini-3.8-flash-low")
    monkeypatch.setattr(AntigravityBridge, "active_effort", "low")
    antes = foto_base()
    r = entorno.client.get("/api/model-info")
    assert r.status_code == 200
    info = r.json()
    assert info["configured_model"] == "gemini-3.8-flash-low"
    assert info["available_models"] == MODELOS
    assert foto_base() == antes


def test_model_config_sin_modelo_da_422(entorno):
    """`model` es obligatorio y de texto; el modelo activo no cambia."""
    activo = AntigravityBridge.active_model
    for cuerpo in ({}, {"model": None}, {"effort": "low"}):
        assert entorno.client.post("/api/model-config", json=cuerpo).status_code == 422
    assert AntigravityBridge.active_model == activo


def test_model_config_cambia_el_modelo_activo_y_avisa_por_sse(entorno):
    """El modelo y el esfuerzo quedan activos para todo el proceso y se emite `model_changed`; la base no cambia."""
    cola = asyncio.Queue()
    server.subscribers.append(cola)
    antes = foto_base()
    r = entorno.client.post("/api/model-config", json={"model": "gemini-3.8-flash-medium", "effort": "low"})
    assert r.status_code == 200, r.text
    assert AntigravityBridge.active_model == "gemini-3.8-flash-medium"
    assert AntigravityBridge.active_effort == "low"
    mensaje = cola.get_nowait()
    assert mensaje.startswith("event: model_changed\n") and "gemini-3.8-flash-medium" in mensaje
    assert foto_base() == antes


# ------------------------------------------------------------------ SSE

def test_stream_por_http_entrega_eventos_se_desuscribe_y_no_toca_la_base(entorno):
    """Por HTTP real: saludo, un evento emitido con `broadcast_event` y cierre limpio, sin colgarse."""
    entregado = {}

    class Suscriptores(list):
        def append(self, cola):
            super().append(cola)

            async def get():
                # Primera vuelta: el evento real; segunda: corta el generador como un cierre de conexión
                if not entregado:
                    entregado["si"] = True
                    await server.broadcast_event("lifecycle_updated", {"id": 7, "estado_postulacion": "POSTULADA"})
                    return cola.get_nowait()
                raise asyncio.CancelledError

            cola.get = get

    server.subscribers = Suscriptores()
    antes = foto_base()
    r = entorno.client.get("/api/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.startswith("event: connected\n")
    assert 'event: lifecycle_updated\ndata: {"id": 7, "estado_postulacion": "POSTULADA"}\n\n' in r.text
    assert list(server.subscribers) == [], "la cola se desuscribe al cerrarse"
    assert foto_base() == antes


# ------------------------------------------------------------------ competencias expandidas

@pytest.fixture
def expansor(tmp_path, monkeypatch):
    """Expansor con almacenamiento temporal y un bridge falso (sin IA)."""
    bridge = Mock()
    bridge.run_prompt = AsyncMock(return_value={"preguntas": [
        {"keyword": "Cypress", "pregunta": "¿Automatizaste pruebas con Cypress en Acme?",
         "categoria_sugerida": "Herramientas", "sugerencia_evidencia": "Suite E2E"}]})
    agente = CapabilityExpanderAgent(bridge=bridge, storage_path=tmp_path / "competencias.json")
    monkeypatch.setattr(server, "expander_agent", agente)
    return agente


COMPETENCIA = {"nombre": "Jira", "categoria": "Herramientas", "nivel": "Avanzado",
               "evidencia": "Gestión de incidencias en Acme", "created_at": "2026-01-02 03:04:05"}


def test_expand_get_refleja_el_archivo_sembrado_sin_escribir(entorno, expansor):
    """La GET lee el archivo del perfil tal cual y no lo reescribe."""
    expansor.storage_path.write_text(json.dumps([COMPETENCIA], ensure_ascii=False), encoding="utf-8")
    bytes_antes = expansor.storage_path.read_bytes()
    antes = foto_base()
    r = entorno.client.get("/api/candidato/expand")
    assert r.status_code == 200
    assert r.json() == {"competencias": [COMPETENCIA]}
    assert expansor.storage_path.read_bytes() == bytes_antes
    assert foto_base() == antes


def test_expand_ask_con_cuerpo_invalido_da_422(entorno, expansor):
    """`missing_keywords` es una lista de textos: otra cosa da 422 sin llamar al bridge."""
    for cuerpo in ({"missing_keywords": "Cypress"}, {"missing_keywords": [1]}, {"target_role": ["QA"]}):
        assert entorno.client.post("/api/candidato/expand/ask", json=cuerpo).status_code == 422
    expansor.bridge.run_prompt.assert_not_awaited()


def test_expand_ask_pregunta_sin_guardar_nada(entorno, expansor):
    """Generar preguntas consulta al bridge (falso) con las brechas, pero no escribe el perfil ni la base."""
    expansor.storage_path.write_text(json.dumps([COMPETENCIA], ensure_ascii=False), encoding="utf-8")
    bytes_antes = expansor.storage_path.read_bytes()
    antes = foto_base()
    r = entorno.client.post("/api/candidato/expand/ask",
                            json={"target_role": "Analista QA", "missing_keywords": ["Cypress", "  "]})
    assert r.status_code == 200, r.text
    assert [p["keyword"] for p in r.json()["preguntas"]] == ["Cypress"]
    expansor.bridge.run_prompt.assert_awaited_once()
    assert "Cypress" in expansor.bridge.run_prompt.await_args.args[0]
    assert expansor.storage_path.read_bytes() == bytes_antes
    assert foto_base() == antes


def test_expand_confirm_con_cuerpo_invalido_da_422(entorno, expansor):
    """`competencias` es una lista de objetos."""
    for cuerpo in ({"competencias": "Jira"}, {"competencias": ["Jira"]}, {"competencias": None}):
        assert entorno.client.post("/api/candidato/expand/confirm", json=cuerpo).status_code == 422
    assert not expansor.storage_path.exists() or json.loads(expansor.storage_path.read_text("utf-8")) == []


def test_expand_confirm_persiste_y_deduplica_en_el_archivo(entorno, expansor):
    """Lo confirmado queda en el archivo del perfil: sin duplicar por nombre y conservando la fecha original."""
    expansor.storage_path.write_text(json.dumps([COMPETENCIA], ensure_ascii=False), encoding="utf-8")
    r = entorno.client.post("/api/candidato/expand/confirm", json={"competencias": [
        {"nombre": "jira", "categoria": "Herramientas", "nivel": "Experto", "evidencia": "Tableros de Acme"},
        {"nombre": "Cypress", "categoria": "Herramientas", "nivel": "Intermedio", "evidencia": "Suite E2E"},
        {"nombre": "   "},
    ]})
    assert r.status_code == 200, r.text
    guardado = json.loads(expansor.storage_path.read_text("utf-8"))
    assert r.json() == {"competencias": guardado, "status": "SAVED"}
    assert [(c["nombre"], c["nivel"]) for c in guardado] == [("jira", "Experto"), ("Cypress", "Intermedio")]
    assert guardado[0]["created_at"] == COMPETENCIA["created_at"]


def test_expand_delete_inexistente_da_404_e_indice_invalido_422(entorno, expansor):
    """Un índice fuera de rango da 404; uno que no es entero, 422. El archivo no cambia."""
    expansor.storage_path.write_text(json.dumps([COMPETENCIA], ensure_ascii=False), encoding="utf-8")
    bytes_antes = expansor.storage_path.read_bytes()
    for indice in ("1", "-1", "99"):
        r = entorno.client.delete(f"/api/candidato/expand/{indice}")
        assert r.status_code == 404, indice
        assert r.json() == {"detail": "Competencia no encontrada"}
    assert entorno.client.delete("/api/candidato/expand/jira").status_code == 422
    assert expansor.storage_path.read_bytes() == bytes_antes


def test_expand_delete_borra_solo_esa_competencia_del_archivo(entorno, expansor):
    """El índice borrado desaparece del archivo y el resto queda igual y en orden."""
    otra = {**COMPETENCIA, "nombre": "Cypress"}
    tercera = {**COMPETENCIA, "nombre": "SQL"}
    expansor.storage_path.write_text(json.dumps([COMPETENCIA, otra, tercera], ensure_ascii=False), encoding="utf-8")
    r = entorno.client.delete("/api/candidato/expand/1")
    assert r.status_code == 200, r.text
    guardado = json.loads(expansor.storage_path.read_text("utf-8"))
    assert guardado == [COMPETENCIA, tercera]
    assert r.json() == {"competencias": guardado, "status": "DELETED"}


# ------------------------------------------------------------------ portal

def test_index_sirve_el_portal_y_tiene_respaldo_sin_static(entorno, monkeypatch, tmp_path):
    """`/` devuelve el HTML del portal sin caché; si falta `index.html`, un HTML mínimo con 200."""
    r = entorno.client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert (server.STATIC_DIR / "index.html").read_text(encoding="utf-8") == r.text
    monkeypatch.setattr(server, "STATIC_DIR", tmp_path / "sin_static")
    r = entorno.client.get("/")
    assert r.status_code == 200
    assert "LinkedOut: backend en marcha" in r.text
