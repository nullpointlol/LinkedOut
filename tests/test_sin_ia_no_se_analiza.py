"""Análisis, selección del CV, CV y mail sin IA fallan o esperan la cuota; nunca salen deterministas."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import bridge as modulo_bridge
from core import cv_maestro as cm
from core import email_composer
from core import strategist
from core.bridge import CUOTA, AntigravityBridge, CuotaAgotada
from core.strategist import AnalisisSinIA, StrategistAgent
from tests.cv_final_prueba import es_seleccion

OFERTA = "Analista\nAcme\n• Python\n• SQL"
RESPUESTA = {"empresa": "Acme", "puesto": "Analista", "requisitos": [
    {"nombre": "Python", "estado": "MATCH_DIRECTO", "texto": "Python"}]}


@pytest.fixture(autouse=True)
def _sin_aprendizajes(monkeypatch, tmp_path):
    import core.aprendizajes as ap
    monkeypatch.setattr(ap, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")


def _agente(bridge):
    return StrategistAgent(bridge=bridge, expander=Mock(get_expanded_competencies=Mock(return_value=[])))


def test_no_quedan_caminos_deterministas():
    assert not hasattr(StrategistAgent, "_deterministic_fallback")
    assert not hasattr(email_composer, "deterministic_email")
    assert not hasattr(email_composer, "deterministic_message")
    from core.writer import WriterAgent
    assert not hasattr(WriterAgent, "_deterministic_fallback")


@pytest.mark.asyncio
@pytest.mark.parametrize("falla", [
    AsyncMock(side_effect=TimeoutError("420s")),
    AsyncMock(side_effect=RuntimeError("503 service unavailable")),
    AsyncMock(return_value={"empresa": "Acme"}),  # sin requisitos
    AsyncMock(return_value="texto suelto"),
])
async def test_una_falla_de_la_ia_falla_el_analisis(falla):
    with pytest.raises(AnalisisSinIA, match="no respondió un análisis válido"):
        await _agente(Mock(effort="low", run_prompt=falla)).analyze_offer(OFERTA)


@pytest.fixture
def cuota_controlada(monkeypatch):
    """La espera de cuota avanza solo cuando el test la libera (sin tiempos fijos)."""
    ahora = [1_800_000_000.0]
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: ahora[0]))
    entradas = asyncio.Queue()
    pendientes = []
    original = CUOTA.esperar_turno

    async def dormir(segundos):
        futuro = asyncio.get_running_loop().create_future()
        pendientes.append(futuro)
        entradas.put_nowait(futuro)
        await futuro

    async def esperar_turno():
        return await original(dormir=dormir)

    def vencer():
        ahora[0] = CUOTA.reanuda_en
        for futuro in pendientes:
            if not futuro.done():
                futuro.set_result(None)
        pendientes.clear()

    monkeypatch.setattr(CUOTA, "esperar_turno", esperar_turno)
    return SimpleNamespace(entrada=lambda: asyncio.wait_for(entradas.get(), 30), vencer=vencer)


@pytest.mark.asyncio
async def test_sin_cuota_el_analisis_espera_y_nunca_sale_determinista(monkeypatch, cuota_controlada):
    llamadas = []

    async def ejecutar(self, prompt, *a, **kw):
        llamadas.append(prompt)
        if len(llamadas) == 1:
            raise CuotaAgotada("RESOURCE_EXHAUSTED: quota exceeded")
        return RESPUESTA

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    tarea = asyncio.create_task(_agente(AntigravityBridge(effort="low")).analyze_offer(OFERTA))
    await cuota_controlada.entrada()
    # Sin cuota: el análisis espera, sin resultado ni error
    assert CUOTA.estado()["agotada"] and not tarea.done() and len(llamadas) == 1
    cuota_controlada.vencer()
    resultado = await asyncio.wait_for(tarea, 30)
    assert len(llamadas) == 2 and [r["nombre"] for r in resultado["requisitos"]] == ["Python"]
    assert "analisis_fallback" not in resultado


@pytest.mark.asyncio
async def test_el_radar_falla_con_la_causa_para_su_reintento(monkeypatch):
    import server
    from core import radar
    monkeypatch.setattr(radar, "_ticket", Mock())

    async def caida(self, prompt, **kw):
        raise RuntimeError("503 service unavailable")

    monkeypatch.setattr(server.AntigravityBridge, "run_prompt", caida)
    with pytest.raises(RuntimeError, match=r"no respondió un análisis válido \(503 service unavailable\)"):
        await server._radar_analyze({"texto": OFERTA, "links_leidos": []})


@pytest.mark.asyncio
async def test_sin_ia_el_mail_falla_en_vez_de_salir_de_plantilla():
    post = {"texto": "Buscamos analista. Enviá tu CV a rrhh@acme.test", "triage": {"puesto": "Analista"}}
    postulacion = {"cv_markdown": "## Experiencia\n- Automatizé reportes con SQL", "titulo": "Analista"}
    caido = Mock(run_prompt=AsyncMock(side_effect=RuntimeError("503 service unavailable")))
    with pytest.raises(RuntimeError, match="503"):
        await email_composer.compose_email(post, postulacion, {"nombre": "Ana Prueba"}, caido)
    with pytest.raises(RuntimeError, match="503"):
        await email_composer.compose_message(dict(post, canal="dm"), postulacion, {"nombre": "Ana Prueba"}, caido)


@pytest.mark.asyncio
async def test_sin_ia_la_seleccion_del_cv_falla_en_vez_de_elegir_por_palabras(monkeypatch):
    from tests.test_cv_maestro import ANALISIS, MAESTRO, PERSONALES
    monkeypatch.setattr(cm, "_ticket", Mock())
    ia = AsyncMock(side_effect=lambda prompt, **kw: (_ for _ in ()).throw(RuntimeError("sin respuesta"))
                   if es_seleccion(prompt) else {})
    flujo = cm.FlujoCV(maestro=MAESTRO, personales=PERSONALES, analisis=ANALISIS, oferta="Buscamos analista.",
                       titulo="ANALISTA", idioma="es", ia=ia)
    with pytest.raises(RuntimeError, match="sin respuesta"):
        await flujo.generar()
    assert flujo.seleccion != "codigo"


@pytest.mark.asyncio
async def test_sin_ia_el_redactor_falla_en_vez_de_un_cv_determinista(monkeypatch):
    from core.writer import WriterAgent
    agente = WriterAgent(bridge=Mock(effort="high", run_prompt=AsyncMock(side_effect=TimeoutError("420s"))))
    perfil = {"experiencia": "Analista en Acme (2020 - Actual)\n- Automaticé reportes", "personales": "Nombre: Ana",
              "educacion": "", "idiomas": ""}
    with pytest.raises(Exception, match="420s"):
        await agente.generate_cv_and_pitch({"puesto": "Analista", "empresa": "Acme", "requisitos": []},
                                           profile=perfil)
