"""Contrato independiente de la frontera final; sólo datos y proveedores sintéticos.

INVARIANT_CHECK corta después de medir la base. Los rojos restantes pertenecen
a la misión 07: no se reemplaza ninguno de los guards que deben detectarlos.
"""
import asyncio
from copy import deepcopy
import os
import re
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import database, radar_store as store, revision_store
from core.auditor import AuditorAgent, scan_anti_ai_writing
from core.bridge import AntigravityBridge
from core.email_composer import MensajeRequiereRevision
from core.mailer import GmailConfig
from core.profile_parser import parse_candidate_profile
from core.prompts_evaluacion import ATS_CRITERIA
from core.radar import RadarLoop
from core.text_normalization import normalizar_guiones
from tests.test_cv_formatting_and_a4_e2e import KEYWORDS, EXPECTED_COMPANIES
from tests.test_radar_envio_verificado import _crear_post_y_postulacion, _fake_smtp_class
from tests.test_radar_workers import make_radar
from tests.test_rendimiento_auditoria import BridgeContado, REVISION_REAL, CV_ES, KW_FALTANTES
from tests.test_rendimiento_finalizacion import Finalizacion, _post, _prohibido


CV = CV_ES.replace("SAM ORTEGA", "ANA PRUEBA").replace(
    "sam.ortega@example.com", "ana@example.com").replace(
    "https://www.linkedin.com/in/sam-ortega-ejemplo/",
    "https://linkedin.com/in/ana-prueba")
PITCH = "Hola equipo, me interesa la vacante."
RESIDUAL = "Desarrolló integraciones con SQL."
SENAL = {"frase": "trayectoria de 7 años", "patron": "plantilla controlada", "reescritura": ""}
# run_pipeline_task y recalculate_cv_task generan el CV desde el maestro: ya no pasan por el
# Redactor ni por este Auditor, y su contrato está en tests/test_cv_maestro_pipeline.py. Esta frontera sigue
# valiendo para la ruta que conserva el Auditor sobre un CV existente.
RUTAS = ("remediate_cv_task",)


@pytest.fixture
def frontera(monkeypatch):
    corregir_puesto_real = server.corregir_puesto
    perfil_real = server._perfil_o_vacio
    f = Finalizacion(monkeypatch)
    f.cv = CV
    # Finalizacion aporta writer/estratega/completitud y captura de eventos.
    # Restaurar el auditor: tanto su dictamen como los guards finales son reales.
    monkeypatch.setattr(server, "AuditorAgent", AuditorAgent)
    monkeypatch.setattr(server, "corregir_puesto", corregir_puesto_real)
    monkeypatch.setattr(server, "_perfil_o_vacio", perfil_real)
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)
    f.bridge_contado = BridgeContado()

    async def responder(self, prompt, **kw):
        return await f.bridge_contado.run_prompt(prompt, **kw)

    monkeypatch.setattr(AntigravityBridge, "run_prompt", responder)
    monkeypatch.setattr(server, "LinkedInHarvester", _prohibido)
    # La mención ATS del título del aviso en PERFIL tiene sus propios tests
    # (test_titulo_cv_*): acá se mide que el contenido aprobado no cambie por otras causas.
    monkeypatch.setattr(server, "asegurar_mencion_ats", lambda cv, titulo: cv)
    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _prohibido)
    monkeypatch.setattr("core.mailer.smtplib.SMTP", _prohibido)
    return f


def _crear(f, keywords=KEYWORDS):
    pid = database.create_postulacion(
        "Analista funcional", "Empresa sintética", "Buscamos analista funcional con SQL.",
        status="READY", origen="manual")
    database.update_postulacion(
        pid, cv_markdown=f.cv, pitch=PITCH, pitches_multicanal={"email": "Canal para equipo."},
        requisitos=[{"nombre": k, "approved": True, "estado": "MATCH_DIRECTO"} for k in keywords],
        analisis={"idioma_cv": "es"})
    return pid


def _limpiar_medidas(f):
    f.eventos.clear()
    f.llamadas.clear()
    f.composiciones.clear()
    f.bridge_contado.llamadas.clear()
    f.writer.reset_mock()
    f.estratega.reset_mock()


async def _ejecutar(ruta, pid):
    await asyncio.wait_for(getattr(server, ruta)(pid), timeout=60)


async def _base_ruta(f, ruta):
    _limpiar_medidas(f)
    pid = _crear(f)
    await _ejecutar(ruta, pid)
    actual = database.get_postulacion(pid)
    # invariante: caso limpio listo, dictamen real aprobado y contenido intacto.
    assert actual["status"] == "READY"
    assert actual["reporte_auditoria"]["approved"] is True
    assert actual["cv_markdown"] == CV
    assert actual["pitch"] == PITCH
    # invariante: una revisión limpia; ninguna remediación ni composición extra.
    assert len(f.bridge_contado.revisiones) == 1
    assert len(f.bridge_contado.remediaciones) == 0
    assert len(f.bridge_contado.llamadas) == 1
    assert f.writer.await_count == (0 if ruta == "remediate_cv_task" else 1)
    assert f.estratega.await_count == (1 if ruta == "run_pipeline_task" else 0)
    assert f.composiciones == []
    assert any(t == "complete" and d.get("status") == "READY" and d.get("id") == pid
               for t, d in f.eventos)
    _limpiar_medidas(f)
    return pid


def _motivos_visibles(pid, post_id=None):
    # La misma revisión persistida debe estar disponible en la API del panel.
    persistidos = [i for i in revision_store.listar() if i["postulacion_id"] == pid
                   and (post_id is None or i["post_id"] == post_id)]
    client = TestClient(server.app)  # sin lifespan, socket ni Radar real
    try:
        response = client.get("/api/radar/revision")
        assert response.status_code == 200
        visibles = [i for i in response.json()["items"] if i["postulacion_id"] == pid
                    and (post_id is None or i["post_id"] == post_id)]
    finally:
        client.close()
    return persistidos, visibles


def _assert_rechazo(f, pid, contenido, *, post_id=None):
    actual = database.get_postulacion(pid)
    persistidos, visibles = _motivos_visibles(pid, post_id)
    motivos = [i for i in persistidos if i["tipo"] == "senal_ia" and i["detalle"].strip()]
    eventos = [(t, d) for t, d in f.eventos if d.get("id") == pid]
    observado = {
        "status": actual["status"],
        "approved": actual["reporte_auditoria"].get("approved"),
        "contenido_preservado": actual["cv_markdown"] == contenido,
        "publico_ready": any(t == "complete" or d.get("status") == "READY" for t, d in eventos),
        "error_visible": any(t == "error" and "Revisá" in d.get("error", "") for t, d in eventos),
        "motivo_persistido": bool(motivos),
        "motivo_en_panel": bool(motivos) and all(
            any(v["id"] == i["id"] and v["detalle"] == i["detalle"] for v in visibles) for i in motivos),
    }
    assert observado == {
        "status": "ERROR", "approved": False, "contenido_preservado": True,
        "publico_ready": False, "error_visible": True,
        "motivo_persistido": True, "motivo_en_panel": True,
    }, observado
    return actual["reporte_auditoria"]


@pytest.mark.asyncio
async def test_invariantes_base(frontera):
    f = frontera
    for ruta in RUTAS:
        await _base_ruta(f, ruta)
    # invariante: identidad/contacto/encabezados/URL exactos e idempotencia.
    entrada = CV + "\nRelevé procesos — documenté acuerdos.\n2018–2025\n01/2018 – 01/2025"
    salida = normalizar_guiones(entrada)
    protegidos = lambda texto: [l for l in texto.splitlines()
                                if l.startswith("# ") or l.startswith("## ") or "@" in l]
    assert protegidos(salida) == protegidos(entrada)
    assert "ANA PRUEBA" in salida and "ana@example.com" in salida
    assert "https://linkedin.com/in/ana-prueba" in salida
    assert normalizar_guiones(salida) == salida
    # invariante: rangos antes/después siguen reconocidos por ATS.
    patron = ATS_CRITERIA["date_format_regex"]
    for antes, despues in (("2018–2025", "2018-2025"), ("01/2018 – 01/2025", "01/2018 - 01/2025")):
        assert re.fullmatch(patron, antes)
        assert normalizar_guiones(antes) == despues
        assert re.fullmatch(patron, despues)
    # invariante: sin fechas no hay años inventados, ni rol tomado de la oferta.
    perfil = parse_candidate_profile("## Empresa Test\n• Probé SQL.", "# ANA PRUEBA\nana@example.com")
    assert perfil["nombre"] == "ANA PRUEBA" and perfil["contacto"] == "ana@example.com"
    assert perfil["empresas"] == ["Empresa Test"]
    assert perfil["anios_experiencia"] is None and perfil["rol"] == ""
    # invariante: siguen bloqueando cobertura, ATS, secciones, empresas y señal LLM.
    auditor = AuditorAgent()
    casos = [(CV, KW_FALTANTES, []), ("# ANA PRUEBA", KEYWORDS, []),
             (CV.replace("## IDIOMAS", "## OTROS"), KEYWORDS, []),
             (CV.replace("KORVA", "Empresa omitida"), KEYWORDS, []), (CV, KEYWORDS, [SENAL]),
             (CV + "\n" + RESIDUAL, KEYWORDS, [])]
    for cv, keywords, senales in casos:
        informe = auditor.audit(cv, PITCH, keywords, expected_companies=EXPECTED_COMPANIES,
                                senales_llm=senales)
        assert informe["approved"] is False
    # invariante: remediación fallida conserva documento y el rechazo, en dos llamadas.
    _limpiar_medidas(f)
    cv, pitch, informe = await auditor.audit_and_remediate(
        CV, PITCH, KW_FALTANTES, expected_companies=EXPECTED_COMPANIES)
    assert cv == CV and pitch == PITCH and informe["approved"] is False
    assert len(f.bridge_contado.revisiones) == len(f.bridge_contado.remediaciones) == 1
    assert len(f.bridge_contado.llamadas) == 2
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("rechazo", ["cobertura", "senal_llm"])
async def test_rechazo_en_tres_rutas(frontera, ruta, rechazo):
    f = frontera
    await _base_ruta(f, ruta)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    keywords = KW_FALTANTES if rechazo == "cobertura" else KEYWORDS
    f.bridge_contado.senales = [SENAL] if rechazo == "senal_llm" else []
    f.estratega.return_value["requisitos"] = [{"nombre": k, "approved": True} for k in keywords]
    pid = _crear(f, keywords)
    esperado = AuditorAgent().audit(CV, PITCH, keywords, expected_companies=EXPECTED_COMPANIES,
                                   senales_llm=f.bridge_contado.senales)
    assert esperado["approved"] is False
    await _ejecutar(ruta, pid)
    assert len(f.bridge_contado.llamadas) == 2, "La frontera final no debe volver a pedir auditoría"
    assert len(f.bridge_contado.revisiones) == len(f.bridge_contado.remediaciones) == 1
    if rechazo == "cobertura":
        # faltan keywords que el perfil no tiene (no se pueden inventar): el CV sigue
        # con observaciones en lugar de quedar en ERROR esperando una revisión manual.
        actual = database.get_postulacion(pid)
        reporte = actual["reporte_auditoria"]
        assert actual["status"] == "READY" and actual["cv_markdown"] == CV
        assert reporte["approved"] is True and reporte["limites_perfil"] is True
        assert reporte["verdict"] == "PASE CON OBSERVACIONES"
        assert reporte["ats"]["missing_keywords"] == esperado["ats"]["missing_keywords"]
        return
    # defecto: un dictamen rechazado jamás se promociona por una pasada textual limpia.
    reporte = _assert_rechazo(f, pid, CV)
    # Las empresas del perfil llegan en mayúsculas al pipeline y en el fixture con su grafía:
    # se comparan sin distinguir mayúsculas (el contenido del dictamen es el mismo).
    def _ats(r):
        return {k: ([e.upper() for e in v] if isinstance(v, list) and k.endswith("_experiences") else v) for k, v in r.items()}
    assert _ats(reporte["ats"]) == _ats(esperado["ats"])
    assert reporte["anti_ai"] == esperado["anti_ai"]
    assert reporte["deducciones"] == esperado["deducciones"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrada", ["cv_al_dia", "radar_compose"])
async def test_cv_heredado_rechazado_no_se_ofrece(frontera, entrada):
    f = frontera
    pid = await _base_ruta(f, "remediate_cv_task")
    post = _post()
    # invariante: ni un borrador limpio implica aprobación de envío.
    assert post["aprobado_at"] is None
    assert store.get_radar_state()["contadores_dia"]["enviados"] == 0
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    reporte = AuditorAgent().audit(CV, PITCH, KW_FALTANTES, expected_companies=EXPECTED_COMPANIES)
    assert reporte["approved"] is False and reporte["anti_ai"]["clean"] is True
    database.update_postulacion(pid, reporte_auditoria=reporte)
    store.update_post(post["id"], postulacion_id=pid)
    error = None
    try:
        if entrada == "cv_al_dia":
            await server._cv_al_dia(database.get_postulacion(pid))
        else:
            await server._radar_compose(store.get_post(post["id"]), pid)
    except RuntimeError as exc:
        error = exc
    actual = database.get_postulacion(pid)
    persistidos, visibles = _motivos_visibles(pid)
    # defecto: el atajo para CV viejos debe respetar el dictamen persistido.
    assert {"status": actual["status"], "revisar": error is not None and "Revisá" in str(error),
            "composiciones": len(f.composiciones),
            "publico_ready": any(t == "complete" or d.get("status") == "READY" for t, d in f.eventos),
            "motivo": any(i["tipo"] == "senal_ia" and i["detalle"].strip() for i in persistidos),
            "visible": bool(visibles)} == {
                "status": "ERROR", "revisar": True, "composiciones": 0,
                "publico_ready": False, "motivo": True, "visible": True}
    assert actual["cv_markdown"] == CV and actual["reporte_auditoria"] == reporte
    assert f.bridge_contado.llamadas == []


@pytest.mark.asyncio
# El Radar genera desde el maestro (sin la completitud del CV ni pitch): su contrato con el mail está en
# tests/test_cv_maestro_pipeline.py
@pytest.mark.parametrize("ruta", RUTAS)
@pytest.mark.parametrize("campo", ["CV", "pitch", "mensaje (email)"])
async def test_completitud_se_escanea_despues(frontera, monkeypatch, tmp_path, ruta, campo):
    f = frontera
    await _base_ruta(f, "remediate_cv_task" if ruta == "radar" else ruta)
    # invariante: el texto aprobado está limpio; la señal a introducir es detectable.
    assert scan_anti_ai_writing(CV + "\n" + PITCH)["clean"] is True
    assert scan_anti_ai_writing(RESIDUAL)["clean"] is False
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    original = f.completar

    async def completar(texto, *, tipo, **kw):
        nuevo, cambios = await original(texto, tipo=tipo, **kw)
        return (nuevo + "\n" + RESIDUAL if tipo == campo else nuevo), cambios

    monkeypatch.setattr(server, "completar", completar)
    if ruta != "radar":
        pid = _crear(f)
        await _ejecutar(ruta, pid)
        assert len(f.bridge_contado.llamadas) == 1
        _assert_rechazo(f, pid, CV + ("\n" + RESIDUAL if campo == "CV" else ""))
        actual = database.get_postulacion(pid)
        texto = actual["pitch"] if campo == "pitch" else actual["pitches_multicanal"].get("email", "")
        if campo != "CV":
            assert RESIDUAL in texto, "El rechazo conserva el contenido a revisar"
    else:
        post = _post()
        loop = RadarLoop(harvester_factory=_prohibido, triage_fn=None, analyze_fn=None,
                         pipeline_fn=server._radar_pipeline,
                         compose_fn=server._radar_compose, send_fn=AsyncMock(side_effect=_prohibido),
                         broadcast=f.broadcast, debug_dir=tmp_path / "debug")
        try:
            await asyncio.wait_for(loop._generar_post(post), timeout=60)
            actual = store.get_post(post["id"])
            # defecto: el worker tampoco ofrece LISTO ni aprueba el contenido residual.
            assert {"estado": actual["estado"], "aprobado": actual["aprobado_at"],
                    "motivo": "Revisá" in (actual.get("error") or ""),
                    "evento_listo": any(d.get("estado") == "LISTO" for _, d in f.eventos)} == {
                        "estado": "ERROR", "aprobado": None, "motivo": True, "evento_listo": False}
            postu = server._buscar_postulacion_radar(actual)
            assert postu is not None
            _assert_rechazo(f, postu["id"], CV + ("\n" + RESIDUAL if campo == "CV" else ""),
                            post_id=post["id"])
            assert len(f.bridge_contado.llamadas) == 1
            assert len(f.composiciones) <= 1
        finally:
            await loop.stop_for_shutdown()
            await f.limpiar()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrada", ["asegurar", "cv_al_dia", "cv_heredado", "mensaje", "componer"])
async def test_cv_y_mail_final_normalizados(frontera, monkeypatch, entrada):
    f = frontera
    pid = await _base_ruta(f, "remediate_cv_task")
    post = _post()
    postu = database.get_postulacion(pid)
    mensaje = {"to": "rrhh@example.com", "subject": "Postulación QA", "body": PITCH, "is_fallback": False}
    # invariante: helper real conserva mensaje válido sin recomponer.
    assert await server._completar_mensaje(post, postu, mensaje) == mensaje
    assert mensaje["body"] == PITCH
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    cola = "\nRelevé procesos — documenté acuerdos.\n2018–2025\n01/2018 – 01/2025"
    cola_final = "\nRelevé procesos - documenté acuerdos.\n2018-2025\n01/2018 - 01/2025"
    original = f.completar

    async def completar(texto, *, tipo, **kw):
        nuevo, cambios = await original(texto, tipo=tipo, **kw)
        return nuevo + cola, cambios

    monkeypatch.setattr(server, "completar", completar)
    if entrada == "asegurar":
        cv, pitch, canales, restos = await server._asegurar_completo(pid, CV, PITCH, {"email": PITCH})
        assert restos == []
        assert (cv, pitch, canales) == (CV + cola_final, PITCH + cola_final, {"email": PITCH + cola_final})
    elif entrada in ("cv_al_dia", "cv_heredado"):
        previo = CV + ("\n[Nombre]" if entrada == "cv_al_dia" else cola)
        database.update_postulacion(pid, cv_markdown=previo)
        final = await server._cv_al_dia(database.get_postulacion(pid))
        esperado = CV + ("\nequipo" if entrada == "cv_al_dia" else "") + cola_final
        assert final["cv_markdown"] == esperado
        assert database.get_postulacion(pid)["cv_markdown"] == esperado
    else:
        if entrada == "mensaje":
            final = await server._completar_mensaje(post, postu, mensaje)
            esperado = mensaje
        else:
            final = await server._componer_mensaje_validado(post, postu)
            esperado = {"subject": f"Postulación {post['id']}", "body": postu["pitch"] + " " + post["texto"]}
            assert len(f.composiciones) == 1
        assert final["subject"] == esperado["subject"] + cola_final
        assert final["body"] == esperado["body"] + cola_final
        assert final["to"] == (mensaje["to"] if entrada == "mensaje" else post["destino_canal"])
    assert f.bridge_contado.llamadas == [], "Normalizar no consume cuota"


@pytest.mark.asyncio
@pytest.mark.parametrize("origen", ["completitud", "composer"])
async def test_error_calidad_no_recompone(frontera, monkeypatch, origen):
    f = frontera
    pid = await _base_ruta(f, "remediate_cv_task")
    post, postu = _post(), database.get_postulacion(pid)
    # invariante: una composición limpia, sin reintentos ni llamadas nuevas al bridge.
    limpio = await server._componer_mensaje_validado(post, postu)
    assert len(f.composiciones) == 1
    assert f.bridge_contado.llamadas == []
    _limpiar_medidas(f)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    original = f.completar
    if origen == "completitud":
        async def completar(texto, *, tipo, **kw):
            nuevo, cambios = await original(texto, tipo=tipo, **kw)
            return (nuevo + "\n" + RESIDUAL if tipo == "mensaje" else nuevo), cambios
        monkeypatch.setattr(server, "completar", completar)
    else:
        async def composer(p, q):
            f.composiciones.append((deepcopy(p), deepcopy(q)))
            raise MensajeRequiereRevision([RESIDUAL])
        monkeypatch.setattr(server, "compose_message", composer)
    error = None
    try:
        await server._componer_mensaje_validado(post, postu)
    except RuntimeError as exc:
        error = exc
    persistidos, visibles = _motivos_visibles(pid, post["id"])
    # defecto: error de calidad visible, sin fallback ni otro intento de composición.
    assert {"revisar": error is not None and "Revisá" in str(error),
            "composiciones": len(f.composiciones), "llamadas": len(f.bridge_contado.llamadas),
            "motivo": any(i["tipo"] == "senal_ia" and i["detalle"].strip() for i in persistidos),
            "visible": bool(visibles)} == {
                "revisar": True, "composiciones": 1, "llamadas": 0, "motivo": True, "visible": True}


@pytest.mark.parametrize("cambio", ["aprobacion", "cv", "mensaje"])
def test_cambio_actual_invalida_aprobacion(frontera, tmp_path, monkeypatch, cambio):
    state = {"events": [], "messages": [], "closed": False}
    pid, post_id = _crear_post_y_postulacion()

    def login():
        if cambio == "aprobacion":
            store.update_post(post_id, aprobado_at=None)
        elif cambio == "cv":
            database.update_postulacion(pid, cv_markdown=CV)
        else:
            store.update_post(post_id, email_body="Contenido nuevo que nadie aprobó.")

    monkeypatch.setattr("core.mailer.smtplib.SMTP_SSL", _fake_smtp_class(state, login))
    config = GmailConfig("yo@example.com", "clave-falsa")
    monkeypatch.setattr(server, "load_gmail_config", lambda: config)
    monkeypatch.setattr("core.radar_api.load_gmail_config", lambda: config)

    def pdf(*a, **kw):
        path = tmp_path / "cv.pdf"
        path.write_bytes(b"%PDF-1.4 synthetic")
        return path

    monkeypatch.setattr(server, "compile_markdown_to_pdf", pdf)
    radar = make_radar(tmp_path, send=server._radar_send)
    monkeypatch.setattr(server, "radar_loop", radar)
    enqueue = radar.enqueue_send

    async def encolar(post_id):
        resultado = await enqueue(post_id)
        if radar._send_task is not None and not radar._send_task.done():
            await asyncio.wait_for(asyncio.shield(radar._send_task), timeout=60)
        return resultado

    radar.enqueue_send = encolar
    client = TestClient(server.app)
    try:
        # invariante: falta de versión y versión vencida no aprueban ni llegan a SMTP.
        path = f"/api/radar/posts/{post_id}/approve"
        for payload, status in (({}, 400), ({"updated_at": "2000-01-01"}, 409)):
            response = client.post(path, json=payload)
            assert response.status_code == status and response.json()["detail"]
        assert state["events"] == []
        # invariante: aun con la versión actual, login puede invalidar contenido/aprobación.
        version = store.get_post(post_id)["updated_at"]
        response = client.post(path, json={"updated_at": version})
        assert response.status_code == 200, response.json()
        actual = store.get_post(post_id)
        assert state["messages"] == [] and "send_message" not in state["events"]
        assert state["closed"] is True and actual["estado"] == "LISTO"
        assert actual["aprobado_at"] is None and actual["sent_at"] is None and actual["message_id"] is None
        assert store.get_radar_state()["contadores_dia"]["enviados"] == 0
        if os.environ.get("INVARIANT_CHECK") == "1":
            return
        # defecto: sanear un CV heredado cambia el adjunto e invalida su aprobación.
        database.update_postulacion(pid, cv_markdown=CV + "\nRelevé procesos — documenté acuerdos.")
        store.update_post(post_id, aprobado_at="2026-11-07 10:00:00", updated_at="2000-01-01 00:00:00")
        anterior = store.get_post(post_id)["updated_at"]
        actualizado = asyncio.run(server._cv_al_dia(database.get_postulacion(pid)))
        actual = store.get_post(post_id)
        assert {"cv": actualizado["cv_markdown"], "aprobado": actual["aprobado_at"],
                "cambio_version": actual["updated_at"] != anterior} == {
                    "cv": CV + "\nRelevé procesos - documenté acuerdos.",
                    "aprobado": None, "cambio_version": True}
        state["events"].clear()
        response = client.post(path, json={"updated_at": anterior})
        assert response.status_code == 409 and response.json()["detail"]
        assert state["events"] == [] and state["messages"] == []
    finally:
        client.close()
        radar._apagado = True
        radar._cerrando = True
        radar._send_stop_event.set()
        radar._stop_event.set()
