"""Datos del portal dev LinkedOut (13/11). Lo usa tools/dev.ps1.

    python tools/dev_datos.py copiar --origen <data real> --destino <dev-datos>
    python tools/dev_datos.py preparar --datos <dev-datos>
    python tools/dev_datos.py env --real <.env real> --destino <dev.env> --prueba <mail>

- copiar: copia la carpeta de datos sin las sesiones de LinkedIn (`linkedin_profile*`), sin `desktop_profile`,
  sin backups, sin `.env` y sin la clave del acceso desde el celular. Las bases se copian con la API de backup de
  SQLite (consistentes aunque el portal real esté escribiendo; el origen se abre solo para leer).
- preparar: en la base copiada, el Radar queda detenido, sin búsquedas, con el análisis y la generación en pausa.
- env: `.env` propio con las credenciales de Gmail y la clave de Jooble copiadas del real y `LINKEDOUT_MAIL_PRUEBA`: todo envío del
  dev va a la dirección de prueba. Nunca imprime los valores.
"""
import argparse
import re
import shutil
import sqlite3
import sys
from pathlib import Path

# Lo que nunca va al dev
EXCLUIDOS = ("linkedin_profile*", "desktop_profile", "backups", ".env", "*.env", "acceso_celular.key",
             "*.db-wal", "*.db-shm", "*.db-journal")
CLAVES_COPIADAS = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "JOOBLE_API_KEY")
_RE_MAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _excluido(nombre: str) -> bool:
    return any(Path(nombre).match(patron) for patron in EXCLUIDOS)


def _validar_destino(origen: Path, destino: Path) -> None:
    o, d = origen.resolve(), destino.resolve()
    if d == o or o in d.parents or d in o.parents:
        raise ValueError(f"El destino {d} no puede ser, contener ni estar adentro de los datos de origen {o}")


def _copiar_base(origen: Path, destino: Path) -> None:
    if origen.stat().st_size == 0:
        destino.write_bytes(b"")
        return
    fuente = sqlite3.connect(f"{origen.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copia = sqlite3.connect(destino)
        try:
            fuente.backup(copia)
        finally:
            copia.close()
    finally:
        fuente.close()


def copiar_datos(origen: Path, destino: Path) -> None:
    """Copia `origen` en `destino` (que se borra antes si existe) sin lo excluido."""
    origen, destino = Path(origen), Path(destino)
    _validar_destino(origen, destino)
    if not origen.is_dir():
        raise ValueError(f"No existe la carpeta de datos de origen {origen}")
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    def copiar(carpeta: Path, hacia: Path) -> None:
        for item in carpeta.iterdir():
            if _excluido(item.name):
                continue
            objetivo = hacia / item.name
            if item.is_dir():
                objetivo.mkdir()
                copiar(item, objetivo)
            elif item.suffix == ".db":
                _copiar_base(item, objetivo)
            else:
                shutil.copy2(item, objetivo)

    copiar(origen, destino)


def preparar(datos: Path) -> None:
    """Radar detenido, sin búsquedas, análisis y generación en pausa (solo las columnas que existan)."""
    base = Path(datos) / "postulaciones.db"
    if not base.is_file():
        return
    conn = sqlite3.connect(base)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_state'").fetchone():
            return
        columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(radar_state)")}
        valores = {"desired_running": 0, "estado": "DETENIDO", "busquedas_activas": 0, "analisis_pausado": 1,
                   "generacion_pausada": 1, "backlog_activo": 0}
        cambios = {k: v for k, v in valores.items() if k in columnas}
        with conn:
            conn.execute(f"UPDATE radar_state SET {', '.join(f'{k} = ?' for k in cambios)} WHERE id = 1",
                         list(cambios.values()))
    finally:
        conn.close()


def escribir_env(real: Path, destino: Path, mail_prueba: str = "") -> None:
    """`.env` del dev: GMAIL_* y JOOBLE_API_KEY copiadas del real. LINKEDOUT_MAIL_PRUEBA solo si se pide
    (creador, 15/11: el modo prueba no va por defecto; con él, los mails salieron a la casilla de prueba)."""
    real, destino = Path(real), Path(destino)
    if real.resolve() == destino.resolve():
        raise ValueError("El .env del dev no puede ser el .env real")
    valores = {}
    if real.is_file():
        for linea in real.read_text(encoding="utf-8-sig").splitlines():
            clave, sep, valor = linea.partition("=")
            if sep and clave.strip() in CLAVES_COPIADAS:
                valores[clave.strip()] = valor.strip()
    if mail_prueba == "propio":  # 17/11: el dev es de prueba; sus mails van a la casilla del remitente
        mail_prueba = valores.get("GMAIL_ADDRESS", "")
        if not mail_prueba:
            raise ValueError("Modo prueba «propio» sin GMAIL_ADDRESS en el .env real")
    if mail_prueba and not _RE_MAIL.match(mail_prueba):
        raise ValueError("La dirección de LINKEDOUT_MAIL_PRUEBA no es válida")
    lineas = [f"{clave}={valores[clave]}" for clave in CLAVES_COPIADAS if clave in valores]
    if mail_prueba:
        lineas.append(f"LINKEDOUT_MAIL_PRUEBA={mail_prueba}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    c = sub.add_parser("copiar")
    c.add_argument("--origen", required=True)
    c.add_argument("--destino", required=True)
    pr = sub.add_parser("preparar")
    pr.add_argument("--datos", required=True)
    e = sub.add_parser("env")
    e.add_argument("--real", required=True)
    e.add_argument("--destino", required=True)
    e.add_argument("--prueba", default="", help="dirección de prueba del mail, o «propio» (la casilla del remitente)")
    a = p.parse_args(argv)
    try:
        if a.accion == "copiar":
            copiar_datos(Path(a.origen), Path(a.destino))
        elif a.accion == "preparar":
            preparar(Path(a.datos))
        else:
            escribir_env(Path(a.real), Path(a.destino), a.prueba)
    except (ValueError, OSError, sqlite3.Error) as e:
        print(f"dev_datos {a.accion}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
