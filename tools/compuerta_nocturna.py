"""Compuerta nocturna (14/11): la suite completa corre de noche en la PC, porque el CI de GitHub está suspendido hasta que
haya minutos.

Cada noche (tarea LinkedOut-compuerta, 03:00, ver tools/compuerta_nocturna.ps1):
1. `git fetch` y elige las ramas: `develop` primero y después cada rama remota con cambios que develop no tiene (`git
   cherry`: una rama mergeada rebasada no cuenta) y con un commit de los últimos DIAS_RAMA_ACTIVA días, de la más nueva
   a la más vieja, menos las de <salida>/excluir.txt (ramas reemplazadas que no se borran; una por línea, # comenta).
   Un commit que ya tiene resultado verde no se vuelve a correr.
2. Por rama, de a una: un worktree temporal en ese commit, `tools/verificar.py` completo con WORKERS workers en prioridad
   BelowNormal (la compuerta la baja sola) y el portal real vigilado: un GET a su status cada INTERVALO_S; ante
   LENTAS_PARA_CORTAR respuestas seguidas de más de LENTO_S, corta la suite y lo anota. Un portal apagado no corta.
3. Después de cada rama escribe el informe `<salida>/AAAAMMDD.md` y `<salida>/ultimo.json` (lo leen las sesiones: una
   rama se mergea solo con la compuerta en verde sobre su último commit). No empieza una rama si no entra en TOPE_NOCHE_S.

    python tools/compuerta_nocturna.py --repo <checkout> [--salida <carpeta>]
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Reglas a la vista
LENTO_S = 3                 # una respuesta del portal real de más de esto es lenta
INTERVALO_S = 15            # cada cuánto se mide el portal real
LENTAS_PARA_CORTAR = 2      # respuestas lentas seguidas que cortan la suite
WORKERS = "4"               # workers de la suite (LINKEDOUT_WORKERS)
DIAS_RAMA_ACTIVA = 7        # una rama sin commits en estos días no se corre
TOPE_NOCHE_S = 3 * 3600     # la tarea se corta a las 3 h
ESTIMADO_RAMA_S = 45 * 60   # no se empieza una rama si no entra antes del tope
RAMAS_EXCLUIDAS = frozenset({"HEAD", "main", "master", "produccion"})
PORTAL_REAL = "http://127.0.0.1:1111/api/radar/status"
SALIDA = Path.home() / "LinkedOut" / "compuerta"
BELOW_NORMAL_PRIORITY_CLASS = 0x4000


@dataclass
class Rama:
    nombre: str
    sha: str


@dataclass
class Config:
    salida: Path = SALIDA
    comando: List[str] = field(default_factory=lambda: [sys.executable, "tools/verificar.py"])
    portal_url: str = PORTAL_REAL
    intervalo_s: float = INTERVALO_S
    lento_s: float = LENTO_S
    tope_s: float = TOPE_NOCHE_S


def _git(repo: Path, *args: str, comprobar: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if comprobar and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def leer_excluidas(archivo: Optional[Path]) -> List[str]:
    """Ramas que no se corren (una por línea; # comenta). Sin archivo, ninguna."""
    try:
        lineas = Path(archivo).read_text(encoding="utf-8-sig").splitlines() if archivo else []
    except OSError:
        return []
    return [l.strip() for l in lineas if l.strip() and not l.strip().startswith("#")]


def ramas_a_verificar(repo: Path, ahora: Optional[datetime] = None, excluir: Optional[Path] = None) -> List[Rama]:
    """develop y las ramas remotas pendientes de merge y recientes, de la más nueva a la más vieja, menos las de
    `excluir` (<salida>/excluir.txt: ramas ya reemplazadas que no se borran)."""
    excluidas = set(leer_excluidas(excluir))
    _git(repo, "fetch", "--quiet", "--prune", "origin")
    ahora = ahora or datetime.now(timezone.utc)
    ramas = [Rama("develop", _git(repo, "rev-parse", "origin/develop"))]
    filas = _git(repo, "for-each-ref", "--sort=-committerdate", "refs/remotes/origin",
                 "--format=%(refname:short)|%(objectname)|%(committerdate:unix)").splitlines()
    for fila in filas:
        nombre, sha, cuando = fila.split("|")
        corto = nombre.split("/", 1)[1] if "/" in nombre else nombre
        if corto in RAMAS_EXCLUIDAS or corto in excluidas or corto == "develop" or not cuando:
            continue
        if ahora - datetime.fromtimestamp(int(cuando), timezone.utc) > timedelta(days=DIAS_RAMA_ACTIVA):
            continue
        # «+»: un commit cuyo cambio no está en develop (una rama mergeada rebasada sale con «-»)
        if any(l.startswith("+") for l in _git(repo, "cherry", "origin/develop", nombre).splitlines()):
            ramas.append(Rama(corto, sha))
    return ramas


class VigiaPortal:
    """Mide el portal real; `medir()` devuelve True cuando hay que cortar la suite."""

    def __init__(self, url: str, lento_s: float = LENTO_S):
        self.url, self.lento_s = url, lento_s
        self.seguidas = 0
        self.lentas = 0
        self.peor = 0.0

    def medir(self) -> bool:
        inicio = time.monotonic()
        try:
            # Timeout holgado: en Windows un puerto cerrado tarda ~2 s en rechazar (reintentos de TCP)
            with urllib.request.urlopen(self.url, timeout=max(self.lento_s * 2, 5)) as r:
                r.read()
        except Exception as e:  # noqa: BLE001
            razon = getattr(e, "reason", e)
            if not isinstance(razon, (TimeoutError, socket.timeout)) and not isinstance(e, (TimeoutError, socket.timeout)):
                self.seguidas = 0  # portal apagado o con error: no es lentitud, la suite no lo frena
                return False
        demora = time.monotonic() - inicio
        if demora > self.lento_s:
            self.seguidas += 1
            self.lentas += 1
            self.peor = max(self.peor, demora)
        else:
            self.seguidas = 0
        return self.seguidas >= LENTAS_PARA_CORTAR


def _matar_arbol(proceso: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proceso.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proceso.pid, 9)
        except OSError:
            proceso.kill()


def _resumen(salida: str) -> Dict[str, object]:
    resumenes = re.findall(r"^\S*=*\s*(\d+ (?:failed|passed)[^\n=]*?)\s*=*$", salida, re.M)
    fallados = sorted(set(re.findall(r"^FAILED (\S+)", salida, re.M)))
    return {"resumen": resumenes[-1].strip() if resumenes else "", "fallados": fallados}


def verificar_rama(repo: Path, rama: Rama, config: Config) -> Dict[str, object]:
    """Corre la compuerta en un worktree temporal del commit de la rama, vigilando el portal real."""
    config.salida.mkdir(parents=True, exist_ok=True)
    (config.salida / "logs").mkdir(exist_ok=True)
    log = config.salida / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^\w.-]+', '_', rama.nombre)}.log"
    carpeta = Path(tempfile.mkdtemp(prefix="linkedout-compuerta-"))
    worktree = carpeta / "repo"
    resultado: Dict[str, object] = {"rama": rama.nombre, "sha": rama.sha, "log": str(log)}
    inicio = time.monotonic()
    try:
        _git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), rama.sha)
        entorno = dict(os.environ, CI="true", LINKEDOUT_WORKERS=WORKERS, PYTHONUTF8="1")
        extra = ({"creationflags": BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NEW_PROCESS_GROUP}
                 if sys.platform == "win32" else {"start_new_session": True})
        vigia = VigiaPortal(config.portal_url, config.lento_s)
        with open(log, "w", encoding="utf-8", errors="replace") as salida:
            proceso = subprocess.Popen(config.comando, cwd=worktree, env=entorno, stdout=salida,
                                       stderr=subprocess.STDOUT, **extra)
            cortada = False
            while proceso.poll() is None:
                try:
                    proceso.wait(timeout=config.intervalo_s)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if vigia.medir():
                    _matar_arbol(proceso)
                    proceso.wait()
                    cortada = True
                    break
        texto = log.read_text(encoding="utf-8", errors="replace")
        resultado.update(_resumen(texto))
        if cortada:
            resultado["resultado"] = "cortada"
            resultado["motivo"] = (f"el portal real tardó más de {config.lento_s:g} s {LENTAS_PARA_CORTAR} veces "
                                   f"seguidas (peor {vigia.peor:.1f} s)")
        else:
            resultado["resultado"] = "verde" if proceso.returncode == 0 else "rojo"
    except Exception as e:  # noqa: BLE001 - una rama que no se puede preparar no frena la noche
        resultado.update(resultado="error", motivo=str(e), resumen="", fallados=[])
    finally:
        resultado["duracion_s"] = round(time.monotonic() - inicio)
        _git(repo, "worktree", "remove", "--force", str(worktree), comprobar=False)
        _git(repo, "worktree", "prune", comprobar=False)
    return resultado


def _anteriores(salida: Path) -> Dict[str, Dict[str, object]]:
    """Resultados verdes previos por commit (para no repetirlos)."""
    try:
        datos = json.loads((salida / "ultimo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r["sha"]: r for r in datos.get("ramas", []) if r.get("resultado") == "verde" and r.get("sha")}


def _escribir(salida: Path, fecha: datetime, inicio: datetime, resultados: List[Dict[str, object]], fin: bool,
              excluidas: Optional[List[str]] = None) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    datos = {"fecha": fecha.strftime("%Y-%m-%d"), "inicio": inicio.isoformat(timespec="seconds"),
             "fin": datetime.now().isoformat(timespec="seconds") if fin else None, "ramas": resultados,
             "excluidas": sorted(excluidas or [])}
    (salida / "ultimo.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    lineas = [f"# Compuerta nocturna {fecha:%d/%m/%Y}", "",
              f"Inicio {inicio:%H:%M}" + (f", fin {datetime.now():%H:%M}" if fin else ", en curso") + ".", "",
              "| Rama | Commit | Resultado | Duración | Resumen |", "|---|---|---|---|---|"]
    for r in resultados:
        duracion = f"{int(r.get('duracion_s', 0)) // 60} min" if r.get("duracion_s") is not None else "-"
        lineas.append(f"| {r['rama']} | {str(r['sha'])[:7]} | {r['resultado']}"
                      f"{' (ya verificado)' if r.get('reutilizado') else ''} | {duracion} | {r.get('resumen', '')} |")
    for r in resultados:
        if r.get("fallados") or r.get("motivo"):
            lineas += ["", f"## {r['rama']} ({r['resultado']})"]
            if r.get("motivo"):
                lineas.append(f"- Motivo: {r['motivo']}")
            lineas += [f"- Falló: `{f}`" for f in r.get("fallados", [])]
            if r.get("log"):
                lineas.append(f"- Log: `{r['log']}`")
    (salida / f"{fecha:%Y%m%d}.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def correr_noche(repo: Path, config: Config, ahora: Optional[datetime] = None) -> List[Dict[str, object]]:
    fecha = ahora or datetime.now()
    inicio_reloj = time.monotonic()
    previos = _anteriores(config.salida)
    resultados: List[Dict[str, object]] = []
    excluir = config.salida / "excluir.txt"
    excluidas = leer_excluidas(excluir)
    for rama in ramas_a_verificar(repo, excluir=excluir):
        if rama.sha in previos:
            resultados.append({**previos[rama.sha], "rama": rama.nombre, "reutilizado": True})
        elif time.monotonic() - inicio_reloj + min(ESTIMADO_RAMA_S, config.tope_s) > config.tope_s:
            resultados.append({"rama": rama.nombre, "sha": rama.sha, "resultado": "sin correr",
                               "motivo": "no entraba antes del tope de la noche", "fallados": [], "resumen": ""})
        else:
            resultados.append(verificar_rama(repo, rama, config))
        _escribir(config.salida, fecha, fecha, resultados, fin=False, excluidas=excluidas)
    _escribir(config.salida, fecha, fecha, resultados, fin=True, excluidas=excluidas)
    return resultados


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compuerta nocturna: suite completa sobre develop y las ramas pendientes.")
    p.add_argument("--repo", required=True, help="checkout del repo (con el remoto origin)")
    p.add_argument("--salida", default=str(SALIDA))
    a = p.parse_args(argv)
    resultados = correr_noche(Path(a.repo), Config(salida=Path(a.salida)))
    for r in resultados:
        print(f"{r['rama']}: {r['resultado']} {r.get('resumen', '')}")
    return 0 if all(r["resultado"] in ("verde", "sin correr") for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
