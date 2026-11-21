"""Tests de detección de no-oferta y flujo protegido sin generación de CV."""
import os
from unittest.mock import AsyncMock, Mock

import pytest

from core import radar_store
from core.radar import RadarLoop
from core.strategist import StrategistAgent
from core.triage import PROMPT_TEMPLATE, prefiltro_exterior

TEXTO_SUFICIENTE = (
    "Buscamos un Analista Funcional con amplia experiencia en relevamiento de procesos "
    "de negocio, metodologías ágiles Scrum, confección de historias de usuario y diagramas de flujo. "
    "Se requiere manejo de SQL, APIs REST y herramientas de gestión como Jira. Modalidad de trabajo "
    "híbrida en Buenos Aires o remoto para el interior del país."
)


@pytest.mark.asyncio
async def test_bridge_false_sin_cv(tmp_path):
    def nuevo_post(urn_id: int):
        texto_largo = f"{TEXTO_SUFICIENTE}\n{TEXTO_SUFICIENTE} (aviso {urn_id})"
        pid = radar_store.insert_post_if_new(
            {
                "urn": f"urn:li:activity:{urn_id}",
                "url": f"https://example.test/{urn_id}",
                "texto": texto_largo,
                "emails": ["rrhh@empresa.test"],
            },
            query="analista",
        )
        radar_store.update_post(
            pid,
            estado="OFERTA",
            triage={"es_busqueda_laboral": True, "puesto": "Analista Funcional", "alcance": "ARGENTINA"},
            email_to="rrhh@empresa.test",
        )
        return radar_store.get_post(pid)

    p1 = nuevo_post(91001)
    p2 = nuevo_post(91002)

    analisis_ok = {
        "puesto": "Analista Funcional",
        "empresa": "Acme",
        "match_score": 90.0,
        "requisitos": [
            {"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True}
        ],
    }

    loop = RadarLoop(
        harvester_factory=lambda: Mock(),
        triage_fn=AsyncMock(),
        analyze_fn=AsyncMock(return_value=dict(analisis_ok)),
        pipeline_fn=AsyncMock(),
        compose_fn=AsyncMock(),
        send_fn=AsyncMock(),
        broadcast=AsyncMock(),
        sleep=AsyncMock(),
        debug_dir=tmp_path / "debug",
    )
    loop._emitir_post = AsyncMock()
    loop.generar_automaticos = AsyncMock()
    loop.wake_generation = AsyncMock()

    # invariante: es_oferta ausente o True conserva comportamiento: pasa a ANALIZADA, error=None, no aprobado_at
    # 1. es_oferta ausente (compatibilidad histórica)
    await RadarLoop._analizar(loop, p1)
    res_p1 = radar_store.get_post(p1["id"])
    assert res_p1["estado"] == "ANALIZADA"
    assert res_p1["error"] is None
    assert res_p1["aprobado_at"] is None
    loop.generar_automaticos.assert_awaited_once_with([p1["id"]])

    # 2. es_oferta: True
    loop.generar_automaticos.reset_mock()
    loop._analyze_fn = AsyncMock(return_value=dict(analisis_ok, es_oferta=True))
    await RadarLoop._analizar(loop, p2)
    res_p2 = radar_store.get_post(p2["id"])
    assert res_p2["estado"] == "ANALIZADA"
    assert res_p2["error"] is None
    assert res_p2["aprobado_at"] is None
    loop.generar_automaticos.assert_awaited_once_with([p2["id"]])

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto: bridge devuelve es_oferta=False con match_score alto (ej. 95)
    # Debe ir a IGNORADA con error="no es una oferta" y NO encolar CV
    p3 = nuevo_post(91003)
    loop.generar_automaticos.reset_mock()
    loop.wake_generation.reset_mock()
    loop._analyze_fn = AsyncMock(return_value=dict(analisis_ok, match_score=95.0, es_oferta=False))

    await RadarLoop._analizar(loop, p3)
    res_p3 = radar_store.get_post(p3["id"])
    assert res_p3["estado"] == "IGNORADA"
    assert res_p3["error"] == "no es una oferta"
    assert res_p3["aprobado_at"] is None
    loop.generar_automaticos.assert_not_awaited()
    loop.wake_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_y_manual_compatibles(tmp_path):
    # invariante: delimitación de datos de terceros y veta a decisiones aprendidas autoatribuidas
    prompt_recibido = []

    async def fake_bridge(p, **kw):
        prompt_recibido.append(p)
        return {
            "puesto": "Analista Funcional",
            "empresa": "Tech",
            "requisitos": [
                {
                    "nombre": "Python",
                    "tipo": "REQUISITO_EXCLUYENTE",
                    "estado": "BRECHA_REAL",
                    "approved": False,
                    "aprendido": True,  # Autoatribuido por IA
                    "eliminatorio_usuario": True,  # Autoatribuido por IA
                }
            ],
        }

    bridge = Mock()
    bridge.run_prompt = fake_bridge

    agent = StrategistAgent(bridge=bridge)
    agent.load_candidate_experience = lambda: "Experiencia base"
    agent.expander = Mock(get_expanded_competencies=lambda: [])

    # Flujo con datos de terceros: respeta delimitación <post>
    res_terceros = await agent.analyze_offer("Buscamos dev Python...", datos_de_terceros=True)
    assert "<post" in prompt_recibido[0]
    req = res_terceros["requisitos"][0]
    assert "eliminatorio_usuario" not in req

    # Flujo manual: no cambia estado de Postulaciones ni crea registros en radar
    post_count_antes = len(radar_store.list_posts())
    res_manual = await agent.analyze_offer("Oferta manual...", datos_de_terceros=False)
    assert isinstance(res_manual, dict)
    assert len(radar_store.list_posts()) == post_count_antes

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # defecto:
    # 1. Prompt de triage incluye convocatoria abierta sin puesto concreto (builders, talento, curiosa, portfolio/github)
    #    sin convertirse en filtro determinístico de palabras
    prompt_triage_lower = PROMPT_TEMPLATE.lower()
    assert "convocatoria abierta" in prompt_triage_lower or "sin puesto concreto" in prompt_triage_lower
    assert any(palabra in prompt_triage_lower for palabra in ("builders", "talento", "curiosa", "curioso"))
    assert any(palabra in prompt_triage_lower for palabra in ("prototipo", "portfolio", "github"))
    # No es filtro determinístico: mención de github o portfolio en puesto real no descarta
    assert prefiltro_exterior("Buscamos developer en CABA, ver github.com/empresa") is None

    # 2. Prompt Estratega solicita explícitamente "es_oferta": bool
    assert '"es_oferta": bool' in prompt_recibido[0] or '"es_oferta"' in prompt_recibido[0]

    # 3. Flujo manual conserva la clave es_oferta sin efectos secundarios en radar
    bridge.run_prompt = AsyncMock(
        return_value={
            "puesto": "Analista",
            "empresa": "Tech",
            "es_oferta": False,
            "requisitos": [
                {"nombre": "SQL", "tipo": "REQUISITO_EXCLUYENTE", "estado": "MATCH_DIRECTO", "approved": True}
            ],
        }
    )
    res_manual_false = await agent.analyze_offer("Convocatoria...", datos_de_terceros=False)
    assert res_manual_false.get("es_oferta") is False
    assert len(radar_store.list_posts()) == post_count_antes
