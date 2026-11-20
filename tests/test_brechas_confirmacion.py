"""Confirmación conservadora con ofertas sintéticas y proveedor simulado."""
import copy
import importlib
import importlib.util
import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import strategist


RESTRICCIONES = {
    "base": "San Isidro, AMBA",
    "presencial_hibrido_permitido": "AMBA (CABA y GBA)", "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro", "san fernando", "tigre", "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero", "san miguel", "jose c paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo", "berazategui", "florencio varela", "ezeiza", "esteban echeverria", "almirante brown"],
    "remoto_desde": "Argentina",
    "viajes_frecuentes_o_interior": False,
    "reubicacion": False,
    "rubro_o_funcion_exigida_sin_experiencia": False,
    "edad": {"actual": 36, "al": "2026-11-09"},
}


def modulo():
    assert importlib.util.find_spec("core.descartes") is not None, "Falta el confirmador de restricciones"
    return importlib.import_module("core.descartes")


@pytest.fixture(autouse=True)
def fecha_de_la_mision(monkeypatch):
    if importlib.util.find_spec("core.descartes"):
        class Fecha(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 9)
        monkeypatch.setattr(modulo(), "date", Fecha)


def requisito(texto, categoria=None, **extra):
    req = {"nombre": texto, "texto": texto, "estado": "BRECHA_REAL",
           "tipo": "REQUISITO_EXCLUYENTE", "approved": False}
    if categoria:
        req.update(eliminatorio=True, categoria_descarte=categoria)
    req.update(extra)
    return req


@pytest.mark.parametrize("categoria", [None, "UBICACION"])
@pytest.mark.parametrize("texto", [
    "Híbrido en Córdoba",
    "Modalidad híbrida en Córdoba",
    "Modalidad híbrida en Rosario (4 días presenciales)",
    "Presencialidad 4 días en Neuquén",
    "Presencial o híbrido en Mendoza",
    "Hybrid en Córdoba",
    "Presencial o remoto en Córdoba",
    *[f"{n} días presenciales en Rosario" for n in range(1, 5)],
    *[f"{n} días en la oficina en Córdoba" for n in range(1, 5)],
])
def test_hibrido_fuera_de_amba(texto, categoria):
    assert modulo().motivos_descarte({"requisitos": [requisito(texto, categoria)]}, RESTRICCIONES) == []


@pytest.mark.parametrize("categoria", [None, "UBICACION"])
@pytest.mark.parametrize("texto", [
    "Híbrido en Córdoba, con residencia en Córdoba",
    "Modalidad híbrida. Excluyente vivir en Rosario",
    "Híbrido en Córdoba; radicado en Córdoba",
    "Híbrida en Córdoba; radicada en Córdoba",
    "Híbrido en Córdoba; residir en Córdoba",
    "Híbrido en Córdoba; domicilio en Córdoba",
    "Presencial en Córdoba",
    "100% presencial en Rosario",
])
def test_hibrido_con_residencia_y_presencial(texto, categoria):
    motivos = modulo().motivos_descarte({"requisitos": [requisito(texto, categoria)]}, RESTRICCIONES)
    assert motivos == [{"requisito": texto, "categoria": "UBICACION", "texto": texto}]


@pytest.mark.parametrize("texto,categoria", [
    ("Híbrido en Córdoba", "UBICACION"),
    ("Lugar de trabajo", "UBICACION"),
    ("Modalidad remota", "UBICACION"),
    ("4 días en la oficina", "UBICACION"),
    ("Residencia requerida", "UBICACION"),
    ("Viajes ocasionales", "VIAJES"),
    ("Mudanza opcional", "REUBICACION"),
    ("Reubicación opcional", "REUBICACION"),
    ("Edad hasta 45", "EDAD"),
    ("Experiencia en gastronomía", "RUBRO"),
])
def test_categoria_manual_por_tema(texto, categoria):
    req = requisito(texto, eliminatorio_usuario=True)
    assert modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == [
        {"requisito": texto, "categoria": categoria, "texto": texto}]


@pytest.mark.parametrize("req,esperada", [
    (requisito("ITC Argentina busca #INSTRUCTORES EN #INFORMÁTICA en #EntreRíos, #Chaco, #SantaFe y #Corrientes",
               "UBICACION", nombre="Ubicación en Entre Ríos, Chaco, Santa Fe o Corrientes"), "UBICACION"),
    (requisito("Personas de 21 a 35 años.", "EDAD", nombre="Rango de edad de 21 a 35 años"), "EDAD"),
    (requisito("Disponibilidad para viajar al interior del país.", eliminatorio=False,
               categoria_descarte=None, nombre="Disponibilidad para viajar al interior"), "VIAJES"),
    (requisito("Modalidad: Presencial. Santiago.", "UBICACION", nombre="Modalidad presencial en Santiago"), "UBICACION"),
    (requisito("Experiencia en la gestión operativa y financiera de servicios TI (control de P&L de contratos, facturación y márgenes).",
               eliminatorio=False, nombre="Gestión financiera P&L de contratos"), None),
])
def test_casos_reales_3993_y_808(req, esperada):
    analisis = {"estado": "ANALIZADA", "requisitos": [req]}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = modulo().motivos_descarte(analisis, restricciones)
    # invariante: confirmar no modifica estado, análisis ni restricciones.
    assert analisis == original
    assert restricciones == RESTRICCIONES
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert [m["categoria"] for m in motivos] == ([esperada] if esperada else [])
    for motivo in motivos:
        assert motivo["requisito"] == req["nombre"]
        assert motivo["texto"] in (req["nombre"], req["texto"])


@pytest.mark.parametrize("texto,categoria", [
    ("Puesto presencial para empresa de Chile", "UBICACION"),
    ("Puesto presencial en CABA para empresa de Chile", "UBICACION"),
    ("Remoto, empresa en Chile", "UBICACION"),
    ("Viajes ocasionales", "VIAJES"),
    ("Disponibilidad de viaje (10-30%)", None),
    ("De 3 a 5 años de experiencia en soporte", "EDAD"),
    ("Ubicación en Quilmes (GBA)", "UBICACION"),
    ("Empresa con sede en Córdoba, modalidad remota", "UBICACION"),
    ("Personas de 21 a 35 años", None),
    ("De 3 a 5 años de trayectoria", "EDAD"),
    ("De 3 a 5 años de antigüedad", "EDAD"),
    ("Viajes esporádicos al interior", "VIAJES"),
    ("Viajes eventuales frecuentes", None),
    ("Ubicación en Córdoba", None),
    ("Modalidad: Presencial. Santiago.", None),
    ("Busca instructores en Chile", None),
])
def test_negativos_tras_ampliar_reglas(texto, categoria):
    assert modulo().motivos_descarte({"requisitos": [requisito(texto, categoria)]}, RESTRICCIONES) == []


@pytest.mark.parametrize("nombre,texto,categoria,esperada,fuente", [
    ("Ubicación", "Ubicación en #SantaFe", "UBICACION", "UBICACION", "texto"),
    ("Ubicación", "ITC busca instructores en #EntreRíos", "UBICACION", "UBICACION", "texto"),
    ("Ubicación", "Modalidad: Presencial. Santiago.", "UBICACION", "UBICACION", "texto"),
    ("Ubicación en Córdoba", "Lugar requerido", "UBICACION", "UBICACION", "nombre"),
    ("Edad requerida", "Personas 21 a 35 años", None, "EDAD", "texto"),
    ("Personas", "Personas de 21 a 35 años", "EDAD", "EDAD", "texto"),
    ("Edad requerida", "Personas de 21 a 36 años", "EDAD", None, None),
    ("Disponibilidad", "Viajes frecuentes", None, "VIAJES", "texto"),
    ("Disponibilidad", "Viajes constantes", None, "VIAJES", "texto"),
    ("Disponibilidad", "Viajes permanentes", None, "VIAJES", "texto"),
    ("Disponibilidad", "Viajes (50 %)", None, "VIAJES", "texto"),
    ("Disponibilidad", "Viajes (75%)", None, "VIAJES", "texto"),
    ("Disponibilidad", "Viajes (49%)", None, None, None),
    ("Viajar al interior", "Disponibilidad requerida", None, "VIAJES", "nombre"),
    ("Ubicación en Córdoba", "Remoto desde Argentina", "UBICACION", None, None),
    ("Modalidad presencial", "Santiago. Puesto en CABA", "UBICACION", None, None),
])
def test_fuentes_y_limites_ampliados(nombre, texto, categoria, esperada, fuente):
    req = requisito(texto, categoria, nombre=nombre)
    motivos = modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES)
    assert motivos == ([{"requisito": nombre, "categoria": esperada, "texto": req[fuente]}] if esperada else [])


CASOS = [
    ("Presencial en Córdoba", None, "UBICACION"),
    ("Presencial en Santiago de Chile", None, "UBICACION"),
    ("Residencia en CABA o GBA", "UBICACION", None),
    ("Presencial en San Isidro", "UBICACION", None),
    ("Híbrido en Vicente López", "UBICACION", None),
    ("Presencial en San Isidro", "UBICACION", None),
    ("Presencial en Buenos Aires", "UBICACION", None),
    ("Presencial en CABA o Córdoba", "UBICACION", None),
    ("Deseable, presencial en Córdoba", "UBICACION", None),
    ("Presencial en lugar desconocido", "UBICACION", None),
    ("Remoto, empresa en Chile", "UBICACION", None),
    ("Empresa en Chile; puesto presencial en CABA", "UBICACION", None),
    ("Empresa en Chile, puesto presencial en CABA", "UBICACION", None),
    ("Presencial en CABA, empresa en Chile", "UBICACION", None),
    ("No se requiere presencial en Córdoba", "UBICACION", None),
    ("Deseable residencia en Córdoba", "UBICACION", None),
    ("Viajes ocasionales", "VIAJES", None),
    ("Viajes eventuales al interior", "VIAJES", None),
    ("Sin disponibilidad para viajar", "VIAJES", None),
    ("No es necesario viajar al interior", "VIAJES", None),
    ("Disponibilidad para viajar al interior", "VIAJES", "VIAJES"),
    ("Trabajo remoto con viajes frecuentes al interior", "VIAJES", "VIAJES"),
    ("Viajes ocasionales y reubicación obligatoria", "VIAJES", None),
    ("Viajes ocasionales y reubicación obligatoria", "REUBICACION", "REUBICACION"),
    ("Disponibilidad para reubicarse", "REUBICACION", "REUBICACION"),
    ("Reubicación opcional", "REUBICACION", None),
    ("8+ años en desarrollo", "RUBRO", None),
    ("Inglés excluyente", "RUBRO", None),
    ("Certificación AWS obligatoria", "RUBRO", None),
    ("Certificación en gastronomía", "RUBRO", None),
    ("Inglés para gastronomía", "RUBRO", None),
    ("Experiencia con Docker", "RUBRO", None),
    ("Experiencia en gastronomía", "RUBRO", "RUBRO"),
    ("Cinco años de experiencia en gastronomía", "RUBRO", "RUBRO"),
    ("Experiencia en planeamiento financiero y presupuesto", "RUBRO", "RUBRO"),
    ("Experiencia en gastronomía deseable", "RUBRO", None),
    ("Sin experiencia en gastronomía", "RUBRO", None),
    ("Edad entre 21 y 35", None, "EDAD"),
    ("Edad hasta 34", "EDAD", "EDAD"),
    ("Edad máximo 35", "EDAD", "EDAD"),
    ("Edad mínima 37 años", "EDAD", "EDAD"),
    ("Edad no mayor de 35 años", "EDAD", "EDAD"),
    ("Edad hasta 36", "EDAD", None),
    ("Edad hasta 45", "EDAD", None),
    ("8 años de experiencia", "EDAD", None),
]


@pytest.mark.parametrize("texto,categoria,esperada", CASOS)
def test_confirmaciones_y_falsos_positivos(texto, categoria, esperada):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    confirmar = modulo().motivos_descarte
    analisis = {"estado": "ANALIZADA", "requisitos": [requisito(texto, categoria)]}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = confirmar(analisis, restricciones)
    # invariante: base medida, confirmar no modifica análisis, restricciones ni estado.
    assert analisis == original
    # invariante: configuración recibida permanece intacta.
    assert restricciones == RESTRICCIONES
    assert motivos == ([{"requisito": texto, "categoria": esperada, "texto": texto}] if esperada else [])


def agente(monkeypatch, resultado=None, error=None):
    bridge = SimpleNamespace(run_prompt=AsyncMock(return_value=copy.deepcopy(resultado), side_effect=error))
    agent = strategist.StrategistAgent(bridge=bridge, expander=SimpleNamespace(get_expanded_competencies=lambda: []))
    monkeypatch.setattr(agent, "load_candidate_experience", lambda: "Perfil sintético")
    monkeypatch.setattr(strategist, "aprendizajes_para_prompt", lambda: "")
    monkeypatch.setattr(strategist, "aplicar_aprendizajes_a_requisitos", lambda tags: 0)
    if importlib.util.find_spec("core.descartes"):
        monkeypatch.setattr(strategist, "cargar_restricciones", lambda: copy.deepcopy(RESTRICCIONES))
    return agent, bridge


@pytest.mark.asyncio
async def test_estratega_inyecta_restricciones_y_confirma(monkeypatch):
    texto = "Disponibilidad para viajar al interior"
    entrada = {"estado": "ANALIZADA", "requisitos": [requisito(texto, "VIAJES")]}
    agent, bridge = agente(monkeypatch, entrada)
    result = await agent.analyze_offer(texto)
    # invariante: base actual devuelve análisis sin transición a DESCARTADO.
    assert result["estado"] == "ANALIZADA"
    # invariante: score de afinidad sigue calculado, sin techo de descarte.
    assert result["match_score"] == 0.0
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert result["no_apto"]["motivos"] == [{"requisito": texto, "categoria": "VIAJES", "texto": texto}]
    prompt = bridge.run_prompt.call_args.args[0]
    assert "viajes_frecuentes_o_interior" in prompt
    assert "2026-11-09" in prompt
    assert "categoria_descarte" in prompt and "literal" in prompt
    assert bridge.run_prompt.call_args.kwargs == {"json_mode": True, "effort": "low"}


@pytest.mark.asyncio
async def test_fallback_sin_sello(monkeypatch):
    agent, _ = agente(monkeypatch, error=RuntimeError("proveedor simulado caído"))
    result = await agent.analyze_offer("Presencial en Córdoba")
    # invariante: base medida fallback conserva bandera y no lleva sello.
    assert result["analisis_fallback"] is True
    # invariante: fallback actual no marca no_apto.
    assert "no_apto" not in result
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert modulo().motivos_descarte({**result, "no_apto": {"motivos": [1]}}, RESTRICCIONES) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("oferta", ["presencial en CABA", "No se requiere presencial en Córdoba", "Deseable: presencial en Córdoba"])
async def test_evidencia_fabricada_o_fuera_de_contexto(monkeypatch, oferta):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    agent, _ = agente(monkeypatch, {"requisitos": [requisito("presencial en Córdoba", "UBICACION")],
                                   "no_apto": {"motivos": ["heredado"]}})
    result = await agent.analyze_offer(oferta)
    assert "no_apto" not in result


def test_edad_vigente(monkeypatch):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    mod = modulo()
    class Fecha(date):
        @classmethod
        def today(cls):
            return cls(2027, 11, 8)
    monkeypatch.setattr(mod, "date", Fecha)
    analisis = {"requisitos": [requisito("Edad hasta 36", "EDAD")]}
    assert mod.motivos_descarte(analisis, RESTRICCIONES) == []
    monkeypatch.setattr(Fecha, "today", classmethod(lambda cls: cls(2027, 11, 9)))
    assert mod.motivos_descarte(analisis, RESTRICCIONES)[0]["categoria"] == "EDAD"


def test_carga_editable_y_permisos(tmp_path, monkeypatch, caplog):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    mod = modulo()
    archivo = tmp_path / "restricciones.json"
    monkeypatch.setattr(mod, "RESTRICCIONES_FILE", archivo)
    assert mod.cargar_restricciones() == {}
    for invalido in ["{", "[]", '{"reubicacion":"false"}', '{"edad":{"actual":true,"al":"2026-11-09"}}']:
        archivo.write_text(invalido, encoding="utf-8")
        assert mod.cargar_restricciones() == {}
    assert caplog.records
    for categoria, campo, texto in [
        ("VIAJES", "viajes_frecuentes_o_interior", "Viajes frecuentes al interior"),
        ("REUBICACION", "reubicacion", "Reubicación obligatoria"),
        ("RUBRO", "rubro_o_funcion_exigida_sin_experiencia", "Experiencia en gastronomía"),
    ]:
        analisis = {"requisitos": [requisito(texto, categoria)]}
        archivo.write_text(json.dumps(RESTRICCIONES), encoding="utf-8")
        assert mod.motivos_descarte(analisis, mod.cargar_restricciones())
        archivo.write_text(json.dumps({**RESTRICCIONES, campo: True}), encoding="utf-8")
        assert mod.motivos_descarte(analisis, mod.cargar_restricciones()) == []
    assert mod.motivos_descarte(analisis, {}) == []
    ampliadas = {**RESTRICCIONES, "presencial_hibrido_permitido": "AMBA o Córdoba",
                 "localidades_permitidas": RESTRICCIONES["localidades_permitidas"] + ["córdoba"]}
    assert mod.motivos_descarte({"requisitos": [requisito("Presencial en Córdoba")]}, ampliadas) == []


@pytest.mark.parametrize("extra", [{"estado": "MATCH_DIRECTO"}, {"estado": "TRANSFERIBLE"},
                                        {"tipo": "REQUISITO_DESEABLE"}, {"eliminatorio_usuario": False}])
def test_no_convertir_coincidencias_ni_decisiones_negativas(extra):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert modulo().motivos_descarte({"requisitos": [requisito("Experiencia en gastronomía", "RUBRO", **extra)]}, RESTRICCIONES) == []
    assert modulo().motivos_descarte({"requisitos": [requisito("Presencial en Córdoba", "UBICACION", **extra)]}, RESTRICCIONES) == []


@pytest.mark.asyncio
async def test_limpia_sello_heredado_y_fallback_del_proveedor(monkeypatch):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    for entrada in [
        {"requisitos": [requisito("Python")], "no_apto": {"motivos": [1]}},
        {"requisitos": [requisito("Presencial en Córdoba")], "analisis_fallback": True, "no_apto": {"motivos": [1]}},
    ]:
        agent, _ = agente(monkeypatch, entrada)
        assert "no_apto" not in await agent.analyze_offer("Presencial en Córdoba y Python")


@pytest.mark.asyncio
async def test_tope_cumplido_no_deja_brecha(monkeypatch):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    texto = "Edad hasta 36 años"
    agent, _ = agente(monkeypatch, {"requisitos": [requisito(texto, "EDAD")]})
    result = await agent.analyze_offer(texto)
    assert "no_apto" not in result
    assert result["requisitos"][0]["estado"] == "MATCH_DIRECTO"
    assert result["requisitos"][0]["eliminatorio"] is False


def test_pureza_y_duplicados():
    mod = modulo()
    analisis = {"estado": "ANALIZADA", "requisitos": [requisito("Presencial en Córdoba")] * 2}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = mod.motivos_descarte(analisis, restricciones)
    # invariante: base medida función pura no modifica análisis ni estado.
    assert analisis == original
    # invariante: base medida función pura no modifica restricciones.
    assert restricciones == RESTRICCIONES
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert len(motivos) == 1


@pytest.mark.parametrize("texto,categoria", [("Edad entre 21 y 34", "EDAD"),
                                            ("Edad entre 21 y 35", "EDAD"),
                                            ("Presencial en Córdoba", "UBICACION")])
@pytest.mark.asyncio
async def test_falso_del_modelo_no_veta_autodeteccion(monkeypatch, texto, categoria):
    req = requisito(texto, categoria, eliminatorio=False)
    original = copy.deepcopy(req)
    motivos = modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES)
    # invariante: el confirmador no modifica el requisito.
    assert req == original
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert motivos == [{"requisito": texto, "categoria": categoria, "texto": texto}]
    agent, _ = agente(monkeypatch, {"requisitos": [req]})
    result = await agent.analyze_offer(texto)
    assert result["no_apto"]["motivos"] == motivos


@pytest.mark.parametrize("decision", [False, True])
@pytest.mark.parametrize("texto,categoria", [("Presencial en Córdoba", "UBICACION"),
                                            ("Edad hasta 45", "EDAD"),
                                            ("Experiencia en gastronomía", None)])
def test_decision_del_usuario_manda(decision, texto, categoria):
    req = requisito(texto, categoria, eliminatorio=not decision, eliminatorio_usuario=decision)
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == (
        [{"requisito": texto, "categoria": categoria or "RUBRO", "texto": texto}] if decision else [])


@pytest.mark.asyncio
async def test_modelo_no_puede_inventar_decision_del_usuario(monkeypatch):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    req = requisito("Edad hasta 45", "EDAD", eliminatorio_usuario=True)
    agent, _ = agente(monkeypatch, {"requisitos": [req]})
    result = await agent.analyze_offer(req["texto"])
    assert "no_apto" not in result
    assert "eliminatorio_usuario" not in result["requisitos"][0]


@pytest.mark.parametrize("texto,categoria", [("Edad entre 21 y 35", "EDAD"),
                                            ("Presencial en Córdoba", "UBICACION")])
def test_decision_manual_infiere_categoria(texto, categoria):
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    req = requisito(texto, eliminatorio=False, eliminatorio_usuario=True)
    assert modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == [
        {"requisito": texto, "categoria": categoria, "texto": texto}]


@pytest.mark.parametrize("texto,esperada", [
    ("Edad no mayor de 45", None),
    ("Edad no mayor de 36", None),
    ("Edad hasta 36", None),
    ("Edad máxima 45 años", None),
    ("Edad no mayor de 35", "EDAD"),
    ("Edad entre 21 y 35", "EDAD"),
    ("Mayores de 40", "EDAD"),
    ("Mayor de 30", None),
    ("Edad no mayor de 45 y mínima 37", "EDAD"),
])
@pytest.mark.asyncio
async def test_tope_no_mayor_de_no_es_minimo(monkeypatch, texto, esperada):
    req = requisito(texto, eliminatorio=False)
    analisis = {"estado": "ANALIZADA", "requisitos": [req]}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = modulo().motivos_descarte(analisis, restricciones)
    # invariante: confirmar no modifica análisis, estado ni restricciones.
    assert analisis == original
    assert restricciones == RESTRICCIONES
    agent, _ = agente(monkeypatch, analisis)
    result = await agent.analyze_offer(texto)
    # invariante: el sello no cambia el estado a DESCARTADO.
    assert result["estado"] == "ANALIZADA"
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    esperados = [{"requisito": texto, "categoria": esperada, "texto": texto}] if esperada else []
    assert motivos == esperados
    assert result.get("no_apto", {}).get("motivos", []) == esperados
    if not esperada:
        assert "no_apto" not in result
        assert result["requisitos"][0]["estado"] == "MATCH_DIRECTO"
        assert result["requisitos"][0]["eliminatorio"] is False
    else:
        assert result["requisitos"][0]["estado"] == "BRECHA_REAL"


@pytest.mark.parametrize("texto,esperada", [
    ("Puesto presencial en CABA para empresa de Chile", None),
    ("Puesto presencial en Santiago de Chile", "UBICACION"),
    ("Disponibilidad presencial en Santiago de Chile", "UBICACION"),
    ("Presencial en Córdoba", "UBICACION"),
    ("Puesto presencial en CABA y Córdoba", None),
    ("Presencial: CABA, Córdoba", None),
    ("Presencial en Chile o CABA", None),
])
@pytest.mark.asyncio
async def test_sede_externa_no_descarta_puesto_local(monkeypatch, texto, esperada):
    analisis = {"estado": "ANALIZADA", "requisitos": [requisito(texto, eliminatorio=False)]}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = modulo().motivos_descarte(analisis, restricciones)
    # invariante: confirmar no modifica análisis, estado ni restricciones.
    assert analisis == original
    assert restricciones == RESTRICCIONES
    agent, _ = agente(monkeypatch, analisis)
    result = await agent.analyze_offer(texto)
    # invariante: el sello no cambia el estado a DESCARTADO.
    assert result["estado"] == "ANALIZADA"
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    esperados = [{"requisito": texto, "categoria": esperada, "texto": texto}] if esperada else []
    assert motivos == esperados
    assert result.get("no_apto", {}).get("motivos", []) == esperados
    if not esperada:
        assert "no_apto" not in result


@pytest.mark.parametrize("texto,esperada", [
    ("Puesto presencial para empresa de Chile", None),
    ("Puesto presencial en CABA para empresa de Chile", None),
    ("Empresa con sede en Córdoba, modalidad remota", None),
    ("Cliente de Chile, presencial en CABA", None),
    ("Presencial en CABA o en Córdoba", None),
    ("Remoto, empresa en Chile", None),
    ("Puesto presencial en Santiago de Chile", "UBICACION"),
    ("Disponibilidad presencial en Santiago de Chile", "UBICACION"),
    ("Presencial en Córdoba", "UBICACION"),
    ("Trabajar en oficinas en Rosario", "UBICACION"),
    ("Residir en Mendoza", "UBICACION"),
])
def test_ubicacion_solo_con_lugar_de_trabajo_explicito(texto, esperada):
    analisis = {"estado": "ANALIZADA", "requisitos": [requisito(texto, eliminatorio=False)]}
    original, restricciones = copy.deepcopy(analisis), copy.deepcopy(RESTRICCIONES)
    motivos = modulo().motivos_descarte(analisis, restricciones)
    # invariante: confirmar no modifica análisis, estado ni restricciones.
    assert analisis == original
    assert restricciones == RESTRICCIONES
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    assert motivos == ([{"requisito": texto, "categoria": esperada, "texto": texto}] if esperada else [])


@pytest.mark.parametrize("texto,esperada", [
    ("Puesto presencial para empresa de Chile", None),
    ("Trabajar en oficinas en Rosario", "UBICACION"),
])
@pytest.mark.asyncio
async def test_estratega_ubicacion_solo_con_lugar_de_trabajo_explicito(monkeypatch, texto, esperada):
    entrada = {"estado": "ANALIZADA", "requisitos": [requisito(texto, eliminatorio=False)]}
    original = copy.deepcopy(entrada)
    agent, bridge = agente(monkeypatch, entrada)
    result = await agent.analyze_offer(texto)
    # invariante: el sello no cambia el estado ni muta la respuesta del proveedor.
    assert result["estado"] == "ANALIZADA"
    assert entrada == original
    bridge.run_prompt.assert_awaited_once()
    if os.getenv("INVARIANT_CHECK") == "1":
        return
    if esperada:
        assert result["no_apto"]["motivos"] == [{"requisito": texto, "categoria": esperada, "texto": texto}]
    else:
        assert "no_apto" not in result
