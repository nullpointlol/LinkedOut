"""Contrato D: pitch definitivo, borrador por ejecución y drenaje de ramas.

El loop configura INVARIANT_CHECK; las guardas de tiempo detectan bloqueos,
no miden velocidad. Sólo proveedores falsos y datos aislados por conftest.
"""
import asyncio
from collections import defaultdict
from copy import deepcopy
import itertools
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.cv_final_prueba import pdf_de_prueba
from fastapi.testclient import TestClient

import server
from core import database, radar_store as store, progreso
from core.auditor import AuditorAgent
from core.bridge import AntigravityBridge
from core.email_composer import (
    compose_message, build_email_signature, build_signature, problemas_de_tono,
)
from core.mailer import GmailConfig
from core.pdf_generator import compile_markdown_to_pdf
from core.radar import RadarLoop
from tests.conftest import VOZ_TEST
from tests.test_cv_formatting_and_a4_e2e import KEYWORDS, EXPECTED_COMPANIES
from tests.test_email_composer import PROFILE
from tests.test_rendimiento_auditoria import (
    BridgeContado, CV_ES, CV_INGLES, KW_FALTANTES, informe_fijo, pdf_fiel,
)
from tests.test_rendimiento_dos_workers import _base_pausa, _base_cuota, _base_fallback
from tests.test_rendimiento_admision import (
    cuota_controlada,
    test_invariante_generacion_cuota_y_cancelacion as _invariante_cuota,
)


REVISION_REAL = AuditorAgent.revisar_senales_ia
RUN_PROMPT_REAL = AntigravityBridge.run_prompt
GUARDA = 15  # tope de espera de vida (no de rendimiento): con -n auto y carga, 3 s daba falsos rojos
_URNS = itertools.count(97000)
PITCH = "Hola [Nombre], me interesa la vacante."
PITCH_FINAL = "Hola equipo, me interesa la vacante."
TOKENS = {"CV": 11, "pitch": 13, "mensaje (email)": 17, "compose": 19}


def _prohibido(*args, **kwargs):
    raise AssertionError("Se intentó usar un proveedor real")


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    monkeypatch.setattr(AntigravityBridge, "run_prompt", AsyncMock(side_effect=_prohibido))
    monkeypatch.setattr(server, "LinkedInHarvester", _prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP", _prohibido)


def _post(*, analizado=True):
    n = next(_URNS)
    post_id = store.insert_post_if_new({
        "urn": f"urn:li:activity:{n}", "autor": f"Equipo {n}",
        "texto": (
            # Captura válida (≥ 400 caracteres) pero breve en palabras: el composer falso arma el
            # cuerpo con este texto y el mail tiene un máximo de palabras.
            f"Buscamos analista funcional, referencia {n}. Responsabilidades: relevamiento, "
            "documentación, especificaciones funcionales, validaciones, seguimiento de requerimientos, "
            "pruebas de aceptación, análisis de incidentes productivos, consultas SQL, coordinación "
            "interdisciplinaria con desarrollo y soporte. Modalidad híbrida, jornada completa, "
            "capacitación permanente. Enviar currículum al correo indicado."
        ),
        "emails": [f"rrhh{n}@example.com"],
    }, "prueba")
    analisis = {
        "puesto": "Analista funcional", "empresa": f"Empresa {n}",
        "dolores_empresa": [f"Demoras {n}"], "match_score": 90,
        "requisitos": [{"nombre": k, "estado": "MATCH_DIRECTO", "approved": True}
                       for k in KEYWORDS],
    }
    store.update_post(post_id, estado="GENERANDO", canal="email",
                      destino_canal=f"rrhh{n}@example.com",
                      triage={"puesto": "Analista funcional", "empresa": f"Empresa {n}"},
                      analisis=analisis if analizado else {})
    return store.get_post(post_id)


async def _esperar(awaitable, mensaje):
    try:
        return await asyncio.wait_for(awaitable, GUARDA)
    except asyncio.TimeoutError:
        pytest.fail(mensaje)


class Finalizacion:
    """Falsos sólo en fronteras: pipeline, completitud y validadores son reales."""
    def __init__(self, monkeypatch):
        self.cv = CV_ES
        self.reporte = {"approved": True, "score_ats": 100}
        self.bloqueadas = set()
        self.fallos = set()
        self.inicio = defaultdict(asyncio.Event)
        self.liberar = defaultdict(asyncio.Event)
        self.fin = defaultdict(asyncio.Event)
        self.cambio = asyncio.Event()
        self.tareas = set()
        self.llamadas = []
        self.composiciones = []
        self.eventos = []
        self.pasos = []
        self.fallback = False
        self.dejar_placeholder = False
        self.writer = AsyncMock(side_effect=self.redactar)
        self.audit = AsyncMock(side_effect=self.auditar)
        self.estratega = AsyncMock(return_value={
            "puesto": "Analista funcional", "empresa": "Empresa sintética",
            "requisitos": [{"nombre": k, "approved": True} for k in KEYWORDS],
            "match_score": 90,
        })
        bridge = SimpleNamespace(last_telemetry={"status": "SUCCESS"})
        monkeypatch.setattr(server, "WriterAgent", lambda *a, **kw: SimpleNamespace(
            generate_cv_and_pitch=self.writer, bridge=bridge))
        monkeypatch.setattr(server, "StrategistAgent", lambda *a, **kw: SimpleNamespace(
            analyze_offer=self.estratega, bridge=bridge))
        monkeypatch.setattr(server, "AuditorAgent", lambda *a, **kw: SimpleNamespace(
            audit_and_remediate_all=self.audit, bridge=bridge))
        monkeypatch.setattr(server, "completar", self.completar)
        monkeypatch.setattr(server, "corregir_puesto", AsyncMock(side_effect=lambda cv, **k: (cv, None)))
        # La mención ATS del título del aviso en PERFIL tiene sus propios tests (test_titulo_cv_*).
        monkeypatch.setattr(server, "asegurar_mencion_ats", lambda cv, titulo: cv)
        monkeypatch.setattr(server, "_perfil_o_vacio", lambda: {})
        monkeypatch.setattr(server, "compose_message", self.compose)
        monkeypatch.setattr(server, "broadcast_event", self.broadcast)
        self._cv_desde_maestro(monkeypatch)

    def _cv_desde_maestro(self, monkeypatch):
        """CV desde el maestro: corre el generador real del server (`_generar_desde_maestro`, con el
        mail en paralelo y el borrador) y solo la IA del CV es falsa, en la frontera «CV»: arranca el mail con el CV
        ajustado y queda en la frontera mientras se audita. Con `fallback`, la IA falla como fallaba el Redactor."""
        import tempfile
        from pathlib import Path
        from core import cv_maestro

        datos = Path(tempfile.mkdtemp(prefix="cv_maestro_test_"))
        (datos / "candidato").mkdir()
        (datos / "candidato" / "experiencia.txt").write_text("## Acme\n### Analista | 2020 - 2024\n• Probé SQL.\n",
                                                             encoding="utf-8")
        (datos / "candidato" / "personales.txt").write_text("Ana Prueba\nana@example.com\n", encoding="utf-8")
        monkeypatch.setattr(server, "DATA_DIR", datos)
        monkeypatch.setattr(server, "asegurar_anios_por_area", AsyncMock(return_value=[]))
        from core.titulo_cv import DecisionTitulo
        monkeypatch.setattr(server, "decidir_titulo", AsyncMock(return_value=DecisionTitulo(
            caso=1, clave=None, titulo="Analista funcional", roles_reales=(), puesto_oferta="Analista funcional")))
        fin = self

        class FlujoSimulado:
            def __init__(self, **kw):
                self.kw, self.segundos, self.historial = kw, {}, []

            async def generar(self):
                redaccion = await fin.writer(self.kw.get("analisis"), datos_de_terceros=True)
                if redaccion.get("is_fallback"):
                    raise RuntimeError(redaccion.get("fallback_reason") or "sin respuesta del modelo")
                if self.kw.get("al_ajustar"):
                    self.kw["al_ajustar"](redaccion["cv_markdown"])
                await fin.frontera("CV")
                reporte = deepcopy(fin.reporte)
                if reporte.get("approved") is False:
                    raise cv_maestro.CVNoAprobado("CV no aprobado tras la corrección: "
                                                  + "; ".join(map(str, reporte.get("deducciones") or ["rechazo"])))
                return SimpleNamespace(markdown=redaccion["cv_markdown"], correcciones=0, reporte=reporte,
                                       keywords_cv={}, segundos={}, habilitados_usados=[],
                                       pdf=pdf_de_prueba())

        monkeypatch.setattr(server.cv_maestro, "FlujoCV", FlujoSimulado)

    async def redactar(self, *a, **kw):
        return {"cv_markdown": self.cv, "pitch": PITCH,
                "pitches_multicanal": {"email": "Canal para [Nombre]."},
                "is_fallback": self.fallback, "fallback_reason": "fallo sintético"}

    async def auditar(self, cv, pitch, pitches, **kw):
        return cv, pitch, pitches, deepcopy(self.reporte)

    async def broadcast(self, tipo, datos):
        self.eventos.append((tipo, deepcopy(datos)))

    async def paso(self, paso, sub=None):
        self.pasos.append(paso)

    async def frontera(self, tipo):
        self.tareas.add(asyncio.current_task())
        self.llamadas.append(tipo)
        self.inicio[tipo].set()
        self.cambio.set()
        try:
            if tipo in self.bloqueadas:
                await self.liberar[tipo].wait()
            if tipo in self.fallos:
                raise RuntimeError(f"fallo controlado {tipo}")
            if tipo in TOKENS:
                # Telemetría real de etapa, sin run_prompt ni last_telemetry compartido.
                bridge = AntigravityBridge()
                bridge.last_telemetry = {"total_tokens": TOKENS[tipo]}
                bridge._anotar_llamada(None)
        finally:
            self.fin[tipo].set()

    async def completar(self, texto, *, tipo, **kw):
        await self.frontera(tipo)
        if self.dejar_placeholder:
            return texto, []
        return texto.replace("[Nombre]", "equipo"), []

    async def compose(self, post, postulacion):
        self.composiciones.append((deepcopy(post), deepcopy(postulacion)))
        await self.frontera("compose")
        return {"to": post.get("destino_canal") or post["emails"][0],
                "subject": f"Postulación {post['id']}",
                "body": PITCH_FINAL + " " + post["texto"], "is_fallback": False}  # sin pitch: apertura fija del mail falso

    async def circuito(self, post):
        with progreso.usar_reportero(self.paso):
            pid = await server._radar_pipeline(post)
            mensaje = await server._radar_compose(store.get_post(post["id"]), pid)
        return pid, mensaje

    async def limpiar(self, *padres):
        # Contener también la implementación roja sin dejar tareas entre tests.
        tareas = (self.tareas | set(padres)) - {asyncio.current_task()}
        for tarea in tareas:
            if not tarea.done():
                tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)

    async def hasta(self, predicado, mensaje):
        async def observar():
            while not predicado():
                self.cambio.clear()
                await self.cambio.wait()
        await _esperar(observar(), mensaje)

    def sin_listo(self, post):
        actual = store.get_post(post["id"])
        assert actual["estado"] != "LISTO"
        assert not actual.get("email_body") and actual.get("aprobado_at") is None
        postu = server._buscar_postulacion_radar(actual)
        assert postu and postu["status"] != "READY"
        assert not any(t == "complete" or d.get("status") == "READY"
                       for t, d in self.eventos)


@pytest.fixture
def finalizacion(monkeypatch):
    return Finalizacion(monkeypatch)


async def _base(finalizacion):
    post = _post()
    pid, msg = await finalizacion.circuito(post)
    actual = database.get_postulacion(pid)
    # invariante: circuito actual completa pitch/canal y preserva el CV sin aprobar envío.
    assert actual["status"] == "READY" and actual["cv_markdown"] == finalizacion.cv
    assert actual["pitch"] == "" and not actual["pitches_multicanal"]  # CV desde el maestro: sin pitch ni canales
    assert msg["body"].startswith(PITCH_FINAL)
    assert msg["to"] == post["destino_canal"]
    assert store.get_post(post["id"])["aprobado_at"] is None
    assert len(finalizacion.composiciones) == 1
    finalizacion.eventos.clear()
    finalizacion.composiciones.clear()
    finalizacion.llamadas.clear()
    finalizacion.tareas.clear()
    finalizacion.inicio.clear()
    finalizacion.fin.clear()
    finalizacion.pasos.clear()
    return post, pid


async def _envio_real_aislado(tmp_path, monkeypatch, pid, msg):
    """Mismo CV finalizado, PDF real y mailer/worker reales con SMTP falso."""
    with monkeypatch.context() as m:
        eventos, entregas, control = [], [], {}
        m.setattr(server, "load_gmail_config", lambda: GmailConfig("test@example.com", "clave-falsa"))
        m.setattr(server, "load_candidate_profile", lambda: dict(PROFILE))
        m.setattr(server, "compile_markdown_to_pdf", lambda cv, **kw: compile_markdown_to_pdf(
            cv, output_filename=str((tmp_path / "envio.pdf").resolve())))

        class SMTP:
            def __init__(self, *a, **kw):
                pass

            def login(self, *a):
                eventos.append("login")
                if control["cambio"] == "revocar":
                    store.update_post(control["post"], aprobado_at=None)
                if control["cambio"] == "cv":
                    database.update_postulacion(pid, cv_markdown=CV_ES + "\nCambio en login")

            def send_message(self, mensaje):
                eventos.append("entrega")
                entregas.append(mensaje)

            def quit(self):
                return 221, b"bye"

            def close(self):
                pass

        m.setattr("core.mailer.smtplib.SMTP_SSL", SMTP)
        enviar = server.send_application_email

        def envio(*args, **kwargs):
            validar = kwargs["before_send"]

            def validacion():
                eventos.append("validacion")
                validar()

            kwargs["before_send"] = validacion
            return enviar(*args, **kwargs)

        m.setattr(server, "send_application_email", envio)
        loop = RadarLoop(harvester_factory=_prohibido, triage_fn=None, analyze_fn=None,
                         pipeline_fn=None, compose_fn=None, send_fn=server._radar_send,
                         broadcast=AsyncMock(), debug_dir=tmp_path / "debug")
        m.setattr(server, "radar_loop", loop)
        client = TestClient(server.app)  # sin lifespan ni arranque del Radar
        try:
            for cambio in ("sin_aprobacion", "vencida", "revocar", "cv", "valido"):
                # Restaurar antes de aprobar: cambiar el CV revoca todas sus aprobaciones.
                database.update_postulacion(pid, cv_markdown=CV_ES)
                post = _post()
                store.update_post(post["id"], estado="LISTO", postulacion_id=pid,
                                  email_to=msg["to"], email_subject=msg["subject"], email_body=msg["body"])
                if cambio in ("sin_aprobacion", "vencida"):
                    payload = {} if cambio == "sin_aprobacion" else {"updated_at": "2000-01-01"}
                    r = client.post(f"/api/radar/posts/{post['id']}/approve", json=payload)
                    # invariante: falta de versión=400; versión vencida=409; cero entregas.
                    assert r.status_code == (400 if cambio == "sin_aprobacion" else 409)
                else:
                    store.update_post(post["id"], aprobado_at="2026-11-07 10:00:00")
                control.update(cambio=cambio, post=post["id"])
                eventos.clear()
                entregas.clear()
                resultado = await loop._procesar_envio(post["id"])
                actual = store.get_post(post["id"])
                # invariante: aprobación vigente y login < validación < entrega.
                if cambio == "valido":
                    assert resultado is True and len(entregas) == 1, (actual, eventos)
                    assert eventos == ["login", "validacion", "entrega"]
                    assert actual["estado"] == "ENVIADO" and actual["sent_at"] and actual["message_id"]
                    assert store.get_radar_state()["contadores_dia"]["enviados"] == 1
                else:
                    assert resultado is False and entregas == []
                    assert eventos == (["login", "validacion"] if cambio in ("cv", "revocar") else [])
                    assert actual["sent_at"] is None and actual["message_id"] is None
                    assert actual["estado"] != "ENVIADO"
                    assert store.get_radar_state()["contadores_dia"]["enviados"] == 0
        finally:
            client.close()
            await loop.stop_for_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["es", "en", "keywords"])
async def test_invariantes_calidad_envio_y_fallback(monkeypatch, tmp_path, finalizacion, caso):
    f = finalizacion
    ingles = caso == "en"
    f.cv = CV_INGLES if ingles else CV_ES
    keywords = ["SQL", "Python", "Power BI"] if ingles else KW_FALTANTES if caso == "keywords" else KEYWORDS
    empresas = ["Acme"] if ingles else EXPECTED_COMPANIES
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)
    bridge = BridgeContado()
    cv, _, f.reporte = await AuditorAgent(bridge=bridge).audit_and_remediate(
        f.cv, PITCH_FINAL, keywords, max_attempts=0, expected_companies=empresas)
    # invariante: lente real, una llamada falsa y referencias actuales completas.
    assert len(cv) == (621 if ingles else 1326)
    assert len(bridge.revisiones) == 1 and bridge.remediaciones == []
    informe_fijo(f.reporte, ingles=ingles, faltantes=caso == "keywords")
    post = _post(analizado=False)
    if caso == "keywords":
        # CV desde el maestro: solo bloquean las keywords en match; las que el perfil no tiene no frenan
        from core import auditor as auditoria_cv
        f.reporte = auditoria_cv.aceptar_limites_del_perfil(deepcopy(f.reporte))
        # faltan keywords que el perfil no tiene: el CV sigue con observaciones (antes
        # quedaba en ERROR para revisar a mano). La medición se conserva.
        pid, msg = await f.circuito(post)
        actual = database.get_postulacion(pid)
        reporte = actual["reporte_auditoria"]
        assert actual["status"] == "READY" and actual["cv_markdown"] == cv
        assert reporte["approved"] is True and reporte["limites_perfil"] is True
        assert reporte["ats"]["missing_keywords"] == f.reporte["ats"]["missing_keywords"]
        f.estratega.assert_awaited_once()
        return
    pid, msg = await f.circuito(post)
    actual = database.get_postulacion(pid)
    assert actual["cv_markdown"] == cv
    informe_fijo(actual["reporte_auditoria"], ingles=ingles, faltantes=caso == "keywords")
    pdf_fiel(actual["cv_markdown"], f.cv, empresas, tmp_path, "finalizado.pdf")
    f.estratega.assert_awaited_once()
    assert msg["body"].startswith(PITCH_FINAL)
    if caso == "es":
        await _envio_real_aislado(tmp_path, monkeypatch, pid, msg)
        # El llamador directo no hace la transición del worker: retirar este caso
        # de su cola antes de medir las colas sintéticas de pausa/cuota.
        store.update_post(post["id"], estado="LISTO")
        await _base_pausa(tmp_path)
        await _base_cuota(tmp_path)
        # _base_fallback probaba el fallback del Redactor viejo, que ya no existe (CV desde el maestro)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["exito_viejo", "seguidor", "recuperacion"])
async def test_invariantes_cuota_generaciones_y_dueno(monkeypatch, tmp_path, cuota_controlada, caso):
    # invariante: reutilizar el escenario de cuota aprobado, con intento físico
    # falso. Respuesta vieja, dueño exclusivo, seguidor cancelado y plazo intacto.
    monkeypatch.setattr(AntigravityBridge, "run_prompt", RUN_PROMPT_REAL)
    await _invariante_cuota(monkeypatch, cuota_controlada, tmp_path, caso)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


@pytest.mark.asyncio
@pytest.mark.parametrize("analizado", [True, False])
@pytest.mark.parametrize("primera", ["CV", "compose"])
async def test_mensaje_empieza_con_pitch_definitivo_mientras_cv_espera(finalizacion, analizado, primera):
    f = finalizacion
    await _base(f)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # defecto: completar CV retenido no impide pitch/canales/composer definitivos.
    post = _post(analizado=analizado)
    f.bloqueadas = {"CV", "compose"}
    tarea = asyncio.create_task(f.circuito(post))
    try:
        await _esperar(f.inicio["CV"].wait(), "No empezó completar(CV)")
        await _esperar(f.inicio["compose"].wait(),
                       "Serialización: compose no empezó mientras completar(CV) esperaba")
        f.sin_listo(post)
        snapshot_post, snapshot = f.composiciones[0]
        assert snapshot["pitch"] == "" and not snapshot["pitches_multicanal"]
        assert snapshot_post["id"] == post["id"]
        f.liberar[primera].set()
        await _esperar(f.fin[primera].wait(), "No finalizó la primera rama liberada")
        f.sin_listo(post)
        assert not tarea.done()
        f.liberar["compose" if primera == "CV" else "CV"].set()
        pid, msg = await _esperar(tarea, "No reunió ambas ramas")
        assert msg["body"].startswith(PITCH_FINAL)
        assert len(f.composiciones) == 1, "Consumir el borrador volvió a componer"
        # Telemetría por etapa del pipeline viejo (pitch, canales): el flujo del maestro mide sus segundos aparte.
        assert "completar_mensaje" in f.pasos
        assert [progreso.PORCENTAJE[p] for p in f.pasos] == sorted(
            progreso.PORCENTAJE[p] for p in f.pasos)
    finally:
        await f.limpiar(tarea)


@pytest.mark.asyncio
# «pitch» y «mensaje (email)» eran ramas del pipeline viejo; si falla el mail, el CV aprobado se guarda
# (test_cv_maestro_pipeline.test_si_falla_el_mail_el_cv_aprobado_queda_guardado), así que «compose» tampoco aplica.
@pytest.mark.parametrize("corte", ["CV", "cancelacion"])
async def test_fallo_cancelacion_no_publican_listo_ni_dejan_tareas(finalizacion, corte):
    f = finalizacion
    await _base(f)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    post = _post()
    f.bloqueadas = {"CV", "compose"}
    if corte in ("pitch", "mensaje (email)"):
        f.bloqueadas.add(corte)
    f.fallos = {corte} if corte != "cancelacion" else set()
    tarea = asyncio.create_task(f.circuito(post))
    try:
        await _esperar(f.inicio["CV"].wait(), "No empezó rama CV")
        otra = corte if corte in ("pitch", "mensaje (email)") else "compose"
        await _esperar(f.inicio[otra].wait(), "Las ramas no se solapan antes del corte")
        f.sin_listo(post)
        if corte == "cancelacion":
            tarea.cancel()
            with pytest.raises(asyncio.CancelledError):
                await _esperar(tarea, "La cancelación no drenó las ramas")
        else:
            f.liberar[corte].set()
            with pytest.raises(RuntimeError):
                await _esperar(tarea, "El fallo no canceló/drenó la rama hermana")
        # Comprobar ANTES del cleanup del test: gather solo no cancela hermanas.
        assert all(t.done() for t in f.tareas), "Quedaron hijas vivas después del corte"
        f.sin_listo(post)
        assert store.get_post(post["id"])["postulacion_id"] is None
        anterior = server._buscar_postulacion_radar(post)["id"]
        f.fallos.clear()
        f.bloqueadas.clear()
        f.eventos.clear()
        f.composiciones.clear()
        pid, msg = await _esperar(f.circuito(store.get_post(post["id"])), "El reintento quedó bloqueado")
        assert pid == anterior and msg["body"].startswith(PITCH_FINAL)
        assert len(f.composiciones) == 1, "Reintento consumió un borrador de la ejecución fallida"
    finally:
        await f.limpiar(tarea)


@pytest.mark.asyncio
@pytest.mark.parametrize("cambio", ["ninguno", "pitch", "canales", "texto", "canal", "destino", "instrucciones", "oferta"])
async def test_borrador_no_se_cruza_ni_reutiliza_si_cambian_entradas(finalizacion, cambio):
    f = finalizacion
    await _base(f)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    a, b = _post(), _post()
    # Barrera real: ambos CV y mensajes están pendientes al mismo tiempo.
    f.bloqueadas = {"CV", "compose"}
    tareas = [asyncio.create_task(server._radar_pipeline(p)) for p in (a, b)]
    try:
        await f.hasta(lambda: f.llamadas.count("CV") == 2 and len(f.composiciones) == 2,
                      "No preparó dos borradores aislados mientras ambos CV esperan")
        assert {p["id"] for p, _ in f.composiciones} == {a["id"], b["id"]}
        f.sin_listo(a)
        f.sin_listo(b)
        f.liberar["compose"].set()
        f.liberar["CV"].set()
        pa, pb = await _esperar(asyncio.gather(*tareas), "No terminaron ambos pipelines")
    finally:
        await f.limpiar(*tareas)
    if cambio == "pitch":
        database.update_postulacion(pa, pitch="Presentación editada por la persona.")
    elif cambio == "canales":
        database.update_postulacion(pa, pitches_multicanal={"email": "Canal editado."})
    elif cambio == "texto":
        store.update_post(a["id"], texto="Oferta editada, otra responsabilidad.")
    elif cambio == "canal":
        store.update_post(a["id"], canal="dm", destino_canal="https://example.com/perfil")
    elif cambio == "destino":
        store.update_post(a["id"], destino_canal="otro@example.com")
    elif cambio == "instrucciones":
        store.update_post(a["id"], triage={**a["triage"], "instrucciones_postulacion": "Asunto: REF-NUEVA"})
    elif cambio == "oferta":
        database.update_postulacion(pa, titulo="Otro puesto", empresa="Otra empresa", dolores_empresa=["Otro dolor"])
    mb = await server._radar_compose(store.get_post(b["id"]), pb)
    assert len(f.composiciones) == 2, "Consumir B recompuso o invalidó el borrador ajeno"
    ma = await server._radar_compose(store.get_post(a["id"]), pa)
    assert len(f.composiciones) == (2 if cambio == "ninguno" else 3)
    assert mb["to"] == b["destino_canal"] and b["texto"] in mb["body"]
    assert b["texto"] not in ma["body"]
    ultimo_post, ultima_postu = f.composiciones[-1 if cambio != "ninguno" else 0]
    if cambio != "ninguno":
        assert ultimo_post == store.get_post(a["id"])
        assert ultima_postu["pitch"] == database.get_postulacion(pa)["pitch"]
        assert ultima_postu["titulo"] == database.get_postulacion(pa)["titulo"]
    # Consumo único: un segundo compose toma el camino legado y produce mensaje nuevo.
    antes = len(f.composiciones)
    await server._radar_compose(store.get_post(a["id"]), pa)
    assert len(f.composiciones) == antes + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("inicial,canal,sin_asunto", [
    ("email", "email", False), ("email", "dm", True), ("dm", "email", True),
])
async def test_recomposicion_sin_cache_usa_post_y_canal_vigentes(
        finalizacion, monkeypatch, tmp_path, inicial, canal, sin_asunto):
    f = finalizacion
    post = _post()
    store.update_post(post["id"], canal=inicial)
    post = store.get_post(post["id"])
    f.bloqueadas = {"CV"}
    original = f.compose

    async def componer(actual, postulacion):
        mensaje = await original(actual, postulacion)
        mensaje["body"] += " " + actual["triage"].get("instrucciones_postulacion", "")
        if sin_asunto:
            mensaje.pop("subject")
        return mensaje

    monkeypatch.setattr(server, "compose_message", componer)
    loop = RadarLoop(harvester_factory=_prohibido, triage_fn=None, analyze_fn=None,
                     pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
                     send_fn=AsyncMock(side_effect=_prohibido), broadcast=AsyncMock(),
                     debug_dir=tmp_path / "debug")
    tarea = asyncio.create_task(loop._generar_post(post))
    try:
        await _esperar(f.inicio["CV"].wait(), "El CV no llegó a la barrera")
        await _esperar(f.fin["compose"].wait(), "No terminó el borrador original")
        nuevo_texto = "Oferta actualizada: buscamos QA para otro equipo."
        destino = "nuevo@example.com" if canal == "email" else "https://example.com/nuevo"
        store.update_post(post["id"], texto=nuevo_texto, canal=canal, destino_canal=destino,
                          triage={**post["triage"], "instrucciones_postulacion": "Referencia NUEVA-42"})
        f.liberar["CV"].set()
        await _esperar(tarea, "La generación no terminó")
        actual = store.get_post(post["id"])
        # invariante: nunca se aprueba automáticamente el contenido nuevo.
        assert actual["aprobado_at"] is None
        if canal == "email" and sin_asunto:
            assert actual["estado"] == "ERROR"
            assert "sin asunto" in actual["error"]
            assert not actual.get("email_body")
        else:
            assert actual["estado"] == "LISTO", actual.get("error")
            assert actual["email_to"] == destino
            assert nuevo_texto in actual["email_body"]
            assert "Referencia NUEVA-42" in actual["email_body"]
            assert "Buscamos analista funcional" not in actual["email_body"]
            assert actual["email_subject"] == ("" if sin_asunto else f"Postulación {post['id']}")
        assert len(f.composiciones) == 2
    finally:
        await f.limpiar(tarea)
        await loop.stop_for_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("corte", ["stop", "shutdown", "descarte"])
async def test_borrador_se_limpia_con_stop_y_descarte(finalizacion, tmp_path, corte):
    f = finalizacion
    semilla, _ = await _base(f)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    store.update_post(semilla["id"], estado="LISTO")
    store.update_radar_state(backlog_activo=False)
    post = _post()
    f.bloqueadas = {"CV", "compose"}
    loop = RadarLoop(harvester_factory=_prohibido, triage_fn=None, analyze_fn=None,
                     pipeline_fn=server._radar_pipeline, compose_fn=server._radar_compose,
                     send_fn=AsyncMock(side_effect=_prohibido), broadcast=AsyncMock(),
                     debug_dir=tmp_path / "debug")
    try:
        # Sólo worker de generación con dobles; no start(), navegador ni portal.
        await loop.wake_generation()
        await _esperar(f.inicio["CV"].wait(), "El worker no inició el CV")
        await _esperar(f.inicio["compose"].wait(), "El worker serializó CV y mensaje")
        f.sin_listo(post)
        if corte == "descarte":
            store.update_post(post["id"], estado="DESCARTADO")
            assert await loop.cancelar_generacion(post["id"]) is True
            assert store.get_post(post["id"])["estado"] == "DESCARTADO"
        elif corte == "stop":
            # stop conserva su contrato de reanudar pendientes; la pausa impide
            # admitir otro trabajo, sin alterar su política en producción.
            store.update_radar_state(generacion_pausada=True)
            await _esperar(loop.stop(), "stop no drenó las ramas")
        else:
            await _esperar(loop.stop_for_shutdown(), "shutdown no drenó las ramas")
        assert all(t.done() for t in f.tareas)
        f.sin_listo(post)
        assert store.get_post(post["id"])["postulacion_id"] is None
        anterior = server._buscar_postulacion_radar(post)["id"]
        await loop.stop_for_shutdown()
        store.update_post(post["id"], estado="GENERANDO")
        f.bloqueadas.clear()
        f.composiciones.clear()
        pid, _ = await _esperar(f.circuito(store.get_post(post["id"])), "El reintento perdió el post")
        assert pid == anterior and len(f.composiciones) == 1
    finally:
        await loop.stop_for_shutdown()
        await f.limpiar()


# Diez casos de mensaje, NO corpus de aceptación de ahorro CV+mail (criterio 1).
CASOS_MENSAJE = [
    ("es_email", "es", "email", False), ("es_asunto", "es", "email", False),
    ("en_email", "en", "email", False), ("en_asunto", "en", "email", False),
    ("es_whatsapp", "es", "whatsapp", False), ("en_whatsapp", "en", "whatsapp", False),
    ("es_dm", "es", "dm", False), ("en_dm", "en", "dm", False),
    ("es_link", "es", "link", False), ("en_otro_reescritura", "en", "otro", True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("caso,idioma,canal,reescribir", CASOS_MENSAJE)
async def test_composer_una_vez_por_post_y_conserva_validadores(
        monkeypatch, finalizacion, caso, idioma, canal, reescribir):
    f = finalizacion
    post, pid = await _base(f)
    ingles = idioma == "en"
    email = canal == "email"
    firma = (build_email_signature if email else build_signature)(PROFILE)
    fijas = [VOZ_TEST["disponibilidad_en" if ingles else "disponibilidad"],
             VOZ_TEST["ubicacion_en" if ingles else "ubicacion"]]
    cuerpo = ("Hi, I tested queries with SQL." if ingles else "Hola, probé consultas con SQL.")
    cuerpo += "\n\n" + "\n".join(fijas) + "\n\n" + ("Best regards," if ingles else "Quedo a disposición,\nSaludos,")
    asunto = "REF-77" if "asunto" in caso else "QA Analyst"
    instrucciones = ("Subject: REF-77" if ingles else "Asunto: REF-77") if "asunto" in caso else ""
    texto = ("We are hiring a QA Analyst to join our team. You will work with the product team "
             "and have experience with test automation. Send your resume to rrhh@example.com"
             if ingles else "Buscamos QA Analyst. Enviá tu CV a rrhh@example.com")
    destino = "rrhh@example.com" if email else "https://example.com/contacto"
    post = {**post, "texto": texto, "canal": canal, "destino_canal": destino,
            "emails": ["rrhh@example.com"], "triage": {"puesto": "QA Analyst", "empresa": "Acme",
            "email_destino": "rrhh@example.com", "instrucciones_postulacion": instrucciones}}
    # La composición usa ahora la oferta persistida vigente, también sin caché.
    store.update_post(post["id"], **{k: post[k] for k in ("texto", "canal", "destino_canal", "emails", "triage")})
    post = store.get_post(post["id"])
    postulacion = database.get_postulacion(pid)
    llamadas = []

    async def bridge(self, prompt, **kwargs):
        llamadas.append(prompt)
        body = "Dear team, I am writing to express my interest." if reescribir and len(llamadas) % 2 else cuerpo
        return {"subject": asunto, "body": body, "to": "intruso@example.com"}

    monkeypatch.setattr(AntigravityBridge, "run_prompt", bridge)
    monkeypatch.setattr(server, "compose_message", lambda p, q: compose_message(p, q, PROFILE))
    # invariante: camino legado real, destinatario ajeno ignorado y validadores existentes.
    msg = await server._radar_compose(post, pid)
    esperadas = 2 if reescribir else 1
    assert len(llamadas) == esperadas
    assert msg["is_fallback"] is False and msg["to"] == destino
    assert msg["subject"] == (asunto if email else "")
    assert msg["body"].endswith(firma) and msg["body"].count(firma) == 1
    assert all(frase in msg["body"] for frase in fijas)
    assert problemas_de_tono(msg["body"], firma, 75 if email else None, fijas, idioma=idioma) == []
    assert len(msg["body"]) <= (500 if canal in ("whatsapp", "dm") else 700 if not email else 2000)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    # defecto: el helper nuevo conserva exactamente la composición individual validada.
    llamadas.clear()
    nuevo = await server._componer_mensaje_validado(post, postulacion)
    assert nuevo == msg and len(llamadas) == esperadas


@pytest.mark.asyncio
# «cv_heredado»: un CV del maestro no se retoca al componer (test_cv_maestro_pipeline.test_un_cv_del_maestro_no_se_retoca_al_componer)
@pytest.mark.parametrize("modo", ["fallback_recuperado", "dos_fallbacks", "placeholder"])
async def test_helper_conserva_fallback_y_completitud(monkeypatch, finalizacion, modo):
    f = finalizacion
    post, pid = await _base(f)
    postu = database.get_postulacion(pid)
    valido = {"to": post["destino_canal"], "subject": "Referencia", "body": "Hola equipo.", "is_fallback": False}
    fallback = {**valido, "is_fallback": True}
    respuestas = [fallback, valido] if modo == "fallback_recuperado" else (
        [fallback, fallback] if modo == "dos_fallbacks" else [{**valido, "body": "Hola [Nombre]."}]
        if modo == "placeholder" else [valido])
    composer = AsyncMock(side_effect=deepcopy(respuestas))
    monkeypatch.setattr(server, "compose_message", composer)
    f.dejar_placeholder = modo == "placeholder"
    if modo == "cv_heredado":
        database.update_postulacion(pid, cv_markdown=CV_ES + "\n[Nombre]")
        # invariante: corrección legada no retrocede el paso de progreso.
        real = server._asegurar_completo
        asegurar = AsyncMock(wraps=real)
        monkeypatch.setattr(server, "_asegurar_completo", asegurar)
    # invariante: un fallback reintenta; dos fallbacks o restos bloquean el mensaje.
    if modo in ("dos_fallbacks", "placeholder"):
        with pytest.raises(RuntimeError, match="dos veces" if modo == "dos_fallbacks" else "sin completar"):
            await server._radar_compose(post, pid)
    else:
        assert await server._radar_compose(post, pid) == valido
    assert composer.await_count == len(respuestas)
    if modo == "cv_heredado":
        asegurar.assert_awaited_once()
        assert asegurar.await_args.kwargs["informar_paso"] is False
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    composer.reset_mock(side_effect=True)
    composer.side_effect = deepcopy(respuestas)
    if modo in ("dos_fallbacks", "placeholder"):
        with pytest.raises(RuntimeError, match="dos veces" if modo == "dos_fallbacks" else "sin completar"):
            await server._componer_mensaje_validado(post, postu)
    else:
        assert await server._componer_mensaje_validado(post, postu) == valido
    assert composer.await_count == len(respuestas)
