"""Aprendizaje de las ofertas con puntaje bajo (creador, 15/11): qué tienen en común y no tienen las demás. Solo
informa, no descarta. Ofertas inventadas."""
import pytest

from core import aprendizaje_puntaje as ap


def _oferta(puntaje, puesto, brechas=(), texto=""):
    return {"id": puesto, "puntaje": puntaje, "puesto": puesto, "brechas": list(brechas), "texto": texto}


OFERTAS = [
    _oferta(30, "Vendedor de seguros", ["Experiencia en ventas"], "Comisiones atractivas y cartera de clientes"),
    _oferta(40, "Ejecutivo comercial", ["Experiencia en ventas"], "Cartera de clientes propia, comisiones"),
    _oferta(45, "Asesor de ventas", ["Experiencia en ventas"], "Comisiones por objetivos"),
    _oferta(80, "Analista funcional", [], "Relevamiento con clientes internos y documentación"),
    _oferta(85, "Analista de soporte", [], "Soporte a clientes internos con Jira"),
    _oferta(90, "Analista de datos", [], "Consultas SQL para clientes"),
]


def test_un_termino_de_las_de_puntaje_bajo_sale_y_uno_comun_a_todas_no():
    r = ap.senales(OFERTAS, umbral=60, min_ofertas=2)
    terminos = {(t["origen"], t["termino"]) for t in r["terminos"]}
    assert ("brecha", "experiencia en ventas") in terminos and ("aviso", "comisiones") in terminos
    assert ("aviso", "clientes") not in terminos  # está en todas: no distingue
    assert r["bajas"] == 3 and r["resto"] == 3
    primero = r["terminos"][0]
    assert primero["bajas"] == 3 and primero["resto"] == 0 and len(primero["ejemplos"]) == 2


def test_menos_ofertas_que_el_minimo_no_cuenta():
    r = ap.senales(OFERTAS, umbral=60, min_ofertas=3)
    assert all(t["bajas"] >= 3 for t in r["terminos"])
    assert ("puesto", "seguros") not in {(t["origen"], t["termino"]) for t in r["terminos"]}


@pytest.mark.parametrize("config,error", [
    (None, "Falta configurar"), ({"umbral": 0, "min_ofertas": 3}, "umbral"), ({"umbral": 60, "min_ofertas": 1}, "min_ofertas"),
    ({"umbral": 60, "min_ofertas": 3, "otro": 1}, "desconocido"),
])
def test_sin_configuracion_valida_no_calcula(config, error):
    assert error in ap.para_perfil(config, [])["error"]


def test_toma_las_ofertas_analizadas_de_los_posts():
    posts = [{"id": 1, "texto": "Comisiones", "analisis": {"match_score": 30, "puesto": "Vendedor",
                                                           "requisitos": [{"nombre": "Ventas", "estado": "BRECHA_REAL"},
                                                                          {"nombre": "SQL", "estado": "MATCH_DIRECTO"}]}},
             {"id": 2, "texto": "x", "analisis": {}}]  # sin puntaje: no cuenta
    assert ap.ofertas_de_posts(posts) == [{"id": 1, "puntaje": 30.0, "puesto": "Vendedor", "brechas": ["Ventas"],
                                           "texto": "Comisiones"}]


def test_una_configuracion_que_no_es_objeto_da_su_error():
    assert "objeto" in ap.para_perfil(["umbral", 60], [])["error"]


def test_con_configuracion_valida_calcula_las_senales():
    assert ap.para_perfil({"umbral": 60, "min_ofertas": 2}, []) == ap.senales([], umbral=60, min_ofertas=2)
