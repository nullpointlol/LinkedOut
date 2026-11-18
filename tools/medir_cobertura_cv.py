"""Medidor (solo lectura): requisitos MATCH o TRANSFERIBLE que no aparecen en el CV de las postulaciones LISTO o
enviadas (creador, 16/11). La meta es 0. Misma regla que el armado y el Auditor (`core/cobertura_requisitos.py`).

    python tools/medir_cobertura_cv.py [--db RUTA] [--detalle]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cobertura_requisitos import exigidos, faltantes, sin_perfil  # noqa: E402
from core.idioma import idioma_de_cv  # noqa: E402

_CONSULTA = """
select p.id, coalesce(l.id, 0), case when p.estado_postulacion = 'POSTULADA' or l.estado = 'ENVIADO' then 'ENVIADO'
       else 'LISTO' end, p.titulo, p.requisitos, p.cv_markdown
from postulaciones p left join linkedin_posts l on l.postulacion_id = p.id
where p.status = 'READY' and coalesce(p.cv_markdown, '') <> ''
  and (l.estado is null or l.estado in ('LISTO', 'ENVIADO') or p.estado_postulacion = 'POSTULADA')
order by p.id"""


def medir(db: Path) -> Dict[str, Any]:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        filas = con.execute(_CONSULTA).fetchall()
    finally:
        con.close()
    total = {"postulaciones": 0, "afectadas": 0, "LISTO": 0, "ENVIADO": 0, "exigidos": 0, "faltan": 0}
    detalle = []
    for pid, post, estado, titulo, requisitos, cv in filas:
        try:
            reqs = json.loads(requisitos or "[]")
        except ValueError:
            reqs = []
        idioma = idioma_de_cv(cv)
        falta = faltantes(sin_perfil(cv), reqs, idioma=idioma)
        total["postulaciones"] += 1
        total["exigidos"] += len(exigidos(reqs, idioma))
        total["faltan"] += len(falta)
        if falta:
            total["afectadas"] += 1
            total[estado] += 1
            detalle.append({"postulacion": pid, "post": post, "estado": estado, "titulo": titulo,
                            "faltan": [f"{f.termino} ({f.estado})" for f in falta]})
    return {"total": total, "detalle": detalle}


def main() -> int:
    from core.paths import DATA_DIR
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DATA_DIR / "postulaciones.db")
    ap.add_argument("--detalle", action="store_true")
    args = ap.parse_args()
    resultado = medir(args.db)
    sys.stdout.reconfigure(encoding="utf-8")
    t = resultado["total"]
    print(f"{t['faltan']} términos faltantes de {t['exigidos']} exigidos; {t['afectadas']} de {t['postulaciones']} "
          f"postulaciones afectadas ({t['LISTO']} LISTO, {t['ENVIADO']} enviadas)")
    if args.detalle:
        for d in resultado["detalle"]:
            print(f"- {d['postulacion']} [{d['estado']}] {d['titulo']}: {', '.join(d['faltan'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
