"""Registro de errores del Radar: cada error guardado en un post queda en el historial,
agrupado por etapa y tipo, para aprender de los que se repiten."""
from pathlib import Path

from core import radar_store as store
from tests.test_radar_workers import crear_post


def test_cada_error_se_registra_con_su_etapa():
    a = crear_post(1, "NUEVO")
    b = crear_post(2, "GENERANDO")
    store.update_post(a, estado="ERROR", error="Falló la clasificación: Timeout 120 s")
    store.update_post(b, estado="ERROR", error="Falló la generación: formato de CV esperado (línea 3)")
    store.update_post(b, estado="GENERANDO", error=None)  # limpiar no registra
    store.update_post(b, estado="ERROR", error="Falló la generación: formato de CV esperado (línea 9)")
    grupos = store.errores_por_tipo()
    assert grupos[0]["etapa"] == "generacion" and grupos[0]["veces"] == 2 and grupos[0]["posts"] == 1
    assert grupos[0]["tipo"] == "Falló la generación: formato de CV esperado"
    assert grupos[1]["etapa"] == "clasificacion" and grupos[1]["tipo"] == "Falló la clasificación: Timeout N s"
    assert grupos[1]["post_ids"] == [a]


def test_tipo_sin_datos_variables():
    assert store.tipo_error("Gmail rechazó rrhh@example.com en https://x.y/z tras 3 intentos") == \
        "Gmail rechazó … en … tras N intentos"
    assert store.tipo_error("") == "Sin detalle"


def test_procesos_y_panel_muestran_los_errores():
    from core.procesos import armar_procesos
    pid = crear_post(1, "OFERTA")
    store.update_post(pid, estado="ERROR", error="Falló el análisis: respuesta vacía")
    errores = armar_procesos(None)["errores"]
    assert errores and errores[0]["etapa"] == "analisis"
    js = (Path(__file__).resolve().parent.parent / "static" / "radar.js").read_text(encoding="utf-8")
    assert "renderErrores(lista, data.errores)" in js and "Errores pendientes por tipo" in js


def test_el_panel_muestra_solo_errores_pendientes():
    """los errores ya resueltos (el post salió de ERROR) no se muestran; el historial queda."""
    from core.procesos import armar_procesos
    a = crear_post(1, "NUEVO")
    b = crear_post(2, "GENERANDO")
    store.update_post(a, estado="ERROR", error="Triage falló (1/3): La respuesta del LLM no contiene la lista")
    store.update_post(b, estado="ERROR", error="Falló la generación: timeout 600 s")
    store.update_post(a, estado="ANALIZADA", error=None)  # se resolvió
    pendientes = armar_procesos(None)["errores"]
    assert [g["post_ids"] for g in pendientes] == [[b]]
    assert pendientes[0]["etapa"] == "generacion" and pendientes[0]["veces"] == 1
    assert len(store.errores_por_tipo()) == 2  # el historial conserva los dos
