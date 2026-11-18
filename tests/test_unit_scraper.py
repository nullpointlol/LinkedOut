"""Unitarios de `core/scraper.py`: extractor HTML, JSON-LD, identificadores de LinkedIn, elección del
post, imágenes y descarga (con `urllib` simulado: nunca sale a la red)."""
import pytest

from core import scraper
from core.scraper import (
    HTMLContentExtractor,
    _clean_company,
    _coincide_post_con_url,
    _extract_json_ld,
    _extraer_imagenes_de_html,
    _normalizar_identificadores_url,
    _seleccionar_post,
    clean_html_text,
    extract_job_from_html,
    filter_job_boilerplate,
)

DESCRIPCION = "Buscamos Analista Funcional para relevar procesos, escribir historias de usuario y coordinar con QA."


def test_textos_vacios_devuelven_vacio():
    """Limpieza y filtrado de texto vacío devuelven cadena vacía."""
    assert filter_job_boilerplate("") == ""
    assert clean_html_text("") == ""


def test_br_genera_salto_de_linea():
    """Un `<br>` separa líneas en el texto extraído."""
    parser = HTMLContentExtractor()
    parser.feed("uno<br>dos")
    assert "".join(parser.text_parts) == "uno\ndos"


def test_cierre_de_ruido_con_etiquetas_anidadas_sin_cerrar():
    """Al cerrar un contenedor de ruido se descartan las etiquetas internas que quedaron abiertas."""
    texto = clean_html_text("<nav><div><span>Menú</nav><p>Contenido real del aviso</p>")
    assert "Menú" not in texto
    assert "Contenido real del aviso" in texto


def test_empresa_que_es_url_o_dominio_se_descarta():
    """Un dominio o URL no es nombre de empresa."""
    for empresa in ["https://www.empleosit.com.ar", "empleosit.com.ar/ofertas"]:
        assert _clean_company(empresa) == ""


def test_json_ld_lista_grafo_e_invalidos():
    """Se encuentra el JobPosting en una lista o en `@graph`, salteando JSON roto e ítems no dict."""
    roto = '<script type="application/ld+json">{roto</script>'
    lista = '<script type="application/ld+json">["texto", {"@type": "JobPosting", "title": "A"}]</script>'
    grafo = '<script type="application/ld+json">{"@graph": [{"@type": ["JobPosting"], "title": "B"}]}</script>'
    assert _extract_json_ld(roto + lista) == {"@type": "JobPosting", "title": "A"}
    assert _extract_json_ld(grafo)["title"] == "B"
    assert _extract_json_ld(roto) is None


def test_normalizar_identificadores():
    """Cada forma de identificar un post de LinkedIn da su urn y su número."""
    for url, esperado in [
        ("", (None, None)),
        ("https://www.linkedin.com/feed/?shareId=777", ("urn:li:share:777", "777")),
        ("https://www.linkedin.com/x?ugcPostId=888", ("urn:li:ugcpost:888", "888")),
        ("https://www.linkedin.com/feed/update/999/", ("urn:li:activity:999", "999")),
        ("12345", (None, "12345")),
        ("https://acme.example/sin-id", (None, None)),
    ]:
        assert _normalizar_identificadores_url(url) == esperado


def test_coincidencia_de_post_por_urn_normalizado_y_por_post_url():
    """Coincide por urn normalizado del post o por el urn del link del post; sin URL nunca."""
    post_urn = {"urn": "urn:li:activity:555?x=1", "post_url": ""}
    assert _coincide_post_con_url(post_urn, "x", "urn:li:activity:555", "555") is True
    post_link = {"urn": "", "post_url": "https://www.linkedin.com/posts/acme-activity-555-abc"}
    assert _coincide_post_con_url(post_link, "https://otra", "urn:li:activity:555", "555") is True
    assert _coincide_post_con_url(post_link, "", "urn:li:activity:555", "555") is False


def test_coincidencia_por_numero_del_link_sin_tipo():
    """Si la URL objetivo es solo un número, alcanza con que coincida el número del link del post."""
    post = {"urn": "", "post_url": "https://www.linkedin.com/feed/update/555"}
    assert _coincide_post_con_url(post, "555", None, "555") is True


def test_un_solo_post_sin_identificador_en_la_url_se_elige():
    """Con un único post y una URL sin identificadores, ese post es el elegido."""
    post = {"urn": "urn:li:activity:1", "post_url": ""}
    assert _seleccionar_post([post], "https://acme.example/aviso") is post


def test_imagenes_sin_html_y_ante_errores_del_parser(monkeypatch):
    """Sin HTML no hay imágenes; si los dos parsers fallan, tampoco (sin propagar el error)."""
    assert _extraer_imagenes_de_html("") == []
    import core.linkedin_parser as lp

    def roto(*a, **k):
        raise RuntimeError("parser roto")

    monkeypatch.setattr(lp, "parse_posts_html", roto)
    monkeypatch.setattr(lp, "_extraer_imagenes_de_contenedor", roto)
    assert _extraer_imagenes_de_html("<html><img src='x'></html>") == []


def test_json_ld_con_organizacion_como_texto():
    """`hiringOrganization` como texto también da la empresa."""
    html = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Analista Funcional",'
            f' "hiringOrganization": "Acme", "description": "{DESCRIPCION}"}}</script>')
    res = extract_job_from_html(html, url="https://acme.example/aviso")
    assert (res["titulo"], res["empresa"]) == ("Analista Funcional", "Acme")
    assert res["contenido"].startswith("Buscamos Analista Funcional")


def test_empresa_desde_el_titulo_de_la_pagina_y_descripcion_og():
    """Sin og:site_name, la empresa sale del segundo tramo del <title>; con cuerpo corto se usa og:description."""
    html = ("<html><head><title>Analista Funcional - Acme</title>"
            f'<meta property="og:description" content="{DESCRIPCION}"></head><body>Corto</body></html>')
    res = extract_job_from_html(html, url="https://acme.example/aviso")
    assert res["empresa"] == "Acme"
    assert res["contenido"] == f"Analista Funcional\n\n{DESCRIPCION}"


@pytest.mark.asyncio
async def test_scrape_agrega_https_a_la_url_sin_esquema(monkeypatch):
    """Una URL sin esquema se pide con https://."""
    pedidas = []

    def fetch(url, timeout):
        pedidas.append(url)
        return f"<html><head><title>Analista - Acme</title></head><body><p>{DESCRIPCION}</p></body></html>"

    monkeypatch.setattr(scraper, "_fetch_url_sync", fetch)
    res = await scraper.scrape_job_url("acme.example/aviso")
    assert pedidas == ["https://acme.example/aviso"]
    assert res["url"] == "https://acme.example/aviso"
    assert res["empresa"] == "Acme"


def test_clean_title_no_se_cuelga_con_parentesis_largos():
    # 17/11: «\((?:[^()]+|...)*\)» se colgaba (backtracking catastrófico, 100 % de CPU con el GIL tomado) con un
    # paréntesis largo seguido de texto: trabó el portal entero dos veces. Corre aparte para que un cuelgue no frene la suite.
    import subprocess
    import sys
    codigo = ("from core.scraper import _clean_title as c; "
              "print(c('Analista (' + 'ab ' * 40 + ') remoto'), '|', c('Analista de datos (CABA)'))")
    r = subprocess.run([sys.executable, "-c", codigo], cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("| Analista de datos")
