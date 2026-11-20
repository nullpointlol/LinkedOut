"""Reglas de Portales completas para los tests (todo valor vive en el perfil). Valores inventados."""
import copy

from core.reglas_portales import PROPIOS, PORTALES, ReglaPortal

COMUNES = {"activo": True, "consultas_por_dia": 10, "minutos_entre_consultas": 15, "ubicacion": "Argentina",
           "resultados_por_consulta": 20, "dias": 7, "modo": "busquedas", "listados": [], "leer_pagina_aviso": False,
           "plazo_consulta_s": 30, "min_descripcion_completa": 400}
PROPIOS_PRUEBA = {
    "jooble": {"url": "https://jooble.test/api/{clave}"},
    "indeed": {"pais": "argentina"},
    "computrabajo": {"url": "https://ar.computrabajo.test"},
    "bumeran": {"url": "https://www.bumeran.test", "sitemap": "https://www.bumeran.test/sitemap_avisos.xml",
                "plazo_pagina_s": 30},
    "remoteok": {"url": "https://remoteok.test/api", "url_busqueda": "https://remoteok.test/api?tag={palabras}"},
    "remotive": {"url": "https://remotive.test/api", "url_busqueda": "https://remotive.test/api?search={palabras}"},
    "himalayas": {"url": "https://himalayas.test/api", "url_busqueda": "https://himalayas.test/api/search?q={palabras}"},
}
DUPLICADOS = {"umbral_titulo": 90, "dias": 14}
assert set(PROPIOS_PRUEBA) == set(PORTALES) and all(set(PROPIOS_PRUEBA[p]) == set(PROPIOS[p]) for p in PORTALES)


def regla_dato(portal, **cambios):
    """El dict de reglas de un portal (como en reglas_portales.json)."""
    return {**copy.deepcopy(COMUNES), **PROPIOS_PRUEBA[portal], **cambios}


def reglas(apagados=(), **por_portal):
    """reglas_portales.json completo: cada portal con sus cambios; los de `apagados`, con activo false."""
    datos = {p: regla_dato(p, **por_portal.get(p, {})) for p in PORTALES}
    for p in apagados:
        datos[p]["activo"] = False
    datos["duplicados"] = dict(DUPLICADOS)
    return datos


def regla(portal, **cambios):
    """El ReglaPortal de un portal, como lo lee core/reglas_portales.py."""
    d = regla_dato(portal, **cambios)
    return ReglaPortal(portal=portal, **{**d, "listados": tuple(d["listados"])})
