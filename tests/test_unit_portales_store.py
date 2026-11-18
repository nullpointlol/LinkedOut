"""Unitarios de `core/portales_store.py` sobre la base aislada del test: bordes de los duplicados
(sin regla aproximada, clasificación ilegible, links dañados) y `ya_vistos`."""
from core import portales_store, radar_store, reglas_portales
from tests.test_portales_nuevos import perfil  # noqa: F401  (perfil de prueba con la regla `duplicados`)


def _oferta(portal, id_, titulo, empresa, link=None):
    return {"portal": portal, "id": id_, "urn": f"{portal}:{id_}", "titulo": titulo, "empresa": empresa,
            "ubicacion": "CABA", "descripcion": "x", "completa": False, "link": link or f"https://{portal}.test/{id_}",
            "link_directo": "", "emails": [], "fecha": ""}


def _sql(consulta, *args):
    conn = radar_store._connect()
    try:
        with conn:
            conn.execute(consulta, args)
    finally:
        conn.close()


def test_sin_regla_de_duplicados_no_compara_titulos_parecidos(perfil, monkeypatch):  # noqa: F811
    """Sin la regla `duplicados` en el perfil, solo valen el link y el título y la empresa exactos."""
    monkeypatch.setattr(reglas_portales, "leer_duplicados", lambda: None)
    portales_store.guardar_oferta(_oferta("computrabajo", "1", "Analista Funcional Sr", "Acme S.A."), "x")
    estado, _ = portales_store.guardar_oferta(_oferta("bumeran", "2", "Analista Funcional Sr.", "ACME SA"), "x")
    assert estado == portales_store.NUEVA


def test_un_post_de_linkedin_con_clasificacion_ilegible_no_cuenta_como_duplicado(perfil):  # noqa: F811
    """Un post del Radar cuya clasificación no es JSON se saltea al buscar duplicados aproximados."""
    pid = radar_store.insert_post_if_new({"urn": "urn:li:1", "texto": "Buscamos analista"}, "consulta")
    _sql("UPDATE linkedin_posts SET triage = ? WHERE id = ?", "{roto", pid)
    estado, nuevo = portales_store.guardar_oferta(_oferta("bumeran", "5", "Analista Funcional", "Acme"), "x")
    assert estado == portales_store.NUEVA and nuevo != pid


def test_duplicado_por_link_con_links_danados_los_rehace(perfil):  # noqa: F811
    """Si el post existente tiene la lista de links dañada, se rehace con el link nuevo y queda como visto."""
    link = "https://bumeran.test/7"
    pid = radar_store.insert_post_if_new({"urn": "urn:li:7", "texto": "Oferta", "post_url": link}, "consulta")
    _sql("UPDATE linkedin_posts SET links = ? WHERE id = ?", "{roto", pid)
    oferta = _oferta("computrabajo", "7", "Analista", "Acme", link=link + "/")
    assert portales_store.guardar_oferta(oferta, "x") == (portales_store.DUPLICADA, pid)
    assert radar_store.get_post(pid)["links"] == [link + "/"]
    assert portales_store.guardar_oferta(oferta, "x") == (portales_store.VISTA, pid)


def test_ya_vistos_devuelve_solo_los_que_entraron(perfil):  # noqa: F811
    """`ya_vistos` ignora vacíos y repetidos y devuelve solo los urns guardados."""
    assert portales_store.ya_vistos(["", None]) == set()
    portales_store.guardar_oferta(_oferta("bumeran", "9", "Contador", "Acme"), "x")
    assert portales_store.ya_vistos(["bumeran:9", "bumeran:9", "", "bumeran:10"]) == {"bumeran:9"}
