"""tools/capturas.py (Fase 4 del plan público): las capturas y el CV de ejemplo salen solo de datos inventados.

Medidas: la herramienta arma su propia carpeta de datos desde config/ejemplo, no hereda variables LINKEDOUT_ ni
GMAIL_ del entorno (apunta a un .env propio con un Gmail inventado), no usa el puerto de producción y lo que publica
(capturas y CV) existe, con PNG livianos.
"""
import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FUENTE = (RAIZ / "tools" / "capturas.py").read_text(encoding="utf-8")


def test_no_hereda_datos_ni_credenciales_del_entorno():
    assert 'not k.startswith(("LINKEDOUT_", "GMAIL_"))' in FUENTE
    assert "LINKEDOUT_ENV_FILE=str(archivo_env)" in FUENTE and "LINKEDOUT_DATA_DIR=str(carpeta)" in FUENTE
    assert 'GMAIL_ADDRESS="sam.ortega@example.com"' in FUENTE
    assert 'shutil.copytree(RAIZ / "config" / "ejemplo" / "candidato", candidato)' in FUENTE
    # Nunca la carpeta de datos real ni un .env del repo
    for prohibido in ('"data"', "'data'", 'RAIZ / ".env"', '"dev.env"', "produccion.env"):
        assert prohibido not in FUENTE, prohibido


def test_no_usa_el_puerto_de_produccion():
    assert "args.puerto == 1111" in FUENTE


def test_el_codigo_compila():
    ast.parse(FUENTE)


@pytest.mark.parametrize("nombre", ["linkedin-1280", "linkedin-lista-390", "linkedin-detalle-390", "linkedin-mail-390",
                                    "linkedin-lista-768", "linkedin-detalle-768", "linkedin-mail-768", "perfil-1280",
                                    "perfil-768", "perfil-indice-390", "perfil-indice-768", "app-1280", "app-768", "app-390"])
def test_capturas_publicadas_livianas(nombre):
    ruta = RAIZ / "docs" / "capturas" / f"{nombre}.png"
    assert ruta.is_file() and ruta.stat().st_size < 150 * 1024, nombre


def test_cv_de_ejemplo_publicado():
    assert (RAIZ / "docs" / "ejemplo" / "CV_Sam_Ortega_ejemplo.pdf").stat().st_size < 300 * 1024
