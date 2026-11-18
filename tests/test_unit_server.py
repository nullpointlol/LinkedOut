"""Unitarios de funciones de apoyo de `server.py` (no endpoints): eventos SSE, keywords para el
ATS, idioma del CV, telemetría, lectura de links (scraper simulado) y asunto literal del envío.
Usan la base aislada del conftest; nunca se arranca el servidor ni el Radar."""
import asyncio
import json

import pytest

import server
from core.database import create_postulacion, get_postulacion, update_postulacion


def _postulacion(**campos):
    pid = create_postulacion("Analista", "Acme", "Buscamos analista.")
    if campos:
        update_postulacion(pid, **campos)
    return pid


@pytest.mark.asyncio
async def test_evento_agrega_origen_y_descarta_colas_rotas(monkeypatch):
    """Dentro de un pipeline, el evento lleva su origen y el post del Radar; las colas que fallan se quitan."""
    monkeypatch.setattr(server, "_descartar_borradores", lambda *a: None)
    sana, rota = asyncio.Queue(), asyncio.Queue(maxsize=1)
    rota.put_nowait("lleno")
    monkeypatch.setattr(server, "subscribers", [sana, rota])
    evento = next(iter(server._EVENTOS_PIPELINE))
    t1 = server._origen_pipeline.set("radar")
    t2 = server._radar_post_pipeline.set(42)
    try:
        await server.broadcast_event(evento, {"id": 1})
    finally:
        server._origen_pipeline.reset(t1)
        server._radar_post_pipeline.reset(t2)
    mensaje = sana.get_nowait()
    datos = json.loads(mensaje.split("data: ", 1)[1])
    assert mensaje.startswith(f"event: {evento}\n")
    assert datos == {"id": 1, "origen": "radar", "radar_post_id": 42}
    assert server.subscribers == [sana]


def test_keywords_de_la_postulacion():
    """Se toman los requisitos aprobados (o del análisis si faltan), sin brechas ni repetidos."""
    postulacion = {"analisis": {"requisitos": [
        {"nombre": "SQL", "approved": True}, {"nombre": "Kubernetes", "approved": False},
        {"nombre": "Docker", "estado": "BRECHA_REAL"}, " Jira ", "SQL", {"nombre": "  "},
    ]}}
    assert server._extract_postulacion_keywords(postulacion) == ["SQL", "Jira"]


def test_guardar_idioma_del_cv_deduce_y_limpia_keywords():
    """Sin idioma explícito se deduce del CV; las keywords en inglés viejas se quitan si no vienen."""
    pid = _postulacion(analisis={"keywords_cv": {"SQL": "SQL"}, "puesto": "Analista"})
    idioma = server._guardar_idioma_del_cv(pid, {"cv_markdown": "## SUMMARY\nx\n## EXPERIENCE\ny"})
    analisis = get_postulacion(pid)["analisis"]
    assert idioma == "en" and analisis["idioma_cv"] == "en"
    assert "keywords_cv" not in analisis and analisis["puesto"] == "Analista"


def test_perfil_o_vacio_ante_error(monkeypatch):
    """Si el perfil no se puede cargar, se usa un perfil vacío."""
    def roto():
        raise OSError("sin disco")

    monkeypatch.setattr(server, "load_candidate_profile", roto)
    assert server._perfil_o_vacio() == {}


def test_sumar_etapa_reemplaza_la_misma_etapa():
    """Agregar una etapa medida reemplaza la anterior del mismo nombre en la telemetría."""
    pid = _postulacion(telemetry={"modelo": "x", "etapas": [{"etapa": "mail", "segundos": 1}]})
    server._sumar_etapa(pid, {"etapa": "mail", "segundos": 5})
    etapas = get_postulacion(pid)["telemetry"]["etapas"]
    assert [e for e in etapas if e["etapa"] == "mail"] == [{"etapa": "mail", "segundos": 5}]


def test_sumar_etapa_con_error_solo_registra(monkeypatch, caplog):
    """Si guardar la etapa falla, se registra y no se propaga."""
    def roto(pid):
        raise RuntimeError("base")

    monkeypatch.setattr(server, "get_postulacion", roto)
    server._sumar_etapa(7, {"etapa": "mail"})
    assert "No se pudo guardar la etapa mail de la postulación 7" in caplog.text


def test_limpiar_fallback_quita_solo_las_marcas():
    """Se quitan las marcas de fallback de un intento previo y se conserva el resto de la telemetría."""
    pid = _postulacion(telemetry={"is_fallback": True, "fallback_reason": "x",
                                  "status": "DETERMINISTIC_FALLBACK", "modelo": "m"})
    server._limpiar_fallback_radar(pid)
    assert get_postulacion(pid)["telemetry"] == {"modelo": "m"}
    server._limpiar_fallback_radar(999_999)  # inexistente: no hace nada


@pytest.mark.asyncio
async def test_leer_link_externo_sigue_al_destino(monkeypatch):
    """El aviso de LinkedIn de link externo se sigue hasta el destino real y se recorta el contenido."""
    paginas = {
        "https://lnkd.in/abc": {"contenido": "This link is not on LinkedIn: https://acme.example/aviso."},
        "https://acme.example/aviso": {"contenido": "x" * (server.MAX_CHARS_POR_LINK + 50)},
    }

    async def scrape(url):
        return paginas[url]

    monkeypatch.setattr(server, "scrape_job_url", scrape)
    url, contenido = await server._leer_link_oferta("https://lnkd.in/abc")
    assert url == "https://acme.example/aviso"
    assert len(contenido) == server.MAX_CHARS_POR_LINK


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [{"contenido": "algo", "error": "403"}, RuntimeError("red")])
async def test_leer_link_con_error_devuelve_vacio(monkeypatch, respuesta):
    """Si la página no se pudo leer (error o excepción) el contenido queda vacío."""
    async def scrape(url):
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    monkeypatch.setattr(server, "scrape_job_url", scrape)
    assert await server._leer_link_oferta("https://acme.example/x") == ("https://acme.example/x", "")


def test_asunto_literal_del_envio(monkeypatch):
    """El asunto exigido por la oferta manda; si no, solo vale el asunto heredado con el puesto literal."""
    monkeypatch.setattr(server, "asunto_exigido", lambda post: "")
    heredado = {"triage": {"puesto": "Analista"}, "email_subject": "Postulación – Analista"}
    assert server._asunto_literal_envio(heredado) == "Postulación – Analista"
    assert server._asunto_literal_envio({**heredado, "email_subject": "Otro – Analista"}) == ""
    monkeypatch.setattr(server, "asunto_exigido", lambda post: "REF-123")
    assert server._asunto_literal_envio(heredado) == "REF-123"
