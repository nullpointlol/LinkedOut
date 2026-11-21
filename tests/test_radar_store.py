"""Tests de persistencia del Radar LinkedIn (linkedin_posts / radar_state)."""
from datetime import datetime, timedelta

from core.database import create_postulacion, list_postulaciones
from core.radar_store import (
    bump_counter,
    claim_for_sending,
    es_canal_email,
    mark_applied,
    get_post,
    get_radar_state,
    filtrar_no_vistos,
    insert_post_if_new,
    list_posts,
    envio_previo_a,
    recipient_sent_within,
    reconcile_on_startup,
    registrar_vistos,
    update_post,
    urns_vistos,
    update_radar_state,
)


def _post(n: int = 1, **over) -> dict:
    p = {
        "urn": f"urn:li:activity:{n}",
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{n}",
        "autor": "Ana Pérez",
        "cargo_autor": "HR Manager",
        "texto": "Buscamos analista funcional, enviar cv a rrhh@x.test",
        "fecha_rel": "3 h",
        "emails": ["rrhh@x.test"],
    }
    p.update(over)
    return p


def test_insert_post_dedup_by_urn():
    p = _post(1, emails=["a@b.test"])
    first = insert_post_if_new(p, query="enviar cv")
    assert isinstance(first, int)
    assert insert_post_if_new(p, query="otra") is None
    assert get_post(first)["emails"] == ["a@b.test"]
    assert get_post(first)["estado"] == "NUEVO"
    assert get_post(first)["query"] == "enviar cv"


def test_get_post_missing_returns_none():
    assert get_post(9999) is None


def test_update_post_serializes_json_and_touches_updated_at():
    pid = insert_post_if_new(_post(1), query="q")
    sentinel = "2000-01-01 00:00:00"
    update_post(pid, updated_at=sentinel)
    assert get_post(pid)["updated_at"] == sentinel
    update_post(pid, triage={"match": 80, "motivos": ["x"]}, emails=["z@z.test"], intentos=2)
    post = get_post(pid)
    assert post["triage"] == {"match": 80, "motivos": ["x"]}
    assert post["emails"] == ["z@z.test"]
    assert post["intentos"] == 2
    assert post["updated_at"] > sentinel


def test_list_posts_order_and_filters():
    ids = [insert_post_if_new(_post(i), query="q") for i in range(1, 5)]
    update_post(ids[1], estado="LISTO")
    update_post(ids[3], estado="LISTO")
    assert [p["id"] for p in list_posts()] == sorted(ids, reverse=True)
    assert [p["id"] for p in list_posts(estado="LISTO")] == [ids[3], ids[1]]
    assert len(list_posts(limit=2)) == 2
    assert list_posts(estado="ENVIADO") == []


def test_list_posts_excluye_estado_sin_limite():
    ids = [insert_post_if_new(_post(i), query="q") for i in range(1, 6)]
    update_post(ids[0], estado="ENVIADO")
    update_post(ids[2], estado="DESCARTADO")
    update_post(ids[4], estado="DESCARTADO")
    assert [p["id"] for p in list_posts(excluir_estados=("DESCARTADO",))] == [ids[3], ids[1], ids[0]]


def test_radar_state_defaults():
    s = get_radar_state()
    assert s["desired_running"] is False and s["estado"] == "DETENIDO"
    assert (s["umbral_match"], s["tope_posts_dia"], s["tope_envios_dia"]) == (70, 600, 40)
    assert s["contadores_dia"] == {"leidos_busquedas": 0, "leidos_feed": 0, "con_email": 0, "match": 0, "listos": 0, "enviados": 0, "postulados": 0}
    assert s["envios_pausados"] is False
    assert s["ciclos_vacios_consecutivos"] == 0
    assert s["next_cycle_at"] is None


def test_update_radar_state_booleans_roundtrip():
    update_radar_state(desired_running=True, envios_pausados=True, estado="CORRIENDO", estado_detalle="ok")
    s = get_radar_state()
    assert s["desired_running"] is True and s["envios_pausados"] is True
    assert s["estado"] == "CORRIENDO" and s["estado_detalle"] == "ok"


def test_bump_counter_returns_current_counters():
    assert bump_counter("leidos_busquedas", 5)["leidos_busquedas"] == 5
    counters = bump_counter("leidos_busquedas")
    assert counters["leidos_busquedas"] == 6
    assert get_radar_state()["contadores_dia"]["leidos_busquedas"] == 6


def test_bump_counter_rejects_unknown_name():
    import pytest
    with pytest.raises(ValueError):
        bump_counter("inexistente")


def test_counters_reset_on_new_day():
    bump_counter("leidos_busquedas", 5)
    update_radar_state(fecha_contadores="2000-01-01")
    assert get_radar_state()["contadores_dia"]["leidos_busquedas"] == 0
    # La fecha nueva queda guardada y los contadores siguen acumulando
    bump_counter("leidos_busquedas", 2)
    assert get_radar_state()["contadores_dia"]["leidos_busquedas"] == 2


def test_recipient_sent_within_window():
    pid = insert_post_if_new(_post(2), query="q")
    ahora = datetime.now()
    update_post(pid, estado="ENVIADO", email_to="rrhh@x.test",
                sent_at=(ahora - timedelta(days=10)).isoformat(sep=" ", timespec="seconds"))
    assert recipient_sent_within("RRHH@x.test") is True
    update_post(pid, sent_at=(ahora - timedelta(days=31)).isoformat(sep=" ", timespec="seconds"))
    assert recipient_sent_within("rrhh@x.test") is False


def test_recipient_sent_within_excludes_post_and_requires_enviado():
    pid = insert_post_if_new(_post(2), query="q")
    ahora = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    update_post(pid, estado="ENVIADO", email_to="rrhh@x.test", sent_at=ahora)
    assert recipient_sent_within("rrhh@x.test", exclude_post_id=pid) is False
    other = insert_post_if_new(_post(3), query="q")
    update_post(other, estado="LISTO", email_to="otro@x.test", sent_at=ahora)
    assert recipient_sent_within("otro@x.test") is False


def test_reconcile_marks_enviando_as_error():
    pid = insert_post_if_new(_post(1), query="q")
    update_post(pid, estado="ENVIANDO")
    ok = insert_post_if_new(_post(2), query="q")
    update_post(ok, estado="LISTO")
    reconcile_on_startup()
    assert get_post(pid)["estado"] == "ERROR" and "verificar en Gmail" in get_post(pid)["error"]
    assert get_post(ok)["estado"] == "LISTO"


def test_list_postulaciones_excludes_linkedin_by_default():
    create_postulacion("M", "E", "c")
    create_postulacion("L", "E", "c", origen="linkedin")
    assert [p["titulo"] for p in list_postulaciones()] == ["M"]
    assert len(list_postulaciones(origen=None)) == 2
    assert [p["titulo"] for p in list_postulaciones(origen="linkedin")] == ["L"]


def test_list_postulaciones_origen_combines_with_lifecycle_filter():
    create_postulacion("M", "E", "c")
    create_postulacion("L", "E", "c", origen="linkedin")
    assert [p["titulo"] for p in list_postulaciones(filtro_lifecycle="POR_POSTULAR")] == ["M"]
    assert len(list_postulaciones(filtro_lifecycle="POR_POSTULAR", origen=None)) == 2


# ------------------------------------------------- fuente, links, WhatsApp y canal


def test_insert_feed_post_roundtrips_fuente_links_and_whatsapp():
    pid = insert_post_if_new(
        _post(10, links=["https://forms.gle/x"], whatsapp=["5491155550000"]),
        query="feed",
        fuente="feed",
    )
    post = get_post(pid)
    assert post["fuente"] == "feed"
    assert post["links"] == ["https://forms.gle/x"]
    assert post["whatsapp"] == ["5491155550000"]
    assert post["canal"] == "" and post["destino_canal"] == ""


def test_insert_post_default_fuente_is_busqueda_with_empty_links():
    pid = insert_post_if_new(_post(11), query="q")
    post = get_post(pid)
    assert post["fuente"] == "busqueda"
    assert post["links"] == [] and post["whatsapp"] == []


def test_insert_post_rejects_unknown_fuente():
    import pytest

    with pytest.raises(ValueError):
        insert_post_if_new(_post(12), query="q", fuente="otra")


def test_list_posts_filters_by_fuente():
    a = insert_post_if_new(_post(1), query="q")
    b = insert_post_if_new(_post(2), query="feed", fuente="feed")
    c = insert_post_if_new(_post(3), query="feed", fuente="feed")
    assert [p["id"] for p in list_posts(fuente="feed")] == [c, b]
    assert [p["id"] for p in list_posts(fuente="busqueda")] == [a]
    assert [p["id"] for p in list_posts()] == [c, b, a]
    update_post(b, estado="LISTO")
    assert [p["id"] for p in list_posts(estado="LISTO", fuente="feed")] == [b]


def test_update_post_persists_canal_destino_links_and_whatsapp():
    pid = insert_post_if_new(_post(13), query="q", fuente="feed")
    update_post(pid, canal="whatsapp", destino_canal="549111555", links=["https://a.com/x"],
                whatsapp=["549111555"], fuente="feed")
    post = get_post(pid)
    assert post["canal"] == "whatsapp"
    assert post["destino_canal"] == "549111555"
    assert post["links"] == ["https://a.com/x"]
    assert post["whatsapp"] == ["549111555"]


def test_old_table_without_new_columns_is_migrated(tmp_path):
    import sqlite3

    db = tmp_path / "viejo.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE linkedin_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, urn TEXT UNIQUE NOT NULL, url TEXT, autor TEXT,
            cargo_autor TEXT, texto TEXT NOT NULL, fecha_rel TEXT, query TEXT, emails TEXT DEFAULT '[]',
            triage TEXT, estado TEXT DEFAULT 'NUEVO', postulacion_id INTEGER, email_to TEXT,
            email_subject TEXT, email_body TEXT, aprobado_at TIMESTAMP, sent_at TIMESTAMP,
            message_id TEXT, error TEXT, intentos INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO linkedin_posts (urn, texto) VALUES ('urn:viejo', 'texto viejo')")
    conn.commit()
    conn.close()

    post = list_posts(db_path=db)[0]
    assert post["urn"] == "urn:viejo"
    assert post["fuente"] == "busqueda"
    assert post["links"] == [] and post["whatsapp"] == []
    assert post["canal"] == "" and post["destino_canal"] == ""
    # Idempotente: una segunda conexión no falla por columnas duplicadas
    assert len(list_posts(db_path=db)) == 1


# ----------------------------------------------------------------- canales

def test_es_canal_email_normalizes_and_treats_empty_as_email():
    assert es_canal_email("email") and es_canal_email(" Email ") and es_canal_email("") and es_canal_email(None)
    assert es_canal_email({"canal": ""}) and es_canal_email({}) and es_canal_email({"canal": "EMAIL"})
    assert not es_canal_email("whatsapp") and not es_canal_email({"canal": " Link "})


def _listo_aprobado(n: int, canal: str) -> int:
    pid = insert_post_if_new(_post(n), "q")
    update_post(pid, estado="LISTO", aprobado_at="2026-11-01 09:00:00", canal=canal)
    return pid


def test_claim_for_sending_refuses_non_email_channels():
    for n, canal in enumerate(["link", "whatsapp", "dm", "otro", " Link "], start=1):
        pid = _listo_aprobado(n, canal)
        assert claim_for_sending(pid) is False
        assert get_post(pid)["estado"] == "LISTO"
    for n, canal in enumerate(["email", "", "EMAIL"], start=20):
        pid = _listo_aprobado(n, canal)
        assert claim_for_sending(pid) is True
        assert get_post(pid)["estado"] == "ENVIANDO"


def test_mark_applied_is_conditional_for_every_channel():
    pid = _listo_aprobado(1, "whatsapp")
    assert mark_applied(pid, "manual:whatsapp", "2026-11-01 10:00:00") is True
    post = get_post(pid)
    assert post["estado"] == "ENVIADO" and post["message_id"] == "manual:whatsapp"
    assert post["sent_at"] == "2026-11-01 10:00:00" and post["aprobado_at"] is None
    assert mark_applied(pid, "manual:whatsapp", "2026-11-01 11:00:00") is False  # ya no está LISTO
    assert get_post(pid)["sent_at"] == "2026-11-01 10:00:00"
    mail = _listo_aprobado(2, "")
    update_post(mail, email_to="rrhh@empresa.test")
    assert mark_applied(mail, "manual:", "2026-11-01 10:00:00") is True  # también por Gmail
    assert get_post(mail)["estado"] == "ENVIADO"
    enviando = _listo_aprobado(4, "email")
    update_post(enviando, estado="ENVIANDO")
    assert mark_applied(enviando, "manual:email", "2026-11-01 10:00:00") is False  # el envío ya salió
    assert get_post(enviando)["estado"] == "ENVIANDO"
    sin_destinatario = _listo_aprobado(3, "email")
    update_post(sin_destinatario, estado="ANALIZADA")
    assert mark_applied(sin_destinatario, "manual:email", "2026-11-01 10:00:00") is True


def test_postulados_counter_is_separate_from_enviados():
    bump_counter("postulados")
    c = get_radar_state()["contadores_dia"]
    assert c["postulados"] == 1 and c["enviados"] == 0


# ---------------------------------------------------------------- posts vistos (tope diario)

def test_registrar_vistos_dedups_and_counts_per_query_today():
    assert filtrar_no_vistos(["a", "b"]) == ["a", "b"]
    registrar_vistos(["a", "b"], "q1")
    registrar_vistos(["b", "c"], "feed")  # "b" ya estaba: no se vuelve a contar
    assert filtrar_no_vistos(["a", "c", "d"]) == ["d"]
    assert filtrar_no_vistos(["d", "d"]) == ["d"]
    assert urns_vistos() == {"a", "b", "c"}


def test_old_default_post_cap_is_migrated_to_600():
    from core import database
    conn = database.get_db_connection()
    try:
        with conn:
            conn.execute("UPDATE radar_state SET tope_posts_dia = 300 WHERE id = 1")
    finally:
        conn.close()
    assert get_radar_state()["tope_posts_dia"] == 600


# ------------------------------------------------- circuito nuevo: análisis, prioridad, borrar

def test_post_analisis_y_prioridad_se_guardan_y_leen():
    from core.radar_store import delete_post  # noqa: F401  (existe)
    pid = insert_post_if_new(_post(1), "feed", fuente="feed")
    p = get_post(pid)
    assert p["analisis"] is None and p["prioridad"] == 0
    update_post(pid, analisis={"match_score": 72.5, "requisitos": []}, prioridad=1)
    p = get_post(pid)
    assert p["analisis"] == {"match_score": 72.5, "requisitos": []} and p["prioridad"] == 1


def test_delete_post_borra_la_fila():
    from core.radar_store import delete_post
    pid = insert_post_if_new(_post(1), "feed", fuente="feed")
    delete_post(pid)
    assert get_post(pid) is None and list_posts() == []


def test_reconcile_devuelve_analizando_a_oferta():
    pid = insert_post_if_new(_post(1), "q")
    update_post(pid, estado="ANALIZANDO")
    reconcile_on_startup()
    assert get_post(pid)["estado"] == "OFERTA"


def _previo(n, email, estado, dias=None, aprobado=False, puesto="Dev", empresa="Acme"):
    pid = insert_post_if_new(_post(n), query="q")
    campos = dict(estado=estado, email_to=email, triage={"puesto": puesto, "empresa": empresa})
    if dias is not None:
        campos["sent_at"] = (datetime.now() - timedelta(days=dias)).isoformat(sep=" ", timespec="seconds")
    if aprobado:
        campos["aprobado_at"] = "2026-11-01 10:00:00"
    update_post(pid, **campos)
    return pid


def test_envio_previo_a_prefiere_el_enviado_mas_reciente_dentro_de_la_ventana():
    _previo(40, "rrhh@x.test", "ENVIADO", dias=20, puesto="Viejo")
    reciente = _previo(41, "rrhh@x.test", "ENVIADO", dias=3, puesto="Nuevo", empresa="Beta")
    _previo(42, "rrhh@x.test", "ENVIADO", dias=45, puesto="Fuera de ventana")
    previo = envio_previo_a("RRHH@x.test", 30)
    assert previo["post_id"] == reciente
    assert previo["puesto"] == "Nuevo" and previo["empresa"] == "Beta"
    assert previo["estado"] == "ENVIADO" and previo["sent_at"]


def test_envio_previo_a_encuentra_enviando_y_listo_aprobado_sin_sent_at():
    en_curso = _previo(43, "cola@x.test", "ENVIANDO")
    previo = envio_previo_a("cola@x.test", 30)
    assert previo == {"post_id": en_curso, "puesto": "Dev", "empresa": "Acme", "sent_at": None, "estado": "ENVIANDO"}
    solo_listo = _previo(44, "listo@x.test", "LISTO")  # sin aprobar: no cuenta
    assert envio_previo_a("listo@x.test", 30) is None
    aprobado = _previo(45, "listo@x.test", "LISTO", aprobado=True)
    assert envio_previo_a("listo@x.test", 30)["post_id"] == aprobado
    assert envio_previo_a("listo@x.test", 30, exclude_post_id=aprobado) is None
    assert solo_listo != aprobado


def test_envio_previo_a_sin_coincidencias_devuelve_none():
    assert envio_previo_a("nadie@x.test", 30) is None
    assert envio_previo_a("", 30) is None


def test_una_seleccion_invalida_de_la_ia_se_reintenta_sola():
    from core.radar_store import candidatos_reintento_generacion
    pid = insert_post_if_new(_post(91), "q")
    update_post(pid, estado="ERROR", error="Falló la generación: El Redactor no generó el CV (ValueError: la selección "
                                           "puntuó 0 de 35 viñetas y 0 de 53 ítems): reintentá")
    assert [p["id"] for p in candidatos_reintento_generacion(3, "9999-12-31")] == [pid]
