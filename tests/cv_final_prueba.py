"""PDF de prueba para los dobles de `cv_maestro.FlujoCV`: el pipeline guarda el PDF auditado como CV final
(`core/cv_final.py`), así que el resultado de un flujo falso también trae su archivo."""
import tempfile
from pathlib import Path


def pdf_de_prueba(nombre: str = "cv_maestro_prueba.pdf", contenido: bytes = b"%PDF-1.4\n% CV de prueba\n") -> Path:
    ruta = Path(tempfile.mkdtemp(prefix="cv_final_")) / nombre
    ruta.write_bytes(contenido)
    return ruta


def puntajes_falsos(prompt: str, valor: float = 7) -> dict:
    """Respuesta de la etapa de selección con IA (core/cv_maestro.pedido_seleccion): el mismo puntaje para cada id."""
    import re
    ids = re.findall(r"^([EH]\d+):", prompt, re.M)
    return {"vinetas": {i: valor for i in ids if i[0] == "E"}, "habilidades": {i: valor for i in ids if i[0] == "H"}}


def es_seleccion(prompt: str) -> bool:
    return "Puntuá cuánto demuestra cada línea del CV" in prompt


CV_DE_PRUEBA = "# Sam Ortega\nANALISTA FUNCIONAL\nsam.ortega@example.com\n\n## PERFIL\nAnalista con experiencia en procesos.\n"


class FlujoCVSimulado:
    """Doble de `cv_maestro.FlujoCV` para los tests del pipeline (pasos, estado, eventos), que no prueban la IA del
    CV: reporta el paso «auditar» como el flujo real y devuelve un CV aprobado, sin llamar a agy (Fase 2, 18/11: antes
    estos tests llamaban al agy real de la PC y en el CI de Linux fallaban)."""

    def __init__(self, **kw):
        self.kw = kw
        self.segundos = {"total": 0.1}
        self.historial = []

    async def generar(self):
        from types import SimpleNamespace
        from core import progreso
        await progreso.reportar("auditar")
        # El encabezado sale del perfil que recibe, como en el flujo real (nombre en mayúsculas)
        lineas = [l.strip() for l in str(self.kw.get("personales") or "").splitlines() if l.strip()]
        nombre = lineas[0].lstrip("# ").upper() if lineas else "SAM ORTEGA"
        markdown = f"# {nombre}\n" + CV_DE_PRUEBA.split("\n", 1)[1]
        return SimpleNamespace(markdown=markdown, correcciones=0, reporte={"approved": True, "ats_score": 97.0},
                               keywords_cv={}, segundos=self.segundos, pdf=pdf_de_prueba())


def simular_cv_maestro(monkeypatch, server) -> None:
    """El pipeline del CV sin IA: flujo simulado, años por área vacíos y el título que decide el código."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    monkeypatch.setattr(server.cv_maestro, "FlujoCV", FlujoCVSimulado)
    monkeypatch.setattr(server, "asegurar_anios_por_area", AsyncMock(return_value=[]))
    monkeypatch.setattr(server, "decidir_titulo", AsyncMock(return_value=SimpleNamespace(titulo="Analista Funcional")))
