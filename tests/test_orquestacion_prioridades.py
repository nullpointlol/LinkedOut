"""Contratos de orquestación, prioridades, captura y recuperación.

Fija en rojo los contratos nuevos de la feature orquestacion-prioridades,
garantizando la validez de los invariantes I1 a I6 sobre la base actual.

Rojo comprobado en la base, por aserción:
- roles: falta prioridad_rol; captura: falta captura_suficiente.
- orden: auto/link precede incorrectamente a backlog/email.
- OCR fallido: analyze_fn se invoca con captura insuficiente.
- concurrencia: se observan 3 análisis activos donde se exige 1.
- migración: falta modo_serie en el schema antiguo inicializado dos veces.
Invariantes y cosecha de 31 posts pasan; no hay errores de importación.
Lista de la misión: 263 pasan con INVARIANT_CHECK=1; en modo normal,
250 pasan y 13 fallan por los contratos nuevos, incluidos los casos
autorizados de umbral, cerradas y OCR de los archivos existentes.
"""
import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from core import radar, radar_store as store
from core.linkedin_harvester import HarvestResult
from core.radar import RadarLoop
from tests.perfil_ti import roles_ti  # noqa: F401

pytestmark = pytest.mark.usefixtures("roles_ti")  # prioridades de un perfil de TI


# =====================================================================
# Doble de prueba de RadarLoop (patrón test_imagenes_motor)
# =====================================================================

def make_radar_loop(**kwargs) -> RadarLoop:
    """Instancia un RadarLoop aislado con dobles de prueba según el patrón de test_imagenes_motor."""
    return RadarLoop(
        harvester_factory=kwargs.pop("harvester_factory", Mock(side_effect=AssertionError("sin captura real"))),
        triage_fn=kwargs.pop("triage_fn", AsyncMock(return_value={})),
        analyze_fn=kwargs.pop("analyze_fn", AsyncMock(return_value={"match_score": 10, "oferta_cerrada": True, "puesto": "Analista QA"})),
        pipeline_fn=kwargs.pop("pipeline_fn", AsyncMock(return_value=123)),
        compose_fn=kwargs.pop("compose_fn", AsyncMock(return_value={
            "to": "rrhh@acme.test", "subject": "QA", "body": "Hola",
        })),
        send_fn=kwargs.pop("send_fn", AsyncMock(side_effect=AssertionError("sin aprobación"))),
        broadcast=kwargs.pop("broadcast", AsyncMock()),
        image_reader_fn=kwargs.pop("image_reader_fn", AsyncMock(return_value="")),
        **kwargs,
    )


async def cerrar_radares(*loops) -> None:
    """Cierra supervisores y drena sus hijas, incluso si falla una aserción."""
    for loop in loops:
        tareas = [t for t in (loop._task, loop._proc_task, loop._gen_task, loop._send_task) if t]
        tareas.extend(loop._vigilantes_gen.values())
        tareas.extend(loop._trabajos_gen.values())
        # Cancelar antes del primer await evita que un supervisor recién creado
        # arranque trabajo durante el propio cierre de la fixture.
        for tarea in tareas:
            if not tarea.done():
                tarea.cancel()
        try:
            await asyncio.wait_for(loop.stop_for_shutdown(), timeout=60)
        finally:
            for tarea in tareas:
                if not tarea.done():
                    tarea.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)


@asynccontextmanager
async def radares_aislados():
    loops = []

    def crear(**kwargs):
        loop = make_radar_loop(**kwargs)
        loops.append(loop)
        return loop

    try:
        yield crear
    finally:
        await cerrar_radares(*loops)


# =====================================================================
# Helper de invariantes actuales (I1–I6)
# =====================================================================

async def comprobar_invariantes_actuales(monkeypatch, tmp_path) -> None:
    """Mide los 6 invariantes actuales (I1–I6) usando exclusivamente APIs existentes.

    No incluye assertions de defecto ni return por INVARIANT_CHECK. Siempre mide
    las seis propiedades sobre la base y aísla y limpia sus registros.
    """
    cleanup_ids: List[int] = []
    loops = []

    def crear_loop(**kwargs):
        loop = make_radar_loop(**kwargs)
        loops.append(loop)
        return loop

    pid_prefix = f"inv_{os.getpid()}_{id(tmp_path)}"
    texto_valido = (
        "Buscamos un Analista Funcional con amplia experiencia en relevamiento de procesos "
        "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
        "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
        "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
        "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
    )
    try:
        # -----------------------------------------------------------------
        # I1: Nada se envía sin aprobación del contenido actual (updated_at)
        # -----------------------------------------------------------------
        p1 = store.insert_post_if_new(
            {"urn": f"{pid_prefix}:i1:p1", "texto": texto_valido, "emails": ["rrhh@empresa.test"]}, "prueba"
        )
        cleanup_ids.append(p1)
        store.update_post(
            p1,
            estado="LISTO",
            email_to="rrhh@empresa.test",
            email_subject="Asunto",
            email_body="Cuerpo inicial",
            aprobado_at="2026-11-08 10:00:00",
            updated_at="2026-11-08 10:00:00",
        )
        post_p1 = store.get_post(p1)
        visto_inicial = post_p1["updated_at"]

        # Modificación posterior con fecha explícita invalida la versión vista:
        store.update_post(p1, email_body="Cuerpo modificado tras revisión", updated_at="2026-11-08 10:00:05")
        # Intento de aprobación con versión vencida es rechazado en la base:
        aprobacion_vencida = store.update_post(p1, aprobado_at="2026-11-08 10:01:00", updated_at_esperado=visto_inicial)
        assert aprobacion_vencida is False, "Aprobación con versión vencida no debe aplicarse"

        # Post sin aprobación (o invalidado): claim_for_sending devuelve False (cero envíos)
        store.update_post(p1, aprobado_at=None)
        assert store.claim_for_sending(p1) is False, "Post sin aprobación no puede reclamarse para envío"

        fake_send = AsyncMock()
        loop_i1 = crear_loop(send_fn=fake_send)
        enviado = await loop_i1._procesar_envio(p1)
        assert enviado is False
        fake_send.assert_not_awaited()

        # Aislamiento I1:
        store.update_post(p1, estado="DESCARTADO")

        # -----------------------------------------------------------------
        # I2: Las pausas se respetan siempre; con generacion_pausada no se genera nada (ni forzadas)
        # -----------------------------------------------------------------
        store.update_radar_state(generacion_pausada=True)
        p_forzada = store.insert_post_if_new({"urn": f"{pid_prefix}:i2:forzada", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_forzada)
        store.update_post(p_forzada, estado="GENERANDO", gen_prioridad=2, triage={"puesto": "Analista Funcional"})
        loop_pausa = crear_loop()
        assert loop_pausa._siguiente_generacion() is None, "Con generacion_pausada no debe tomarse ni una forzada"

        # Aislamiento p_forzada antes de reanudar:
        store.update_post(p_forzada, estado="DESCARTADO")

        # Con analisis_pausado, _siguiente_trabajo devuelve None
        store.update_radar_state(generacion_pausada=False, analisis_pausado=True)
        p_analisis = store.insert_post_if_new({"urn": f"{pid_prefix}:i2:analisis", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_analisis)
        store.update_post(p_analisis, estado="OFERTA", prioridad=1, triage={"puesto": "Analista Funcional"})
        assert loop_pausa._siguiente_trabajo(todo=True) is None, "Con analisis_pausado no se toma trabajo"

        # Aislamiento p_analisis:
        store.update_post(p_analisis, estado="DESCARTADO")

        # Con envios_pausados, _procesar_envio no envía
        store.update_radar_state(analisis_pausado=False, envios_pausados=True)
        p_envio = store.insert_post_if_new({"urn": f"{pid_prefix}:i2:envio", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_envio)
        store.update_post(
            p_envio, estado="LISTO", email_to="rrhh@empresa.test", email_subject="S", email_body="B",
            aprobado_at="2026-11-08 10:00:00",
        )
        fake_send_pausa = AsyncMock()
        loop_envio = crear_loop(send_fn=fake_send_pausa)
        enviado_pausa = await loop_envio._procesar_envio(p_envio)
        assert enviado_pausa is False
        fake_send_pausa.assert_not_awaited()
        store.update_radar_state(envios_pausados=False)

        # Aislamiento p_envio:
        store.update_post(p_envio, estado="DESCARTADO")

        # -----------------------------------------------------------------
        # I3: Ningún post se procesa dos veces a la vez (reservas atómicas actuales)
        # -----------------------------------------------------------------
        p_reserva = store.insert_post_if_new({"urn": f"{pid_prefix}:i3:reserva", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_reserva)
        store.update_post(
            p_reserva,
            estado="ANALIZADA",
            triage={"puesto": "Analista Funcional"},
            analisis={"match_score": 85.0},
            email_body="",
        )
        assert store.tomar_automatico(p_reserva) is True, "Primera toma debe tener éxito"
        assert store.tomar_automatico(p_reserva) is False, "Segunda toma atómica debe devolver False"

        # Reservas antes de await en RadarLoop:
        loop_i3 = crear_loop()
        store.update_post(p_reserva, estado="OFERTA", prioridad=1, triage={"puesto": "Analista Funcional"})
        loop_i3._en_vuelo.add(p_reserva)
        sig = loop_i3._siguiente_trabajo(todo=True)
        assert sig is None or p_reserva not in [p["id"] for p in sig[1]], "Post en vuelo no debe volver a elegirse"
        loop_i3._en_vuelo.clear()

        store.update_post(p_reserva, estado="GENERANDO", gen_prioridad=2)
        loop_i3._reservas_gen.add(p_reserva)
        assert loop_i3._siguiente_generacion() is None, "Post en _reservas_gen no debe volver a tomarse"
        loop_i3._reservas_gen.clear()

        # Aislamiento p_reserva:
        store.update_post(p_reserva, estado="DESCARTADO")

        # -----------------------------------------------------------------
        # I4: Llamadas a la IA por oferta: iguales que hoy
        # -----------------------------------------------------------------
        # Triage: 1 por lote <= 15
        p_triage = store.insert_post_if_new({"urn": f"{pid_prefix}:i4:triage", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_triage)
        triage_mock = AsyncMock(return_value={p_triage: {"es_busqueda_laboral": True, "alcance": "ARGENTINA", "puesto": "Analista Funcional"}})
        loop_i4 = crear_loop(triage_fn=triage_mock)
        await loop_i4._clasificar([store.get_post(p_triage)])
        triage_mock.assert_awaited_once()
        store.update_post(p_triage, estado="DESCARTADO")

        # Analyze: 1 por oferta
        p_ana = store.insert_post_if_new({"urn": f"{pid_prefix}:i4:ana", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_ana)
        store.update_post(p_ana, estado="OFERTA", triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True})
        analyze_mock = AsyncMock(return_value={"match_score": 85.0, "puesto": "Analista Funcional"})
        loop_i4_ana = crear_loop(analyze_fn=analyze_mock)
        await loop_i4_ana._analizar(store.get_post(p_ana))
        await cerrar_radares(loop_i4_ana)
        analyze_mock.assert_awaited_once()
        store.update_post(p_ana, estado="DESCARTADO")

        # Pipeline: 1 si falta postulacion_id, 0 si ya existe; compose: 1
        pipe_mock = AsyncMock(return_value=999)
        comp_mock = AsyncMock(return_value={"to": "a@b.test", "subject": "S", "body": "B"})
        loop_i4_gen = crear_loop(pipeline_fn=pipe_mock, compose_fn=comp_mock)
        p_gen_sin = store.insert_post_if_new({"urn": f"{pid_prefix}:i4:sin_id", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_gen_sin)
        store.update_post(p_gen_sin, estado="GENERANDO", postulacion_id=None, triage={"puesto": "Analista Funcional"})
        await loop_i4_gen._generar_post(store.get_post(p_gen_sin))
        pipe_mock.assert_awaited_once()
        comp_mock.assert_awaited_once()
        store.update_post(p_gen_sin, estado="DESCARTADO")

        pipe_mock_con = AsyncMock(return_value=999)
        comp_mock_con = AsyncMock(return_value={"to": "a@b.test", "subject": "S", "body": "B"})
        loop_i4_con = crear_loop(pipeline_fn=pipe_mock_con, compose_fn=comp_mock_con)
        p_gen_con = store.insert_post_if_new({"urn": f"{pid_prefix}:i4:con_id", "texto": texto_valido}, "prueba")
        cleanup_ids.append(p_gen_con)
        store.update_post(p_gen_con, estado="GENERANDO", postulacion_id=999, triage={"puesto": "Analista Funcional"})
        await loop_i4_con._generar_post(store.get_post(p_gen_con))
        pipe_mock_con.assert_not_awaited()
        comp_mock_con.assert_awaited_once()
        store.update_post(p_gen_con, estado="DESCARTADO")

        # -----------------------------------------------------------------
        # I5: La cosecha del Radar no se frena ni pierde posts
        # -----------------------------------------------------------------
        # 1. Unicidad de URN: inserción idempotente, 0 duplicados
        urn_i5 = f"{pid_prefix}:i5:cosecha_unica"
        id_orig = store.insert_post_if_new({"urn": urn_i5, "texto": texto_valido}, "query_test")
        cleanup_ids.append(id_orig)
        id_dup = store.insert_post_if_new({"urn": urn_i5, "texto": texto_valido}, "query_test")
        assert id_orig is not None
        assert id_dup is None
        store.update_post(id_orig, estado="DESCARTADO")

        # 2. Captura independiente: _leer_consulta almacena en SQLite como NUEVO
        # sin ejecutar triage ni análisis de forma inline
        class FakeHarvesterI5:
            async def harvest(self, *args, **kwargs):
                return HarvestResult(posts=[
                    {
                        "urn": f"{pid_prefix}:i5:indep_{i}",
                        "url": f"https://linkedin.com/post/{i}",
                        "autor": "HR",
                        "cargo_autor": "Recruiter",
                        "texto": f"{texto_valido} (indep {i})",
                        "fecha_rel": "1 h",
                        "emails": [f"rrhh_{i}@empresa.test"],
                    }
                    for i in range(1, 4)
                ])

        triage_i5 = AsyncMock()
        analyze_i5 = AsyncMock()
        loop_i5 = crear_loop(triage_fn=triage_i5, analyze_fn=analyze_i5)
        leidos_i5 = await loop_i5._leer_consulta(FakeHarvesterI5(), "query_i5", feed=False)
        await cerrar_radares(loop_i5)
        assert leidos_i5 == 3
        posts_indep = [
            store.get_post(p["id"])
            for p in store.list_posts(estado="NUEVO")
            if p["urn"].startswith(f"{pid_prefix}:i5:indep_")
        ]
        assert len(posts_indep) == 3
        for p in posts_indep:
            cleanup_ids.append(p["id"])
            assert p["estado"] == "NUEVO"
            store.update_post(p["id"], estado="DESCARTADO")
        triage_i5.assert_not_awaited()
        analyze_i5.assert_not_awaited()

        # -----------------------------------------------------------------
        # I6: Lo forzado a mano por el usuario sigue yendo primero
        # -----------------------------------------------------------------
        lista_orden = [
            {"id": 501, "gen_prioridad": 1},
            {"id": 502, "gen_prioridad": 0},
            {"id": 503, "gen_prioridad": 2},
        ]
        resultado_orden = radar.orden_generacion(lista_orden)
        assert resultado_orden[0]["id"] == 503, "Prioridad 2 (forzada) debe preceder auto y backlog"

    finally:
        await cerrar_radares(*loops)
        store.update_radar_state(generacion_pausada=False, analisis_pausado=False, envios_pausados=False)
        for pid in cleanup_ids:
            if pid:
                try:
                    store.delete_post(pid)
                except Exception:
                    pass


# =====================================================================
# Tests de especificación y defectos (rojos en base actual)
# =====================================================================

@pytest.mark.asyncio
async def test_invariantes_actuales(monkeypatch, tmp_path):
    """Verifica que los 6 invariantes actuales pasan limpiamente sobre la base."""
    # invariante: base actual pasa I1-I6
    tareas_previas = asyncio.all_tasks()
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    assert not (asyncio.all_tasks() - tareas_previas), "El helper debe retornar sin tareas pendientes"


@pytest.mark.asyncio
async def test_roles_25_alias(monkeypatch, tmp_path):
    """Tabla de >= 25 roles con alias, acentos, inglés/castellano y trampas de subpalabras."""
    # invariante: medir invariantes actuales antes de APIs nuevas
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: core.radar.prioridad_rol clasifica determinísticamente en a, b, c
    assert callable(getattr(radar, "prioridad_rol", None)), "Falta el contrato prioridad_rol"
    prioridad_rol = radar.prioridad_rol

    tabla_a = [
        "Análisis Funcional",
        "analisis funcional",
        "Analista Funcional",
        "Técnico Funcional",
        "tecnico funcional",
        "Business Analyst",
        "analista de negocio",
        "Incident Manager",
        "Gestión de Incidentes",
        "gestion de incidentes",
        "Analista de Monitoreo",
        "Monitoring Analyst",
        "Operations",
        "Operador de Monitoreo",
        "Operador NOC",
        "Data Center Operator",
        "Command Center Operator",
        "Centro de Cómputos",
        "centro de computos",
        "Asistente Virtual",
        "Chatbot Specialist",
        "Voicebot Developer",
        "Soporte L1",
        "Soporte N2",
        "Help Desk L1",
        "Service Desk L2",
        "Soporte Técnico N1",
    ]
    for puesto in tabla_a:
        assert prioridad_rol(puesto) == "a", f"Fallo en rol 'a': {puesto}"

    tabla_b = [
        "Desarrollador Java",
        "QA Automation",
        "DevOps Engineer",
        "Data Engineer",
        "Cloud Architect",
        "Especialista en Seguridad de la Información",
        "Administrador de Sistemas",
        "Fullstack Developer",
        "Arquitecto de Software",
        "DBA Oracle",
        "IT Project Manager",
    ]
    for puesto in tabla_b:
        assert prioridad_rol(puesto) == "b", f"Fallo en rol 'b': {puesto}"

    tabla_c = [
        "Vendedor",
        "Administrativo contable",
        "Recepcionista",
        "Abogado corporativo",
        "Mozo",
        "Diseñador gráfico de interiores",
        "Posición no especificada",
        "Puesto no especificado",
        "",
        None,
    ]
    for puesto in tabla_c:
        assert prioridad_rol(puesto) == "c", f"Fallo en rol 'c': {puesto}"

    # Trampas de subpalabras (debe buscar palabras completas, no coincidencias parciales)
    assert prioridad_rol("Transformación Digital") == "c"  # no es IT por 'digital'
    assert prioridad_rol("Conocimientos generales") == "c"  # no es NOC por 'conocimientos'


@pytest.mark.asyncio
async def test_ordenes_y_empates(monkeypatch, tmp_path):
    """Orden de generación (forzada -> email a/b/c -> link a/b/c) y orden de análisis por email/rol/fecha conservando origen."""
    # invariante: base actual de orden_generacion
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: nuevo orden_generacion y desempates estables
    from core.radar import orden_generacion

    # 1 forzada + 6 ofertas (email/link x grupos a/b/c) mezclando auto (1) y backlog (0)
    # Prioridad 2 > 1 > 0; dentro de cada prioridad, contacto y rol.
    p_forzada = {
        "id": 100, "gen_prioridad": 2, "canal": "link", "destino_canal": "https://link.com",
        "triage": {"puesto": "Vendedor"}, "analisis": {"match_score": 50.0},
    }
    p_email_a = {
        "id": 10, "gen_prioridad": 0, "canal": "email", "email_to": "rrhh@a.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": 80.0},
    }
    p_email_b = {
        "id": 11, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@b.test",
        "triage": {"puesto": "Desarrollador Java"}, "analisis": {"match_score": 85.0},
    }
    p_email_c = {
        "id": 12, "gen_prioridad": 0, "canal": "email", "email_to": "rrhh@c.test",
        "triage": {"puesto": "Vendedor"}, "analisis": {"match_score": 90.0},
    }
    p_link_a = {
        "id": 13, "gen_prioridad": 1, "canal": "link", "destino_canal": "https://apply.com",
        "triage": {"puesto": "Incident Manager"}, "analisis": {"match_score": 82.0},
    }
    p_link_b = {
        "id": 14, "gen_prioridad": 0, "canal": "link", "destino_canal": "https://apply.com",
        "triage": {"puesto": "QA Tester"}, "analisis": {"match_score": 88.0},
    }
    p_link_c = {
        "id": 15, "gen_prioridad": 1, "canal": "link", "destino_canal": "https://apply.com",
        "triage": {"puesto": "Contador"}, "analisis": {"match_score": 95.0},
    }

    posts = [p_link_c, p_email_b, p_forzada, p_link_a, p_email_c, p_link_b, p_email_a]
    copia = list(posts)
    ordenados = orden_generacion(posts)
    assert posts == copia, "orden_generacion no debe mutar la lista de entrada"
    assert [p["id"] for p in ordenados] == [100, 11, 13, 15, 10, 12, 14]

    # Empates de score e id en generación
    empate_score_1 = {
        "id": 20, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@emp.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": 85.0},
    }
    empate_score_2 = {
        "id": 21, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@emp.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": 90.0},
    }
    assert [p["id"] for p in orden_generacion([empate_score_1, empate_score_2])] == [21, 20]

    empate_id_1 = {
        "id": 30, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@emp.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": 85.0},
    }
    empate_id_2 = {
        "id": 31, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@emp.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": 85.0},
    }
    assert [p["id"] for p in orden_generacion([empate_id_2, empate_id_1])] == [30, 31]

    # Scores NaN o ausentes se colocan al final de su grupo de canal/rol
    roto = {
        "id": 40, "gen_prioridad": 1, "canal": "email", "email_to": "rrhh@emp.test",
        "triage": {"puesto": "Analista Funcional"}, "analisis": {"match_score": "NaN"},
    }
    assert [p["id"] for p in orden_generacion([roto, empate_score_1])] == [20, 40]

    # -----------------------------------------------------------------
    # Orden de análisis: origen conservado (feed antes de búsqueda) -> con email -> rol a/b/c -> fecha DESC -> id ASC
    # -----------------------------------------------------------------
    loop_analisis = make_radar_loop()
    texto_largo = "Buscamos Analista Funcional con amplia experiencia comprobable en relevamiento... " * 10
    # Creamos posts en estado OFERTA
    p_busq_email = store.insert_post_if_new(
        {"urn": "ord:busq_email", "texto": texto_largo + " 1", "emails": ["rrhh@busq.test"]}, "q1", fuente="busqueda"
    )
    store.update_post(p_busq_email, estado="OFERTA", triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True}, created_at="2026-11-08 10:00:00")

    p_feed_sin_email = store.insert_post_if_new(
        {"urn": "ord:feed_sin", "texto": texto_largo + " 2", "emails": []}, "feed", fuente="feed"
    )
    store.update_post(p_feed_sin_email, estado="OFERTA", triage={"puesto": "Vendedor", "es_busqueda_laboral": True}, created_at="2026-11-08 09:00:00")

    p_feed_email_b = store.insert_post_if_new(
        {"urn": "ord:feed_b", "texto": texto_largo + " 3", "emails": ["rrhh@b.test"]}, "feed", fuente="feed"
    )
    store.update_post(p_feed_email_b, estado="OFERTA", triage={"puesto": "Desarrollador Java", "es_busqueda_laboral": True}, created_at="2026-11-08 09:30:00")

    p_feed_email_a_viejo = store.insert_post_if_new(
        {"urn": "ord:feed_a_viejo", "texto": texto_largo + " 4", "emails": ["rrhh@a1.test"]}, "feed", fuente="feed"
    )
    store.update_post(p_feed_email_a_viejo, estado="OFERTA", triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True}, created_at="2026-11-08 08:00:00")

    p_feed_email_a_nuevo = store.insert_post_if_new(
        {"urn": "ord:feed_a_nuevo", "texto": texto_largo + " 5", "emails": ["rrhh@a2.test"]}, "feed", fuente="feed"
    )
    store.update_post(p_feed_email_a_nuevo, estado="OFERTA", triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True}, created_at="2026-11-08 09:00:00")

    orden_obtenido = []
    while True:
        siguiente = loop_analisis._siguiente_trabajo(todo=True)
        if siguiente is None or siguiente[0] != "analizar":
            break
        pid = siguiente[1][0]["id"]
        orden_obtenido.append(pid)
        loop_analisis._en_vuelo.add(pid)  # simula reserva atómica

    # Orden esperado:
    # Todos los de feed (nivel 1) antes de búsqueda (nivel 2).
    # Dentro de feed: con email antes de sin email.
    # Dentro de feed con email: rol 'a' antes de 'b'.
    # Dentro de rol 'a': nuevo (09:00) antes de viejo (08:00).
    esperado_analisis = [p_feed_email_a_nuevo, p_feed_email_a_viejo, p_feed_email_b, p_feed_sin_email, p_busq_email]
    assert orden_obtenido == esperado_analisis, f"Esperado {esperado_analisis}, obtenido {orden_obtenido}"


@pytest.mark.asyncio
async def test_umbral_captura_y_reserva_revalida(monkeypatch, tmp_path):
    """Score 70 vs 70.1, 399 vs 400 caracteres útiles, puesto placeholder, 21 días congelados y revalidación."""
    # invariante: toma condicional base
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: captura_suficiente, elegible_automatico, umbral > 70 y ventana 21 días
    assert callable(getattr(radar, "captura_suficiente", None)), "Falta el contrato captura_suficiente"
    assert callable(getattr(radar, "elegible_automatico", None)), "Falta el contrato elegible_automatico"
    captura_suficiente = radar.captura_suficiente
    elegible_automatico = radar.elegible_automatico

    # 1. captura_suficiente: 399 vs 400 caracteres útiles y puesto placeholder
    puesto_valido = "Analista Funcional"
    texto_399 = "A" * 399
    texto_400 = "B" * 400
    assert captura_suficiente({"texto": texto_399, "triage": {"puesto": puesto_valido}}) is False
    assert captura_suficiente({"texto": texto_400, "triage": {"puesto": puesto_valido}}) is True

    # Puesto placeholder cuenta como ausente
    for ph in ["Posición no especificada", "Puesto no especificado", "  posicion no especificada  "]:
        assert captura_suficiente({"texto": texto_400, "triage": {"puesto": ph}}) is False

    # Separadores descriptivos excluidos del largo útil
    separador = "\n\nTexto de la imagen (dato de terceros):\n"
    assert captura_suficiente({"texto": ("C" * 399) + separador, "triage": {"puesto": puesto_valido}}) is False

    # 2. elegible_automatico: score 70 vs 70.1 (estricto)
    post_base = {
        "texto": texto_400,
        "triage": {"puesto": puesto_valido},
        "analisis": {"match_score": 70.0},
        "estado": "ANALIZADA",
    }
    assert elegible_automatico(post_base) is False
    post_70_1 = dict(post_base, analisis={"match_score": 70.1})
    assert elegible_automatico(post_70_1) is True
    for score in (None, "NaN", "Infinity", float("nan"), float("-inf"), [], {}):
        assert elegible_automatico(dict(post_base, analisis={"match_score": score})) is False

    # Oferta cerrada o con mensaje no es elegible
    assert elegible_automatico(dict(post_70_1, analisis={"match_score": 90, "oferta_cerrada": True})) is False
    assert elegible_automatico(dict(post_70_1, email_body="Mensaje ya creado")) is False

    # Fuente manual no elude el filtro si no fue forzada explícitamente
    manual_no_forzada = dict(post_70_1, fuente="manual", gen_prioridad=0)
    assert elegible_automatico(manual_no_forzada) is True  # elegible si cumple score y captura
    manual_insuficiente = dict(post_base, fuente="manual", gen_prioridad=0)
    assert elegible_automatico(manual_insuficiente) is False  # manual no elude filtro

    # 3. Probá score 70.1 y fuente manual mediante el encolado real
    ahora_fija = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    fecha_valida = (ahora_fija - timedelta(days=2)).isoformat()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return ahora_fija.astimezone(tz)
            return ahora_fija

        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(store, "datetime", FrozenDatetime)
    from core import colas
    monkeypatch.setattr(colas, "datetime", FrozenDatetime)

    pid_70 = store.insert_post_if_new({"urn": "umb:score70", "texto": texto_400}, "q")
    store.update_post(pid_70, estado="ANALIZADA", triage={"puesto": puesto_valido}, analisis={"match_score": 70.0}, created_at=fecha_valida)

    pid_70_1 = store.insert_post_if_new({"urn": "umb:score70_1", "texto": texto_400}, "q")
    store.update_post(pid_70_1, estado="ANALIZADA", triage={"puesto": puesto_valido}, analisis={"match_score": 70.1}, created_at=fecha_valida)

    pid_man_baja = store.insert_post_if_new({"urn": "umb:man_baja", "texto": texto_400}, "q", fuente="manual")
    store.update_post(pid_man_baja, estado="ANALIZADA", triage={"puesto": puesto_valido}, analisis={"match_score": 70.0}, gen_prioridad=0, created_at=fecha_valida)

    pid_man_alta = store.insert_post_if_new({"urn": "umb:man_alta", "texto": texto_400}, "q", fuente="manual")
    store.update_post(pid_man_alta, estado="ANALIZADA", triage={"puesto": puesto_valido}, analisis={"match_score": 70.1}, gen_prioridad=0, created_at=fecha_valida)

    async with radares_aislados() as crear_loop:
        # Son candidatos de backlog mientras siguen ANALIZADA y sin reserva.
        backlog_actual = [p["id"] for p in store.backlog_pendiente()]
        assert pid_70 not in backlog_actual
        assert pid_70_1 in backlog_actual

        loop_encolado = crear_loop()
        encolados = await loop_encolado.generar_automaticos([pid_70, pid_70_1, pid_man_baja, pid_man_alta])
        # Score 70 no se encola por Radar; una manual es un pedido explícito exento.
        assert pid_70 not in encolados, "Score 70 exacto no debe encolarse automáticamente"
        assert pid_man_baja in encolados, "El pedido manual se encola aun con score <= 70"
        # Score 70.1 califica, pero la automática espera en la cola dinámica:
        assert pid_70_1 not in encolados and store.get_post(pid_70_1)["estado"] == "ANALIZADA"
        assert pid_man_alta in encolados, "Oferta manual con score 70.1 se encola automáticamente"
        assert store.tomar_candidata(pid_70, "prioritaria") is False, "Score 70 exacto no califica"
        assert store.tomar_candidata(pid_70_1, "prioritaria") is True

        # La reserva lo retira del backlog: ya no puede ser elegido por ese camino.
        assert store.get_post(pid_70_1)["estado"] == "GENERANDO"
        assert pid_70_1 not in [p["id"] for p in store.backlog_pendiente()]
        assert store.get_post(pid_man_alta)["gen_prioridad"] == 2, "El pedido manual tiene prioridad forzada"

        # Ya encolada como forzada, no se duplica al volver a pedir generación.
        with pytest.raises(ValueError, match="ya tiene CV"):
            await loop_encolado.generate_cv(pid_man_baja)
        assert store.get_post(pid_man_baja)["estado"] == "GENERANDO"
        assert store.get_post(pid_man_baja)["gen_prioridad"] == 2

        # 4. Ventana 21 días y +1 microsegundo en backlog con reloj congelado determinístico
        hace_21_exactos = (ahora_fija - timedelta(days=21)).isoformat()
        hace_21_y_un_micro = (ahora_fija - timedelta(days=21, microseconds=1)).isoformat()

        pid_21 = store.insert_post_if_new({"urn": "umb:21d", "texto": texto_400}, "q")
        store.update_post(
            pid_21, estado="ANALIZADA", triage={"puesto": puesto_valido},
            analisis={"match_score": 85.0}, created_at=hace_21_exactos,
        )
        pid_vencido = store.insert_post_if_new({"urn": "umb:21d_vencido", "texto": texto_400}, "q")
        store.update_post(
            pid_vencido, estado="ANALIZADA", triage={"puesto": puesto_valido},
            analisis={"match_score": 85.0}, created_at=hace_21_y_un_micro,
        )

        backlog_ids = [p["id"] for p in store.backlog_pendiente()]
        assert pid_21 in backlog_ids, "Oferta con exactamente 21 días debe entrar al backlog"
        assert pid_vencido not in backlog_ids, "Oferta con 21 días + 1 microsegundo debe quedar fuera del backlog"

        # 5. Revalidación al reservar: listá primero y luego simulá por separado caída de score, cierre y pérdida de captura
        pid_carrera = store.insert_post_if_new({"urn": "umb:carrera", "texto": texto_400}, "q")
        store.update_post(
            pid_carrera, estado="ANALIZADA", triage={"puesto": puesto_valido},
            analisis={"match_score": 85.0, "oferta_cerrada": False}, texto=texto_400,
            created_at=fecha_valida,
        )
        # Verificamos que aparece en el listado antes de cada mutación:
        assert pid_carrera in [p["id"] for p in store.backlog_pendiente()], "Debe ser visible en backlog antes de mutar"

        # Simulación A: caída de score entre listar y reservar
        store.update_post(pid_carrera, analisis={"match_score": 60.0})
        assert store.tomar_automatico(pid_carrera) is False, "Caída de score debe rechazar la reserva automática"
        assert store.tomar_backlog(pid_carrera) is False, "Caída de score debe rechazar la reserva de backlog"
        assert store.get_post(pid_carrera)["estado"] == "ANALIZADA", "El estado no debe cambiar si la reserva fue rechazada"

        # Restaurar y verificar presencia en el listado:
        store.update_post(pid_carrera, analisis={"match_score": 85.0, "oferta_cerrada": False})
        assert pid_carrera in [p["id"] for p in store.backlog_pendiente()], "Debe ser visible en backlog antes de mutar"

        # Simulación B: cierre de la oferta entre listar y reservar
        store.update_post(pid_carrera, analisis={"match_score": 85.0, "oferta_cerrada": True})
        assert store.tomar_automatico(pid_carrera) is False, "Oferta cerrada debe rechazar la reserva automática"
        assert store.tomar_backlog(pid_carrera) is False, "Oferta cerrada debe rechazar la reserva de backlog"
        assert store.get_post(pid_carrera)["estado"] == "ANALIZADA", "El estado no debe cambiar si la reserva fue rechazada"

        # Restaurar y verificar presencia en el listado:
        store.update_post(pid_carrera, analisis={"match_score": 85.0, "oferta_cerrada": False})
        assert pid_carrera in [p["id"] for p in store.backlog_pendiente()], "Debe ser visible en backlog antes de mutar"

        # Simulación C: pérdida de captura suficiente entre listar y reservar
        store.update_post(pid_carrera, texto="Texto muy corto")
        assert store.tomar_automatico(pid_carrera) is False, "Texto insuficiente debe rechazar la reserva automática"
        assert store.tomar_backlog(pid_carrera) is False, "Texto insuficiente debe rechazar la reserva de backlog"
        assert store.get_post(pid_carrera)["estado"] == "ANALIZADA", "El estado no debe cambiar si la reserva fue rechazada"


@pytest.mark.asyncio
async def test_ocr_link_antes_de_validar(monkeypatch, tmp_path):
    """Lectura de OCR y link antes de validar captura; ERROR en captura incompleta y sin doble lectura."""
    # invariante: imagen base
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: prepare_fn y captura insuficiente antes de llamar a analyze_fn
    from core.radar import RadarLoop

    async with radares_aislados() as crear_loop:
        texto_corto = "Oferta laboral en empresa destacada. Contacto directo por LinkedIn."
        pid = store.insert_post_if_new(
            {"urn": "ocr:corto", "texto": texto_corto, "imagenes": ["https://img.test/1.png"]}, "q"
        )
        store.update_post(
            pid, estado="OFERTA", triage={"puesto": "Posición no especificada", "es_busqueda_laboral": True}
        )

        # Caso A: OCR fallido, sin link -> Captura incompleta, 0 llamadas a analyze_fn
        analyze_mock = AsyncMock(return_value={"match_score": 85.0, "puesto": "Analista Funcional"})
        ocr_fallido = AsyncMock(return_value="")
        loop = crear_loop(image_reader_fn=ocr_fallido, analyze_fn=analyze_mock)
        await loop._analizar(store.get_post(pid))

        analyze_mock.assert_not_awaited()
        post_error = store.get_post(pid)
        assert post_error["estado"] == "ERROR"
        assert "Captura incompleta" in (post_error.get("error") or "")

        # Forzada a mano sí genera aunque tenga captura incompleta usando generate_cv()
        pipeline_mock = AsyncMock(return_value=555)
        compose_mock = AsyncMock(return_value={"to": "rrhh@empresa.test", "subject": "S", "body": "B"})
        loop_forzado = crear_loop(pipeline_fn=pipeline_mock, compose_fn=compose_mock)

        # El botón Generar CV establece gen_prioridad=2 y gen_manual: la pausa solo frena lo automático
        #, así que lo pedido a mano se toma aunque esté pausada.
        store.update_radar_state(generacion_pausada=True)
        await loop_forzado.generate_cv(pid)
        assert store.get_post(pid)["gen_prioridad"] == 2
        assert store.get_post(pid)["gen_manual"] == 1
        assert store.get_post(pid)["estado"] == "GENERANDO"
        assert loop_forzado._siguiente_generacion()["id"] == pid

        # Ejecuta pipeline y compose falsos y termina en LISTO sin aprobación
        await loop_forzado._generar_post(store.get_post(pid))
        pipeline_mock.assert_awaited_once()
        compose_mock.assert_awaited_once()
        post_listo = store.get_post(pid)
        assert post_listo["estado"] == "LISTO"
        assert post_listo["aprobado_at"] is None, "Nada se envía sin aprobación explícita"

        # Caso B: OCR fallido pero link enlazado rescata >= 400 caracteres y puesto conocido
        texto_rescate = (
            "Buscamos un Analista Funcional con amplia experiencia en relevamiento de procesos "
            "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
            "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
            "híbrida en Buenos Aires o remoto para el interior del país. Ofrecemos excelentes condiciones de "
            "contratación, prepaga de primer nivel y plan de carrera en proyectos de transformación digital."
        )
        assert len(texto_rescate) >= 400

        pid_rescate = store.insert_post_if_new(
            {"urn": "ocr:rescate", "texto": texto_corto, "links": ["https://oferta.test/detalle"],
             "imagenes": ["https://img.test/rescate.png"]}, "q"
        )
        store.update_post(
            pid_rescate, estado="OFERTA", triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True}
        )

        # Usar la preparación/análisis reales de server permite detectar una
        # segunda lectura del mismo link dentro de _radar_analyze.
        import server

        assert callable(getattr(server, "_radar_preparar_analisis", None)), "Falta la preparación de páginas"
        leer_link = AsyncMock(return_value=("https://oferta.test/detalle", texto_rescate))
        estratega = AsyncMock(return_value={"match_score": 85.0, "puesto": "Analista Funcional", "requisitos": []})
        monkeypatch.setattr(server, "_leer_link_oferta", leer_link)
        monkeypatch.setattr(server.StrategistAgent, "analyze_offer", estratega)
        prepare_fn = AsyncMock(wraps=server._radar_preparar_analisis)
        analyze_rescate = AsyncMock(wraps=server._radar_analyze)
        ocr_rescate = AsyncMock(return_value="")
        loop_rescate = crear_loop(prepare_fn=prepare_fn, analyze_fn=analyze_rescate,
                                  image_reader_fn=ocr_rescate)
        await loop_rescate._analizar(store.get_post(pid_rescate))

        prepare_fn.assert_awaited_once()
        leer_link.assert_awaited_once_with("https://oferta.test/detalle")
        ocr_rescate.assert_awaited_once_with(["https://img.test/rescate.png"])
        analyze_rescate.assert_awaited_once()
        estratega.assert_awaited_once()
        assert texto_rescate in estratega.await_args.args[0]
        # Con score 85.0, generar_automaticos puede transicionar a GENERANDO (flujo válido):
        post_res = store.get_post(pid_rescate)
        assert post_res["estado"] in ("ANALIZADA", "GENERANDO")
        assert post_res["texto_pagina"] == texto_rescate
        assert post_res["texto"] == texto_corto, "La captura conserva el texto original"


@pytest.mark.asyncio
async def test_un_analisis_con_generacion_activa(monkeypatch, tmp_path):
    """Concurrencia de análisis es 1 (CONCURRENCIA_PROCESO=1) y respeta tope con generación activa."""
    # invariante: concurrencia base
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: CONCURRENCIA_PROCESO == 1
    from core.radar import CONCURRENCIA_PROCESO, RadarLoop

    texto_valido = "Buscamos Analista Funcional con experiencia en SQL y metodologías ágiles... " * 10
    ids_analisis = [
        store.insert_post_if_new({"urn": f"concurrencia:ana:{i}", "texto": texto_valido, "emails": ["rrhh@emp.test"]}, "q")
        for i in range(1, 4)
    ]
    for pid in ids_analisis:
        store.update_post(
            pid, estado="OFERTA", prioridad=1,
            triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True, "alcance": "ARGENTINA"},
        )

    # 1 post en generación activa
    pid_gen = store.insert_post_if_new({"urn": "concurrencia:gen:activo", "texto": texto_valido, "emails": ["rrhh@gen.test"]}, "q")
    store.update_post(pid_gen, estado="GENERANDO", gen_prioridad=2, triage={"puesto": "Analista Funcional"})

    gate_gen = asyncio.Event()
    gen_activa = asyncio.Event()
    gate_analisis = asyncio.Event()
    analisis_iniciado = asyncio.Event()
    analisis_completados = asyncio.Event()
    generacion_lista = asyncio.Event()
    analizados = set()
    analisis_en_curso = 0
    max_analisis_simultaneos = 0

    async def observar_post(evento, datos):
        if evento != "radar_post":
            return
        if datos["id"] == pid_gen and datos["estado"] == "LISTO":
            generacion_lista.set()
        if datos["id"] in ids_analisis and datos["estado"] in ("ANALIZADA", "GENERANDO"):
            analizados.add(datos["id"])
            if analizados == set(ids_analisis):
                analisis_completados.set()

    async def fake_pipe(post):
        gen_activa.set()
        await gate_gen.wait()
        return 999

    async def fake_analyze(post):
        nonlocal analisis_en_curso, max_analisis_simultaneos
        analisis_en_curso += 1
        max_analisis_simultaneos = max(max_analisis_simultaneos, analisis_en_curso)
        analisis_iniciado.set()
        try:
            await gate_analisis.wait()
            return {"match_score": 85.0, "puesto": "Analista Funcional"}
        finally:
            analisis_en_curso -= 1

    loop = make_radar_loop(analyze_fn=fake_analyze, pipeline_fn=fake_pipe, broadcast=observar_post)

    try:
        # Mecanismo único de arranque via wakes de RadarLoop (no tareas manuales duplicadas):
        await loop.wake_generation()
        loop._wake_processing()

        # Dos wakes no duplican supervisores ni rompen reservas:
        await loop.wake_generation()
        loop._wake_processing()

        # Sincronización determinística mediante eventos y wait_for:
        await asyncio.wait_for(gen_activa.wait(), timeout=60)
        await asyncio.wait_for(analisis_iniciado.wait(), timeout=60)

        # Comprobación de que la generación estuvo efectivamente activa:
        assert gen_activa.is_set(), "La generación debe estar activamente ejecutándose"
        assert pid_gen in loop._reservas_gen, "pid_gen debe estar reservado en la generación"

        # Verificamos reservas atómicas y máximos concurrentes:
        assert max_analisis_simultaneos == 1, "Nunca debe haber más de 1 análisis a la vez"
        assert CONCURRENCIA_PROCESO == 1
        assert len(loop._reservas_gen) <= 1, "Nunca debe haber más de 1 CV generándose a la vez"
        assert len(loop._en_vuelo) <= 1, "Nunca debe haber más de 1 análisis reservado en vuelo"

        # Liberamos análisis y dejamos terminar de forma sincronizada:
        gate_analisis.set()
        await asyncio.wait_for(analisis_completados.wait(), timeout=60)

        # Respuestas con score 85 mueven inmediatamente el post a GENERANDO (flujo válido):
        for pid in ids_analisis:
            post_analizado = store.get_post(pid)
            assert post_analizado["analisis"] is not None
            assert post_analizado["estado"] in ("ANALIZADA", "GENERANDO")

        # Liberamos generación y verificamos que culmina:
        gate_gen.set()
        await asyncio.wait_for(generacion_lista.wait(), timeout=60)
        assert store.get_post(pid_gen)["estado"] == "LISTO"

        # Reinicio y toma única:
        pid_reinicio = store.insert_post_if_new(
            {"urn": "concurrencia:reinicio", "texto": texto_valido, "emails": ["rrhh@reinicio.test"]}, "q"
        )
        store.update_post(pid_reinicio, estado="ANALIZANDO", triage={"puesto": "Analista Funcional"})
        store.reconcile_on_startup()
        assert store.get_post(pid_reinicio)["estado"] == "OFERTA", "Reconciliación debe devolver ANALIZANDO a OFERTA"

        # Toma única: la primera toma tiene éxito, la segunda no duplica:
        sig_trabajo = loop._siguiente_trabajo(todo=True)
        assert sig_trabajo is not None and sig_trabajo[1][0]["id"] == pid_reinicio
        loop._en_vuelo.add(pid_reinicio)
        sig_trabajo_2 = loop._siguiente_trabajo(todo=True)
        assert sig_trabajo_2 is None or pid_reinicio not in [p["id"] for p in sig_trabajo_2[1]]

    finally:
        gate_analisis.set()
        gate_gen.set()
        await cerrar_radares(loop)


@pytest.mark.asyncio
async def test_cosecha_durante_espera(monkeypatch, tmp_path):
    """Cosecha de 31 posts durante análisis bloqueado almacena todos los URNs sin pérdida y procesa en lotes <= 15."""
    # invariante: unicidad de cosecha base
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)

    from core.radar import RadarLoop, LOTE_TRIAGE

    texto_largo = "Buscamos Analista Funcional con amplia experiencia comprobable en relevamiento... " * 10
    pid_bloqueo = store.insert_post_if_new({"urn": "cosecha:bloqueo", "texto": texto_largo, "emails": ["rrhh@bloqueo.test"]}, "q")
    store.update_post(pid_bloqueo, estado="OFERTA", prioridad=1, triage={"puesto": "Analista Funcional", "es_busqueda_laboral": True})

    gate_analisis = asyncio.Event()
    analisis_en_curso = asyncio.Event()
    analisis_bloqueado_terminado = asyncio.Event()

    async def fake_analyze_bloqueado(post):
        analisis_en_curso.set()
        await gate_analisis.wait()
        return {"match_score": 85.0, "puesto": "Analista Funcional"}

    async def observar_bloqueado(evento, datos):
        if (evento == "radar_post" and datos["id"] == pid_bloqueo
                and datos["estado"] in ("ANALIZADA", "GENERANDO")):
            analisis_bloqueado_terminado.set()

    lotes_recibidos = []
    async def triage_burst(posts):
        lotes_recibidos.append([p["id"] for p in posts])
        return {p["id"]: {"es_busqueda_laboral": False, "alcance": "ARGENTINA"} for p in posts}

    # Una consulta dispone de cuota suficiente para los 31 posts en esta lectura.
    # Con las 12 consultas por defecto el reparto recorta el caso antes de medir concurrencia.
    store.update_radar_state(tope_posts_dia=2400, backlog_activo=False)
    loop = make_radar_loop(analyze_fn=fake_analyze_bloqueado, triage_fn=triage_burst,
                           queries=["query_burst"], broadcast=observar_bloqueado)

    try:
        # Arranque limpio mediante _wake_processing:
        loop._wake_processing()
        await asyncio.wait_for(analisis_en_curso.wait(), timeout=60)

        # Análisis está activamente esperando: ejecutamos cosecha de 31 posts
        urns = [f"cosecha:burst:{i}" for i in range(1, 32)]
        posts_cosecha = [
            {
                "urn": u,
                "url": f"https://linkedin.com/post/{u}",
                "autor": "HR",
                "cargo_autor": "Recruiter",
                "texto": f"Aviso de búsqueda laboral número {i} para profesionales de TI con experiencia.",
                "fecha_rel": "1 h",
                "emails": [f"rrhh_{i}@empresa.test"],
            }
            for i, u in enumerate(urns, start=1)
        ]

        class FakeHarvesterBurst:
            # Cada respuesta respeta el máximo real de 30 posts por consulta.
            harvest = AsyncMock(side_effect=[
                HarvestResult(posts=posts_cosecha[:15]),
                HarvestResult(posts=posts_cosecha[15:]),
            ])

        # Leemos la consulta durante el bloqueo
        harvester = FakeHarvesterBurst()
        leidos = await loop._leer_consulta(harvester, "query_burst", feed=False)
        leidos += await loop._leer_consulta(harvester, "query_burst", feed=False)
        assert leidos == 31
        assert not gate_analisis.is_set()
        assert not analisis_bloqueado_terminado.is_set()

        # Verificamos que los 31 URNs quedaron almacenados en SQLite como NUEVO mientras el análisis sigue esperando
        guardados = {p["urn"]: p for p in store.list_posts(estado="NUEVO")}
        for u in urns:
            assert u in guardados, f"URN {u} no fue guardado durante la cosecha"

        # Liberamos el análisis bloqueado
        gate_analisis.set()
        await asyncio.wait_for(analisis_bloqueado_terminado.wait(), timeout=60)

        # Flujo válido: score 85.0 puede encolar automáticamente a GENERANDO
        post_bloq = store.get_post(pid_bloqueo)
        assert post_bloq["analisis"] is not None and post_bloq["analisis"]["match_score"] == 85.0
        assert post_bloq["estado"] in ("ANALIZADA", "GENERANDO")

        # invariante: base actual cosecha durante espera y preserva los 31 URNs
        assert len(guardados) >= 31

        if os.environ.get("INVARIANT_CHECK") == "1":
            return

        # defecto: ráfaga de 31 posts (>2 lotes de 15) conserva todos los urns y procesa en lotes <= 15
        assert LOTE_TRIAGE == 15
        trabajo1 = loop._siguiente_trabajo(todo=True)
        assert trabajo1 is not None and trabajo1[0] == "clasificar"
        assert len(trabajo1[1]) == 15
        await loop._ejecutar(trabajo1)

        trabajo2 = loop._siguiente_trabajo(todo=True)
        assert trabajo2 is not None and trabajo2[0] == "clasificar"
        assert len(trabajo2[1]) == 15
        await loop._ejecutar(trabajo2)

        trabajo3 = loop._siguiente_trabajo(todo=True)
        assert trabajo3 is not None and trabajo3[0] == "clasificar"
        assert len(trabajo3[1]) == 1  # 15 + 15 + 1 = 31 posts
        await loop._ejecutar(trabajo3)

        assert loop._siguiente_trabajo(todo=True) is None
        assert [len(lote) for lote in lotes_recibidos] == [15, 15, 1]
        assert {pid for lote in lotes_recibidos for pid in lote} == {guardados[u]["id"] for u in urns}

    finally:
        gate_analisis.set()
        await cerrar_radares(loop)


@pytest.mark.asyncio
async def test_reinicio_y_schema_aditivo(monkeypatch, tmp_path):
    """Schema viejo, migración aditiva idempotente (dos ejecuciones) y recuperación al reinicio."""
    # invariante: medir I1-I6 antes de usar columnas nuevas.
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)
    db_file = tmp_path / "vieja.db"
    fecha_fija = "2026-11-08"
    monkeypatch.setattr(store, "_today", lambda: fecha_fija)
    conn = sqlite3.connect(db_file)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE radar_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    desired_running INTEGER DEFAULT 0,
                    estado TEXT DEFAULT 'DETENIDO',
                    estado_detalle TEXT DEFAULT '',
                    next_cycle_at TIMESTAMP,
                    umbral_match INTEGER DEFAULT 60,
                    tope_posts_dia INTEGER DEFAULT 600,
                    tope_envios_dia INTEGER DEFAULT 40,
                    contadores_dia TEXT DEFAULT '{"match": 5}',
                    fecha_contadores TEXT,
                    ciclos_vacios_consecutivos INTEGER DEFAULT 0,
                    envios_pausados INTEGER DEFAULT 1,
                    generacion_pausada INTEGER DEFAULT 1,
                    analisis_pausado INTEGER DEFAULT 1,
                    backlog_activo INTEGER DEFAULT 1
                )
            """)
            conn.execute(
                "INSERT INTO radar_state (id, desired_running, generacion_pausada, contadores_dia, fecha_contadores) "
                "VALUES (1, 1, 1, '{\"match\": 5}', ?)",
                (fecha_fija,),
            )
            conn.execute("""
                CREATE TABLE linkedin_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    urn TEXT UNIQUE NOT NULL,
                    url TEXT,
                    autor TEXT,
                    cargo_autor TEXT,
                    texto TEXT NOT NULL,
                    fecha_rel TEXT,
                    query TEXT,
                    emails TEXT DEFAULT '[]',
                    triage TEXT,
                    estado TEXT DEFAULT 'NUEVO',
                    postulacion_id INTEGER,
                    email_to TEXT,
                    email_subject TEXT,
                    email_body TEXT,
                    aprobado_at TIMESTAMP,
                    sent_at TIMESTAMP,
                    message_id TEXT,
                    error TEXT,
                    intentos INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fuente TEXT DEFAULT 'busqueda',
                    links TEXT DEFAULT '[]',
                    whatsapp TEXT DEFAULT '[]',
                    canal TEXT DEFAULT '',
                    destino_canal TEXT DEFAULT '',
                    imagenes TEXT DEFAULT '[]',
                    texto_imagen TEXT DEFAULT ''
                )
            """)
            conn.execute("INSERT INTO linkedin_posts (urn, texto, estado) VALUES ('viejo:1', 'Texto post', 'ANALIZANDO')")
            conn.execute("INSERT INTO linkedin_posts (urn, texto, estado) VALUES ('viejo:2', 'Texto post 2', 'GENERANDO')")
    finally:
        conn.close()

    # Primera inicialización
    conn1 = sqlite3.connect(db_file)
    try:
        store.init_radar_tables(conn1)
    finally:
        conn1.close()

    # Segunda inicialización (debe ser idempotente)
    conn2 = sqlite3.connect(db_file)
    try:
        store.init_radar_tables(conn2)
    finally:
        conn2.close()

    # invariante: preservar pausas y contadores tras ambas inicializaciones
    st = store.get_radar_state(db_path=db_file)
    assert st["generacion_pausada"] is True
    assert st["analisis_pausado"] is True
    assert st["envios_pausados"] is True
    assert st["contadores_dia"].get("match") == 5
    assert st["desired_running"] is True
    assert st["umbral_match"] == 60, "La migración debe conservar el valor legacy persistido"

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: nuevas columnas en schema
    conn_check = sqlite3.connect(db_file)
    try:
        cols_state = {r[1] for r in conn_check.execute("PRAGMA table_info(radar_state)").fetchall()}
        cols_posts = {r[1] for r in conn_check.execute("PRAGMA table_info(linkedin_posts)").fetchall()}
    finally:
        conn_check.close()

    assert "modo_serie" in cols_state
    assert "agy_serie_hasta" in cols_state
    assert "agy_historial" in cols_state
    assert "texto_pagina" in cols_posts
    assert st["modo_serie"] is False
    assert st["agy_serie_hasta"] is None
    assert st["agy_historial"] == []
    assert store.get_post(1, db_path=db_file)["texto_pagina"] == ""

    # Reconciliación al reinicio
    store.reconcile_on_startup(db_path=db_file)
    p_recup = store.get_post(1, db_path=db_file)
    assert p_recup["estado"] == "OFERTA", "ANALIZANDO debe volver a OFERTA tras reinicio"

    p_gen = store.get_post(2, db_path=db_file)
    assert p_gen["estado"] == "GENERANDO", "GENERANDO debe preservarse para recuperación limpia"

    # Toma única después del reinicio: exigir éxito antes del rechazo evita
    # que una reserva rota que siempre devuelve False pase la prueba.
    store.update_post(1, db_path=db_file, estado="ANALIZADA", texto="A" * 400,
                      triage={"puesto": "Analista Funcional"}, analisis={"match_score": 85.0})
    assert store.tomar_automatico(1, db_path=db_file) is True
    assert store.get_post(1, db_path=db_file)["estado"] == "GENERANDO"
    assert store.tomar_automatico(1, db_path=db_file) is False
    assert store.tomar_automatico(2, db_path=db_file) is False, "Post en GENERANDO no se duplica en toma automática"
