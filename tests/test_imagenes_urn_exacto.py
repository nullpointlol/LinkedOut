"""Tests de regresión para comparación exacta de identificadores URN/URL en scraper."""
import pytest
from core.scraper import (
    _coincide_post_con_url,
    _normalizar_identificadores_url,
    extract_job_from_html,
)


@pytest.mark.parametrize("atributo", ["componentkey", "id"])
@pytest.mark.parametrize("identificador,tipo", [("shareId", "share"), ("ugcPostId", "ugcPost")])
def test_permalink_grafico_sin_caja_textual(atributo, identificador, tipo):
    from core.linkedin_parser import parse_posts_html

    numero = "7100000000000000001"
    url = f"https://www.linkedin.com/feed/update/urn:li:{tipo}:{numero}/"
    imagen = "https://media.licdn.com/feedshare/objetivo.jpg"
    html = f'''<div role="listitem" componentkey="update-card-focusgraficoFeedType_test">
        <section {atributo}="traducible {identificador}={numero}"><img src="{imagen}"></section>
    </div>
    <div role="listitem" componentkey="update-card-focusajenoFeedType_test">
        <img src="https://media.licdn.com/feedshare/ajena.jpg">
    </div>'''
    post = parse_posts_html(html)[0]
    assert post["texto"] == ""
    assert post["post_url"] == url
    oferta = extract_job_from_html(html, url=url)
    assert oferta["imagenes"] == [imagen]
    assert oferta["contenido"] == ""


@pytest.mark.parametrize("anidado", [
    'role="listitem" componentkey="update-card-focusinteriorFeedType_test"',
    'data-urn="urn:li:share:7100000000000000002"',
])
def test_permalink_grafico_no_toma_identificadores_ni_enlaces_anidados(anidado):
    from core.linkedin_parser import parse_posts_html

    url = "https://www.linkedin.com/feed/update/urn:li:ugcPost:7100000000000000002/"
    html = f'''<div role="listitem" componentkey="update-card-focusexteriorFeedType_test">
        <img src="https://media.licdn.com/feedshare/exterior.jpg">
        <div {anidado}>
            <section componentkey="traducible shareId=7100000000000000002">
                <img src="https://media.licdn.com/feedshare/interior.jpg">
                <a href="{url}">Post ajeno</a>
            </section>
        </div>
    </div>'''
    assert parse_posts_html(html)[0]["post_url"] == ""
    assert extract_job_from_html(html, url=url)["imagenes"] == []


@pytest.mark.parametrize("tipo", ["share", "ugcPost"])
@pytest.mark.parametrize("tarjeta", [False, True])
def test_imagenes_aisladas_de_share_y_ugcpost(tipo, tarjeta):
    from bs4 import BeautifulSoup
    from core.linkedin_parser import _extraer_imagenes_de_contenedor, parse_posts_html

    exterior = "https://media.licdn.com/feedshare/exterior.jpg"
    interior = "https://media.licdn.com/feedshare/interior.jpg"
    atributos = ('role="listitem" componentkey="update-card-focus7100000000000000001FeedType_test"'
                 if tarjeta else 'data-urn="urn:li:activity:7100000000000000001"')
    html = f'''<div {atributos}>
        <a href="https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/">Post</a>
        <div class="update-components-text">Vacante de analista</div>
        <img src="{exterior}">
        <div data-urn="urn:li:{tipo}:7100000000000000002">
            <img src="{interior}">
        </div>
    </div>'''
    soup = BeautifulSoup(html, "html.parser")
    assert _extraer_imagenes_de_contenedor(soup.div) == [exterior]
    assert _extraer_imagenes_de_contenedor(soup.find(attrs={"data-urn": f"urn:li:{tipo}:7100000000000000002"})) == [interior]
    assert parse_posts_html(html)[0]["imagenes"] == [exterior]
    assert extract_job_from_html(
        html, url="https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/"
    )["imagenes"] == [exterior]


def test_normalizar_identificadores_url():
    """Verifica la normalización exacta de URNs y URLs de LinkedIn."""
    # URN explícita
    urn_norm, num_id = _normalizar_identificadores_url("urn:li:activity:123456")
    assert urn_norm == "urn:li:activity:123456"
    assert num_id == "123456"

    # URL feed/update
    urn_norm, num_id = _normalizar_identificadores_url("https://www.linkedin.com/feed/update/urn:li:activity:789012/")
    assert urn_norm == "urn:li:activity:789012"
    assert num_id == "789012"

    # URL con slug de actividad
    urn_norm, num_id = _normalizar_identificadores_url("https://www.linkedin.com/posts/empresa_busqueda-activity-555666777-xyz")
    assert urn_norm == "urn:li:activity:555666777"
    assert num_id == "555666777"

    # Tarjeta card
    urn_norm, num_id = _normalizar_identificadores_url("urn:li:card:998877")
    assert urn_norm == "urn:li:card:998877"
    assert num_id == "998877"


def test_coincide_post_con_url_evita_subcadena():
    """Una URN '12345' NO debe coincidir con una URL con '123456' aunque sea subcadena."""
    url_target = "https://www.linkedin.com/feed/update/urn:li:activity:123456/"
    urn_target, id_target = _normalizar_identificadores_url(url_target)
    assert urn_target == "urn:li:activity:123456"
    assert id_target == "123456"

    # Post con URN prefijo (12345 es subcadena de 123456): no debe coincidir
    post_prefijo = {"urn": "urn:li:activity:12345", "post_url": ""}
    assert _coincide_post_con_url(post_prefijo, url_target, urn_target, id_target) is False

    # Post con URN sufijo inverso: tampoco
    url_corta = "https://www.linkedin.com/feed/update/urn:li:activity:12345/"
    urn_corta, id_corta = _normalizar_identificadores_url(url_corta)
    post_largo = {"urn": "urn:li:activity:123456", "post_url": ""}
    assert _coincide_post_con_url(post_largo, url_corta, urn_corta, id_corta) is False

    # Post con URN exacta: debe coincidir
    post_exacto = {"urn": "urn:li:activity:123456", "post_url": ""}
    assert _coincide_post_con_url(post_exacto, url_target, urn_target, id_target) is True

    # Post con tarjeta card y mismo ID numérico: NO debe coincidir porque los tipos contradicen (card != activity)
    post_card = {"urn": "urn:li:card:123456", "post_url": ""}
    assert _coincide_post_con_url(post_card, url_target, urn_target, id_target) is False


def test_coincide_post_con_url_rechaza_urn_contradictoria():
    """URNs con tipos distintos (share vs activity, card vs activity) no deben coincidir aunque compartan número."""
    url_target = "https://www.linkedin.com/feed/update/urn:li:activity:123456/"
    urn_target, id_target = _normalizar_identificadores_url(url_target)

    # URN completa contradictoria (share vs activity): no debe coincidir
    post_share = {"urn": "urn:li:share:123456", "post_url": ""}
    assert _coincide_post_con_url(post_share, url_target, urn_target, id_target) is False

    # URN completa contradictoria (card vs activity): no debe coincidir
    post_card = {"urn": "urn:li:card:123456", "post_url": ""}
    assert _coincide_post_con_url(post_card, url_target, urn_target, id_target) is False

    # URN completa contradictoria en post_url (share vs activity): no debe coincidir
    post_url_share = {"urn": "", "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123456/"}
    assert _coincide_post_con_url(post_url_share, url_target, urn_target, id_target) is False

    # Identificadores sin tipo: sí se permite la comparación numérica
    post_sin_tipo = {"urn": "123456", "post_url": ""}
    assert _coincide_post_con_url(post_sin_tipo, url_target, urn_target, id_target) is True

    # Target sin tipo (solo ID numérico): coincide con post tipado con mismo número
    assert _coincide_post_con_url(post_share, "123456", None, "123456") is True


def test_extract_job_from_html_urn_exacto_ambiguedad_resuelta():
    """Si en el HTML aparecen urn:li:activity:12345 y urn:li:activity:123456, no declara ambigüedad errónea."""
    html_doble = """
    <div data-urn="urn:li:activity:12345">
        <div class="update-components-actor">
            <span class="update-components-actor__title">Empresa A</span>
        </div>
        <div class="update-components-text">Oferta 12345 dev python</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_12345.jpg" />
    </div>
    <div data-urn="urn:li:activity:123456">
        <div class="update-components-actor">
            <span class="update-components-actor__title">Empresa B</span>
        </div>
        <div class="update-components-text">Oferta 123456 dev golang</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_123456.jpg" />
    </div>
    """
    # Al pedir 123456, debe extraer exclusivamente la imagen de 123456
    job_123456 = extract_job_from_html(
        html_doble,
        url="https://www.linkedin.com/feed/update/urn:li:activity:123456/",
    )
    assert job_123456["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_123456.jpg"
    ]

    # Al pedir 12345, debe extraer exclusivamente la imagen de 12345
    job_12345 = extract_job_from_html(
        html_doble,
        url="https://www.linkedin.com/feed/update/urn:li:activity:12345/",
    )
    assert job_12345["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_12345.jpg"
    ]


def test_extract_job_from_html_urn_exacto_no_importa_post_ajeno():
    """Si en el HTML solo aparece urn:li:activity:12345 y se busca 123456, NO debe importar fotos del post 12345."""
    html_solo_12345 = """
    <div data-urn="urn:li:activity:12345">
        <div class="update-components-actor">
            <span class="update-components-actor__title">Empresa A</span>
        </div>
        <div class="update-components-text">Oferta 12345 dev python</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_12345.jpg" />
    </div>
    """
    # La URL apunta a 123456, pero en el HTML sólo está 12345.
    # No debe tomar las fotos de 12345 por coincidencia parcial de subcadena.
    job = extract_job_from_html(
        html_solo_12345,
        url="https://www.linkedin.com/feed/update/urn:li:activity:123456/",
    )
    assert job["imagenes"] == []


def test_extract_job_from_html_urn_contradictoria_no_importa():
    """Si en el HTML hay urn:li:share:123456 y se consulta urn:li:activity:123456, no debe importar fotos."""
    html_share = """
    <div data-urn="urn:li:share:123456">
        <div class="update-components-actor">
            <span class="update-components-actor__title">Empresa Share</span>
        </div>
        <div class="update-components-text">Oferta share 123456</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_share.jpg" />
    </div>
    """
    job = extract_job_from_html(
        html_share,
        url="https://www.linkedin.com/feed/update/urn:li:activity:123456/",
    )
    assert job["imagenes"] == []


def test_extract_job_from_html_urn_contradictoria_no_declara_ambiguedad():
    """Si en el HTML están share:123456 y activity:123456, pedir activity:123456 debe resolver sin ambigüedad."""
    html_doble = """
    <div data-urn="urn:li:share:123456">
        <div class="update-components-text">Oferta en share</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_share.jpg" />
    </div>
    <div data-urn="urn:li:activity:123456">
        <div class="update-components-text">Oferta en activity</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_activity.jpg" />
    </div>
    """
    job = extract_job_from_html(
        html_doble,
        url="https://www.linkedin.com/feed/update/urn:li:activity:123456/",
    )
    assert job["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_activity.jpg"
    ]


def test_publicacion_anidada_conserva_imagenes_sin_incorporar_ajenas():
    """Verifica que una publicación exterior no incorpora imágenes de una anidada,
    y que la publicación anidada conserva sus propias imágenes."""
    from bs4 import BeautifulSoup
    from core.linkedin_parser import _extraer_imagenes_de_contenedor

    html = """
    <div data-urn="urn:li:activity:1000" class="post-exterior">
        <p>Texto post exterior</p>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_exterior.jpg" />

        <div data-urn="urn:li:activity:2000" class="post-anidado">
            <p>Texto post anidado (repost o publicación compartida)</p>
            <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_anidada.jpg" />
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    post_exterior = soup.find("div", {"data-urn": "urn:li:activity:1000"})
    post_anidado = soup.find("div", {"data-urn": "urn:li:activity:2000"})

    # El post exterior solo debe conservar su propia foto y no incorporar la anidada
    imgs_exterior = _extraer_imagenes_de_contenedor(post_exterior)
    assert imgs_exterior == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_exterior.jpg"
    ]

    # El post anidado debe conservar su propia foto y no descartarla por estar dentro de otro post
    imgs_anidado = _extraer_imagenes_de_contenedor(post_anidado)
    assert imgs_anidado == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_anidada.jpg"
    ]


def test_tarjeta_con_repost_anidado_conserva_imagenes_respectivas():
    """Verifica en DOM de tarjetas que el post exterior y el anidado no mezclan imágenes."""
    from bs4 import BeautifulSoup
    from core.linkedin_parser import _extraer_imagenes_de_contenedor

    html = """
    <div role="listitem" componentkey="update-card-focus8888FeedType_..." class="tarjeta-exterior">
        <p>Texto tarjeta exterior</p>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_tarjeta_ext.jpg" />

        <div data-urn="urn:li:activity:9999" class="repost-anidado">
            <p>Texto repost interior</p>
            <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_repost_int.jpg" />
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    tarjeta_ext = soup.find("div", {"role": "listitem"})
    repost_int = soup.find("div", {"data-urn": "urn:li:activity:9999"})

    imgs_ext = _extraer_imagenes_de_contenedor(tarjeta_ext)
    assert imgs_ext == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_tarjeta_ext.jpg"
    ]

    imgs_int = _extraer_imagenes_de_contenedor(repost_int)
    assert imgs_int == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_repost_int.jpg"
    ]


def test_tarjetas_anidadas_parser_y_permalink_conservan_imagenes_propias():
    """El flujo de tarjetas con nodos preseleccionados respeta cada contenedor."""
    from core.linkedin_parser import parse_posts_html

    imagen_exterior = "https://media.licdn.com/feedshare/exterior.jpg"
    imagen_interior = "https://media.licdn.com/feedshare/interior.jpg"
    url_exterior = "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/"
    url_interior = "https://www.linkedin.com/feed/update/urn:li:activity:71000000000000000012/"
    html = f"""
    <div role="listitem" componentkey="update-card-focus7100000000000000001FeedType_test">
        <a href="{url_exterior}">Publicación exterior</a>
        <section><img src="{imagen_exterior}"></section>
        <div role="listitem" componentkey="update-card-focus71000000000000000012FeedType_test">
            <a href="{url_interior}">Publicación interior</a>
            <section><img src="{imagen_interior}"></section>
        </div>
    </div>
    """

    posts = {post["urn"]: post for post in parse_posts_html(html)}
    assert set(posts) == {"urn:li:card:7100000000000000001", "urn:li:card:71000000000000000012"}
    assert posts["urn:li:card:7100000000000000001"]["imagenes"] == [imagen_exterior]
    assert posts["urn:li:card:71000000000000000012"]["imagenes"] == [imagen_interior]
    assert extract_job_from_html(html, url=url_exterior)["imagenes"] == [imagen_exterior]
    assert extract_job_from_html(html, url=url_interior)["imagenes"] == [imagen_interior]

