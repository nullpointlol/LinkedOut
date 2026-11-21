"""Unitarios de `core/radar_store.py` sobre la base aislada del test (conftest): normalización de
filas dañadas, validaciones de columnas y bordes de lecturas, textos y vistos."""
from datetime import datetime, timedelta, timezone

import pytest

from core import radar_store as rs


def test_fila_de_post_con_nulos_se_normaliza():
    """Listas nulas o con otro tipo quedan vacías, textos nulos vacíos y progreso ilegible None."""
    d = rs._post_row_to_dict({"emails": "no-json", "links": None, "texto_imagen": None,
                              "texto_pagina": None, "progreso": "[]"})
    assert (d["emails"], d["links"], d["whatsapp"], d["imagenes"]) == ([], [], [], [])
    assert (d["texto_imagen"], d["texto_pagina"], d["progreso"]) == ("", "", None)


def test_estado_del_radar_con_valores_danados():
    """Hora de fin de modo serie, historial y contadores ilegibles toman valores seguros."""
    d = rs._state_row_to_dict({"id": 1, "agy_serie_hasta": "mañana", "agy_historial": "{roto",
                               "contadores_dia": "[roto"})
    assert "id" not in d
    assert d["agy_serie_hasta"] is None
    assert d["agy_historial"] == []
    assert d["contadores_dia"] == rs._empty_counters()


def test_actualizar_post_valida_columnas_y_vacio():
    """Columnas desconocidas lanzan ValueError; sin campos no se actualiza nada."""
    with pytest.raises(ValueError, match="Columnas desconocidas"):
        rs.update_post(1, columna_inventada=1)
    assert rs.update_post(1) is False


def test_actualizar_estado_valida_columnas_y_vacio():
    """En radar_state, columnas desconocidas lanzan ValueError y sin campos es un no-op."""
    with pytest.raises(ValueError, match="radar_state"):
        rs.update_radar_state(columna_inventada=1)
    antes = rs.get_radar_state()
    rs.update_radar_state()
    assert rs.get_radar_state() == antes


def test_bordes_sin_datos():
    """Texto vacío nunca está duplicado y sin urns no hay nada por ver."""
    assert rs.existe_texto("   ") is False
    assert rs.filtrar_no_vistos(["", None]) == []


def test_post_con_imagenes_como_texto_se_guarda_tal_cual():
    """Si las imágenes vienen como texto JSON, se guardan sin reconvertir."""
    post = {"urn": "urn:li:activity:4242", "texto": "Buscamos analista en Acme", "autor": "Acme",
            "imagenes": '["https://media.licdn.com/dms/image/feedshare/a"]'}
    pid = rs.insert_post_if_new(post, query="analista")
    assert rs.get_post(pid)["imagenes"] == ["https://media.licdn.com/dms/image/feedshare/a"]


def test_inicios_de_bloqueo_sin_zona_se_toman_como_utc():
    """Un inicio de bloqueo guardado sin zona horaria se interpreta como UTC."""
    inicio = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None, microsecond=0)
    conn = rs._connect()
    try:
        with conn:
            columnas = {f["name"] for f in conn.execute("PRAGMA table_info(radar_lecturas)")}
            valores = {"query": "analista", "fuente": "busqueda", "inicio": inicio.isoformat(),
                       "motivo_fin": "bloqueo"}
            valores = {k: v for k, v in valores.items() if k in columnas}
            conn.execute(f"INSERT INTO radar_lecturas ({', '.join(valores)}) VALUES "
                         f"({', '.join('?' * len(valores))})", tuple(valores.values()))
    finally:
        conn.close()
    inicios = rs.inicios_bloqueo(datetime.now(timezone.utc) - timedelta(hours=1))
    assert inicios == [inicio.replace(tzinfo=timezone.utc)]
