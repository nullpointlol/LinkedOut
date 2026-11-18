"""Años respaldados por historial, fuera del circuito por oferta."""
import asyncio
import ast
import importlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core import profile_parser


AREAS = [{"area": "Experiencia laboral total", "anios": "7"},
         {"area": "soporte", "anios": "más de 3"}]


@pytest.fixture
def perfil(tmp_path):
    (tmp_path / "experiencia.txt").write_text(
        "## Empresa\n### Soporte | 01/2018 - 01/2025\n"
        "Resolví incidentes. Capacité operadores con más de 20 años de experiencia.",
        encoding="utf-8")
    (tmp_path / "personales.txt").write_text("# Ana Prueba", encoding="utf-8")
    return tmp_path


@pytest.fixture
def areas_module():
    assert importlib.util.find_spec("core.perfil_areas"), "Falta el análisis de años por área"
    return importlib.import_module("core.perfil_areas")


def bridge(respuesta=AREAS):
    return type("BridgeFalso", (), {"run_prompt": AsyncMock(return_value=respuesta)})()


@pytest.mark.asyncio
async def test_cache_igual_no_llama_y_perfil_cambiado_regenera(perfil, areas_module):
    primero = bridge()
    assert await areas_module.asegurar_anios_por_area(primero, perfil) == AREAS
    primero.run_prompt.assert_awaited_once()
    opciones = primero.run_prompt.call_args.kwargs
    assert opciones["json_mode"] is True
    assert opciones["effort"] == "low"
    cache = perfil / "anios_por_area.json"
    anterior = json.loads(cache.read_text(encoding="utf-8"))
    assert len(anterior["hash"]) == 64
    assert anterior["generado_en"]
    segundo = bridge()
    assert await areas_module.asegurar_anios_por_area(segundo, perfil) == AREAS
    segundo.run_prompt.assert_not_awaited()
    assert areas_module.anios_por_area_en_cache(perfil) == AREAS
    for archivo in ("personales.txt", "experiencia.txt"):
        with (perfil / archivo).open("a", encoding="utf-8") as f:
            f.write("\nDato nuevo")
        assert areas_module.anios_por_area_en_cache(perfil) == []
        nuevo = bridge()
        assert await areas_module.asegurar_anios_por_area(nuevo, perfil) == AREAS
        nuevo.run_prompt.assert_awaited_once()
        actual = json.loads(cache.read_text(encoding="utf-8"))
        assert anterior["hash"] != actual["hash"]
        anterior = actual


@pytest.mark.asyncio
async def test_descarta_cifras_y_areas_invalidas(perfil, areas_module):
    respuesta = [
        {"area": "soporte", "anios": "más de 3"},
        {"area": " Soporte ", "anios": "4"},
        {"area": "", "anios": "2"},
        {"area": "ITSM", "anios": "8"},
        {"area": "incidentes", "anios": "0"},
        {"area": "DevOps", "anios": "1.5"},
        {"area": "guardias", "anios": 2},
        {"area": "negativos", "anios": "-1"},
    ]
    # El total va primero y sale de las fechas (criterio v2, 08/11).
    assert await areas_module.asegurar_anios_por_area(bridge(respuesta), perfil) == [
        {"area": "Experiencia laboral total", "anios": "7"}, respuesta[0]]


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [None, "no es JSON", {}, [],
    [{"area": "soporte", "anios": "20"}], [{"area": "", "anios": "2"}]])
async def test_respuesta_invalida_no_cachea(perfil, areas_module, respuesta):
    falso = bridge(respuesta)
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == []
    assert not (perfil / "anios_por_area.json").exists()
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == []
    assert falso.run_prompt.await_count == 2


@pytest.mark.asyncio
async def test_error_bridge_no_cachea(perfil, areas_module):
    falso = bridge()
    falso.run_prompt.side_effect = RuntimeError("AGY no disponible")
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == []
    assert not (perfil / "anios_por_area.json").exists()


@pytest.mark.asyncio
async def test_maximo_ocho_y_wrapper_json(perfil, areas_module):
    respuesta = [{"area": f"área {i}", "anios": "1"} for i in range(10)]
    assert await areas_module.asegurar_anios_por_area(
        bridge({"anios_por_area": respuesta}), perfil) == [{"area": "Experiencia laboral total", "anios": "7"},
                                                         *respuesta[:7]]


@pytest.mark.asyncio
async def test_load_lee_cache_sin_modelo_y_parse_sigue_puro(perfil, areas_module, monkeypatch):
    await areas_module.asegurar_anios_por_area(bridge(), perfil)
    def prohibido(*args, **kwargs):
        raise AssertionError("La lectura del perfil no puede construir un bridge")

    monkeypatch.setattr(areas_module, "AntigravityBridge", prohibido)
    monkeypatch.setattr(areas_module, "asegurar_anios_por_area", AsyncMock(
        side_effect=AssertionError("No analizar por oferta")))
    assert profile_parser.load_candidate_profile(perfil)["anios_por_area"] == AREAS
    assert "anios_por_area" not in profile_parser.parse_candidate_profile("", "")
    (perfil / "personales.txt").write_text("# Otro nombre", encoding="utf-8")
    cargado = profile_parser.load_candidate_profile(perfil)
    assert cargado == profile_parser.parse_candidate_profile(
        (perfil / "experiencia.txt").read_text(encoding="utf-8"), "# Otro nombre")


@pytest.mark.asyncio
async def test_cache_corrupto_se_recalcula(perfil, areas_module):
    (perfil / "anios_por_area.json").write_text("{", encoding="utf-8")
    assert areas_module.anios_por_area_en_cache(perfil) == []
    falso = bridge()
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == AREAS
    falso.run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_publica_perfil_que_cambio_durante_analisis(perfil, areas_module):
    falso = bridge()

    async def cambiar(*args, **kwargs):
        (perfil / "experiencia.txt").write_text("Historial nuevo", encoding="utf-8")
        return AREAS

    falso.run_prompt.side_effect = cambiar
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == []
    assert not (perfil / "anios_por_area.json").exists()


@pytest.mark.asyncio
async def test_timeout_no_cachea_y_cancela_bridge(perfil, areas_module, monkeypatch):
    cancelado = asyncio.Event()

    async def colgado(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelado.set()

    falso = bridge()
    falso.run_prompt.side_effect = colgado
    monkeypatch.setattr(areas_module, "TIMEOUT_ANALISIS", 0.02)
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == []
    assert cancelado.is_set()
    assert not (perfil / "anios_por_area.json").exists()


@pytest.mark.asyncio
async def test_prompt_prohibe_inventar_anios_y_cifras_de_terceros(perfil, areas_module):
    falso = bridge()
    await areas_module.asegurar_anios_por_area(falso, perfil)
    prompt = falso.run_prompt.call_args.args[0]
    for regla in ("conservadoras", "redondeando hacia abajo", "No superpongas años",
                  "fechas y tareas", "terceros", "operadores con más de 20 años"):
        assert regla in prompt


def test_load_sin_cache_conserva_formato_del_parser(perfil):
    esperado = profile_parser.parse_candidate_profile(
        (perfil / "experiencia.txt").read_text(encoding="utf-8"),
        (perfil / "personales.txt").read_text(encoding="utf-8"))
    assert profile_parser.load_candidate_profile(perfil) == esperado


@pytest.mark.asyncio
async def test_lifespan_no_bloquea_y_cancela_tarea(monkeypatch):
    import server
    assert hasattr(server, "asegurar_anios_por_area"), "Falta el análisis al arrancar"
    iniciado, cancelado = asyncio.Event(), asyncio.Event()

    async def lento():
        iniciado.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelado.set()

    monkeypatch.setattr(server, "asegurar_anios_por_area", lento)
    monkeypatch.setattr(server.radar_loop, "resume_if_needed", AsyncMock())
    monkeypatch.setattr(server.radar_loop, "stop_for_shutdown", AsyncMock())
    async with server.lifespan(server.app):
        await asyncio.wait_for(iniciado.wait(), 60)
    assert cancelado.is_set()


@pytest.mark.asyncio
async def test_lifespan_sobrevive_error_de_analisis(monkeypatch):
    import server
    assert hasattr(server, "asegurar_anios_por_area"), "Falta el análisis al arrancar"
    falso = AsyncMock(side_effect=RuntimeError("Sin AGY"))
    monkeypatch.setattr(server, "asegurar_anios_por_area", falso)
    monkeypatch.setattr(server.radar_loop, "resume_if_needed", AsyncMock())
    monkeypatch.setattr(server.radar_loop, "stop_for_shutdown", AsyncMock())
    async with server.lifespan(server.app):
        await asyncio.sleep(0)
    falso.assert_awaited_once()


def test_pipeline_no_llama_a_analisis_por_oferta():
    # Llamadores permitidos: el hook del ciclo de vida y la generación del CV desde el maestro (14/11), que usa el
    # caché por huella del perfil: una llamada a la IA por perfil, nunca una por oferta (Mi perfil avisa que los
    # años se recalculan en el próximo CV).
    archivos = [Path("server.py"), *Path("core").rglob("*.py")]
    for archivo in archivos:
        if archivo.name == "perfil_areas.py":
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                llamadas = [n for n in ast.walk(nodo) if isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Name)
                            and n.func.id == "asegurar_anios_por_area"]
                if llamadas:
                    assert archivo.name == "server.py" and nodo.name in {
                        "lifespan", "_analizar_perfil_al_iniciar", "_generar_desde_maestro"}



@pytest.mark.asyncio
async def test_total_de_fechas_y_sin_tareas_sin_fecha(perfil, areas_module):
    """El total de la IA se reemplaza por el de las fechas; el prompt pide omitir tareas sin fechas propias."""
    falso = bridge([{"area": "Experiencia total", "anios": "6"}, {"area": "soporte", "anios": "más de 3"}])
    assert await areas_module.asegurar_anios_por_area(falso, perfil) == [
        {"area": "Experiencia laboral total", "anios": "7"}, {"area": "soporte", "anios": "más de 3"}]
    prompt = falso.run_prompt.await_args.args[0]
    assert "NO incluyas la experiencia total" in prompt and "OMITILA" in prompt

