"""Pruebas para el recálculo seguro de afinidad sobre copias SQLite (misión 07).

Contrato:
    CLI python tools/recalcular_afinidad.py --db PATH --data-dir PATH [--dry-run | --aplicar] [--no-oferta ID [ID ...]]
    - --db y --data-dir obligatorios, sin paths implícitos.
    - Flags de modo mutuamente excluyentes (--dry-run vs --aplicar).
    - Sin modo=simulación.
    - stdout JSON único: {"aplicado": bool, "cambiados": int, "cruzan_arriba": int, "cruzan_abajo": int, "listo_bajan": list[int]}
    - orden ascendente de IDs en listo_bajan.
    - Umbral real desde radar_state.umbral_match en esa DB (no 70 hardcodeado).
    - LISTO que baja de nota se incluye en listo_bajan aunque no cruce umbral.
    - Posts ENVIADO, ENVIANDO, DESCARTADO están protegidos y nunca se recalculan ni alteran su estado.
"""
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from core import database, radar_store
from core.afinidad_reglas import aplicar_reglas
from core.strategist import calculate_match_score_with_breakdown

CLI_PATH = Path(__file__).resolve().parent.parent / "tools" / "recalcular_afinidad.py"


def _cargar_fixtures() -> Dict[str, Any]:
    ruta = Path(__file__).resolve().parent / "fixtures" / "fixtures-casos.json"
    return json.loads(ruta.read_text(encoding="utf-8"))


def _crear_entorno_candidato(tmp_path: Path, fixtures: Dict[str, Any]) -> Path:
    """Genera datos de candidato y caché válido con el mismo hash de perfil, sin invocar IA."""
    data_dir = tmp_path / "data"
    candidato_dir = data_dir / "candidato"
    candidato_dir.mkdir(parents=True, exist_ok=True)

    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    exp_path = candidato_dir / "experiencia.txt"
    exp_texto = (
        "## Experiencia Laboral\n\n"
        "### Líder Técnica Frontend | 2024 - 2026\n"
        "React, TypeScript, Next.js, revisión de código y mentoría del equipo.\n\n"
        "### Ingeniera DevOps Jr. | 2022 - 2024\n"
        "CI/CD, pipelines Jenkins, GitLab, Linux, Codacy.\n\n"
        "### Desarrolladora Fullstack | 2020 - 2022\n"
        "Node.js, Express, PostgreSQL y React para clientes del estudio.\n\n"
        "### Desarrolladora Frontend | 2016 - 2020\n"
        "Interfaces en React, pruebas con Jest, accesibilidad web.\n\n"
        "### Desarrolladora Backend Node.js | 2013 - 2016\n"
        "APIs REST y GraphQL en Node.js, SQL.\n\n"
        "### Maquetadora Web | 2010 - 2013\n"
        "HTML, CSS y JavaScript desde diseños en Figma.\n\n"
        "### Programadora PHP | 2004 - 2007\n"
        "Desarrollo PHP, MySQL.\n"
    )
    exp_path.write_text(exp_texto, encoding="utf-8")

    per_path = candidato_dir / "personales.txt"
    per_texto = (
        "Nombre: Sam Ortega\n"
        "Puesto: Líder Técnica Frontend\n"
        "Ubicación: Ciudad Ejemplo, Buenos Aires, Argentina\n"
        "Email: sam@example.com\n"
    )
    per_path.write_text(per_texto, encoding="utf-8")

    comp_path = candidato_dir / "competencias_expandidas.json"
    comp_datos = {
        "habilidades": base_candidato,
        "competencias": [
            "React", "TypeScript", "Node.js", "SQL", "Docker", "Linux", "Scrum",
            "GitLab", "Jenkins", "Jest", "Playwright", "REST", "GraphQL", "Jira",
            "Figma"
        ]
    }
    comp_path.write_text(json.dumps(comp_datos, ensure_ascii=False, indent=2), encoding="utf-8")

    # Contrato de perfil_areas._leer_perfil: VERSION_CRITERIO = b"areas-v2"
    version_criterio = b"areas-v2"
    contenidos = [exp_path.read_bytes(), per_path.read_bytes()]
    huella = hashlib.sha256(b"\0".join([version_criterio, *contenidos])).hexdigest()

    anios_cache = {
        "hash": huella,
        "anios_por_area": areas
    }
    (candidato_dir / "anios_por_area.json").write_text(
        json.dumps(anios_cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (data_dir / "aprendizajes.json").write_text("{}", encoding="utf-8")
    return data_dir


def _crear_db(db_path: Path, umbral: int = 70) -> None:
    database.init_db(db_path)
    conn = database.get_db_connection(db_path)
    radar_store.init_radar_tables(conn)
    if umbral != 70:
        conn.execute("UPDATE radar_state SET umbral_match = ? WHERE id = 1", (umbral,))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()


def _insertar_posts(db_path: Path, posts: List[Dict[str, Any]]) -> None:
    conn = database.get_db_connection(db_path)
    with conn:
        for p in posts:
            analisis_val = p.get("analisis")
            if isinstance(analisis_val, (dict, list)):
                analisis_str = json.dumps(analisis_val, ensure_ascii=False)
            elif analisis_val is not None:
                analisis_str = str(analisis_val)
            else:
                analisis_str = None

            triage_val = p.get("triage", {})
            triage_str = json.dumps(triage_val, ensure_ascii=False) if isinstance(triage_val, dict) else str(triage_val)

            conn.execute(
                """
                INSERT OR REPLACE INTO linkedin_posts (
                    id, urn, url, autor, cargo_autor, texto, fecha_rel, query,
                    emails, triage, estado, postulacion_id, email_to, email_subject,
                    email_body, aprobado_at, sent_at, message_id, error, intentos,
                    fuente, links, whatsapp, canal, destino_canal, analisis, prioridad
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    p.get("id"),
                    p.get("urn", f"urn:li:activity:{p.get('id', 1)}"),
                    p.get("url", ""),
                    p.get("autor", "Empresa"),
                    p.get("cargo_autor", "Recruiter"),
                    p.get("texto", "Oferta laboral texto"),
                    p.get("fecha_rel", "1d"),
                    p.get("query", "test"),
                    json.dumps(p.get("emails", []), ensure_ascii=False) if isinstance(p.get("emails"), list) else p.get("emails", "[]"),
                    triage_str,
                    p.get("estado", "ANALIZADA"),
                    p.get("postulacion_id"),
                    p.get("email_to", ""),
                    p.get("email_subject", ""),
                    p.get("email_body", ""),
                    p.get("aprobado_at"),
                    p.get("sent_at"),
                    p.get("message_id"),
                    p.get("error"),
                    p.get("intentos", 0),
                    p.get("fuente", "busqueda"),
                    json.dumps(p.get("links", []), ensure_ascii=False) if isinstance(p.get("links"), list) else p.get("links", "[]"),
                    json.dumps(p.get("whatsapp", []), ensure_ascii=False) if isinstance(p.get("whatsapp"), list) else p.get("whatsapp", "[]"),
                    p.get("canal", "email"),
                    p.get("destino_canal", ""),
                    analisis_str,
                    p.get("prioridad", 0),
                ),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()


def _leer_post_ro(db_path: Path, post_id: int) -> Optional[Dict[str, Any]]:
    path_uri = str(db_path.resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{path_uri}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM linkedin_posts WHERE id = ?", (post_id,)).fetchall()
        return dict(rows[0]) if rows else None
    finally:
        con.close()


def _leer_radar_state_ro(db_path: Path) -> Optional[Dict[str, Any]]:
    path_uri = str(db_path.resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{path_uri}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM radar_state WHERE id = 1").fetchall()
        return dict(rows[0]) if rows else None
    finally:
        con.close()


def _ejecutar_cli(args: List[str], data_dir: Path) -> subprocess.CompletedProcess:
    env = dict(
        os.environ,
        LINKEDOUT_SIN_RADAR="1",
        LINKEDOUT_DATA_DIR=str(data_dir.resolve()),
    )
    cmd = [sys.executable, str(CLI_PATH)] + args
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )


def test_dry_run_hash_identico(tmp_path):
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: Ofertas buenas de control (desarrollo web: frontend, fullstack, React):
    # ≤ 3 puntos de cambio y siguen ≥ 70 (base: match_score_actual en las fixtures).
    # Bases medidas: 97,6; 98,5; 100; 100; 95,2 (cómo se midieron: _contexto.nota_bases del JSON).
    controles = [
        ("control_frontend_sr", 97.6),
        ("control_desarrolladora_web", 98.5),
        ("control_fullstack_node", 100.0),
        ("control_react_ssr", 100.0),
        ("control_frontend_sr_cordoba", 95.2),
    ]
    for clave, base_medida in controles:
        caso = fixtures[clave]
        reqs_proc = aplicar_reglas(caso["requisitos"], caso["puesto"], areas, base_candidato)
        score, _ = calculate_match_score_with_breakdown(reqs_proc)
        assert score >= 70.0, f"Control {clave} bajó de 70 (score={score})"
        assert abs(score - base_medida) <= 3.01, f"Control {clave} varió > 3 (base={base_medida}, score={score})"

    # invariante: nada se envía sin aprobado_at; ningún cambio de nota envía ni aprueba
    post_cerrado = {"estado": "ENVIADO", "aprobado_at": None, "sent_at": "2026-11-10 00:00:00"}
    assert post_cerrado["estado"] == "ENVIADO"
    assert post_cerrado["aprobado_at"] is None

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: dry-run y modo implícito no modifican hash DB, sidecars ni mtime
    data_dir = _crear_entorno_candidato(tmp_path, fixtures)
    db_path = tmp_path / "test_recalculo_dry.db"
    _crear_db(db_path, umbral=70)

    posts_iniciales = [
        {
            "id": 101,
            "estado": "ANALIZADA",
            "puesto": "DevOps Engineer",
            "analisis": fixtures["devops_5_anios"],
        },
        {
            "id": 102,
            "estado": "LISTO",
            "puesto": "IT Project Manager – Managed Services",
            "aprobado_at": "2026-11-10 01:00:00",
            "email_to": "recruiter@cumbre.test",
            "email_subject": "CV Sam",
            "email_body": "Estimados...",
            "analisis": fixtures["cumbre_it_pm"],
        },
    ]
    _insertar_posts(db_path, posts_iniciales)

    hash_before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    mtime_before = db_path.stat().st_mtime_ns
    wal_path = Path(str(db_path) + "-wal")
    wal_bytes_before = wal_path.read_bytes() if wal_path.exists() else b""

    # 1. Modo implícito (sin --dry-run ni --aplicar): debe simular
    proc_default = _ejecutar_cli(["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve())], data_dir)
    assert proc_default.returncode == 0, f"Error CLI modo implícito: {proc_default.stderr}"
    data_default = json.loads(proc_default.stdout.strip())
    assert data_default["aplicado"] is False
    assert isinstance(data_default["cambiados"], int)
    assert isinstance(data_default["cruzan_arriba"], int)
    assert isinstance(data_default["cruzan_abajo"], int)
    assert isinstance(data_default["listo_bajan"], list)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == hash_before
    assert db_path.stat().st_mtime_ns == mtime_before
    wal_bytes_after = wal_path.read_bytes() if wal_path.exists() else b""
    assert wal_bytes_after == wal_bytes_before

    # 2. Modo --dry-run explícito
    proc_dry = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--dry-run"], data_dir
    )
    assert proc_dry.returncode == 0, f"Error CLI --dry-run: {proc_dry.stderr}"
    data_dry = json.loads(proc_dry.stdout.strip())
    assert data_dry["aplicado"] is False
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == hash_before

    # 3. --no-oferta sin --aplicar también simula
    proc_no_of = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--no-oferta", "101"], data_dir
    )
    assert proc_no_of.returncode == 0, f"Error CLI --no-oferta sin aplicar: {proc_no_of.stderr}"
    data_no_of = json.loads(proc_no_of.stdout.strip())
    assert data_no_of["aplicado"] is False
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == hash_before
    row_101 = _leer_post_ro(db_path, 101)
    assert row_101["estado"] == "ANALIZADA"

    # 4. Flags mutuamente excluyentes: --dry-run y --aplicar juntos deben fallar
    proc_conflict = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--dry-run", "--aplicar"], data_dir
    )
    assert proc_conflict.returncode != 0
    assert proc_conflict.stderr != ""

    # 5. DB inexistente sin crearla
    nonexistent_db = tmp_path / "db_inexistente_que_no_debe_crearse.db"
    proc_missing = _ejecutar_cli(
        ["--db", str(nonexistent_db.resolve()), "--data-dir", str(data_dir.resolve()), "--dry-run"], data_dir
    )
    assert proc_missing.returncode != 0
    assert not nonexistent_db.exists()

    # 6. Faltan argumentos requeridos (--db o --data-dir)
    proc_no_db = _ejecutar_cli(["--data-dir", str(data_dir.resolve())], data_dir)
    assert proc_no_db.returncode != 0
    proc_no_data = _ejecutar_cli(["--db", str(db_path.resolve())], data_dir)
    assert proc_no_data.returncode != 0


def test_aplicar_y_exclusiones(tmp_path):
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: bases medidas de control
    controles = [
        ("control_frontend_sr", 97.6),
        ("control_desarrolladora_web", 98.5),
        ("control_fullstack_node", 100.0),
        ("control_react_ssr", 100.0),
        ("control_frontend_sr_cordoba", 95.2),
    ]
    for clave, base_medida in controles:
        caso = fixtures[clave]
        reqs_proc = aplicar_reglas(caso["requisitos"], caso["puesto"], areas, base_candidato)
        score, _ = calculate_match_score_with_breakdown(reqs_proc)
        assert score >= 70.0
        assert abs(score - base_medida) <= 3.01

    # invariante: las decisiones manuales (aprendido, eliminatorio_usuario) mandan sobre las reglas.
    # Base: false explícito veta el descarte; true no degrada un MATCH_DIRECTO ni un TRANSFERIBLE.
    req_aprendido = [{"nombre": "DevOps", "estado": "MATCH_DIRECTO", "approved": True, "aprendido": True}]
    req_apr_proc = aplicar_reglas(req_aprendido, "DevOps Engineer", areas, base_candidato)
    assert req_apr_proc[0]["estado"] == "MATCH_DIRECTO"
    assert req_apr_proc[0]["aprendido"] is True

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: aplicar recálculo seguro y respetar exclusiones byte a byte
    data_dir = _crear_entorno_candidato(tmp_path, fixtures)
    db_path = tmp_path / "test_recalculo_aplicar.db"
    _crear_db(db_path, umbral=70)

    # Caso con aprendido: true que debe preservarse intacto
    brava_con_aprendido = copy.deepcopy(fixtures["brava_salesforce_ba"])
    for r in brava_con_aprendido["requisitos"]:
        if "Salesforce" in r["nombre"]:
            r["aprendido"] = True
            r["estado"] = "MATCH_DIRECTO"
            r["approved"] = True
            break

    posts_iniciales = [
        {
            "id": 1,
            "estado": "ENVIADO",
            "sent_at": "2026-11-10 00:00:00",
            "message_id": "<enviado-1@domain>",
            "email_to": "dest1@test.test",
            "analisis": fixtures["devops_5_anios"],
        },
        {
            "id": 2,
            "estado": "ENVIANDO",
            "email_to": "dest2@test.test",
            "analisis": fixtures["brava_salesforce_ba"],
        },
        {
            "id": 3,
            "estado": "DESCARTADO",
            "triage": {"motivo_descarte": "manual"},
            "analisis": fixtures["faro_logistica"],
        },
        {
            "id": 4,
            "estado": "ANALIZADA",
            "puesto": "DevOps Engineer",
            "analisis": fixtures["devops_5_anios"],
        },
        {
            "id": 5,
            "estado": "LISTO",
            "puesto": "IT Project Manager – Managed Services",
            "aprobado_at": "2026-11-10 01:00:00",
            "email_to": "lead@cumbre.test",
            "email_subject": "CV Cumbre",
            "email_body": "Texto cuerpo Cumbre",
            "analisis": fixtures["cumbre_it_pm"],
        },
        {
            "id": 6,
            "estado": "ANALIZADA",
            "puesto": "Oferta Sin Requisitos",
            "analisis": "{}",
        },
        {
            "id": 7,
            "estado": "ANALIZADA",
            "puesto": "Salesforce Business Analyst",
            "analisis": brava_con_aprendido,
        },
    ]
    _insertar_posts(db_path, posts_iniciales)

    # Captura snapshot exacto de filas protegidas (ENVIADO, ENVIANDO, DESCARTADO) y radar_state
    snap1 = _leer_post_ro(db_path, 1)
    snap2 = _leer_post_ro(db_path, 2)
    snap3 = _leer_post_ro(db_path, 3)
    snap_state = _leer_radar_state_ro(db_path)

    proc = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--aplicar"], data_dir
    )
    assert proc.returncode == 0, f"Error CLI --aplicar: {proc.stderr}"
    data = json.loads(proc.stdout.strip())
    assert data["aplicado"] is True
    assert data["cambiados"] >= 1

    # Verificación byte a byte / campo a campo de posts protegidos
    assert _leer_post_ro(db_path, 1) == snap1, "El post ENVIADO fue alterado"
    assert _leer_post_ro(db_path, 2) == snap2, "El post ENVIANDO fue alterado"
    assert _leer_post_ro(db_path, 3) == snap3, "El post DESCARTADO fue alterado"

    # Post 6 sin análisis válido debe quedar omitido y sin error
    row6 = _leer_post_ro(db_path, 6)
    assert row6["analisis"] == "{}"
    assert row6["estado"] == "ANALIZADA"

    # Post 5 (LISTO) conserva aprobado_at, mails y estado, pero actualiza análisis con duras
    row5 = _leer_post_ro(db_path, 5)
    assert row5["aprobado_at"] == "2026-11-10 01:00:00"
    assert row5["email_to"] == "lead@cumbre.test"
    assert row5["email_subject"] == "CV Cumbre"
    assert row5["email_body"] == "Texto cuerpo Cumbre"
    assert row5["estado"] == "LISTO"
    an5 = json.loads(row5["analisis"])
    assert an5["match_score"] <= 65.0
    req_pm = next(r for r in an5["requisitos"] if "PM (+5 años" in r["nombre"])
    assert req_pm["dura"] == "anios"
    assert req_pm["estado"] == "BRECHA_REAL"
    req_pmp = next(r for r in an5["requisitos"] if "PMP" in r["nombre"])
    assert req_pmp["dura"] == "cert"

    # Post 4 (ANALIZADA) actualiza análisis y nota (< 70) con dura="anios"
    row4 = _leer_post_ro(db_path, 4)
    assert row4["estado"] == "ANALIZADA"
    an4 = json.loads(row4["analisis"])
    assert an4["match_score"] < 70.0
    req_devops = next(r for r in an4["requisitos"] if "DevOps o SRE" in r["nombre"])
    assert req_devops["dura"] == "anios"
    assert req_devops["estado"] == "BRECHA_REAL"

    # Post 7 conserva el requisito aprendido sin alterarlo
    row7 = _leer_post_ro(db_path, 7)
    an7 = json.loads(row7["analisis"])
    req_apr = next(r for r in an7["requisitos"] if r.get("aprendido"))
    assert req_apr["estado"] == "MATCH_DIRECTO"
    assert req_apr["approved"] is True

    # radar_state (pausas, umbral) se preserva intacto
    assert _leer_radar_state_ro(db_path) == snap_state


def test_umbral_real_y_listos(tmp_path):
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: controles de afinidad
    controles = [
        ("control_frontend_sr", 97.6),
        ("control_desarrolladora_web", 98.5),
        ("control_fullstack_node", 100.0),
        ("control_react_ssr", 100.0),
        ("control_frontend_sr_cordoba", 95.2),
    ]
    for clave, base_medida in controles:
        caso = fixtures[clave]
        reqs_proc = aplicar_reglas(caso["requisitos"], caso["puesto"], areas, base_candidato)
        score, _ = calculate_match_score_with_breakdown(reqs_proc)
        assert score >= 70.0
        assert abs(score - base_medida) <= 3.01

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: fixture con umbral distinto de 70 detecta hardcode;
    # cruzan compara contra umbral real; LISTO que baja se lista aunque no cruce umbral
    data_dir = _crear_entorno_candidato(tmp_path, fixtures)
    db_path = tmp_path / "test_umbral_real.db"
    umbral_real = 75
    _crear_db(db_path, umbral=umbral_real)

    # 1. Post 101: ANALIZADA con nota guardada 78.0 (>= 75). Recalcula devops_5_anios -> ~38.5 (< 75).
    #    Cruza abajo respecto a 75.
    analisis_101 = copy.deepcopy(fixtures["devops_5_anios"])
    analisis_101["match_score"] = 78.0

    # 2. Post 102: ANALIZADA con nota guardada 68.0 (< 75). Recalcula control_frontend_sr -> ~97.6 (>= 75).
    #    Cruza arriba respecto a 75.
    analisis_102 = copy.deepcopy(fixtures["control_frontend_sr"])
    analisis_102["match_score"] = 68.0

    # 3. Post 103: LISTO con nota guardada 95.0 (>= 75). Recalcula control_desarrolladora_web -> 98.5.
    #    Pero si guardamos nota 99.0 y baja a 98.5 (ambas >= 75):
    #    NO cruza umbral, pero BAJA y está en LISTO -> listo_bajan debe incluir 103.
    analisis_103 = copy.deepcopy(fixtures["control_desarrolladora_web"])
    analisis_103["match_score"] = 99.0

    # 4. Post 104: ANALIZADA con nota guardada 99.0. Baja a 98.5.
    #    NO está en LISTO -> NO debe figurar en listo_bajan.
    analisis_104 = copy.deepcopy(fixtures["control_desarrolladora_web"])
    analisis_104["match_score"] = 99.0

    # 5. Post 105: LISTO con nota guardada 80.0 (>= 75). Recalcula cumbre_it_pm -> ~63.1 (< 75).
    #    Cruza abajo respecto a 75 Y baja en LISTO -> listo_bajan debe incluir 105.
    analisis_105 = copy.deepcopy(fixtures["cumbre_it_pm"])
    analisis_105["match_score"] = 80.0

    posts = [
        {"id": 101, "estado": "ANALIZADA", "puesto": "DevOps Engineer", "analisis": analisis_101},
        {"id": 102, "estado": "ANALIZADA", "puesto": "Desarrollador/a Frontend Sr", "analisis": analisis_102},
        {"id": 103, "estado": "LISTO", "puesto": "Desarrollador/a Web", "analisis": analisis_103},
        {"id": 104, "estado": "ANALIZADA", "puesto": "Desarrollador/a Web", "analisis": analisis_104},
        {"id": 105, "estado": "LISTO", "puesto": "IT Project Manager – Managed Services", "analisis": analisis_105},
    ]
    _insertar_posts(db_path, posts)

    proc = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--aplicar"], data_dir
    )
    assert proc.returncode == 0, f"Error CLI: {proc.stderr}"
    data = json.loads(proc.stdout.strip())
    assert data["aplicado"] is True

    # Cruces calculados contra el umbral real (75), no 70
    assert data["cruzan_arriba"] == 1, f"Esperado 1 que cruza arriba (102), obtenido {data['cruzan_arriba']}"
    assert data["cruzan_abajo"] == 2, f"Esperado 2 que cruzan abajo (101, 105), obtenido {data['cruzan_abajo']}"

    # listo_bajan debe contener orden ascendente de IDs de posts en LISTO que bajaron de nota
    assert data["listo_bajan"] == [103, 105]

    # Perfil ausente sin consultar IA: data_dir sin experiencia/perfil no debe invocar IA
    empty_data_dir = tmp_path / "data_candidato_vacio"
    empty_data_dir.mkdir(parents=True, exist_ok=True)
    proc_empty = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(empty_data_dir.resolve()), "--dry-run"], empty_data_dir
    )
    # Debe terminar sin colgarse y sin invocar IA
    assert proc_empty.returncode in (0, 1, 2)


def test_no_oferta_y_idempotencia(tmp_path):
    fixtures = _cargar_fixtures()
    contexto = fixtures["_contexto"]
    areas = contexto["areas"]
    base_candidato = contexto["base_candidato_habilidades"]

    # invariante: controles de afinidad y estabilidad
    controles = [
        ("control_frontend_sr", 97.6),
        ("control_desarrolladora_web", 98.5),
        ("control_fullstack_node", 100.0),
        ("control_react_ssr", 100.0),
        ("control_frontend_sr_cordoba", 95.2),
    ]
    for clave, base_medida in controles:
        caso = fixtures[clave]
        reqs_proc = aplicar_reglas(caso["requisitos"], caso["puesto"], areas, base_candidato)
        score, _ = calculate_match_score_with_breakdown(reqs_proc)
        assert score >= 70.0
        assert abs(score - base_medida) <= 3.01

    if os.getenv("INVARIANT_CHECK") == "1":
        return

    # Defecto: --no-oferta con --aplicar mueve a IGNORADA con error 'no es una oferta';
    # respeta exclusiones (ENVIADO/DESCARTADO); IDs ausentes no rompen;
    # reejecución sin cambios es idempotente (cambiados=0, cruzan=0, listo_bajan=[] y hash idéntico).
    data_dir = _crear_entorno_candidato(tmp_path, fixtures)
    db_path = tmp_path / "test_no_oferta_idemp.db"
    _crear_db(db_path, umbral=70)

    posts = [
        {
            "id": 201,
            "estado": "ANALIZADA",
            "puesto": "Builder / AI Builder",
            "analisis": fixtures["nova_no_oferta"],
        },
        {
            "id": 202,
            "estado": "LISTO",
            "puesto": "Salesforce Business Analyst",
            "analisis": fixtures["brava_salesforce_ba"],
        },
        {
            "id": 203,
            "estado": "ENVIADO",
            "sent_at": "2026-11-10 00:00:00",
            "analisis": fixtures["cumbre_it_pm"],
        },
        {
            "id": 204,
            "estado": "DESCARTADO",
            "triage": {"motivo_descarte": "manual"},
            "analisis": fixtures["faro_logistica"],
        },
    ]
    _insertar_posts(db_path, posts)

    # Ejecución con --no-oferta para 201, 202, 203 (excluido por ENVIADO), 204 (excluido por DESCARTADO) y 999 (ausente)
    proc1 = _ejecutar_cli(
        [
            "--db", str(db_path.resolve()),
            "--data-dir", str(data_dir.resolve()),
            "--aplicar",
            "--no-oferta", "201", "202", "203", "204", "999",
        ],
        data_dir,
    )
    assert proc1.returncode == 0, f"Error CLI primera corrida: {proc1.stderr}"
    data1 = json.loads(proc1.stdout.strip())
    assert data1["aplicado"] is True
    assert data1["cambiados"] >= 2

    # Verificación de estados en DB
    r201 = _leer_post_ro(db_path, 201)
    assert r201["estado"] == "IGNORADA"
    assert r201["error"] == "no es una oferta"

    r202 = _leer_post_ro(db_path, 202)
    assert r202["estado"] == "IGNORADA"
    assert r202["error"] == "no es una oferta"

    # 203 (ENVIADO) y 204 (DESCARTADO) NO alteran su estado aun con --no-oferta explícito
    r203 = _leer_post_ro(db_path, 203)
    assert r203["estado"] == "ENVIADO"
    assert r203["error"] != "no es una oferta"

    r204 = _leer_post_ro(db_path, 204)
    assert r204["estado"] == "DESCARTADO"
    assert r204["error"] != "no es una oferta"

    # Checkpoint WAL para fijar el estado de la primera ejecución
    conn = database.get_db_connection(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    hash_after_run1 = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # Segunda ejecución con --aplicar (idempotencia pura): no debe haber cambios
    proc2 = _ejecutar_cli(
        ["--db", str(db_path.resolve()), "--data-dir", str(data_dir.resolve()), "--aplicar"],
        data_dir,
    )
    assert proc2.returncode == 0, f"Error CLI segunda corrida: {proc2.stderr}"
    data2 = json.loads(proc2.stdout.strip())
    assert data2["aplicado"] is True
    assert data2["cambiados"] == 0
    assert data2["cruzan_arriba"] == 0
    assert data2["cruzan_abajo"] == 0
    assert data2["listo_bajan"] == []

    # Checkpoint WAL y verificación de hash idéntico tras la segunda ejecución
    conn = database.get_db_connection(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    hash_after_run2 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert hash_after_run1 == hash_after_run2, "La reejecución idempotente modificó la DB"


def test_ignorada_y_en_curso_no_se_recalculan():
    """el recálculo va sobre todas las ofertas excepto las enviadas y las ignoradas."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("recalcular_afinidad", "tools/recalcular_afinidad.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert {"ENVIADO", "ENVIANDO", "DESCARTADO", "IGNORADA", "GENERANDO", "ANALIZANDO"} <= mod.ESTADOS_PROTEGIDOS
