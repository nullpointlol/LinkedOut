"""Pruebas de sintaxis de la búsqueda de publicaciones de LinkedIn, de a una y solo con la cuenta 2.

    python tools/sintaxis_linkedin.py --listar          # casos y su estado
    python tools/sintaxis_linkedin.py --siguiente       # corre el primer caso pendiente (una búsqueda)
    python tools/sintaxis_linkedin.py --caso or_simple  # corre ese caso (una búsqueda)
    python tools/sintaxis_linkedin.py --informe         # rehace docs/linkedin-sintaxis.md con lo medido
    python tools/sintaxis_linkedin.py --busqueda "Soporte"   # prueba una búsqueda de busquedas.json (una búsqueda)

Las pruebas de las búsquedas del usuario quedan solo en `<DATA_DIR>`: no van al informe del repo, que es público.

Cada corrida hace UNA búsqueda con el perfil de la cuenta 2 (`<DATA_DIR>/linkedin_profile_2`, con la sesión iniciada
por `tools/login_cuenta.py`) y guarda captura, HTML y resultado en `<DATA_DIR>/radar_debug/sintaxis/`. Respeta el
presupuesto del Radar para una cuenta (el del perfil, reglas_radar.json, contando las de este script) y corta ante
un bloqueo. Correrlo con el Radar detenido: un perfil no se abre en dos Edge a la vez. Los casos están en
`tools/sintaxis_linkedin_casos.json`.
"""
import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from core.paths import sesion_marcada  # noqa: E402

CASOS = RAIZ / "tools" / "sintaxis_linkedin_casos.json"
INFORME = RAIZ / "docs" / "linkedin-sintaxis.md"
REGISTRO = "resultados.json"
NO_FUNCIONA = ("sin_resultados", "bloqueo", "login")


class NoSePuede(Exception):
    """La prueba no se corre (sin sesión de la cuenta 2, sin presupuesto o LinkedIn bloqueó)."""


def leer_resultados(salida: Path) -> List[Dict[str, Any]]:
    ruta = Path(salida) / REGISTRO
    if not ruta.is_file():
        return []
    return json.loads(ruta.read_text(encoding="utf-8"))


def _guardar_resultado(salida: Path, resultado: Dict[str, Any]) -> None:
    resultados = leer_resultados(salida) + [resultado]
    Path(salida).mkdir(parents=True, exist_ok=True)
    (Path(salida) / REGISTRO).write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")


def _presupuesto_del_perfil() -> tuple:
    """(por hora, por día) de una cuenta, de las reglas del Radar del perfil. Sin reglas válidas no se prueba nada."""
    from core.reglas_radar import ReglasInvalidas, leer_reglas
    try:
        return leer_reglas().presupuesto(dos_pausadas=False)
    except ReglasInvalidas as exc:
        raise NoSePuede(str(exc)) from exc


def presupuesto_restante(resultados: List[Dict[str, Any]], ahora: datetime) -> int:
    """Búsquedas que quedan ahora: el mínimo entre lo que falta de la última hora y del día (presupuesto del perfil)."""
    por_hora, por_dia = _presupuesto_del_perfil()
    fechas = [datetime.fromisoformat(r["fecha"]) for r in resultados if r.get("fecha")]
    hora = sum(1 for f in fechas if ahora - f < timedelta(hours=1))
    dia = sum(1 for f in fechas if f.date() == ahora.date())
    return max(0, min(por_hora - hora, por_dia - dia))


def siguiente(casos: List[Dict[str, Any]], resultados: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    hechos = {r["id"] for r in resultados}
    return next((c for c in casos if c["id"] not in hechos), None)


def caso_de_busqueda(nombre: str) -> Optional[Dict[str, Any]]:
    """El caso de prueba de una búsqueda de `<DATA_DIR>/candidato/busquedas.json` (por nombre, sin mayúsculas)."""
    from core.radar_consultas import cargar_busquedas
    for b in cargar_busquedas():
        if b["nombre"].casefold() == nombre.strip().casefold():
            return {"id": f"busqueda:{b['nombre']}", "mide": f"Búsqueda del usuario «{b['nombre']}»",
                    "consulta": b["consulta"]}
    return None


def correr_caso(
    caso: Dict[str, Any], *, perfil: Path, salida: Path,
    harvester_factory: Callable[..., Any], ahora: datetime,
) -> Dict[str, Any]:
    """Una búsqueda de `caso` con la cuenta 2: captura + HTML en `salida/<fecha>-<id>/` y el resultado registrado."""
    if not sesion_marcada(Path(perfil)):
        raise NoSePuede(f"La cuenta 2 no tiene la sesión iniciada ({perfil}): corré antes tools/login_cuenta.py.")
    if presupuesto_restante(leer_resultados(salida), ahora) <= 0:
        por_hora, por_dia = _presupuesto_del_perfil()
        raise NoSePuede(f"Sin presupuesto: máximo {por_hora} por hora y {por_dia} por día.")
    carpeta = Path(salida) / f"{ahora:%Y%m%d-%H%M%S}-{re.sub(r'[^0-9A-Za-z_]+', '-', caso['id']).strip('-')}"

    async def _correr() -> Any:
        harvester = harvester_factory(profile_dir=Path(perfil))
        try:
            resultado = await harvester.harvest(caso["consulta"], max_posts=30, max_scrolls=2, paciencia=1)
            await harvester.dump_debug(carpeta)
            return resultado
        finally:
            await harvester.close()

    lectura_cruda = asyncio.run(_correr())
    lectura = getattr(lectura_cruda, "lectura", {}) or {}
    motivo = lectura.get("motivo_fin") or ("login" if getattr(lectura_cruda, "needs_login", False) else "")
    posts = len(lectura.get("urns_parseados") or getattr(lectura_cruda, "posts", None) or [])
    resultado = {
        "id": caso["id"], "consulta": caso["consulta"], "fecha": ahora.isoformat(timespec="seconds"),
        "motivo_fin": motivo, "posts": posts, "funciona": posts > 0 and motivo not in NO_FUNCIONA,
        "url": lectura.get("url", ""), "carpeta": str(carpeta),
    }
    _guardar_resultado(salida, resultado)
    if motivo == "bloqueo":
        raise NoSePuede("LinkedIn bloqueó las búsquedas de la cuenta 2: se corta acá. Probá mañana.")
    return resultado


def informe(casos: List[Dict[str, Any]], resultados: List[Dict[str, Any]]) -> str:
    """El contenido de docs/linkedin-sintaxis.md: una fila por caso con el último resultado medido."""
    ultimo = {r["id"]: r for r in resultados}
    filas = []
    for c in casos:
        r = ultimo.get(c["id"])
        consulta = c["consulta"].replace("|", "\\|")
        if r is None:
            filas.append(f"| {c['mide']} | `{consulta}` | pendiente | | |")
            continue
        estado = "funciona" if r["funciona"] else f"no funciona ({r['motivo_fin'] or 'sin posts'})"
        fecha = datetime.fromisoformat(r["fecha"]).strftime("%Y-%m-%d %H:%M")
        filas.append(f"| {c['mide']} | `{consulta}` | {estado} | {r['posts']} | {fecha} |")
    return "\n".join([
        "# Sintaxis de la búsqueda de publicaciones de LinkedIn",
        "",
        "Qué acepta de verdad la búsqueda de contenido de LinkedIn (ordenada por fecha, última semana), medido con la",
        "cuenta 2 y `tools/sintaxis_linkedin.py`, de a una búsqueda y dentro del presupuesto (6 por hora). Cada prueba",
        "guarda captura y HTML en `<DATA_DIR>/radar_debug/sintaxis/`. «Funciona» = trajo al menos una publicación.",
        "",
        "La validación de `core/radar_consultas.py` (`validar_consulta`, `LARGO_MAXIMO`, `SEPARADOR_BLOQUES`) se",
        "ajusta a esta tabla: una búsqueda que LinkedIn no acepta no se puede guardar.",
        "",
        "Antecedente (11/11, Radar con la cuenta principal): `\"cv a\"` sola trajo 86 publicaciones y un OR de dos",
        "frases en inglés trajo 29; las 3 búsquedas largas con OR y AND anidados terminaron «sin resultados» en tres",
        "corridas seguidas.",
        "",
        "## Conclusión (12/11)",
        "",
        "- **El límite es de 5 operadores escritos (AND, OR y NOT): con 6 o más, «sin resultados»**, aun en",
        "  consultas cortas (8 palabras con OR en 89 caracteres: cero) y sin importar la cantidad de términos (8",
        "  términos con 5 OR: 24 publicaciones; 7 términos con 6 OR: cero). Los AND cuentan (4 OR + 2 AND: cero;",
        "  3 OR + 2 AND: 21); el AND implícito (espacio) no. `validar_consulta` rechaza más de `MAX_OPERADORES = 5`.",
        "- **Sin explicar todavía:** `\"cv a\"` sola trajo 18 el 11/11 y 0 en tres pruebas del 12/11, igual que",
        "  `(\"cv a\" OR \"enviar cv\")` sola; con un segundo bloque (rol o ubicación) sí traen. Hasta entenderlo,",
        "  una búsqueda de un solo bloque con esas frases puede terminar en cero (y abrir un ticket).",
        "- Funcionan: 2 y 3 bloques con OR (AND implícito), AND anidado dentro de un OR, NOT y un bloque de 4",
        "  frases con OR. `or` en minúscula también trae resultados, pero la validación exige mayúsculas.",
        "- El largo no fue el factor en lo medido (hasta ~140 caracteres con 5 OR).",
        "",
        "## Pruebas",
        "",
        "| Qué mide | Consulta | Resultado | Posts | Fecha |",
        "|---|---|---|---|---|",
        *filas,
        "",
    ])


def main(argv: Optional[List[str]] = None) -> int:
    from core.linkedin_harvester import LinkedInHarvester
    from core.paths import DATA_DIR

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--listar", action="store_true")
    grupo.add_argument("--siguiente", action="store_true")
    grupo.add_argument("--caso")
    grupo.add_argument("--informe", action="store_true")
    grupo.add_argument("--busqueda")
    args = parser.parse_args(argv)

    casos = json.loads(CASOS.read_text(encoding="utf-8"))
    salida = DATA_DIR / "radar_debug" / "sintaxis"
    resultados = leer_resultados(salida)
    if args.listar:
        hechos = {r["id"]: r for r in resultados}
        for c in casos:
            r = hechos.get(c["id"])
            estado = "pendiente" if r is None else ("funciona" if r["funciona"] else "no funciona")
            print(f"{c['id']:<18} {estado:<12} {c['consulta']}")
        print(f"Presupuesto ahora: {presupuesto_restante(resultados, datetime.now())}")
        return 0
    if args.informe:
        INFORME.write_text(informe(casos, resultados), encoding="utf-8")
        print(f"Informe en {INFORME}")
        return 0
    if args.busqueda:
        caso = caso_de_busqueda(args.busqueda)
    elif args.siguiente:
        caso = siguiente(casos, resultados)
    else:
        caso = next((c for c in casos if c["id"] == args.caso), None)
    if caso is None:
        print("No hay caso para correr (todos medidos, id o búsqueda desconocida).")
        return 1
    try:
        r = correr_caso(caso, perfil=DATA_DIR / "linkedin_profile_2", salida=salida,
                        harvester_factory=LinkedInHarvester, ahora=datetime.now())
    except NoSePuede as exc:
        print(exc)
        return 2
    print(f"{caso['id']}: {'funciona' if r['funciona'] else 'no funciona'} ({r['posts']} posts, {r['motivo_fin']})."
          f" Captura en {r['carpeta']}")
    INFORME.write_text(informe(casos, leer_resultados(salida)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
