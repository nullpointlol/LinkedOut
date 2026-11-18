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
