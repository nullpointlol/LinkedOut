"""Guardián de procesos (creador, 16/11: «que no haya procesos inútiles e innecesarios NUNCA»).

Corre cada 10 minutos (tarea «LinkedOut-guardian»). Solo existen:
- producción 1111 con la marca `~/LinkedOut/produccion-activa` y lo que cuelga de ella (Edge del Radar y de los
  portales, AGY);
- el dev 1112, solo con la marca `~/LinkedOut/dev-activo` (la deja `dev.ps1 -Iniciar` y la quita `-Detener`;
  creador, 17/11: producción siempre prendida y el dev apagado hasta que haga falta);
- los temporales vigentes de `tools/temporal.py` y lo que cuelga de ellos.
Apaga (con su árbol) los portales sin permiso, los Edge de LinkedOut sin portal vivo, los AGY huérfanos o de más de
`MAX_AGY_MIN`, los pytest de más de `MAX_PYTEST_MIN` y los temporales vencidos. Nunca mira otros programas.
Cada apagado queda en `~/LinkedOut/guardian.log`. `--simular` solo lista.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import temporal  # noqa: E402

PUERTO_DEV = 1112
PUERTO_PROD = 1111
MAX_AGY_MIN = 15      # AGY corta a los 420 s: con 15 min ya quedó colgado
MAX_PYTEST_MIN = 45   # la suite completa tarda unos 15 min
MARCA_PROD = temporal.BASE / "produccion-activa"
MARCA_DEV = temporal.BASE / "dev-activo"
LOG = temporal.BASE / "guardian.log"

_RE_PORTAL = re.compile(r"uvicorn\s+server:app\b.*?--port\s+(\d+)", re.IGNORECASE)
_RE_EDGE_LINKEDOUT = re.compile(r"--user-data-dir=\"?[^\"]*\\LinkedOut\\[^\"]*?(?:linkedin_profile|portales_navegador)",
                                re.IGNORECASE)
_RE_PYTEST = re.compile(r"(?:-m\s+pytest|pytest(?:\.exe)?)\b", re.IGNORECASE)


def _puerto(proc: Dict) -> Optional[int]:
    if (proc.get("nombre") or "").lower() != "python.exe":
        return None
    m = _RE_PORTAL.search(proc.get("cmd") or "")
    return int(m.group(1)) if m else None


def _ancestros(pid: int, por_pid: Dict[int, Dict]) -> List[int]:
    salida, vistos = [], {pid}
    actual = por_pid.get(pid)
    while actual is not None:
        padre = actual.get("ppid")
        if padre in vistos or padre not in por_pid:
            break
        vistos.add(padre)
        salida.append(padre)
        actual = por_pid[padre]
    return salida


def _minutos(proc: Dict, ahora: datetime) -> float:
    inicio = proc.get("inicio")
    return (ahora - inicio).total_seconds() / 60 if isinstance(inicio, datetime) else 0.0


def decidir(procesos: List[Dict], temporales: List[Dict], ahora: datetime, *,
            prod_activa: bool, dev_activo: bool = False) -> List[Tuple[int, str]]:
    """(pid, motivo) de lo que hay que apagar, solo las raíces (se apaga con su árbol)."""
    por_pid = {p["pid"]: p for p in procesos}
    vigentes, vencidos = set(), {}
    for t in temporales:
        try:
            vence = datetime.fromisoformat(t["vence"])
        except (KeyError, TypeError, ValueError):
            continue
        (vigentes.add(t["pid"]) if vence > ahora else vencidos.__setitem__(t["pid"], t.get("motivo") or ""))

    def portal_permitido(pid: int) -> bool:
        puerto = _puerto(por_pid[pid])
        return ((puerto == PUERTO_DEV and dev_activo) or (puerto == PUERTO_PROD and prod_activa)
                or pid in vigentes)

    apagar: Dict[int, str] = {}
    for proc in procesos:
        pid, nombre, cmd = proc["pid"], (proc.get("nombre") or "").lower(), proc.get("cmd") or ""
        linaje = [pid, *_ancestros(pid, por_pid)]
        if pid in vencidos:
            apagar[pid] = f"temporal vencido ({vencidos[pid]})"
            continue
        if any(x in vigentes for x in linaje):
            continue
        cuelga_de_portal = any(_puerto(por_pid[x]) is not None and portal_permitido(x) for x in linaje[1:])
        puerto = _puerto(proc)
        if puerto is not None and not portal_permitido(pid):
            apagar[pid] = f"portal sin permiso en el puerto {puerto}"
        elif nombre == "msedge.exe" and _RE_EDGE_LINKEDOUT.search(cmd) and "--type=" not in cmd and not cuelga_de_portal:
            apagar[pid] = "Edge de LinkedOut sin portal vivo"
        elif nombre == "agy.exe":
            if proc.get("ppid") not in por_pid:
                apagar[pid] = "AGY huérfano"
            elif _minutos(proc, ahora) > MAX_AGY_MIN:
                apagar[pid] = f"AGY de más de {MAX_AGY_MIN} min"
        elif nombre == "python.exe" and _RE_PYTEST.search(cmd) and _minutos(proc, ahora) > MAX_PYTEST_MIN:
            apagar[pid] = f"pytest de más de {MAX_PYTEST_MIN} min"
    # Solo las raíces: el árbol de un proceso apagado cae con él
    return [(pid, motivo) for pid, motivo in apagar.items()
            if not any(a in apagar for a in _ancestros(pid, por_pid))]


# ------------------------------------------------------------------ Windows

_PS_PROCESOS = ("Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, "
                "@{n='Inicio';e={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress")


def listar_procesos() -> List[Dict]:
    salida = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_PROCESOS], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", creationflags=temporal.SIN_VENTANA,
                            check=True).stdout
    datos = json.loads(salida or "[]", strict=False)  # una línea de comandos puede traer caracteres de control
    procesos = []
    for d in datos if isinstance(datos, list) else [datos]:
        try:
            inicio = datetime.fromisoformat(str(d.get("Inicio"))[:26]).replace(tzinfo=None)
        except ValueError:
            inicio = None
        procesos.append({"pid": d.get("ProcessId"), "ppid": d.get("ParentProcessId"), "nombre": d.get("Name") or "",
                         "cmd": d.get("CommandLine") or "", "inicio": inicio})
    return procesos


def _anotar(lineas: Iterable[str]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for linea in lineas:
            f.write(linea + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true", help="solo lista lo que apagaría")
    a = ap.parse_args(argv)
    ahora = datetime.now()
    procesos = listar_procesos()
    temporal.limpiar(p["pid"] for p in procesos)
    decision = decidir(procesos, temporal.leer(), ahora, prod_activa=MARCA_PROD.exists(),
                       dev_activo=MARCA_DEV.exists())
    por_pid = {p["pid"]: p for p in procesos}
    lineas = []
    for pid, motivo in decision:
        proc = por_pid[pid]
        lineas.append(f"{ahora:%Y-%m-%d %H:%M:%S} {'simulado' if a.simular else 'apagado'} {pid} "
                      f"{proc['nombre']}: {motivo} | {proc['cmd'][:160]}")
        if not a.simular:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                           creationflags=temporal.SIN_VENTANA)
            temporal.limpiar(p for p in por_pid if p != pid)
    if a.simular:
        print("\n".join(lineas) or "Nada para apagar.")
    elif lineas:
        _anotar(lineas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
