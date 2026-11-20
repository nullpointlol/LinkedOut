"""los años que fija por área mandan sobre la estimación de la IA."""
import json

import pytest

from core import perfil_areas
from tests.test_perfil_areas import bridge, perfil  # noqa: F401


def fijar(base, areas):
    (base / perfil_areas.ARCHIVO_CREADOR).write_text(json.dumps({"anios_por_area": areas}), encoding="utf-8")


@pytest.mark.asyncio
async def test_lo_fijado_reemplaza_y_suma_areas(perfil):  # noqa: F811
    fijar(perfil, [{"area": "Soporte", "anios": "6"}, {"area": "análisis funcional", "anios": "5"},
                   {"area": "Experiencia laboral total", "anios": "1"}])
    areas = await perfil_areas.asegurar_anios_por_area(bridge(), perfil)
    assert {a["area"]: a["anios"] for a in areas} == {
        "Experiencia laboral total": "7", "Soporte": "6", "análisis funcional": "5"}
    # El caché guarda la estimación de la IA; lo fijado se aplica al leer
    assert perfil_areas.anios_por_area_en_cache(perfil) == areas
    guardado = json.loads((perfil / "anios_por_area.json").read_text(encoding="utf-8"))
    assert {a["area"]: a["anios"] for a in guardado["anios_por_area"]}["soporte"] == "más de 3"


@pytest.mark.asyncio
async def test_lo_fijado_no_supera_el_total_ni_rompe_con_archivo_invalido(perfil):  # noqa: F811
    fijar(perfil, [{"area": "soporte", "anios": "30"}])
    areas = await perfil_areas.asegurar_anios_por_area(bridge(), perfil)
    assert {a["area"]: a["anios"] for a in areas}["soporte"] == "más de 3"
    (perfil / perfil_areas.ARCHIVO_CREADOR).write_text("{", encoding="utf-8")
    assert perfil_areas.anios_por_area_en_cache(perfil) == areas


def test_archivo_de_ejemplo_tiene_el_formato_esperado():
    """Los años fijados de cada candidato viven en <DATA_DIR>/candidato (sin versionar); se versiona un ejemplo."""
    from pathlib import Path
    ruta = Path(__file__).resolve().parent.parent / "config" / "ejemplo" / "candidato" / "anios_por_area_creador.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["anios_por_area"] and all(
        isinstance(a["area"], str) and a["anios"].isdigit() and isinstance(a.get("reemplaza", []), list)
        for a in datos["anios_por_area"])


@pytest.mark.asyncio
async def test_reemplaza_la_misma_area_con_otro_nombre(perfil):  # noqa: F811
    (perfil / perfil_areas.ARCHIVO_CREADOR).write_text(json.dumps({"anios_por_area": [
        {"area": "soporte e incidentes", "anios": "6", "reemplaza": ["soporte", "incidentes"]}]}), encoding="utf-8")
    ia = [{"area": "soporte", "anios": "más de 3"}, {"area": "gestión de incidentes", "anios": "2"},
          {"area": "monitoreo", "anios": "1"}]
    areas = await perfil_areas.asegurar_anios_por_area(bridge(ia), perfil)
    assert {a["area"]: a["anios"] for a in areas} == {
        "Experiencia laboral total": "7", "soporte e incidentes": "6", "monitoreo": "1"}
