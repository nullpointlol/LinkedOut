"""Palabras que separan los posts útiles de los descartados y rendimiento por búsqueda (solo lectura).

Uso: python scripts/palabras_clave.py --db <ruta a postulaciones.db> [--dias 14] [--top 40]

Útiles: LISTO, ENVIADO, ENVIANDO, GENERANDO o ANALIZADA con afinidad > 70. Descartados: DESCARTADO.
"""
import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from typing import Iterable, List, Set

ESTADOS_UTILES = ("LISTO", "ENVIADO", "ENVIANDO", "GENERANDO")
FUERA_DEL_PORTAL = ("DESCARTADO", "IGNORADA", "NUEVO")
STOPWORDS = set("""
a al algo ante como con de del desde donde e el ella en entre es esta este esto la las le lo los mas
me mi muy no o para pero por que se si sin sobre su sus te tu un una uno y ya
an and are as at be by for from has have in is it of on or that the this to we with you your our will
""".split())
_RE_DOMINIO = re.compile(r"@([a-z0-9-]+(?:\.[a-z0-9-]+)+)")
_RE_PALABRA = re.compile(r"\w+")


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def tokens(texto: str) -> Set[str]:
    """Unigramas y bigramas sin stopwords, más los dominios de email (`.com.ar`, `gmail.com`)."""
    norm = normalizar(texto)
    palabras = [p for p in _RE_PALABRA.findall(norm) if p not in STOPWORDS and not p.isdigit()]
    resultado = set(palabras)
    resultado.update(f"{a} {b}" for a, b in zip(palabras, palabras[1:]))
    for dominio in _RE_DOMINIO.findall(norm):
        resultado.add(dominio)
        if dominio.endswith(".com.ar"):
            resultado.add(".com.ar")
    return resultado


def lift(util: int, desc: int, n_util: int, n_desc: int) -> float:
    pu = 100.0 * util / n_util if n_util else 0.0
    pd = 100.0 * desc / n_desc if n_desc else 0.0
    return (pu + 0.5) / (pd + 0.5)


def _contar(textos: Iterable[str]) -> Counter:
    conteo: Counter = Counter()
    for texto in textos:
        conteo.update(tokens(texto))
    return conteo


def reporte(conn: sqlite3.Connection, dias: int = 14, top: int = 40) -> str:
    utiles = [r[0] for r in conn.execute(
        f"SELECT texto FROM linkedin_posts WHERE estado IN {ESTADOS_UTILES} "
        "OR (estado = 'ANALIZADA' AND CAST(json_extract(analisis, '$.match_score') AS REAL) > 70)")]
    descartados = [r[0] for r in conn.execute("SELECT texto FROM linkedin_posts WHERE estado = 'DESCARTADO'")]
    cu, cd = _contar(utiles), _contar(descartados)
    nu, nd = len(utiles), len(descartados)
    filas = [(t, cu[t], cd[t], lift(cu[t], cd[t], nu, nd)) for t in cu if cu[t] >= 3]
    filas.sort(key=lambda f: (-f[3], -f[1], f[0]))
    lineas: List[str] = [f"## Palabras que separan (útiles {nu}, descartados {nd})",
                         f"{'palabra':30} {'% útiles':>9} {'% descart.':>10} {'lift':>7}"]
    for t, u, d, l in filas[:top]:
        lineas.append(f"{t:30} {100.0 * u / max(1, nu):9.1f} {100.0 * d / max(1, nd):10.1f} {l:7.1f}")

    lineas += ["", f"## Rendimiento por búsqueda (últimos {dias} días)",
               f"{'guard.':>6} {'email':>6} {'portal':>6} {'útiles':>6} {'% útil':>7} {'% portal':>8}  búsqueda"]
    consulta = (
        "SELECT query, COUNT(*), "
        "SUM(CASE WHEN emails IS NOT NULL AND emails NOT IN ('', '[]') THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN estado NOT IN {FUERA_DEL_PORTAL} THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN estado IN {ESTADOS_UTILES} OR (estado = 'ANALIZADA' AND "
        "CAST(json_extract(analisis, '$.match_score') AS REAL) > 70) THEN 1 ELSE 0 END) "
        "FROM linkedin_posts WHERE datetime(created_at) >= datetime('now', ?) GROUP BY query ORDER BY 2 DESC")
    for query, n, email, portal, util in conn.execute(consulta, (f"-{int(dias)} days",)):
        n = n or 0
        lineas.append(f"{n:6} {email or 0:6} {portal or 0:6} {util or 0:6} "
                      f"{100.0 * (util or 0) / max(1, n):7.1f} {100.0 * (portal or 0) / max(1, n):8.1f}  {query}")
    return "\n".join(lineas)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--dias", type=int, default=14)
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)
    ruta = args.db.replace("\\", "/")
    conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        texto = reporte(conn, args.dias, args.top)
    finally:
        conn.close()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
