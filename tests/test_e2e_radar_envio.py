"""Punta a punta, encadenado en UN test: post nuevo → clasificación → análisis → CV y mail → LISTO →
aprobar desde la API → worker de envío → SMTP simulado → ENVIADO en la base con `message_id` y `sent_at`.

El portal es el real (`server.app` con uvicorn en un puerto libre) y el Radar es un `RadarLoop` real armado
con las funciones reales del servidor. Los dobles están solo en los bordes:
- navegador de LinkedIn: un harvester falso que devuelve un post nuevo en el feed;
- IA: `AntigravityBridge.run_prompt` responde por etapa (clasificación, análisis, redactor, mail…);
- SMTP: `smtplib.SMTP_SSL` falso que guarda el mensaje. Ningún mail real sale.
Además corre en modo de prueba del mail (LINKEDOUT_MAIL_PRUEBA): el envío va a la dirección de prueba.
"""
import email
import email.policy
import json
import re
import threading
import time
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

import server
from core import database, radar_store
from core.bridge import AntigravityBridge
from core.radar import RadarLoop
from tests.conftest import VOZ_TEST
from tests.test_cv_formatting_and_a4_e2e import KEYWORDS
from tests.test_radar_envio_verificado import _fake_smtp_class
from tests.test_rendimiento_auditoria import CV_ES
from tests.test_ui_navegador import _puerto_libre

pytestmark = pytest.mark.timeout(180)

PUESTO = "Analista Funcional"
EMPRESA = "Empresa Sintética"
DESTINO_REAL = "rrhh@acme.test"
DESTINO_PRUEBA = "pruebas@example.org"
TEXTO_POST = (f"Buscamos {PUESTO} para {EMPRESA} en CABA, modalidad híbrida. Requisitos: SQL, APIs REST, "
              f"análisis funcional, testing y microservicios. Enviá tu CV a {DESTINO_REAL} con asunto REF-42.")
URN = "urn:li:activity:7300000000000000001"


class HarvesterFalso:
    """Borde «navegador de LinkedIn»: el feed trae un post nuevo una sola vez; las búsquedas, nada."""

    def __init__(self, *a, **kw):
        self.lecturas = 0

    async def open(self):
        pass

    async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
        self.lecturas += 1
        post = {"urn": URN, "url": f"https://www.linkedin.com/feed/update/{URN}/", "autor": EMPRESA,
                "cargo_autor": "Selección", "fecha_rel": "1 h", "texto": TEXTO_POST,
                "emails": [DESTINO_REAL], "links": [], "whatsapp": [], "imagenes": []}
        posts = [post] if self.lecturas == 1 else []
        return SimpleNamespace(posts=posts, needs_login=False, html="<html></html>",
                               lectura={"urns_parseados": [p["urn"] for p in posts], "motivo_fin": "fin"})

    async def harvest(self, query, **kw):
        return SimpleNamespace(posts=[], needs_login=False, html="", lectura={"motivo_fin": "fin"})

    async def check_logged_in(self):
        return True

    async def show(self):
        pass

    async def dump_debug(self, target_dir):
        pass

    async def close(self):
        pass


def _cv() -> str:
    cv = CV_ES.replace("Inglés: C1 Profesional", "Inglés: Intermedio")
    return cv.replace("# SAM ORTEGA\n", f"# SAM ORTEGA\n{PUESTO.upper()}\n", 1)


ANALISIS = {
    "es_oferta": True, "puesto": PUESTO, "empresa": EMPRESA, "idioma_cv": "es", "match_score": 90,
    "dolores_empresa": ["Demoras en las pruebas"],
    "requisitos": [{"nombre": k, "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE",
                    "justificacion": "Fixture sintética"} for k in KEYWORDS],
}
CUERPO_MAIL = ("Hola, probé consultas con SQL.\n\n" + VOZ_TEST["disponibilidad"] + "\n" + VOZ_TEST["ubicacion"]
               + "\n\nSaludos,")


def _ia_por_etapa(etapas: list):
    """Borde «IA»: responde según la etapa que pide el prompt y anota cada llamada."""
    canales = {k: "Hola, me interesa la vacante." for k in ("email", "inmail", "web_ats")}

    async def run_prompt(self, prompt, json_mode=True, **kw):
        if "Sos un clasificador de posts de LinkedIn" in prompt:
            etapa = "clasificacion"
            ids = [int(i) for i in re.findall(r'<post id="(\d+)">', prompt)]
            respuesta = {"r": [[i, 1, "AR", "Argentina", "email", PUESTO, "Asunto: REF-42"] for i in ids]}
        elif "Puntuá cuánto demuestra cada línea del CV" in prompt:
            # Selección con IA: puntúa cada viñeta y cada ítem del maestro
            etapa = "seleccion"
            respuesta = {"vinetas": {i: 7 for i in re.findall(r"^(E\d+):", prompt, re.M)},
                         "habilidades": {i: 7 for i in re.findall(r"^(H\d+):", prompt, re.M)}}
        elif "Ajustá este CV de 1 página" in prompt or "Corregí" in prompt[:200]:
            # CV desde el maestro: el ajuste no cambia líneas; el perfil lo escribe la IA
            etapa, respuesta = "ajuste", {"perfil": "Desarrolladora fullstack con experiencia en " + ", ".join(KEYWORDS) + ".",
                                          "lineas": [], "keywords_cv": {}}
        elif "CV A REMEDIAR" in prompt:
            etapa, respuesta = "remediacion", {"lineas": [], "pitch": ""}
        elif "PUESTO A POSTULAR:" in prompt:
            etapa = "redactor"
            respuesta = {"cv_markdown": _cv(), "pitch": "Hola equipo, me interesa la vacante.",
                         "pitches_multicanal": canales}
        elif "OFERTA LABORAL A ANALIZAR" in prompt:
            etapa, respuesta = "analisis", ANALISIS
        elif "Sos el corrector final" in prompt:
            etapa = "completitud"
            texto = prompt.split("<texto>\n", 1)[1].split("\n</texto>", 1)[0]
            respuesta = {"texto": texto, "cambios": []}
        elif "POST ORIGINAL DE LINKEDIN" in prompt:
            etapa, respuesta = "mail", {"subject": "REF-42", "body": CUERPO_MAIL, "to": "intruso@example.com"}
        elif kw.get("effort") == "low" and kw.get("timeout_seconds") == 120:
            etapa, respuesta = "revision", {"senales": []}
        else:
            raise AssertionError("Etapa de IA no reconocida: " + prompt[:200])
        etapas.append(etapa)
        return deepcopy(respuesta)

    return run_prompt


def _prohibido(*a, **kw):
    raise AssertionError("El test intentó usar la red o un servicio real")


def _esperar(condicion, segundos: float, que: str):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(0.2)
    pytest.fail(f"No se llegó a {que} en {segundos} s; post: {_post_actual()}")


def _post_actual():
    posts = [p for p in radar_store.list_posts() if p.get("urn") == URN]
    return posts[0] if posts else None


def test_post_nuevo_hasta_enviado_con_portal_y_radar_reales(monkeypatch, tmp_path):
    # --- Bordes: Gmail y modo de prueba desde un .env de prueba (nunca el .env real), SMTP falso
    env = tmp_path / "prueba.env"
    env.write_text(f"GMAIL_ADDRESS=yo@example.com\nGMAIL_APP_PASSWORD=clave falsa\n"
                   f"LINKEDOUT_MAIL_PRUEBA={DESTINO_PRUEBA}\n", encoding="utf-8")
    monkeypatch.setenv("LINKEDOUT_ENV_FILE", str(env))
    for clave in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "LINKEDOUT_MAIL_PRUEBA"):
        monkeypatch.setenv(clave, "")
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "1")  # el lifespan no reanuda nada solo
    smtp = {}
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(smtp))
    monkeypatch.setattr("core.mailer.smtplib.SMTP", _prohibido)
    monkeypatch.setattr(server, "scrape_job_url", _prohibido)
    monkeypatch.setattr(server, "LinkedInHarvester", _prohibido)
    etapas = []
    monkeypatch.setattr(AntigravityBridge, "run_prompt", _ia_por_etapa(etapas))
    # El envío espera 30–90 s entre mails: con uno solo no hay espera, pero se acota igual
    monkeypatch.setattr("core.radar.PAUSA_ENTRE_ENVIOS", (0.0, 0.0))

    # --- Radar real con las funciones reales del servidor; solo el harvester es falso
    radar = RadarLoop(
        harvester_factory=HarvesterFalso, triage_fn=server.triage_batch, ingest_fn=server._radar_ingestar_manual,
        analyze_fn=server._radar_analyze, pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
        send_fn=server._radar_send, broadcast=server.broadcast_event, prepare_fn=server._radar_preparar_analisis,
        queries=[], debug_dir=tmp_path / "radar_debug", perfil_secundario=tmp_path / "sin_cuenta_2",
    )
    monkeypatch.setattr(server, "radar_loop", radar)

    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=puerto, log_level="warning",
                                             lifespan="on", timeout_graceful_shutdown=5))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + 20
    while not servidor.started:
        assert time.monotonic() < limite and hilo.is_alive(), "el portal de prueba no arrancó"
        time.sleep(0.05)
    base = f"http://127.0.0.1:{puerto}"
    try:
        with httpx.Client(base_url=base, timeout=30) as api:
            assert api.get("/api/radar/status").json()["mail_prueba"] is True

            # 1) Arranca el Radar desde la API: lee el feed y guarda el post nuevo
            assert api.post("/api/radar/start").status_code == 200
            post = _esperar(_post_actual, 20, "guardar el post nuevo")
            assert post["texto"] == TEXTO_POST

            # 2) Clasificación → análisis → CV y mail → LISTO (sin aprobar)
            post = _esperar(lambda: (p := _post_actual()) and p["estado"] in ("LISTO", "ERROR") and p, 90,
                            "LISTO")
            assert post["estado"] == "LISTO", post.get("error")
            assert post["aprobado_at"] is None
            assert post["triage"]["puesto"] == PUESTO and post["canal"] == "email"
            assert post["analisis"]["empresa"] == EMPRESA
            assert post["email_to"] == DESTINO_REAL and post["email_subject"] == "REF-42"
            postulacion = database.get_postulacion(post["postulacion_id"])
            assert postulacion["status"] == "READY" and postulacion["cv_markdown"].strip()
            assert {"clasificacion", "analisis", "seleccion", "ajuste", "mail"} <= set(etapas), etapas
            assert smtp.get("messages") is None  # nada se envía sin aprobar

            # 3) Aprobar desde la API con la versión revisada → worker de envío → SMTP simulado → ENVIADO
            detalle = api.get(f"/api/radar/posts/{post['id']}").json()
            r = api.post(f"/api/radar/posts/{post['id']}/approve", json={"updated_at": detalle["updated_at"]})
            assert r.status_code == 200, r.text
            enviado = _esperar(lambda: (p := _post_actual()) and p["estado"] in ("ENVIADO", "ERROR") and p, 60,
                               "ENVIADO")
            assert enviado["estado"] == "ENVIADO", enviado.get("error")

            # 4) Base: message_id del mensaje entregado y sent_at; contador de enviados
            assert len(smtp["messages"]) == 1
            msg = email.message_from_bytes(smtp["messages"][0].as_bytes(), policy=email.policy.default)
            assert enviado["message_id"] == str(msg["Message-ID"]) and enviado["message_id"]
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*", enviado["sent_at"] or "")
            # El worker marca ENVIADO y enseguida suma el contador: se espera ese segundo paso
            _esperar(lambda: radar_store.get_radar_state()["contadores_dia"]["enviados"] == 1, 10, "contar el envío")
            assert smtp["login"] == ("yo@example.com", "clavefalsa")
            assert smtp["events"].count("send_message") == 1

            # 5) Modo de prueba: fue a la dirección de prueba, nunca al destinatario real, con «[PRUEBA]»
            assert msg["To"] == DESTINO_PRUEBA
            assert str(msg["Subject"]) == "[PRUEBA] REF-42"
            assert DESTINO_REAL not in " ".join(str(msg.get(h) or "") for h in ("To", "Cc", "Bcc"))
            adjunto = next(msg.iter_attachments())
            assert adjunto.get_content_type() == "application/pdf" and adjunto.get_content()[:4] == b"%PDF"
            assert "probé consultas con SQL" in msg.get_body(("plain",)).get_content()

            assert api.post("/api/radar/stop").status_code == 200
    finally:
        servidor.should_exit = True
        hilo.join(timeout=20)
