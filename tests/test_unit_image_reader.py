"""Unitarios de `core/image_reader.py`: tipo de imagen por firma, URLs no permitidas y lectura con
descarga y OCR simulados (nunca sale a la red ni corre el OCR real ni la IA)."""
import pytest

from core import image_reader
from core.image_reader import _descargar_imagen, _extension, leer_imagenes

URL = "https://media.licdn.com/dms/image/v2/feedshare-shrink_800/abc"


def test_extension_por_firma():
    """La extensión sale de los primeros bytes; lo desconocido es «.bin»."""
    for contenido, ext in [
        (b"\x89PNG\r\n\x1a\n...", ".png"),
        (b"\xff\xd8\xff\xe0...", ".jpg"),
        (b"GIF89a...", ".gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ".webp"),
        (b"II\x2a\x00...", ".tiff"),
        (b"BM...", ".bmp"),
        (b"RIFF\x00\x00\x00\x00AVI ", ".bin"),
        (b"otra cosa", ".bin"),
    ]:
        assert _extension(contenido) == ext


def test_descarga_rechaza_urls_fuera_de_feedshare():
    """Solo se descargan imágenes de feedshare de media.licdn.com."""
    with pytest.raises(ValueError, match="no permitida"):
        _descargar_imagen("https://acme.example/imagen.png")


@pytest.mark.asyncio
async def test_lectura_saltea_no_textos_vacios_y_demasiado_grandes(monkeypatch):
    """Ítems que no son texto se ignoran; descargas vacías o de más de 5 MiB no se usan: sin imágenes
    útiles el resultado es vacío y no se llama al OCR."""
    contenidos = {URL: b"", URL + "2": b"x" * (image_reader._MAX_BYTES + 1)}
    monkeypatch.setattr(image_reader, "_descargar_imagen", lambda url: contenidos[url])

    def ocr(contenido):
        raise AssertionError("no debería leer")

    monkeypatch.setattr(image_reader, "_leer_ocr", ocr)
    assert await leer_imagenes([None, 7, URL, URL + "2"]) == ""


@pytest.mark.asyncio
async def test_lectura_con_ocr_suficiente_no_usa_la_ia(monkeypatch):
    """Si el OCR lee texto suficiente de cada imagen, ese texto es el resultado."""
    monkeypatch.setattr(image_reader, "_descargar_imagen", lambda url: b"\x89PNG\r\n\x1a\nfoto")
    texto = "Buscamos Analista Funcional con experiencia en SQL y Jira para Acme"
    monkeypatch.setattr(image_reader, "_leer_ocr", lambda contenido: texto)

    class SinIA:
        async def run_prompt(self, *a, **k):
            raise AssertionError("no debería llamar a la IA")

    assert await leer_imagenes([URL], bridge=SinIA()) == texto
