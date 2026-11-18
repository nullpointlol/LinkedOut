#!/usr/bin/env python3
"""Herramienta offline de recálculo determinístico de afinidad y cierre seguro (misión 08).

CLI:
    python tools/recalcular_afinidad.py --db PATH --data-dir PATH [--dry-run | --aplicar] [--no-oferta ID [ID ...]]

Contrato:
    - --db y --data-dir obligatorios, sin paths implícitos.
    - Flags mutuamente excluyentes (--dry-run vs --aplicar).
    - Sin flags de modo: simulación por defecto (aplicado=False).
    - stdout JSON único: {"aplicado": bool, "cambiados": int, "cruzan_arriba": int, "cruzan_abajo": int, "listo_bajan": list[int]}
    - orden ascendente de IDs en listo_bajan.
    - Umbral real desde radar_state.umbral_match en esa DB (no 70 hardcodeado).
    - LISTO que baja de nota se incluye en listo_bajan aunque no cruce umbral.
    - Posts ENVIADO, ENVIANDO, DESCARTADO están protegidos y nunca se recalculan ni alteran su estado.
    - Abre SQLite mode=ro en simulación; con --aplicar abre mode=rw tras validar entradas.
    - Sin cambios de esquema ni PRAGMAs sobre la fuente en dry-run.
"""
import argparse
import copy
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Resolver imports de core desde la raíz del repo sin depender del cwd
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.afinidad_reglas import aplicar_reglas, cargar_base_candidato
from core.perfil_areas import anios_por_area_en_cache
from core.strategist import calculate_match_score_with_breakdown

# IGNORADA no se recalcula (creador, 10/11); GENERANDO y ANALIZANDO están en curso
ESTADOS_PROTEGIDOS = {"ENVIADO", "ENVIANDO", "DESCARTADO", "IGNORADA", "GENERANDO", "ANALIZANDO"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recálculo offline determinístico de scoring de afinidad en SQLite."
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Ruta absoluta o relativa a la base SQLite a procesar.",
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Ruta al directorio de datos con candidato/ (experiencia, competencias, áreas).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulación de solo lectura sin modificar la base de datos.",
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Aplica los cambios de manera persistente en la base de datos.",
    )
    parser.add_argument(
        "--no-oferta",
        nargs="*",
        default=[],
        help="Lista de IDs de publicaciones a marcar como no-oferta (IGNORADA, error='no es una oferta').",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Validación de flags mutuamente excluyentes
    if args.dry_run and args.aplicar:
        sys.stderr.write("Error: --dry-run y --aplicar son mutuamente excluyentes.\n")
        sys.exit(2)

    aplicar = bool(args.aplicar)

    db_path = Path(args.db).resolve()
    if not db_path.exists() or not db_path.is_file():
        sys.stderr.write(f"Error: La base de datos especificada no existe: {db_path}\n")
        sys.exit(1)

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists() or not data_dir.is_dir():
        sys.stderr.write(f"Error: El directorio de datos especificado no existe: {data_dir}\n")
        sys.exit(1)

    # Parsear IDs de no-oferta
    no_oferta_ids: Set[int] = set()
    for raw_id in args.no_oferta:
        try:
            no_oferta_ids.add(int(raw_id))
        except (ValueError, TypeError):
            continue

    # Carga pura de candidato (sin IA)
    candidato_dir = data_dir / "candidato"
    areas = anios_por_area_en_cache(candidato_dir)
    base_candidato = cargar_base_candidato(candidato_dir)

    # Conexión SQLite con URI para controlar modo ro / rw
    path_uri = str(db_path).replace("\\", "/")
    mode = "rw" if aplicar else "ro"
    uri_conn_str = f"file:{path_uri}?mode={mode}"

    try:
        conn = sqlite3.connect(uri_conn_str, uri=True)
    except Exception as exc:
        sys.stderr.write(f"Error al abrir la base de datos en modo {mode}: {exc}\n")
        sys.exit(1)

    conn.row_factory = sqlite3.Row

    try:
        # 1. Leer umbral_match de radar_state
        umbral = 70.0
        try:
            cur = conn.execute("SELECT umbral_match FROM radar_state WHERE id = 1")
            row_state = cur.fetchone()
            if row_state and row_state["umbral_match"] is not None:
                umbral = float(row_state["umbral_match"])
        except sqlite3.OperationalError:
            pass

        # 2. Consultar posts
        try:
            cur = conn.execute(
                "SELECT id, estado, triage, analisis, error, cargo_autor FROM linkedin_posts ORDER BY id ASC"
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = []

        cambiados = 0
        cruzan_arriba = 0
        cruzan_abajo = 0
        listo_bajan: List[int] = []
        updates: List[Tuple[str, Optional[str], Optional[str], int]] = []

        for row in rows:
            post_id = int(row["id"])
            old_estado = row["estado"] or ""
            old_error = row["error"]

            # Exclusión estricta de estados protegidos
            if old_estado in ESTADOS_PROTEGIDOS:
                continue

            # Parsear triage
            triage_raw = row["triage"]
            triage: Dict[str, Any] = {}
            if triage_raw:
                try:
                    triage = json.loads(triage_raw) if isinstance(triage_raw, str) else triage_raw
                except Exception:
                    triage = {}

            # Parsear análisis
            analisis_raw = row["analisis"]
            analisis: Optional[Dict[str, Any]] = None
            if analisis_raw:
                try:
                    analisis_parsed = json.loads(analisis_raw) if isinstance(analisis_raw, str) else analisis_raw
                    if isinstance(analisis_parsed, dict):
                        analisis = analisis_parsed
                except Exception:
                    analisis = None

            # Determinar puesto
            puesto = ""
            if isinstance(analisis, dict):
                puesto = str(analisis.get("puesto") or "")
            if not puesto and isinstance(triage, dict):
                puesto = str(triage.get("puesto") or "")
            if not puesto:
                puesto = str(row["cargo_autor"] or "")

            is_no_oferta = post_id in no_oferta_ids

            new_estado = old_estado
            new_error = old_error
            new_analisis: Optional[Dict[str, Any]] = None
            score_nuevo: Optional[float] = None

            # Determinar old_score
            old_score: Optional[float] = None
            if isinstance(analisis, dict):
                if analisis.get("match_score") is not None:
                    try:
                        old_score = float(analisis["match_score"])
                    except (ValueError, TypeError):
                        pass
                elif analisis.get("match_score_actual") is not None:
                    try:
                        old_score = float(analisis["match_score_actual"])
                    except (ValueError, TypeError):
                        pass

            tiene_requisitos = (
                isinstance(analisis, dict)
                and isinstance(analisis.get("requisitos"), list)
                and len(analisis["requisitos"]) > 0
            )

            if tiene_requisitos:
                reqs_originales = analisis["requisitos"]
                reqs_procesados = aplicar_reglas(
                    requisitos=reqs_originales,
                    puesto=puesto,
                    areas=areas,
                    base_candidato=base_candidato,
                )
                score_calc, desglose_calc = calculate_match_score_with_breakdown(reqs_procesados)
                score_nuevo = score_calc

                new_analisis = copy.deepcopy(analisis)
                new_analisis["requisitos"] = reqs_procesados
                new_analisis["match_score"] = score_nuevo
                new_analisis["desglose_afinidad"] = desglose_calc

                if is_no_oferta:
                    new_analisis["es_oferta"] = False
                    new_estado = "IGNORADA"
                    new_error = "no es una oferta"
            elif is_no_oferta:
                new_estado = "IGNORADA"
                new_error = "no es una oferta"
                if isinstance(analisis, dict):
                    new_analisis = copy.deepcopy(analisis)
                    new_analisis["es_oferta"] = False
                else:
                    new_analisis = analisis
            else:
                # Post sin análisis válido ni en --no-oferta: se omite
                new_analisis = analisis

            # Evaluar cruces y bajas de LISTO
            if old_score is not None and score_nuevo is not None:
                if old_score < umbral and score_nuevo >= umbral:
                    cruzan_arriba += 1
                elif old_score >= umbral and score_nuevo < umbral:
                    cruzan_abajo += 1

                if old_estado == "LISTO" and score_nuevo < (old_score - 1e-5):
                    listo_bajan.append(post_id)

            # Evaluar si hubo cambio real (delta)
            analisis_modificado = False
            if analisis is None and new_analisis is not None:
                analisis_modificado = True
            elif analisis is not None and new_analisis is None:
                analisis_modificado = True
            elif analisis is not None and new_analisis is not None:
                analisis_modificado = (analisis != new_analisis)

            estado_modificado = (new_estado != old_estado)
            error_modificado = (new_error != old_error)

            if analisis_modificado or estado_modificado or error_modificado:
                cambiados += 1
                if aplicar:
                    new_analisis_str = (
                        json.dumps(new_analisis, ensure_ascii=False)
                        if new_analisis is not None
                        else None
                    )
                    updates.append((new_estado, new_error, new_analisis_str, post_id))

        # Aplicar actualizaciones en una única transacción atómica
        if aplicar and updates:
            with conn:
                conn.executemany(
                    """
                    UPDATE linkedin_posts
                    SET estado = ?, error = ?, analisis = ?
                    WHERE id = ?
                    """,
                    updates,
                )

        listo_bajan.sort()

        resultado = {
            "aplicado": aplicar,
            "cambiados": cambiados,
            "cruzan_arriba": cruzan_arriba,
            "cruzan_abajo": cruzan_abajo,
            "listo_bajan": listo_bajan,
        }

        sys.stdout.write(json.dumps(resultado, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    finally:
        conn.close()


if __name__ == "__main__":
    main()
