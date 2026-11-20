"""Backup completo del portal local (tools/backup_local.ps1).

El 7z tiene datos personales, sesiones de LinkedIn y el .env: nunca entra a git. Las bases se copian consistentes
(API de backup de SQLite, origen abierto de solo lectura) aunque el portal esté escribiendo, sin tocar el original.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "tools" / "backup_local.ps1"
POWERSHELL = shutil.which("powershell")
sys.path.insert(0, str(RAIZ / "tools"))
import backup_bases  # noqa: E402


def test_el_script_de_backup_existe_con_bom():
    assert SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(POWERSHELL is None, reason="sin Windows PowerShell")
def test_el_script_de_backup_parsea_en_powershell_5():
    comando = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{SCRIPT}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.Extent.StartLineNumber.ToString() + ': ' + $_.Message }}"
    )
    salida = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", comando],
                            capture_output=True, text=True, timeout=60)
    assert salida.stdout.strip() == "", f"backup_local.ps1 no parsea:\n{salida.stdout}"


def test_backups_locales_y_7z_estan_en_gitignore():
    lineas = [l.strip() for l in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "backups_locales/" in lineas
    assert "*.7z" in lineas


@pytest.mark.skipif(shutil.which("git") is None, reason="sin git")
def test_git_ignora_un_7z_dentro_de_backups_locales():
    r = subprocess.run(["git", "-C", str(RAIZ), "check-ignore", "-q", "backups_locales/linkedout-local-20261112-000000.7z"],
                       timeout=60)
    assert r.returncode == 0


def _base_wal_con_datos_sin_checkpoint(ruta: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ruta)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, texto TEXT)")
    conn.executemany("INSERT INTO posts (texto) VALUES (?)", [(f"post {i}",) for i in range(50)])
    conn.commit()
    return conn  # queda abierta, como el portal: lo último está solo en el -wal


def test_copia_consistente_incluye_lo_del_wal_y_no_toca_el_original(tmp_path):
    carpeta = tmp_path / "origen"
    carpeta.mkdir()
    origen = carpeta / "postulaciones.db"
    conn = _base_wal_con_datos_sin_checkpoint(origen)
    try:
        antes = {p.name: p.read_bytes() for p in carpeta.iterdir() if p.name != "postulaciones.db-shm"}
        assert "postulaciones.db-wal" in antes
        destino = tmp_path / "copia" / "postulaciones.db"
        r = backup_bases.copiar_base(origen, destino)
        assert r["estado"] == "copiada" and r["integridad"] == "ok"
        assert sorted(p.name for p in destino.parent.iterdir()) == ["postulaciones.db"], "quedó un -wal o -shm al lado de la copia"
        assert sqlite3.connect(destino).execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 50
        despues = {p.name: p.read_bytes() for p in carpeta.iterdir() if p.name != "postulaciones.db-shm"}
        assert despues == antes, "la copia modificó la base original o su -wal"
    finally:
        conn.close()


def test_un_archivo_que_no_es_sqlite_queda_en_crudo(tmp_path):
    falso = tmp_path / "notas.db"
    falso.write_text("no soy una base", encoding="utf-8")
    assert backup_bases.copiar_base(falso, tmp_path / "x.db")["estado"] == "no_sqlite"
    assert not (tmp_path / "x.db").exists()


def test_una_base_bloqueada_da_error_sin_dejar_copia(tmp_path):
    origen = tmp_path / "bloqueada.db"
    conn = sqlite3.connect(origen, isolation_level=None)
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")  # con locking_mode EXCLUSIVE el lock queda tomado
    try:
        r = backup_bases.copiar_base(origen, tmp_path / "copia.db", espera_s=0.2)
        assert r["estado"] == "error"
        assert not (tmp_path / "copia.db").exists()
    finally:
        conn.close()


def test_cli_copiar_y_verificar(tmp_path):
    origen = tmp_path / "tickets.db"
    conn = _base_wal_con_datos_sin_checkpoint(origen)
    conn.close()
    pares = tmp_path / "pares.json"
    destino = tmp_path / "out" / "tickets.db"
    pares.write_text(json.dumps([{"origen": str(origen), "destino": str(destino)}]), encoding="utf-8")
    resultado = tmp_path / "res.json"
    assert backup_bases.main(["copiar", str(pares), str(resultado)]) == 0
    assert json.loads(resultado.read_text(encoding="utf-8"))[0]["estado"] == "copiada"
    salida = subprocess.run([sys.executable, "-I", str(RAIZ / "tools" / "backup_bases.py"), "verificar", str(destino)],
                            capture_output=True, text=True, timeout=60)
    assert json.loads(salida.stdout) == [{"base": str(destino), "integridad": "ok"}]


# ---------------------------------------------------------------- Save-SesionesEdge (tools/despliegue_comun.ps1)
COMUN = RAIZ / "tools" / "despliegue_comun.ps1"


def _cookies_sqlite(ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
    conn.execute("INSERT INTO cookies VALUES ('.linkedin.com', 'li_at')")
    conn.commit()
    conn.close()


def _guardar_sesiones(datos: Path, registro: Path, espera_s: int = 2):
    comando = (f". '{COMUN}'; $cfg = @{{ datos = '{datos}'; registro = '{registro}' }}; "
               f"Save-SesionesEdge $cfg {espera_s} 3; exit 0")
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", comando],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


@pytest.mark.skipif(POWERSHELL is None, reason="sin Windows PowerShell")
def test_el_deploy_copia_las_cookies_y_deja_las_3_ultimas(tmp_path):
    datos = tmp_path / "datos"
    _cookies_sqlite(datos / "linkedin_profile" / "Default" / "Network" / "Cookies")
    sesiones = datos / "backups" / "sesiones"
    sesiones.mkdir(parents=True)
    for i in range(3):
        (sesiones / f"linkedin_profile-Cookies-20260101-00000{i}").write_bytes(b"vieja")
    registro = tmp_path / "despliegues.log"
    r = _guardar_sesiones(datos, registro)
    assert r.returncode == 0, r.stdout + r.stderr
    copias = sorted(p.name for p in sesiones.iterdir())
    assert len(copias) == 3 and "linkedin_profile-Cookies-20260101-000000" not in copias
    nueva = sesiones / copias[-1]
    assert backup_bases.integridad(nueva) == "ok"
    assert sqlite3.connect(nueva).execute("SELECT name FROM cookies").fetchone() == ("li_at",)
    assert "Sesión de Edge copiada: linkedin_profile" in registro.read_text(encoding="utf-8-sig")


@pytest.mark.skipif(POWERSHELL is None, reason="sin Windows PowerShell")
def test_una_cookie_tomada_no_corta_el_deploy(tmp_path):
    datos = tmp_path / "datos"
    cookies = datos / "linkedin_profile" / "Default" / "Network" / "Cookies"
    _cookies_sqlite(cookies)
    conn = sqlite3.connect(cookies, isolation_level=None)
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("INSERT INTO cookies VALUES ('x', 'y')")  # el lock queda tomado, como Edge
    try:
        registro = tmp_path / "despliegues.log"
        r = _guardar_sesiones(datos, registro, espera_s=1)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Sesión de Edge NO copiada: linkedin_profile" in registro.read_text(encoding="utf-8-sig")
        assert not any((datos / "backups" / "sesiones").glob("linkedin_profile-Cookies-*"))
    finally:
        conn.close()
