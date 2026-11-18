"""Librerías del navegador copiadas en el repo (M1/M2 de docs/seguridad/auditoria-20261117.md, decisión del creador 17/11).

Reemplazan a los CDN: versión fija y sin depender de un tercero. Cada archivo coincide con el SHA-256 que figura en
`static/vendor/VERSIONES.json` (copiado del tarball de npm): un cambio a mano o una suba sin registrar falla.
"""
import hashlib
import json
import re
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "static" / "vendor"
MANIFIESTO = json.loads((VENDOR / "VERSIONES.json").read_text(encoding="utf-8"))["paquetes"]
NO_SON_LIBRERIAS = {"VERSIONES.json", ".gitattributes"}


def test_cada_archivo_coincide_con_su_sha256():
    for carpeta, datos in MANIFIESTO.items():
        for relativo, sha in datos["archivos"].items():
            contenido = (VENDOR / carpeta / relativo).read_bytes()
            assert hashlib.sha256(contenido).hexdigest() == sha, f"{carpeta}/{relativo} cambió"


def test_no_hay_archivos_sin_registrar():
    registrados = {f"{c}/{r}" for c, d in MANIFIESTO.items() for r in d["archivos"]}
    presentes = {str(f.relative_to(VENDOR)).replace("\\", "/") for f in VENDOR.rglob("*")
                 if f.is_file() and f.name not in NO_SON_LIBRERIAS}
    assert presentes == registrados


def test_carpeta_con_la_version_exacta_del_paquete_y_su_licencia():
    for carpeta, datos in MANIFIESTO.items():
        version = datos["paquete"].rsplit("@", 1)[1]
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), datos["paquete"]
        assert carpeta.endswith("-" + version)
        assert any(r.startswith("LICENSE") for r in datos["archivos"]), f"{carpeta} sin licencia"
        assert datos["integrity_npm"].startswith("sha512-")


def test_font_awesome_trae_las_fuentes_que_pide_su_css():
    css = (VENDOR / "fontawesome-free-6.4.0" / "css" / "all.min.css").read_text(encoding="utf-8")
    pedidas = set(re.findall(r"\.\./(webfonts/[\w.-]+)", css))
    assert pedidas and pedidas <= set(MANIFIESTO["fontawesome-free-6.4.0"]["archivos"])


def test_los_binarios_no_pasan_por_la_conversion_de_fines_de_linea():
    assert "* -text" in (VENDOR / ".gitattributes").read_text(encoding="utf-8")
