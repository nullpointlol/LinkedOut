"""Tests del parser puro de LinkedIn y la extracción de emails (sin red ni playwright)."""
from pathlib import Path

import pytest

from core.linkedin_parser import (
    build_search_url,
    es_posible_busqueda,
    extract_emails,
    extract_whatsapp,
    is_login_or_checkpoint_url,
    parse_posts_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_search_posts.html"


@pytest.mark.parametrize("text,expected", [
    ("Enviar CV a rrhh@acme.test", ["rrhh@acme.test"]),
    ("escribí a rrhh@acme.test.", ["rrhh@acme.test"]),
    ("(talento@empresa.test.ar)", ["talento@empresa.test.ar"]),
    ("jobs [at] acme [dot] test [dot] io", ["jobs@acme.test.io"]),
    ("jobs(arroba)acme.test", ["jobs@acme.test"]),
    ("jobs @ acme . test", ["jobs@acme.test"]),
    ("jobs at acme dot test", ["jobs@acme.test"]),
    ("A@X.test y a@x.test", ["a@x.test"]),
    ("perfil en someone@linkedin.com", []),
    ("formato ejemplo@ejemplo.com o x@example.com", []),
    ("sin email acá, solo @menciones", []),
    # Prosa con "at" que no forma un email válido: no debe producir emails
    ("we are at the office", []),
    ("estamos at home y dot", []),
    # Orden de aparición con varios emails y puntuación pegada
    ("a@x.test; b@y.test, c@z.test.", ["a@x.test", "b@y.test", "c@z.test"]),
    # Un punto de fin de oración separado no se une al dominio
    ("jobs @ acme . test . Gracias", ["jobs@acme.test"]),
])
def test_extract_emails(text, expected):
    assert extract_emails(text) == expected


@pytest.mark.parametrize("text", [
    "Join us at acme.com. Great team",
    "contact us at hr.acme.com",
    "visitanos at acme.com",
    "Apply at careers.google.com today",
    "Visitá nuestra web at www.acme.com.ar",
    "Postulate at jobs.lever.co/acme",
    "Más info at acme.com o escribinos",
    "Software Engineer @ stripe.com",
    "Contact\nat acme.com",
])
def test_extract_emails_prosa_no_genera_destinatarios(text):
    assert extract_emails(text) == []


@pytest.mark.parametrize("text,expected", [
    ("rrhh @acme.test", ["rrhh@acme.test"]),
    ("rrhh@ acme.test", ["rrhh@acme.test"]),
    ("jobs (at) acme (dot) test (dot) io", ["jobs@acme.test.io"]),
    ("jobs [at] acme.test", ["jobs@acme.test"]),
    # Falta el espacio tras el punto final de la oración: la palabra siguiente no se pega
    ("CV a rrhh@acme.test.Gracias!", ["rrhh@acme.test"]),
    ("info@acme.test.Saludos", ["info@acme.test"]),
    ("RRHH@ACME.test", ["rrhh@acme.test"]),
    ("RRHH@ACME.test.AR", ["rrhh@acme.test.ar"]),
    ("talento@empresa.test.ar", ["talento@empresa.test.ar"]),
])
def test_extract_emails_variantes_y_limites_de_dominio(text, expected):
    assert extract_emails(text) == expected


def test_parse_posts_html_fixture():
    posts = parse_posts_html(FIXTURE.read_text(encoding="utf-8"))
    assert len(posts) == 4
    assert posts[0]["urn"] == "urn:li:activity:7000000000000000001"
    assert posts[0]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/"
    assert posts[0]["emails"] == ["rrhh@acme.test"]
    assert posts[1]["emails"] == []
    assert len(posts[2]["emails"]) == 2
    assert posts[3]["texto"].strip() != ""


def test_parse_posts_html_fixture_fields():
    posts = parse_posts_html(FIXTURE.read_text(encoding="utf-8"))
    assert posts[0]["autor"] == "María Gómez"
    assert posts[0]["cargo_autor"] == "Talent Acquisition en Acme"
    assert posts[0]["fecha_rel"] == "3 d"
    assert "Analista Funcional" in posts[0]["texto"]
    assert "ver más" not in posts[0]["texto"]
    assert posts[2]["emails"] == ["cv@initech.test", "jobs@initech.test.io"]
    # Repost: texto anidado; el autor es el del contenedor, no el del sub-actor
    assert posts[3]["autor"] == "Carlos Ruiz"
    assert posts[3]["cargo_autor"] == ""
    assert posts[3]["emails"] == ["talento@globex.test.ar"]


def test_parse_posts_html_tolera_subselectores_faltantes_y_omite_sin_texto():
    html = """
    <div data-urn="urn:li:activity:1"><div class="update-components-text">Hola a@b.test</div></div>
    <div data-urn="urn:li:activity:2"><span class="update-components-actor__title">Sin texto</span></div>
    <div data-urn="urn:li:activity:1"><div class="update-components-text">Duplicado</div></div>
    <div data-urn="urn:li:share:3"><div class="update-components-text">No es activity</div></div>
    """
    posts = parse_posts_html(html)
    assert [p["urn"] for p in posts] == ["urn:li:activity:1"]
    assert posts[0]["autor"] == "" and posts[0]["cargo_autor"] == "" and posts[0]["fecha_rel"] == ""
    assert posts[0]["emails"] == ["a@b.test"]


def test_parse_posts_html_contenedores_anidados_no_duplican():
    html = """
    <div data-urn="urn:li:activity:10">
      <div class="update-components-text">Comentario</div>
      <div data-urn="urn:li:activity:11"><div class="update-components-text">Interior x@y.test</div></div>
    </div>
    """
    posts = parse_posts_html(html)
    assert [p["urn"] for p in posts] == ["urn:li:activity:10"]
    assert posts[0]["emails"] == ["x@y.test"]


def test_parse_posts_html_vacio():
    assert parse_posts_html("") == []
    assert parse_posts_html("<html><body>nada</body></html>") == []


def test_build_search_url_encodes_query():
    url = build_search_url('"enviar cv"')
    assert url.startswith("https://www.linkedin.com/search/results/content/?keywords=")
    assert "%22enviar%20cv%22" in url and 'sortBy=%22date_posted%22' in url and 'datePosted=%22past-week%22' in url


@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/login", True),
    ("https://www.linkedin.com/checkpoint/challenge/x", True),
    ("https://www.linkedin.com/authwall?trk=x", True),
    ("https://www.linkedin.com/uas/login", True),
    ("https://www.linkedin.com/search/results/content/?keywords=x", False),
])
def test_is_login_or_checkpoint_url(url, expected):
    assert is_login_or_checkpoint_url(url) is expected


# --- DOM de LinkedIn de noviembre 2026 (tarjetas role="listitem" + componentkey) ---

FIXTURE_2026_11 = Path(__file__).parent / "fixtures" / "linkedin_search_posts_2026_11.html"


def _posts_2026_11():
    return parse_posts_html(FIXTURE_2026_11.read_text(encoding="utf-8"))


def test_parse_dom_2026_11_finds_every_card():
    posts = _posts_2026_11()
    assert [p["urn"] for p in posts] == [
        "urn:li:card:AAAkeyUno111",
        "urn:li:card:BBBkeyDos222",
        "urn:li:card:CCCkeyTres333",
    ]


def test_parse_dom_2026_11_fields():
    sin_email, mailto, en_texto = _posts_2026_11()

    assert sin_email["emails"] == []
    assert sin_email["autor"] == "Laser Ficticio"
    assert sin_email["url"] == "https://www.linkedin.com/in/laser-ficticio-123/"

    assert mailto["autor"] == "Ana Pérez"
    assert mailto["cargo_autor"] == "Sr. Talent Advisor"
    assert mailto["fecha_rel"] == "2 h"
    assert mailto["emails"] == ["rrhh.ficticio@correo-ficticio.test"]
    assert mailto["texto"].startswith("¡Estamos contratando!\nBuscamos Recepcionista & Atención al Cliente.")
    assert "con el asunto Recepcionista – Escazú." in mailto["texto"]

    assert en_texto["cargo_autor"] == "Lic. en Psicología | Recruiting"
    assert en_texto["fecha_rel"] == "2 h"
    assert en_texto["emails"] == ["busquedas.ficticias@correo-ficticio.test"]


def test_parse_dom_2026_11_drops_ver_mas_button_text():
    for post in _posts_2026_11():
        assert "más" not in post["texto"].splitlines()[-1]
        assert "…" not in post["texto"]


def test_parse_dom_2026_11_date_inside_coauthor_line():
    html = (
        '<div role="listitem" componentkey="update-card-focusDDDkey444FeedType_FLAGSHIP_SEARCH">'
        '<a href="https://www.linkedin.com/company/raga-ficticia/"><p><span>RaGa Ficticia</span></p></a>'
        '<p><span>junto con Persona Ficticia • 2&nbsp;h • Editado</span></p>'
        '<p><span data-testid="expandable-text-box">Buscamos analista. CV a cv.ficticio@correo-ficticio.test</span></p>'
        '</div>'
    )
    (post,) = parse_posts_html(html)
    assert post["autor"] == "RaGa Ficticia"
    assert post["fecha_rel"] == "2 h"
    assert post["url"] == "https://www.linkedin.com/company/raga-ficticia/"
    assert post["emails"] == ["cv.ficticio@correo-ficticio.test"]


# --- Feed de inicio (DOM de noviembre 2026): contexto social, links y WhatsApp ---

FEED = Path(__file__).parent / "fixtures" / "linkedin_feed_2026_11.html"


def test_feed_cards_skip_social_context_and_suggestions():
    posts = parse_posts_html(FEED.read_text(encoding="utf-8"))
    assert [p["autor"] for p in posts] == [
        "Nicole Ficticia", "Quilsoft Ficticia", "TGV Ficticia", "Marcos Ficticio", "Lucía Ficticia",
    ]
    assert posts[0]["cargo_autor"] == "Analista Senior Atracción del Talento"
    assert posts[0]["fecha_rel"] == "4 días"
    assert posts[0]["url"].endswith("/in/nicole-ficticia/")
    assert posts[1]["url"].endswith("/company/quilsoft-ficticia/")
    assert posts[1]["cargo_autor"] == ""          # "N seguidores" y "Promocionado" no son cargo
    assert posts[2]["fecha_rel"] == "6 días"
    assert posts[3]["cargo_autor"] == "Reclutador IT | Talento Ficticio"
    assert posts[3]["fecha_rel"] == "3 d"


def test_feed_links_and_whatsapp():
    p1, _, p3, p5, _ = parse_posts_html(FEED.read_text(encoding="utf-8"))
    assert p1["links"] == ["https://forms.gle/abc123"] and p1["whatsapp"] == []
    assert p3["whatsapp"] == ["5491155555555"] and p3["emails"] == []
    assert p5["whatsapp"] == ["5491166667777"] and p5["emails"] == ["rrhh.ficticio@correo-ficticio.test"]


def test_feed_links_desenvuelve_redireccion_y_excluye_whatsapp_y_mailto():
    _, p2, p3, p5, _ = parse_posts_html(FEED.read_text(encoding="utf-8"))
    assert p2["links"] == ["https://www.quilsoft-ficticia.example/contactanos?utm=feed"]
    assert p3["links"] == ["https://tgv-ficticia.example/bolsa"]   # safety/go/?url=... desenvuelto
    assert p5["links"] == []                                         # wa.me va a whatsapp; mailto a emails


def test_links_conserva_lnkd_in_y_jobs_view_y_urls_en_texto():
    html = (
        '<div role="listitem" componentkey="update-card-focusFFFkey666FeedType_MAIN_FEED">'
        '<a href="https://www.linkedin.com/company/x-ficticia/"><p>X Ficticia</p></a>'
        '<p><span data-testid="expandable-text-box">Vacante: https://bit.ly/ficticio, y más en '
        '<a href="https://lnkd.in/abcDEF">lnkd</a> '
        '<a href="https://www.linkedin.com/jobs/view/12345/">oferta</a> '
        '<a href="https://www.linkedin.com/in/otra-persona/">perfil</a> '
        '<a href="https://www.linkedin.com/feed/hashtag/empleo/">#empleo</a> '
        '<a href="http://inseguro.example/x">http</a> '
        '<a href="https://lnkd.in/abcDEF">de nuevo</a></span></p></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["links"] == [
        "https://lnkd.in/abcDEF",
        "https://www.linkedin.com/jobs/view/12345/",
        "https://bit.ly/ficticio",
    ]


def test_parse_dom_antiguo_incluye_links_y_whatsapp():
    for post in parse_posts_html(FIXTURE.read_text(encoding="utf-8")):
        assert isinstance(post["links"], list) and isinstance(post["whatsapp"], list)


@pytest.mark.parametrize("texto,esperado", [
    ("WhatsApp: +54 9 11 4444-3333", ["5491144443333"]),
    ("wsp 011 4444 3333", []),                      # sin código de país → no se inventa
    ("wpp 11 4444-3333", []),
    ("https://api.whatsapp.com/send?phone=5493512223344", ["5493512223344"]),
    ("Escribinos: https://wa.me/5491122223333", ["5491122223333"]),
    ("Escribime al whatsapp 54 9 351 222-3344 o al wsp +54 9 351 222-3344", ["5493512223344"]),
    ("llamame al +54 9 11 4444-3333", []),          # sin la palabra clave no es WhatsApp
    ("sin números", []),
])
def test_extract_whatsapp(texto, esperado):
    assert extract_whatsapp(texto, []) == esperado


def test_extract_whatsapp_desde_hrefs():
    hrefs = ["https://wa.me/5491166667777?text=hola", "https://web.whatsapp.com/send?phone=%2B5493512223344",
             "https://wa.me/message/ABCDEF", "https://example.com/x"]
    assert extract_whatsapp("", hrefs) == ["5491166667777", "5493512223344"]


@pytest.mark.parametrize("texto,esperado", [
    ("¡Estamos buscando Analista Funcional! Postulate", True), ("We're hiring a QA", True),
    ("#empleo Vacante en CABA", True), ("Gran charla sobre liderazgo ayer", False),
    ("Feliz de anunciar mi nuevo puesto", False),
    ("Búsqueda laboral: Analista", True), ("Enviá tu CV a rrhh", True), ("Oportunidad laboral en Córdoba", True),
    ("We are hiring! Job opening", True), ("Estamos contratando", True), ("Vaga para desenvolvedor", True),
    ("Please apply here", True), ("#trabajo en IT", True),
])
def test_es_posible_busqueda(texto, esperado):
    assert es_posible_busqueda(texto) is esperado


@pytest.mark.parametrize("contexto", [
    "Pedro Ficticio le encanta esto", "Pedro Ficticio le gusta esto", "Pedro Ficticio y otras personas les parece interesante",
])
def test_feed_contexto_de_reaccion_no_es_el_autor(contexto):
    html = (
        '<div role="listitem" componentkey="update-card-focusGGGkey777FeedType_MAIN_FEED">'
        f'<p><a href="https://www.linkedin.com/in/pedro-ficticio/">{contexto}</a></p>'
        '<a href="https://www.linkedin.com/in/ines-ficticia/"><p>Ines Ficticia</p></a>'
        '<p>Recruiter</p><p>1 sem</p>'
        '<p><span data-testid="expandable-text-box">Vacante abierta</span></p></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["autor"] == "Ines Ficticia"
    assert post["url"].endswith("/in/ines-ficticia/")
    assert post["cargo_autor"] == "Recruiter"
    assert post["fecha_rel"] == "1 sem"


@pytest.mark.parametrize("texto", [
    "Incorporamos developers", "Seleccionamos personal", "Estamos incorporando", "Se busca vendedor",
    "Estamos reclutando", "Oferta laboral", "Ofertas laborales en CABA", "Contratamos", "Envianos tu CV",
    "Envíanos tu CV", "Mandá tu CV", "Dejá tu CV", "Aplicá acá", "Join our team", "We are looking for a PM",
    "Posición disponible", "Nueva posición abierta", "Se necesita administrativo",
])
def test_es_posible_busqueda_recall_ampliado(texto):
    assert es_posible_busqueda(texto) is True


def _tarjeta_con_contexto(contexto: str, cargo: str = "Recruiter") -> str:
    return (
        '<div role="listitem" componentkey="update-card-focusHHHkey888FeedType_MAIN_FEED">'
        f'<p><a href="https://www.linkedin.com/in/pedro-ficticio/">{contexto}</a></p>'
        '<a href="https://www.linkedin.com/in/ines-ficticia/"><p>Ines Ficticia</p></a>'
        f'<p>{cargo}</p><p>1 sem</p>'
        '<p><span data-testid="expandable-text-box">Vacante abierta</span></p></div>'
    )


@pytest.mark.parametrize("contexto", [
    "Pedro Ficticio comentó esto", "Pedro Ficticio compartió esto", "Pedro Ficticio volvió a publicar esto",
    "Pedro Ficticio le gustan estas publicaciones", "Pedro Ficticio loves this", "Pedro Ficticio celebrates this",
    "Pedro Ficticio reposted this", "Pedro Ficticio supports this", "Pedro Ficticio finds this insightful",
    "Quién ha visto tu perfil", "Seguido por Pedro Ficticio", "Followed by Pedro Ficticio",
    "Pedro Ficticio cree que esto es interesante", "Pedro y Ana creen que esto es útil",
])
def test_feed_variantes_de_contexto_conservan_el_autor_real(contexto):
    (post,) = parse_posts_html(_tarjeta_con_contexto(contexto))
    assert post["autor"] == "Ines Ficticia"
    assert post["url"].endswith("/in/ines-ficticia/")
    assert post["cargo_autor"] == "Recruiter"


def test_feed_cargo_con_apoya_o_celebra_no_se_descarta():
    (post,) = parse_posts_html(_tarjeta_con_contexto("Pedro Ficticio recomienda esto", "Apoya startups | Celebra el talento"))
    assert post["autor"] == "Ines Ficticia"
    assert post["cargo_autor"] == "Apoya startups | Celebra el talento"


# --- Contexto social en plural/pasado y destinos solo del cuerpo del post (nunca de comentarios) ---

def test_feed_contexto_plural_con_dos_perfiles_y_comentario_de_tercero():
    *_, post = parse_posts_html(FEED.read_text(encoding="utf-8"))
    assert post["autor"] == "Lucía Ficticia"
    assert post["url"].endswith("/in/lucia-ficticia/")
    assert post["cargo_autor"] == "Talent Acquisition | Ficticia SA"
    assert post["fecha_rel"] == "1 d"
    # Solo el cuerpo del post y su vista previa de enlace: nada del comentario de Carlos
    assert post["texto"] == "Buscamos Analista Funcional. CV a seleccion.lucia@correo-ficticio.test o por WhatsApp al +54 9 11 3333-2222"
    assert post["emails"] == ["seleccion.lucia@correo-ficticio.test"]
    assert post["whatsapp"] == ["5491133332222"]
    assert post["links"] == ["https://empleos-ficticios.example/analista"]


def test_comentarios_sin_barra_social_tampoco_aportan_destinos():
    """Si LinkedIn cambia las etiquetas de la barra, el bloque de comentarios sigue siendo el límite."""
    html = (
        '<div role="listitem" componentkey="update-card-focusJJJkey999FeedType_MAIN_FEED">'
        '<a href="https://www.linkedin.com/in/ines-ficticia/"><p>Ines Ficticia</p></a>'
        '<p><span data-testid="expandable-text-box">Buscamos QA, escribinos</span></p>'
        '<div data-testid="JJJ-commentListFeedType_MAIN_FEED"><div>'
        '<p><span data-testid="expandable-text-box">mi mail: otro@correo-ficticio.test, wsp +54 9 11 4444-0000</span></p>'
        '<a href="mailto:otro@correo-ficticio.test">x</a><a href="https://otro.example/cv">cv</a></div></div></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["texto"] == "Buscamos QA, escribinos"
    assert post["emails"] == [] and post["whatsapp"] == [] and post["links"] == []


def test_tarjeta_sin_cuerpo_propio_no_toma_el_texto_de_un_comentario():
    html = (
        '<div role="listitem" componentkey="update-card-focusKKKkey000FeedType_MAIN_FEED">'
        '<a href="https://www.linkedin.com/in/ines-ficticia/"><p>Ines Ficticia</p></a>'
        '<button aria-label="Comentar"></button>'
        '<div><p><span data-testid="expandable-text-box">Buscamos: otro@correo-ficticio.test</span></p></div></div>'
    )
    assert parse_posts_html(html) == []


@pytest.mark.parametrize("contexto", [
    "Pedro Ficticio y Ana Ficticia recomiendan esto", "Pedro Ficticio comentó", "Pedro y 3 personas más recomiendan esto",
    "Pedro Ficticio ha recomendado esto", "Pedro Ficticio y Ana Ficticia han comentado esto",
    "Pedro Ficticio y Ana Ficticia comentaron", "Pedro Ficticio comenta", "Pedro Ficticio reaccionó a esto",
    "Pedro Ficticio y Ana Ficticia reaccionaron", "Pedro Ficticio y Ana Ficticia han reaccionado a esto",
    "Pedro Ficticio y Ana Ficticia compartieron esto", "A Pedro y Ana les gustan esto", "Pedro Ficticio y otros",
    "Pedro Ficticio y otras 4 personas", "Pedro Ficticio y 12 personas más", "Pedro Ficticio and 3 others like this",
    "PEDRO FICTICIO RECOMIENDA ESTO",
])
def test_feed_contexto_plural_y_pasado_no_es_el_autor(contexto):
    (post,) = parse_posts_html(_tarjeta_con_contexto(contexto))
    assert post["autor"] == "Ines Ficticia"
    assert post["url"].endswith("/in/ines-ficticia/")
    assert post["cargo_autor"] == "Recruiter"


@pytest.mark.parametrize("fuera_del_enlace", [" y otra persona celebran esto", " opina sobre esto", " finds this"])
def test_feed_contexto_estructural_perfil_mas_texto_que_termina_en_esto(fuera_del_enlace):
    """Regla estructural: <p> con enlace a un perfil + texto fuera del enlace que termina en esto/this."""
    html = (
        '<div role="listitem" componentkey="update-card-focusLLLkey111FeedType_MAIN_FEED">'
        f'<p><a href="https://www.linkedin.com/in/pedro-ficticio/">Pedro Ficticio</a><span>{fuera_del_enlace}</span></p>'
        '<a href="https://www.linkedin.com/in/ines-ficticia/"><p>Ines Ficticia</p></a>'
        '<p>Recruiter</p><p>1 sem</p>'
        '<p><span data-testid="expandable-text-box">Vacante abierta</span></p></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["autor"] == "Ines Ficticia"
    assert post["url"].endswith("/in/ines-ficticia/")


def test_autor_dentro_de_enlace_no_es_contexto_estructural():
    """Un <p> envuelto por el enlace del autor (sin texto fuera del enlace) sigue siendo el autor."""
    html = (
        '<div role="listitem" componentkey="update-card-focusMMMkey222FeedType_MAIN_FEED">'
        '<a href="https://www.linkedin.com/in/esto-ficticio/"><p>Ernesto Esto</p></a>'
        '<p>Recruiter</p>'
        '<p><span data-testid="expandable-text-box">Vacante abierta</span></p></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["autor"] == "Ernesto Esto"


@pytest.mark.parametrize("boton", ["Contactar", "Seguir", "Conectar", "Enviar mensaje", "Follow", "Connect"])
def test_feed_texto_de_boton_no_es_el_cargo(boton):
    """Los botones del encabezado ("Contactar", "Seguir"…) no son el cargo del autor."""
    html = (
        '<div role="listitem" componentkey="update-card-focusJJJkey999FeedType_MAIN_FEED">'
        '<a href="https://www.linkedin.com/company/bdtglobal/"><p>bdtglobal</p></a>'
        f'<p>{boton}</p><p>3 días</p>'
        '<p><span data-testid="expandable-text-box">Buscamos Data Analyst</span></p></div>'
    )
    (post,) = parse_posts_html(html)
    assert post["autor"] == "bdtglobal" and post["cargo_autor"] == "" and post["fecha_rel"] == "3 días"


# ------------------------------------------------- link a la publicación (DOM de 2026)

_FEED_2026 = (Path(__file__).parent / "fixtures" / "linkedin_feed_2026_11.html").read_text(encoding="utf-8")


def _por_autor(html):
    from core.linkedin_parser import parse_posts_html
    return {p["urn"]: p for p in parse_posts_html(html)}


def test_post_url_from_translatable_share_id():
    # El texto del post va envuelto en un bloque traducible que trae el shareId
    html = _FEED_2026.replace(
        '<p><span data-testid="expandable-text-box">¡Estamos buscando',
        '<div id="translatable-commentary-FeTranslationUrn(contentUrnCommentUrn=null, contentUrnGroupPostUrn=null, '
        'contentUrnShareUrn=ContentUrnShareUrn(shareUrn=ShareUrn(shareId=7000000000000000002)), '
        'contentUrnUgcPostUrn=null, detectedLocale=, targetLocale=es)">'
        '<p><span data-testid="expandable-text-box">¡Estamos buscando', 1)
    posts = _por_autor(html)
    assert posts["urn:li:card:AAAA1111"]["post_url"] == \
        "https://www.linkedin.com/feed/update/urn:li:share:7000000000000000002/"
    # El perfil del autor sigue en `url` (destino del canal dm)
    assert posts["urn:li:card:AAAA1111"]["url"].startswith("https://www.linkedin.com/in/")


def test_post_url_from_ugc_post_id():
    html = _FEED_2026.replace(
        '<p><span data-testid="expandable-text-box">¡Estamos buscando',
        '<div componentkey="translatable-commentary-FeTranslationUrn(contentUrnShareUrn=null, '
        'contentUrnUgcPostUrn=ContentUrnUgcPostUrn(ugcPostUrn=UgcPostUrn(ugcPostId=7511111111111111111)))">'
        '<p><span data-testid="expandable-text-box">¡Estamos buscando', 1)
    assert _por_autor(html)["urn:li:card:AAAA1111"]["post_url"] == \
        "https://www.linkedin.com/feed/update/urn:li:ugcPost:7511111111111111111/"


def test_post_url_from_group_highlighted_update_link():
    html = _FEED_2026.replace(
        '<p><span data-testid="expandable-text-box">Abrimos oportunidades.',
        '<a href="https://www.linkedin.com/groups/6613456/?q=highlightedFeedForGroups&amp;'
        'highlightedUpdateUrn=urn%3Ali%3Aactivity%3A7000000000000000003">Grupo</a>'
        '<p><span data-testid="expandable-text-box">Abrimos oportunidades.', 1)
    assert _por_autor(html)["urn:li:card:CCCC3333"]["post_url"] == \
        "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000003/"


def test_post_url_empty_when_dom_has_no_id():
    # Nunca se usa el perfil del autor como link de la publicación
    assert all(p["post_url"] == "" for p in _por_autor(_FEED_2026).values())
