"""Copias consistentes de bases SQLite para tools/backup_local.ps1.

La base se abre de solo lectura (`mode=ro`) y se copia con la API de backup de SQLite: la copia es una foto
consistente aunque el portal esté escribiendo, y el original no se toca (ni checkpoint ni journal).

    python tools/backup_bases.py copiar <pares.json> <resultado.json>
        pares.json: [{"origen": "...", "destino": "..."}]; resultado.json: un registro por par.
    python tools/backup_bases.py verificar <base> [<base> ...]
        Imprime en JSON el integrity_check de cada base (abierta de solo lectura).

Un archivo que no es SQLite queda con `estado = "no_sqlite"`; uno que no se puede abrir (bloqueado, journal
pendiente) queda con `estado = "error"` y su motivo: el script de PowerShell lo incluye en crudo y lo anota.
"""
import json
import sqlite3
import sys
from pathlib import Path

CABECERA_SQLITE = b"SQLite format 3\x00"
ESPERA_LOCK_S = 60  # solo contra bloqueos: la copia en sí es inmediata


def es_sqlite(ruta) -> bool:
    try:
        with open(ruta, "rb") as f:
            return f.read(16) == CABECERA_SQLITE
    except OSError:
        return False


def _abrir_solo_lectura(ruta, espera_s: float) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(ruta).resolve().as_uri()}?mode=ro", uri=True, timeout=espera_s)


def integridad(ruta, espera_s: float = ESPERA_LOCK_S) -> str:
    conn = _abrir_solo_lectura(ruta, espera_s)
    try:
        return "; ".join(str(fila[0]) for fila in conn.execute("PRAGMA integrity_check"))
    finally:
        conn.close()


def copiar_base(origen, destino, espera_s: float = ESPERA_LOCK_S) -> dict:
    registro = {"origen": str(origen), "destino": str(destino)}
    if not es_sqlite(origen):
        return {**registro, "estado": "no_sqlite"}
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        fuente = _abrir_solo_lectura(origen, espera_s)
        try:
            # Connection.backup reintenta sin fin si la base está bloqueada (p. ej. Edge con locking_mode EXCLUSIVE).
            # Se toma antes la lectura, que sí respeta el timeout; el backup usa esa misma foto.
            fuente.execute("BEGIN")
            fuente.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            copia = sqlite3.connect(destino)
            try:
                fuente.backup(copia)
            finally:
                copia.close()
        finally:
            fuente.close()
        # La copia es nuestra: se abre normal para que al cerrar no queden un -wal vacío ni un -shm al lado
        conn = sqlite3.connect(destino)
        try:
            estado_integridad = "; ".join(str(fila[0]) for fila in conn.execute("PRAGMA integrity_check"))
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as e:
        destino.unlink(missing_ok=True)
        return {**registro, "estado": "error", "motivo": f"{type(e).__name__}: {e}"}
    return {**registro, "estado": "copiada", "integridad": estado_integridad, "bytes": destino.stat().st_size}


def main(argv) -> int:
    if len(argv) == 3 and argv[0] == "copiar":
        pares = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
        resultado = [copiar_base(p["origen"], p["destino"], float(p.get("espera_s", ESPERA_LOCK_S))) for p in pares]
        Path(argv[2]).write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    if len(argv) >= 2 and argv[0] == "verificar":
        salida = []
        for ruta in argv[1:]:
            try:
                salida.append({"base": ruta, "integridad": integridad(ruta)})
            except (sqlite3.Error, OSError) as e:
                salida.append({"base": ruta, "integridad": f"error: {type(e).__name__}: {e}"})
        print(json.dumps(salida))  # ASCII: la consola de Windows no siempre es UTF-8
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
