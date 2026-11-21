"""Unitarios de `core/expander.py`: lectura y escritura de competencias (con fallas de disco
simuladas), borrado y preguntas de descubrimiento (IA simulada con un doble del bridge)."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.expander import CapabilityExpanderAgent


class _RutaSinEscritura(type(Path())):
    """Ruta que se puede leer pero no escribir (disco lleno o permisos)."""

    def write_text(self, *a, **k):
        raise PermissionError("sin permiso")


def _agente(ruta, respuesta=None):
    b = AsyncMock()
    b.run_prompt.return_value = respuesta
    agente = CapabilityExpanderAgent(bridge=b, storage_path=ruta)
    agente.storage_path = ruta  # conserva la subclase de ruta de los dobles
    return agente


def test_carpeta_imposible_devuelve_lista_vacia(tmp_path):
    """Si no se puede crear el archivo (la carpeta es un archivo), la lista queda vacía."""
    (tmp_path / "bloqueo").write_text("x", encoding="utf-8")
    assert _agente(tmp_path / "bloqueo" / "comp.json").get_expanded_competencies() == []


@pytest.mark.parametrize("contenido", ["", "{\"no\": \"lista\"}", "{roto"])
def test_archivo_vacio_no_lista_o_roto_da_lista_vacia(tmp_path, contenido):
    """Un archivo vacío, con un objeto o con JSON roto se lee como lista vacía."""
    ruta = tmp_path / "comp.json"
    ruta.write_text(contenido, encoding="utf-8")
    assert _agente(ruta).get_expanded_competencies() == []


def test_confirmar_saltea_sin_nombre_y_avisa_si_no_puede_guardar(tmp_path, capsys):
    """Las competencias sin nombre se ignoran; si no se puede guardar se avisa y se devuelve la lista."""
    ruta = _RutaSinEscritura(tmp_path / "comp.json")
    res = _agente(ruta).confirm_competencies([{"nombre": "  "}, {"nombre": "Docker"}])
    assert [c["nombre"] for c in res] == ["Docker"]
    assert res[0]["categoria"] == "Infraestructura"
    assert "Error guardando competencias" in capsys.readouterr().out


def test_borrar_de_lista_vacia_o_sin_poder_guardar(tmp_path):
    """Borrar sin competencias devuelve False; si no se puede guardar el cambio, también."""
    assert _agente(tmp_path / "vacio.json").remove_competency("Docker") is False
    ruta = tmp_path / "comp.json"
    ruta.write_text(json.dumps([{"nombre": "Docker"}]), encoding="utf-8")
    assert _agente(_RutaSinEscritura(ruta)).remove_competency("docker") is False
    assert json.loads(ruta.read_text(encoding="utf-8")) == [{"nombre": "Docker"}]


@pytest.mark.asyncio
async def test_preguntas_sin_palabras_clave_no_llaman_a_la_ia(tmp_path):
    """Sin palabras clave útiles no hay preguntas ni llamada al modelo."""
    agente = _agente(tmp_path / "c.json")
    assert await agente.generate_discovery_questions("Analista", ["", "  "]) == []
    agente.bridge.run_prompt.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("respuesta", [{"items": [{"keyword": "Docker"}]}, [{"keyword": "Docker"}]])
async def test_preguntas_aceptan_items_o_lista(tmp_path, respuesta):
    """La respuesta del modelo puede venir como `items` o como lista suelta."""
    agente = _agente(tmp_path / "c.json", respuesta)
    assert await agente.generate_discovery_questions("Analista", ["Docker"]) == [{"keyword": "Docker"}]


@pytest.mark.asyncio
async def test_preguntas_deterministicas_por_categoria(tmp_path):
    """Si el modelo no sirve, cada palabra clave recibe la pregunta de su categoría."""
    agente = _agente(tmp_path / "c.json", {"nada": True})
    preguntas = await agente.generate_discovery_questions("Analista", ["Kanban", "BPMN", "Liderazgo"])
    categorias = [p["categoria_sugerida"] for p in preguntas]
    assert categorias == ["Metodologías", "Procesos", "Habilidades Blandas"]
    assert preguntas[0]["pregunta"].startswith("¿Has participado en ceremonias")
    assert preguntas[1]["sugerencia_evidencia"] == "Documentación y modelado técnico de BPMN"
