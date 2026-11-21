"""Aceptación determinista. No usa datos reales ni proveedores."""
from copy import deepcopy
import importlib
import os
import json
import pytest

RESTRICCIONES = {
    "base": "San Isidro, AMBA", "presencial_hibrido_permitido": "AMBA (CABA y GBA)", "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro", "san fernando", "tigre", "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero", "san miguel", "jose c paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo", "berazategui", "florencio varela", "ezeiza", "esteban echeverria", "almirante brown"],
    "remoto_desde": "Argentina", "viajes_frecuentes_o_interior": False,
    "reubicacion": False, "rubro_o_funcion_exigida_sin_experiencia": False,
    "edad": {"actual": 36, "al": "2026-11-09"},
}

def modulo():
    # Importar dentro de cada test: falta de feature es fallo, no error de colección.
    import importlib.util
    assert importlib.util.find_spec("core.descartes"), "Falta confirmación determinista de descartes"
    return importlib.import_module("core.descartes")

def requisito(texto, categoria, marcado=True, estado="BRECHA_REAL", tipo="REQUISITO_EXCLUYENTE"):
    return {"nombre": texto, "texto": texto, "estado": estado, "tipo": tipo,
            "eliminatorio": marcado, "categoria_descarte": categoria, "approved": False}


@pytest.mark.parametrize("marcado", [False, True])
@pytest.mark.parametrize("nombre,texto,esperada", [
    ("Híbrido en Córdoba", "Híbrido en Córdoba", None),
    ("Modalidad híbrida en Córdoba", "Modalidad híbrida en Córdoba", None),
    ("Modalidad híbrida en Rosario (4 días presenciales)", "Modalidad híbrida en Rosario (4 días presenciales)", None),
    ("Presencialidad 4 días en Neuquén", "Presencialidad 4 días en Neuquén", None),
    ("Presencial o híbrido en Mendoza", "Presencial o híbrido en Mendoza", None),
    ("Híbrido en Córdoba, con residencia en Córdoba", "Híbrido en Córdoba, con residencia en Córdoba", "UBICACION"),
    ("Modalidad híbrida. Excluyente vivir en Rosario", "Modalidad híbrida. Excluyente vivir en Rosario", "UBICACION"),
    ("Híbrido en Córdoba; radicado en Córdoba", "Híbrido en Córdoba; radicado en Córdoba", "UBICACION"),
    ("Presencial en Córdoba", "Presencial en Córdoba", "UBICACION"),
    ("100% presencial en Rosario", "100% presencial en Rosario", "UBICACION"),
    ("Modalidad presencial en Santiago", "Modalidad: Presencial. Santiago.", "UBICACION"),
    ("Modalidad híbrida", "Presencial en Córdoba", None),
    ("Presencial en Córdoba", "Modalidad híbrida", None),
    ("Híbrido en Córdoba", "Híbrido en Córdoba; residencia deseable en Córdoba", None),
    ("Híbrido en Córdoba", "Híbrido en Córdoba; no requiere residir en Córdoba", None),
    ("Híbrido en Córdoba", "Híbrido en Córdoba; residencia en lugar desconocido", None),
    ("Híbrido en Córdoba", "Híbrido en Córdoba; empresa con residencia en Córdoba", None),
    ("Puesto presencial para empresa de Chile", "Puesto presencial para empresa de Chile", None),
    ("Puesto presencial en CABA para empresa de Chile", "Puesto presencial en CABA para empresa de Chile", None),
    ("Remoto, empresa en Chile", "Remoto, empresa en Chile", None),
    ("Híbrido en San Isidro, GBA", "Híbrido en San Isidro, GBA", None),
    ("Empresa con sede en Córdoba, modalidad remota", "Empresa con sede en Córdoba, modalidad remota", None),
])
def test_hibridos_y_presenciales_por_requisito(nombre, texto, esperada, marcado):
    req = dict(requisito(texto, "UBICACION", marcado), nombre=nombre)
    analisis = {"estado": "ANALIZADA", "requisitos": [req]}
    original = deepcopy(analisis)
    motivos = modulo().motivos_descarte(analisis, RESTRICCIONES)
    # invariante: la confirmación no cambia el análisis ni su estado.
    assert analisis == original
    if os.getenv("INVARIANT_CHECK") == "1": return
    assert [m["categoria"] for m in motivos] == ([esperada] if esperada else [])


def test_manual_hibrido_sin_categoria_conserva_autoridad():
    req = requisito("Híbrido en Córdoba", None, False)
    req.pop("categoria_descarte")
    req["eliminatorio_usuario"] = True
    assert modulo().motivos_descarte({"requisitos": [req]}, RESTRICCIONES) == [
        {"requisito": "Híbrido en Córdoba", "categoria": "UBICACION", "texto": "Híbrido en Córdoba"}]

@pytest.mark.parametrize("texto,categoria,marcado", [
    ("Experiencia exigida en rubro gastronómico", "RUBRO", True),
    ("Experiencia exigida en planeamiento financiero y presupuesto", "RUBRO", True),
    ("Disponibilidad para viajar al interior", "VIAJES", True),
    ("Presencial en Santiago de Chile", "UBICACION", True),
    ("Presencial en Córdoba", "UBICACION", False),
    ("Edad entre 21 y 34", "EDAD", False),
    ("Edad entre 21 y 35", "EDAD", False),
    ("Edad máxima 30 años", "EDAD", True),
    ("Reubicación obligatoria a Córdoba", "REUBICACION", True),
])
def test_confirma_evidencia_y_no_muta(texto, categoria, marcado):
    a = {"requisitos": [requisito(texto, categoria, marcado)]}
    original, restricciones = deepcopy(a), deepcopy(RESTRICCIONES)
    # invariante: entrada de análisis independiente del estado; hoy no existe transición automática.
    assert "estado" not in a and "no_apto" not in a
    if os.getenv("INVARIANT_CHECK") == "1": return
    motivos = modulo().motivos_descarte(a, restricciones)
    assert a == original and restricciones == RESTRICCIONES
    assert motivos == [{"requisito": texto, "categoria": categoria, "texto": texto}]

@pytest.mark.parametrize("texto,categoria,estado,tipo", [
    ("Residencia en CABA o GBA", "UBICACION", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Híbrido en San Isidro, GBA", "UBICACION", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Presencial en Vicente López", "UBICACION", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Remoto, empresa en Chile", "UBICACION", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Viajes ocasionales", "VIAJES", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Viajes eventuales a Córdoba", "VIAJES", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("No requiere viajar ni reubicarse", "VIAJES", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Edad hasta 45", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Edad hasta 40", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Edad hasta 44", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Edad hasta 36", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Buena presencia", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("8+ años en desarrollo", "EDAD", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("8+ años en desarrollo", "RUBRO", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Inglés avanzado", "RUBRO", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Certificación AWS", "RUBRO", "BRECHA_REAL", "REQUISITO_EXCLUYENTE"),
    ("Experiencia gastronómica", "RUBRO", "TRANSFERIBLE", "REQUISITO_EXCLUYENTE"),
    ("Experiencia gastronómica", "RUBRO", "MATCH_DIRECTO", "REQUISITO_EXCLUYENTE"),
    ("Experiencia gastronómica deseable", "RUBRO", "BRECHA_REAL", "REQUISITO_DESEABLE"),
])
def test_no_confirma_falsos_positivos(texto, categoria, estado, tipo):
    a = {"requisitos": [requisito(texto, categoria, estado=estado, tipo=tipo)]}
    assert modulo().motivos_descarte(a, RESTRICCIONES) == []

def test_usa_cita_original_y_no_justificacion_inventada():
    a = {"requisitos": [dict(requisito("Lugar externo", "UBICACION"),
         texto="Remoto desde Argentina", justificacion="No vive en Santiago de Chile") ]}
    assert modulo().motivos_descarte(a, RESTRICCIONES) == []

def test_fallback_e_invalidas_nunca_descartan():
    m = modulo()
    a = {"analisis_fallback": True, "requisitos": [requisito("Híbrido en Córdoba", "UBICACION")]}
    assert m.motivos_descarte(a, RESTRICCIONES) == []
    a.pop("analisis_fallback")
    assert m.motivos_descarte(a, {}) == []

@pytest.mark.asyncio
async def test_estratega_aplica_restricciones_y_limpia_sello_falso(tmp_path, monkeypatch):
    from core import strategist
    m = modulo()
    f = tmp_path / "restricciones.json"
    f.write_text(json.dumps(RESTRICCIONES), encoding="utf-8")
    monkeypatch.setattr(m, "RESTRICCIONES_FILE", f)
    import core.aprendizajes as ap
    monkeypatch.setattr(ap, "APRENDIZAJES_FILE", tmp_path / "aprendizajes.json")
    llamadas = []
    class Bridge:
        effort = "low"
        async def run_prompt(self, prompt, **kw):
            llamadas.append(prompt)
            return {"puesto": "Analista", "requisitos": [requisito("Presencial en Córdoba", "UBICACION", False)]}
    class Expander:
        def get_expanded_competencies(self): return []
    agente = strategist.StrategistAgent(bridge=Bridge(), expander=Expander())
    monkeypatch.setattr(agente, "load_candidate_experience", lambda: "Experiencia sintética en soporte IT")
    r = await agente.analyze_offer("Presencial en Córdoba")
    assert "San Isidro" in llamadas[0] and "eliminatorio" in llamadas[0]
    assert r["no_apto"]["motivos"][0]["categoria"] == "UBICACION"
    async def respuesta_remota(*args, **kwargs):
        return {"puesto": "Analista", "no_apto": {"motivos": [{"texto": "inventado"}]},
                "requisitos": [requisito("Remoto, empresa en Chile", "UBICACION")]}
    monkeypatch.setattr(agente.bridge, "run_prompt", respuesta_remota)
    assert "no_apto" not in await agente.analyze_offer("Remoto, empresa en Chile")

@pytest.mark.asyncio
async def test_fallback_del_estratega_sin_sello(monkeypatch):
    from core import strategist
    class Bridge:
        effort = "low"
        async def run_prompt(self, *args, **kwargs): raise RuntimeError("proveedor simulado")
    class Expander:
        def get_expanded_competencies(self): return []
    agente = strategist.StrategistAgent(bridge=Bridge(), expander=Expander())
    monkeypatch.setattr(agente, "load_candidate_experience", lambda: "Soporte IT")
    # Sin IA no hay análisis: ni un sello de descarte determinista
    with pytest.raises(strategist.AnalisisSinIA):
        await agente.analyze_offer("Híbrido en Córdoba")
