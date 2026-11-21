import pytest

from core.linkedin_parser import parse_posts_html


def _tarjeta(autor, contexto=""):
    return (
        '<div role="listitem" componentkey="update-card-focusGrupo111FeedType_MAIN_FEED">'
        f'{contexto}<p>{autor}</p><p>1 h</p>'
        '<p><span data-testid="expandable-text-box">Buscamos analista</span></p></div>'
    )


@pytest.mark.parametrize("contexto", [
    "Nuevo comentario en tu grupo",
    "nueva publicacion en tu grupo",
    "nuevas publicaciones en tu grupo",
    "actividad en tu grupo",
    "publico en tu grupo",
    "new comment in your group",
    "new post in your group",
    "Nueva publicación en tu grupo",
    "Publicó en tu grupo",
])
@pytest.mark.parametrize("autor", ["Ofertas Laborales ARGENTINA", "Ana Ficticia"])
def test_notificacion_de_grupo_no_es_el_autor(contexto, autor):
    (post,) = parse_posts_html(_tarjeta(autor, f"<p>{contexto}</p>"))
    assert post["autor"] == autor
    assert post["fecha_rel"] == "1 h"
    assert post["cargo_autor"] == ""
    assert post["texto"] == "Buscamos analista"


def test_tarjeta_normal_conserva_el_autor():
    (post,) = parse_posts_html(_tarjeta("Ana Ficticia"))
    assert post["autor"] == "Ana Ficticia"
    assert post["fecha_rel"] == "1 h"
    assert post["cargo_autor"] == ""
    assert post["texto"] == "Buscamos analista"
