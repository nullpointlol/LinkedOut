"""CV final de una postulación (creador, 15/11): el mismo archivo que aprobó la auditoría es el que se descarga, se
adjunta y se envía.

Al aprobarse el CV desde el maestro, el PDF auditado se copia a `exports/finales/<id>/` con el nombre formal
(«CV - <Nombre> - <ROL>.pdf») y en la postulación quedan tres datos: la ruta relativa a `exports`, la huella
SHA-256 del archivo y la del texto del CV con el que se armó. Antes de servirlo o enviarlo, `verificar` compara las
dos huellas: si el archivo falta, no coincide o el texto del CV cambió después de auditarlo, levanta
`CVFinalInvalido` (no se envía y queda un ticket).

Un CV sin huella es del flujo anterior al maestro: `verificar` devuelve None y quien llama lo compila como antes.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

CARPETA = "finales"
CAMPOS = ("cv_pdf", "cv_pdf_sha256", "cv_md_sha256")


class CVFinalInvalido(Exception):
    """El CV final de la postulación no es el que aprobó la auditoría."""


def huella_archivo(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def huella_texto(texto: str) -> str:
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()


def _exports() -> Path:
    from core import pdf_generator
    return Path(pdf_generator.EXPORTS_DIR)


def guardar(postulacion_id: int, pdf: Path, markdown: str, nombre: str) -> Dict[str, str]:
    """Copia el PDF auditado como CV final y devuelve los campos para `update_postulacion` (van junto con el
    `cv_markdown` del que salió, en la misma escritura)."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise CVFinalInvalido(f"No existe el PDF auditado: {pdf}")
    carpeta = _exports() / CARPETA / str(postulacion_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("*.pdf"):
        viejo.unlink()
    destino = carpeta / Path(nombre).name
    shutil.copyfile(pdf, destino)
    return {"cv_pdf": destino.relative_to(_exports()).as_posix(), "cv_pdf_sha256": huella_archivo(destino),
            "cv_md_sha256": huella_texto(markdown)}


def tiene_final(postulacion: Optional[Dict[str, Any]]) -> bool:
    return any((postulacion or {}).get(k) for k in CAMPOS)


def verificar(postulacion: Dict[str, Any]) -> Optional[Path]:
    """El archivo del CV final, comprobado contra sus huellas; None si la postulación no tiene CV final."""
    if not tiene_final(postulacion):
        return None
    ruta, huella, huella_md = (postulacion.get(k) or "" for k in CAMPOS)
    if not (ruta and huella and huella_md):
        raise CVFinalInvalido("Faltan datos del CV final (ruta o huellas)")
    if huella_texto(postulacion.get("cv_markdown") or "") != huella_md:
        raise CVFinalInvalido("El texto del CV cambió después de auditarlo")
    archivo = _exports() / ruta
    if not archivo.is_file():
        raise CVFinalInvalido(f"Falta el archivo del CV final ({ruta})")
    if huella_archivo(archivo) != huella:
        raise CVFinalInvalido(f"El archivo del CV final no coincide con el auditado ({ruta})")
    return archivo


def campos_vacios() -> Dict[str, str]:
    """Para borrar el CV final de una postulación que se vuelve a generar."""
    return {k: "" for k in CAMPOS}
