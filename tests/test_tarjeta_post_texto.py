"""La tarjeta del Radar nunca muestra el bloque «Post» vacío (creador, 08/11).

Si el detalle completo no cargó se ve el extracto con un aviso y «Reintentar»; si el post de verdad
no tiene texto, «Sin texto capturado».
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RADAR_JS = Path(__file__).resolve().parent.parent / "static" / "radar.js"
JS = RADAR_JS.read_text(encoding="utf-8")


def _funcion(nombre):
    ini = JS.index(f"function {nombre}(")
    fin = JS.index("\n  }\n", ini)
    return JS[ini:fin]


def test_el_bloque_post_usa_el_texto_con_respaldo():
    assert "bloque('Post', htmlTextoPost(p))" in JS
    assert "escapeHtml(p.texto || '')" not in JS
    f = _funcion("htmlTextoPost")
    assert "Sin texto capturado" in f
    assert "No se pudo cargar el texto completo" in f
    assert "Cargando el texto completo" in f
    assert "p.extracto" in f and "escapeHtml(t)" in f


def test_falla_del_detalle_habilita_reintentar():
    carga = _funcion("cargarDetalle")
    assert re.search(r"S\.detalleFallido = id;", carga)
    assert "renderDetalle()" in carga
    assert "S.detalleFallido = null" in carga
    detalle = _funcion("renderDetalle")
    assert "radarPostReintentar" in detalle and "cargarDetalle(p.id, true)" in detalle and "S.detalleFallido === p.id" in detalle


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_html_texto_post_en_los_tres_casos():
    f = _funcion("htmlTextoPost")
    script = (
        "const S = { detalleFallido: 7 };\n"
        "const escapeHtml = (t) => String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;');\n"
        + f + "\n}\n"
        "const r = [htmlTextoPost({ id: 1, texto: 'Buscamos QA <b>' }),\n"
        "           htmlTextoPost({ id: 2, texto: '' }),\n"
        "           htmlTextoPost({ id: 7, extracto: 'Analista funcional' }),\n"
        "           htmlTextoPost({ id: 8, extracto: 'Analista funcional' })];\n"
        "console.log(JSON.stringify(r));\n"
    )
    salida = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    completo, vacio, fallido, cargando = __import__("json").loads(salida)
    assert "Buscamos QA &lt;b>" in completo
    assert "Sin texto capturado" in vacio
    assert "Analista funcional…" in fallido and "radarPostReintentar" in fallido
    assert "Analista funcional…" in cargando and "Cargando el texto completo" in cargando
    assert "radarPostReintentar" not in cargando
