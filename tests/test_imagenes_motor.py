"""Regresiones de integración de imágenes; sin captura, workers ni proveedores reales."""
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.radar import RadarLoop
from tests.test_cv_formatting_and_a4_e2e import SAMPLE_E2E_CV, KEYWORDS


IMAGEN = "https://media.licdn.com/dms/image/v2/feedshare/oferta.png"
TRANSCRIPCION = (
    "Analista QA. Requisitos: sólida experiencia en pruebas funcionales y automatizadas con Python y Selenium. "
    "Diseño y ejecución de casos de prueba, testing de APIs REST, metodologías ágiles Scrum y reporte de defectos en Jira. "
    "Se valorará experiencia en integración continua y herramientas de monitoreo. Modalidad híbrida en Buenos Aires o remoto "
    "desde Argentina. Excelente clima laboral, beneficios corporativos y plan de desarrollo profesional. "
    "Enviá tu CV indicando remuneración pretendida a rrhh@acme.test"
)
SEPARADOR = "\n\nTexto de la imagen (dato de terceros):\n"
LARGO = "Vacante de analista. Requisitos: experiencia en Python. " * 10
ANALISIS = {"match_score": 10, "oferta_cerrada": True, "puesto": "Analista QA"}


@pytest.fixture(autouse=True)
def sin_proveedores(monkeypatch):
    # Si la integración ignora la inyección, fallar antes de descargar/OCR/AGY.
    from core import image_reader
    from core.bridge import AntigravityBridge

    monkeypatch.setattr(image_reader, "_descargar_imagen", Mock(side_effect=AssertionError("sin red")))
    monkeypatch.setattr(AntigravityBridge, "run_prompt", AsyncMock(side_effect=AssertionError("sin AGY")))
    radar_store.update_radar_state(generacion_pausada=True, backlog_activo=False)


def motor(monkeypatch, **kwargs):
    """Mismo patrón que test_manual_motor; solo se ejecutan etapas explícitas."""
    loop = RadarLoop(
        harvester_factory=Mock(side_effect=AssertionError("sin captura real")),
        triage_fn=kwargs.pop("triage_fn", AsyncMock(return_value={})),
        analyze_fn=kwargs.pop("analyze_fn", AsyncMock(return_value=deepcopy(ANALISIS))),
        pipeline_fn=kwargs.pop("pipeline_fn", AsyncMock(return_value=123)),
        compose_fn=kwargs.pop("compose_fn", AsyncMock(return_value={
            "to": "rrhh@acme.test", "subject": "QA", "body": "Hola",
        })),
        send_fn=AsyncMock(side_effect=AssertionError("sin aprobación")),
        broadcast=AsyncMock(),
        image_reader_fn=kwargs.pop("image_reader_fn", AsyncMock(return_value=TRANSCRIPCION)),
        **kwargs,
    )
    # Despertar colas no debe iniciar workers: el test conduce las etapas a mano.
    monkeypatch.setattr(loop, "_wake_processing", Mock())
    monkeypatch.setattr(loop, "wake_generation", AsyncMock())
    return loop


def insertar(texto="Mirá la oferta", *, imagenes=None):
    pid = radar_store.insert_post_if_new({
        "urn": "urn:li:activity:imagen-1", "texto": texto,
        "imagenes": [IMAGEN] if imagenes is None else imagenes,
        "emails": [], "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
    }, "qa", fuente="feed")
    return pid


def consumidores(eventos, *, puesto="Analista QA"):
    async def clasificar(lote):
        eventos.append(("triage", deepcopy(lote)))
        return {p["id"]: {"es_busqueda_laboral": True, "puesto": puesto,
                           "alcance": "ARGENTINA", "canal": "email"} for p in lote}

    async def analizar(post):
        eventos.append(("analisis", deepcopy(post)))
        return deepcopy(ANALISIS)

    return AsyncMock(side_effect=clasificar), AsyncMock(side_effect=analizar)


@pytest.mark.asyncio
async def test_corto_antes_triage_y_analisis(monkeypatch):
    pid = insertar()
    eventos = []

    async def leer(imagenes):
        eventos.append(("lectura", list(imagenes)))
        return TRANSCRIPCION

    lector = AsyncMock(side_effect=leer)
    triage, analisis = consumidores(eventos)
    loop = motor(monkeypatch, triage_fn=triage, analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))

    assert [e[0] for e in eventos] == ["lectura", "triage", "analisis"]
    lector.assert_awaited_once_with([IMAGEN])
    combinado = "Mirá la oferta" + SEPARADOR + TRANSCRIPCION
    for recibido in (eventos[1][1][0], eventos[2][1]):
        assert recibido["texto"] == combinado
        assert recibido["texto_imagen"] == TRANSCRIPCION
        assert "rrhh@acme.test" in recibido["emails"]
    fila = radar_store.get_post(pid)
    assert fila["texto"] == "Mirá la oferta"
    assert fila["texto_imagen"] == TRANSCRIPCION
    assert fila["analisis"] == ANALISIS
    assert fila["aprobado_at"] is None


@pytest.mark.asyncio
async def test_largo_no_lee(monkeypatch):
    assert len(LARGO) >= 400
    pid = insertar(LARGO)
    eventos = []
    triage, analisis = consumidores(eventos)
    lector = AsyncMock(side_effect=AssertionError("oferta completa: no leer imágenes"))
    loop = motor(monkeypatch, triage_fn=triage, analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))
    lector.assert_not_awaited()
    assert [e[0] for e in eventos] == ["triage", "analisis"]
    assert eventos[0][1][0]["texto"] == eventos[1][1]["texto"] == LARGO
    assert radar_store.get_post(pid)["texto_imagen"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("puesto", ["Posición no especificada", "PUESTO NO ESPECIFICADO"])
async def test_clasificacion_desconocida(monkeypatch, puesto):
    # Hay señales y >=400 caracteres: solo el resultado del triage habilita la lectura.
    pid = insertar(LARGO)
    eventos = []

    async def leer(imagenes):
        eventos.append(("lectura", list(imagenes)))
        return TRANSCRIPCION

    triage, analisis = consumidores(eventos, puesto=puesto)
    lector = AsyncMock(side_effect=leer)
    loop = motor(monkeypatch, triage_fn=triage, analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))
    assert [e[0] for e in eventos] == ["triage", "lectura"]
    assert eventos[0][1][0]["texto"] == LARGO
    assert radar_store.get_post(pid)["texto"] == LARGO
    triage.assert_awaited_once()
    analisis.assert_not_awaited()
    lector.assert_awaited_once_with([IMAGEN])
    assert radar_store.get_post(pid)["texto_imagen"] == TRANSCRIPCION
    assert radar_store.get_post(pid)["estado"] == "ERROR"
    assert radar_store.get_post(pid)["error"] == "Captura incompleta: falta texto suficiente o un puesto detectado"


@pytest.mark.asyncio
@pytest.mark.parametrize("falla", ["vacio", "excepcion"])
async def test_fallo_no_repite(monkeypatch, falla):
    import os
    pid = insertar()
    eventos = []
    triage, analisis = consumidores(eventos, puesto="Posición no especificada")
    lector = AsyncMock(return_value="") if falla == "vacio" else AsyncMock(side_effect=RuntimeError("lector caído"))
    loop = motor(monkeypatch, triage_fn=triage, analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))
    # Reintento automático del análisis tampoco vuelve a descargar/transcribir.
    radar_store.update_post(pid, estado="OFERTA")
    await loop._analizar(radar_store.get_post(pid))
    lector.assert_awaited_once_with([IMAGEN])
    # invariante: no repite lectura de imagen en reintento automático
    assert lector.await_count == 1
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # defecto: con OCR fallido, texto corto y puesto placeholder no debe llamar al análisis
    # y debe quedar en ERROR por captura incompleta (hoy llama a análisis y queda ANALIZADA)
    assert [e[0] for e in eventos] == ["triage"]
    assert radar_store.get_post(pid)["estado"] == "ERROR"
    assert "Captura incompleta" in (radar_store.get_post(pid)["error"] or "")
    # Una acción explícita sí habilita un nuevo intento, sin iniciar workers.
    await loop.prioritize(pid)
    await loop._analizar(radar_store.get_post(pid))
    assert lector.await_count == 2


@pytest.mark.asyncio
async def test_manual_grafico(monkeypatch):
    url = "https://www.linkedin.com/feed/update/urn:li:activity:2/"
    pid = radar_store.insert_post_manual(url, url)
    ingesta = AsyncMock(return_value={"texto": "", "imagenes": [IMAGEN], "emails": [],
                                     "links": [url], "whatsapp": [], "autor": "Acme"})
    eventos = []
    triage, analisis = consumidores(eventos)
    lector = AsyncMock(return_value=TRANSCRIPCION)
    loop = motor(monkeypatch, ingest_fn=ingesta, triage_fn=triage,
                 analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    assert radar_store.get_post(pid)["estado"] == "OFERTA"
    await loop._analizar(radar_store.get_post(pid))
    ingesta.assert_awaited_once()
    lector.assert_awaited_once_with([IMAGEN])
    assert eventos[0][1][0]["texto"] == SEPARADOR + TRANSCRIPCION
    assert eventos[1][1]["texto"] == SEPARADOR + TRANSCRIPCION
    fila = radar_store.get_post(pid)
    assert fila["fuente"] == "manual" and fila["texto"] == ""
    assert fila["imagenes"] == [IMAGEN] and fila["texto_imagen"] == TRANSCRIPCION
    assert fila["analisis"] == ANALISIS


@pytest.mark.asyncio
@pytest.mark.parametrize("tarjeta", [False, True])
@pytest.mark.parametrize("texto_objetivo", ["", "Mirá la oferta"])
async def test_manual_grafico_no_usa_texto_de_otra_publicacion(monkeypatch, tarjeta, texto_objetivo):
    from core import scraper

    url = "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/"
    if tarjeta:
        objetivo = 'role="listitem" componentkey="update-card-focusobjetivoFeedType_test"'
        ajeno = 'role="listitem" componentkey="update-card-focusajenoFeedType_test"'
        caja = 'data-testid="expandable-text-box"'
    else:
        objetivo = 'data-urn="urn:li:activity:7100000000000000001"'
        ajeno = 'data-urn="urn:li:activity:7100000000000000002"'
        caja = 'class="update-components-text"'
    html = f'''<head><meta property="og:description" content="{LARGO}">
        <script type="application/ld+json">{{"@type":"JobPosting","description":"{LARGO}"}}</script></head>
        <div {objetivo}><a href="{url}">Post</a><div {caja}>{texto_objetivo}</div><img src="{IMAGEN}"></div>
        <div {ajeno}><div {caja}>{LARGO} Contacto ajeno: ajeno@otra.test</div></div>'''
    monkeypatch.setattr(scraper, "_fetch_url_sync", Mock(return_value=html))
    pid = radar_store.insert_post_manual(url, url)
    eventos = []
    triage, analisis = consumidores(eventos)
    lector = AsyncMock(return_value=TRANSCRIPCION)
    loop = motor(monkeypatch, ingest_fn=server._radar_ingestar_manual,
                 triage_fn=triage, analyze_fn=analisis, image_reader_fn=lector)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))
    lector.assert_awaited_once_with([IMAGEN])
    assert eventos[1][1]["texto"] == texto_objetivo + SEPARADOR + TRANSCRIPCION
    assert eventos[1][1]["emails"] == ["rrhh@acme.test"]
    assert radar_store.get_post(pid)["texto"] == texto_objetivo


@pytest.mark.asyncio
async def test_pipeline_real_reutiliza_analisis_sin_requisitos(monkeypatch):
    from core.image_reader import texto_para_analisis

    analisis = {"puesto": "Analista QA", "empresa": "Acme", "requisitos": [], "match_score": 80,
                "idioma_cv": "es"}
    pid = insertar()
    radar_store.update_post(pid, texto_imagen=TRANSCRIPCION, analisis=analisis)
    estratega = AsyncMock(return_value=deepcopy(analisis))

    async def desde_maestro(postulacion_id, analisis_recibido):
        server.update_postulacion(postulacion_id, cv_markdown=SAMPLE_E2E_CV, pitch="", pitches_multicanal={},
                                  status="READY")

    # El CV sale del maestro: se mide que el Radar reutilice el análisis sin repetir el Estratega
    generar = AsyncMock(side_effect=desde_maestro)
    monkeypatch.setattr(server.StrategistAgent, "analyze_offer", estratega)
    monkeypatch.setattr(server, "_generar_desde_maestro", generar)
    post = radar_store.get_post(pid)
    post["texto"] = texto_para_analisis(post)
    postulacion_id = await server._radar_pipeline(post)
    estratega.assert_not_awaited()
    generar.assert_awaited_once()
    assert generar.await_args.args[1]["requisitos"] == []
    postulacion = server.get_postulacion(postulacion_id)
    assert postulacion["status"] == "READY"
    assert postulacion["contenido_oferta"] == "Mirá la oferta" + SEPARADOR + TRANSCRIPCION
    assert postulacion["analisis"] == analisis
    assert radar_store.get_post(pid)["aprobado_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fuente", ["feed", "busqueda"])
async def test_guardado_graficos_distintos(monkeypatch, fuente):
    loop = motor(monkeypatch)
    posts = [{"urn": f"urn:li:activity:grafico-{i}", "texto": "", "emails": [],
              "imagenes": [IMAGEN.replace("oferta.png", f"oferta-{i}.png")]} for i in (1, 2)]
    # Conserva el filtro de posts vacíos sin imágenes.
    posts.append({"urn": "urn:li:activity:sin-contenido", "texto": "", "emails": [], "imagenes": []})
    await loop._guardar_posts(posts, "qa", fuente=fuente)
    filas = radar_store.list_posts()
    assert {p["urn"] for p in filas} == {p["urn"] for p in posts[:2]}
    assert all(p["texto"] == "" and p["imagenes"] for p in filas)
    await loop._guardar_posts(posts, "qa", fuente=fuente)
    assert {p["id"] for p in radar_store.list_posts()} == {p["id"] for p in filas}


@pytest.mark.asyncio
async def test_cv_con_imagen_sin_reanalisis(monkeypatch):
    pid = insertar()
    radar_store.update_post(pid, texto_imagen=TRANSCRIPCION, analisis=ANALISIS, estado="GENERANDO")
    fila = radar_store.get_post(pid)
    original = deepcopy(fila)
    pipeline, compose = AsyncMock(return_value=123), AsyncMock(return_value={
        "to": "rrhh@acme.test", "subject": "QA", "body": "Hola",
    })
    analisis = AsyncMock(side_effect=AssertionError("no reanalizar al generar"))
    lector = AsyncMock(side_effect=AssertionError("no releer al generar"))
    loop = motor(monkeypatch, pipeline_fn=pipeline, compose_fn=compose,
                 analyze_fn=analisis, image_reader_fn=lector)
    await loop._generar_post(fila)
    pipeline.assert_awaited_once()
    compose.assert_awaited_once()
    for recibido in (pipeline.await_args.args[0], compose.await_args.args[0]):
        assert recibido["texto"] == "Mirá la oferta" + SEPARADOR + TRANSCRIPCION
        assert recibido["analisis"] == ANALISIS
    assert compose.await_args.args[1] == 123
    analisis.assert_not_awaited()
    lector.assert_not_awaited()
    assert fila == original, "Los consumidores deben recibir una copia"
    guardado = radar_store.get_post(pid)
    assert guardado["texto"] == original["texto"]
    assert guardado["texto_imagen"] == TRANSCRIPCION
    assert guardado["estado"] == "LISTO" and guardado["aprobado_at"] is None


def test_detalle_api(monkeypatch):
    pid = insertar()
    assert pid == 1
    texto = '<img src=x onerror="alert(1)">\nAnalista QA & requisitos'
    radar_store.update_post(pid, texto_imagen=texto)
    monkeypatch.setattr(server, "radar_loop", Mock())
    client = TestClient(server.app)  # Sin lifespan: nunca arrancar Radar/servidor.
    try:
        respuesta = client.get("/api/radar/posts/1")
        assert respuesta.status_code == 200
        detalle = respuesta.json()
        assert detalle["imagenes"] == [IMAGEN]
        assert detalle["texto_imagen"] == texto
        assert detalle["texto"] == "Mirá la oferta"
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fuente", ["feed", "busqueda"])
@pytest.mark.parametrize("compatible", [True, False])
async def test_geografia_despues_de_leer_imagen(monkeypatch, fuente, compatible):
    eventos = []
    transcripcion = "Vacante de analista. Requisitos: experiencia. " + (
        "Aceptamos candidatos de Argentina." if compatible else "Presencial en Madrid."
    )

    async def leer(imagenes):
        eventos.append("lectura")
        return transcripcion

    async def clasificar(lote):
        eventos.append("triage")
        assert lote[0]["texto"] == "Vacante en Madrid" + SEPARADOR + transcripcion
        return {p["id"]: {"es_busqueda_laboral": True, "puesto": "Analista",
                           "alcance": "EXTERIOR_COMPATIBLE"} for p in lote}

    lector = AsyncMock(side_effect=leer)
    triage = AsyncMock(side_effect=clasificar)
    loop = motor(monkeypatch, image_reader_fn=lector, triage_fn=triage)
    await loop._guardar_posts([{"urn": "urn:li:activity:geo", "texto": "Vacante en Madrid",
                                "imagenes": [IMAGEN], "emails": []}], "qa", fuente=fuente)
    fila, = radar_store.list_posts()
    assert fila["estado"] == "NUEVO", "No descartar antes de completar la lectura"
    await loop._clasificar([fila])
    guardado = radar_store.get_post(fila["id"])
    lector.assert_awaited_once_with([IMAGEN])
    assert guardado["texto"] == "Vacante en Madrid"
    assert guardado["texto_imagen"] == transcripcion
    if compatible:
        assert eventos == ["lectura", "triage"]
        assert guardado["estado"] == "OFERTA"
    else:
        assert eventos == ["lectura"]
        triage.assert_not_awaited()
        assert guardado["estado"] == "DESCARTADO"
        assert guardado["triage"]["motivo_descarte"] == "prefiltro"


@pytest.mark.asyncio
@pytest.mark.parametrize("imagenes", [[], [IMAGEN]])
async def test_prefiltro_se_conserva_para_texto_completo(monkeypatch, imagenes):
    lector = AsyncMock(side_effect=AssertionError("no leer oferta completa"))
    triage = AsyncMock(side_effect=AssertionError("conservar prefiltro sin IA"))
    loop = motor(monkeypatch, image_reader_fn=lector, triage_fn=triage)
    await loop._guardar_posts([{"urn": "urn:li:activity:completo", "texto": LARGO + " Presencial en Madrid.",
                                "imagenes": imagenes, "emails": ["rrhh@acme.test"]}], "qa", fuente="feed")
    fila, = radar_store.list_posts()
    assert fila["estado"] == "DESCARTADO"
    assert fila["triage"]["motivo_descarte"] == "prefiltro"
    lector.assert_not_awaited()
    triage.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefiltro_no_pisa_descarte_durante_lectura(monkeypatch):
    pid = insertar("Vacante en Madrid")

    async def leer(imagenes):
        radar_store.update_post(pid, estado="DESCARTADO", triage={"motivo_descarte": "manual"})
        return "Vacante presencial en Madrid. Requisitos: experiencia en Python."

    triage = AsyncMock(side_effect=AssertionError("El usuario ya lo descartó"))
    loop = motor(monkeypatch, image_reader_fn=leer, triage_fn=triage)
    await loop._clasificar([radar_store.get_post(pid)])
    fila = radar_store.get_post(pid)
    assert fila["estado"] == "DESCARTADO"
    assert fila["triage"] == {"motivo_descarte": "manual"}
    assert fila["aprobado_at"] is None
    triage.assert_not_awaited()


@pytest.mark.asyncio
async def test_contactos_persisten_en_detalle_y_generacion(monkeypatch):
    pid = insertar()
    radar_store.update_post(pid, emails=["html@acme.test"], links=["https://acme.test/original"],
                            whatsapp=["5491122223333"])
    transcripcion = (TRANSCRIPCION + " Postulate en https://acme.test/empleos "
                     "o por WhatsApp https://wa.me/5491144445555")
    eventos = []
    triage, analisis = consumidores(eventos)
    lector = AsyncMock(return_value=transcripcion)
    pipeline, compose = AsyncMock(return_value=123), AsyncMock(return_value={
        "to": "rrhh@acme.test", "subject": "QA", "body": "Hola",
    })
    loop = motor(monkeypatch, image_reader_fn=lector, triage_fn=triage, analyze_fn=analisis,
                 pipeline_fn=pipeline, compose_fn=compose)
    await loop._clasificar([radar_store.get_post(pid)])
    await loop._analizar(radar_store.get_post(pid))
    fila = radar_store.get_post(pid)
    assert fila["emails"] == ["html@acme.test", "rrhh@acme.test"]
    assert "https://acme.test/original" in fila["links"]
    assert "https://acme.test/empleos" in fila["links"]
    assert fila["whatsapp"] == ["5491122223333", "5491144445555"]
    assert fila["destino_canal"] == "" and fila["email_to"] is None
    assert fila["aprobado_at"] is None
    monkeypatch.setattr(server, "radar_loop", Mock())
    client = TestClient(server.app)  # Sin lifespan ni workers.
    try:
        respuesta = client.get(f"/api/radar/posts/{pid}")
        assert respuesta.status_code == 200
        detalle = respuesta.json()
    finally:
        client.close()
    radar_store.update_post(pid, estado="GENERANDO")
    await loop._generar_post(radar_store.get_post(pid))
    for recibido in (detalle, eventos[0][1][0], eventos[1][1],
                     pipeline.await_args.args[0], compose.await_args.args[0]):
        for campo in ("emails", "links", "whatsapp"):
            assert recibido[campo] == fila[campo]
    lector.assert_awaited_once_with([IMAGEN])
    assert radar_store.get_post(pid)["aprobado_at"] is None
    loop._send_fn.assert_not_awaited()
