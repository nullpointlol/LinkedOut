"""Datos del portal dev LinkedOut (`tools/dev_datos.py`, 13/11): copia de `data/` sin las sesiones de LinkedIn
ni el `.env`, las bases copiadas con la API de backup de SQLite, el Radar detenido y en pausa, y un `.env` propio
con el mail en modo prueba. Todo sobre carpetas de prueba: nunca `data/` ni el `.env` reales."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import dev_datos  # noqa: E402

MAIL_PRUEBA = "pruebas@example.org"


@pytest.fixture
def origen(tmp_path):
    """Una carpeta de datos «real» simulada, con todo lo que tiene la de verdad."""
    d = tmp_path / "datos-reales-simulados"
    (d / "candidato").mkdir(parents=True)
    (d / "candidato" / "busquedas.json").write_text(json.dumps({"busquedas": []}), encoding="utf-8")
    for perfil in ("linkedin_profile", "linkedin_profile_2", "desktop_profile", "backups"):
        (d / perfil).mkdir()
        (d / perfil / "Cookies").write_text("secreto", encoding="utf-8")
    (d / ".env").write_text("GMAIL_APP_PASSWORD=no-copiar", encoding="utf-8")
    (d / "acceso_celular.key").write_text("clave", encoding="utf-8")
    (d / "tickets").mkdir()
    conn = sqlite3.connect(d / "postulaciones.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE radar_state (id INTEGER PRIMARY KEY, desired_running INTEGER, estado TEXT, "
                 "busquedas_activas INTEGER, analisis_pausado INTEGER, generacion_pausada INTEGER, "
                 "backlog_activo INTEGER)")
    conn.execute("INSERT INTO radar_state VALUES (1, 1, 'CORRIENDO', 1, 0, 0, 1)")
    conn.execute("CREATE TABLE linkedin_posts (id INTEGER PRIMARY KEY, texto TEXT)")
    conn.executemany("INSERT INTO linkedin_posts (texto) VALUES (?)", [(f"post {i}",) for i in range(50)])
    conn.commit()  # queda en el WAL: la copia tiene que traerlo igual
    tickets = sqlite3.connect(d / "tickets" / "tickets.db")
    tickets.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY)")
    tickets.commit()
    tickets.close()
    yield d
    conn.close()


def test_copia_sin_sesiones_de_linkedin_ni_secretos(origen, tmp_path):
    destino = tmp_path / "dev-datos"
    dev_datos.copiar_datos(origen, destino)
    nombres = {p.name for p in destino.rglob("*")}
    assert not [n for n in nombres if n.startswith("linkedin_profile")]
    assert "desktop_profile" not in nombres and "backups" not in nombres
    assert ".env" not in nombres and "acceso_celular.key" not in nombres
    assert (destino / "candidato" / "busquedas.json").is_file()
    copia = sqlite3.connect(destino / "postulaciones.db")
    assert copia.execute("SELECT COUNT(*) FROM linkedin_posts").fetchone()[0] == 50
    assert copia.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copia.close()
    assert (destino / "tickets" / "tickets.db").is_file()


def test_preparar_deja_el_radar_detenido_y_en_pausa(origen, tmp_path):
    destino = tmp_path / "dev-datos"
    dev_datos.copiar_datos(origen, destino)
    dev_datos.preparar(destino)
    fila = sqlite3.connect(destino / "postulaciones.db").execute(
        "SELECT desired_running, estado, busquedas_activas, analisis_pausado, generacion_pausada, backlog_activo "
        "FROM radar_state").fetchone()
    assert fila == (0, "DETENIDO", 0, 1, 1, 0)
    original = sqlite3.connect(origen / "postulaciones.db").execute("SELECT desired_running FROM radar_state")
    assert original.fetchone()[0] == 1  # la base de origen no se tocó


def test_env_propio_con_gmail_copiado_y_mail_de_prueba(tmp_path):
    real = tmp_path / "real.env"
    real.write_text("GMAIL_ADDRESS=yo@example.com\nGMAIL_APP_PASSWORD=abcd efgh\nJOOBLE_API_KEY=clave-de-prueba\nOTRA=no\n",
                    encoding="utf-8")
    destino = tmp_path / "dev.env"
    dev_datos.escribir_env(real, destino, MAIL_PRUEBA)
    lineas = destino.read_text(encoding="utf-8").splitlines()
    assert "GMAIL_ADDRESS=yo@example.com" in lineas and "GMAIL_APP_PASSWORD=abcd efgh" in lineas
    assert "JOOBLE_API_KEY=clave-de-prueba" in lineas
    assert f"LINKEDOUT_MAIL_PRUEBA={MAIL_PRUEBA}" in lineas
    assert not [l for l in lineas if l.startswith("OTRA")]


def test_env_sin_pedir_el_modo_prueba_no_lleva_mail_de_prueba(tmp_path):
    # Creador, 15/11: el modo prueba no va por defecto (6 mails salieron a la casilla de prueba)
    real = tmp_path / "real.env"
    real.write_text("GMAIL_ADDRESS=yo@example.com\n", encoding="utf-8")
    destino = tmp_path / "dev.env"
    dev_datos.escribir_env(real, destino)
    assert not [l for l in destino.read_text(encoding="utf-8").splitlines() if "MAIL_PRUEBA" in l]
    assert dev_datos.main(["env", "--real", str(real), "--destino", str(destino)]) == 0
    assert "MAIL_PRUEBA" not in destino.read_text(encoding="utf-8")


def test_env_sin_mail_de_prueba_valido_no_se_escribe(tmp_path):
    real = tmp_path / "real.env"
    real.write_text("GMAIL_ADDRESS=yo@example.com\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dev_datos.escribir_env(real, tmp_path / "dev.env", "no-es-un-mail")
    assert not (tmp_path / "dev.env").exists()


@pytest.mark.parametrize("caso", ["igual", "adentro", "contiene"])
def test_nunca_copia_sobre_los_datos_de_origen(origen, caso):
    destino = {"igual": origen, "adentro": origen / "beta", "contiene": origen.parent}[caso]
    with pytest.raises(ValueError):
        dev_datos.copiar_datos(origen, destino)
    assert (origen / "postulaciones.db").is_file() and (origen / "linkedin_profile").is_dir()


def test_env_con_mail_de_prueba_propio_usa_la_casilla_del_remitente(tmp_path):
    # 17/11: el dev es solo de prueba; por defecto los mails van a la propia casilla, nunca a los destinatarios
    real = tmp_path / "real.env"
    real.write_text("GMAIL_ADDRESS=yo@example.com\n", encoding="utf-8")
    destino = tmp_path / "dev.env"
    dev_datos.escribir_env(real, destino, "propio")
    assert "LINKEDOUT_MAIL_PRUEBA=yo@example.com" in destino.read_text(encoding="utf-8").splitlines()


def test_env_propio_sin_remitente_no_se_escribe(tmp_path):
    real = tmp_path / "real.env"
    real.write_text("JOOBLE_API_KEY=x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dev_datos.escribir_env(real, tmp_path / "dev.env", "propio")
    assert not (tmp_path / "dev.env").exists()
