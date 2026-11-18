"""Procesos temporales de las sesiones (portal de prueba, suite, espera), con plazo (creador, 16/11).

Todo lo que lance una sesión va anotado con su vencimiento en `~/LinkedOut/procesos-temporales.json`; el guardián
(`tools/guardian_procesos.py`) apaga lo vencido.

    python tools/temporal.py lanzar --minutos 60 --motivo "portal de prueba 1197" -- python -m uvicorn server:app --port 1197
    python tools/temporal.py anotar --pid 1234 --minutos 30 --motivo "suite en segundo plano"
    python tools/temporal.py detener --pid 1234
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BASE = Path(os.environ.get("LINKEDOUT_BASE") or Path.home() / "LinkedOut")
REGISTRO = BASE / "procesos-temporales.json"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def leer() -> List[Dict]:
    try:
        datos = json.loads(REGISTRO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in datos if isinstance(d, dict) and isinstance(d.get("pid"), int)] if isinstance(datos, list) else []


def _guardar(datos: List[Dict]) -> None:
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    temporal = REGISTRO.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, REGISTRO)


def anotar(pid: int, minutos: int, motivo: str, ahora: Optional[datetime] = None) -> Dict:
    ahora = ahora or datetime.now()
    entrada = {"pid": int(pid), "motivo": motivo, "desde": ahora.isoformat(),
               "vence": (ahora + timedelta(minutes=int(minutos))).isoformat()}
    _guardar([d for d in leer() if d["pid"] != entrada["pid"]] + [entrada])
    return entrada


def limpiar(vivos: Iterable[int]) -> None:
    """Saca del registro lo que ya no existe."""
    vivos = set(vivos)
    datos = leer()
    quedan = [d for d in datos if d["pid"] in vivos]
    if quedan != datos:
        _guardar(quedan)


def detener(pid: int) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=SIN_VENTANA)
    _guardar([d for d in leer() if d["pid"] != int(pid)])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="accion", required=True)
    lanzar = sub.add_parser("lanzar")
    lanzar.add_argument("--minutos", type=int, required=True)
    lanzar.add_argument("--motivo", required=True)
    lanzar.add_argument("comando", nargs=argparse.REMAINDER)
    anotar_p = sub.add_parser("anotar")
    anotar_p.add_argument("--pid", type=int, required=True)
    anotar_p.add_argument("--minutos", type=int, required=True)
    anotar_p.add_argument("--motivo", required=True)
    detener_p = sub.add_parser("detener")
    detener_p.add_argument("--pid", type=int, required=True)
    sub.add_parser("listar")
    a = ap.parse_args(argv)
    if a.accion == "lanzar":
        comando = a.comando[1:] if a.comando[:1] == ["--"] else a.comando
        if not comando:
            ap.error("falta el comando después de --")
        proc = subprocess.Popen(comando, creationflags=SIN_VENTANA)
        anotar(proc.pid, a.minutos, a.motivo)
        print(proc.pid)
    elif a.accion == "anotar":
        anotar(a.pid, a.minutos, a.motivo)
    elif a.accion == "detener":
        detener(a.pid)
    else:
        print(json.dumps(leer(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
