"""Ronda 3: experiencia personal, identidad en asuntos y calibración offline.

Plan: reproducir los defectos, corregir sin IA extra y ejecutar la selección
de la misión. La compuerta total queda a cargo del loop.
"""
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from core import email_composer as composer, quality_calibration as calibration
from core.profile_parser import parse_candidate_profile


@pytest.mark.parametrize("detalle", [
    "Migré registros con más de 20 años de antigüedad",
    "Administré equipos con más de 20 años de uso",
])
def test_antiguedad_de_objetos_no_es_experiencia(detalle):
    historial = f"## Empresa\n### Analista | 01/2018 - 01/2025\n- {detalle}"
    assert parse_candidate_profile(historial, "ANA PRUEBA")["anios_experiencia"] == 7


@pytest.mark.parametrize("declaracion", [
    "Con más de 12 años de experiencia en soporte.",
    "12 años de experiencia en soporte.",
    "Analista con más de 12 años de experiencia.",
])
def test_declaracion_no_reemplaza_las_fechas(declaracion):
    # una frase suelta nunca da la cifra; mandan las fechas (01/2018 - 01/2025 = 7).
    assert parse_candidate_profile("01/2018 - 01/2025", "ANA\n## Perfil\n" + declaracion)[
        "anios_experiencia"] == 7


PROFILE = {"nombre": "ANA — PRUEBA", "contacto": "ana@example.test"}
SUBJECT = "QA — ANA — PRUEBA — SQL"
EXPECTED = "QA - ANA — PRUEBA - SQL"
BODY = "Hola, adjunto mi CV.\n\nANA — PRUEBA"


def test_validar_final_conserva_nombre_en_asunto():
    mensaje = composer._validar_final({"subject": SUBJECT, "body": BODY},
                                     composer.build_email_signature(PROFILE), 180, [], "es")
    assert mensaje["subject"] == EXPECTED
    assert mensaje["body"] == BODY
    composer.validar_mensaje_final({}, mensaje, PROFILE, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("reescribir", [False, True])
async def test_composicion_y_segunda_version_protegen_asunto(monkeypatch, reescribir):
    monkeypatch.setattr(composer, "_cargar_voz", lambda: {})
    limpio = {"subject": SUBJECT, "body": BODY}
    respuestas = ([{"subject": SUBJECT, "body": "Por medio de la presente adjunto mi CV."}]
                  if reescribir else []) + [limpio]
    bridge = type("Bridge", (), {"run_prompt": AsyncMock(side_effect=respuestas)})()
    mensaje = await composer.compose_email({}, {"pitch": "Adjunto mi CV."}, PROFILE, bridge)
    assert mensaje["subject"] == EXPECTED
    assert mensaje["body"] == BODY
    assert "is_fallback" not in mensaje
    assert bridge.run_prompt.await_count == 1 + int(reescribir)


@pytest.mark.parametrize("etiquetado", [False, True])
def test_cli_selecciona_listo_recientes_y_calibra_todos_los_campos(tmp_path, monkeypatch, etiquetado):
    copia = tmp_path / "copia.sqlite"
    with sqlite3.connect(copia) as conn:
        conn.executescript("""
            CREATE TABLE postulaciones (id INTEGER PRIMARY KEY, cv_markdown TEXT, pitch TEXT);
            CREATE TABLE linkedin_posts (id INTEGER PRIMARY KEY, postulacion_id INTEGER,
                estado TEXT, email_subject TEXT, email_body TEXT, created_at TEXT, updated_at TEXT);
        """)
        for i in range(1, 13):
            conn.execute("INSERT INTO postulaciones VALUES (?, ?, ?)",
                         (100 + i, f"CV privado {i}", "SQL — API"))
            # Fecha en orden inverso al id: la selección debe ordenar por fecha.
            conn.execute("INSERT INTO linkedin_posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (i, 100 + i, "LISTO" if i <= 10 else "ERROR", f"Asunto privado {i}",
                          f"Cuerpo privado {i}", f"2026-11-{13-i:02d}", f"2026-11-{13-i:02d}"))
    etiquetas = tmp_path / "etiquetas.json"
    etiquetas.write_text(json.dumps({"1": ["raya"]}), encoding="utf-8")
    recibidos = []
    original = calibration.calibrar_senales

    def observar(casos):
        recibidos.extend(casos)
        return original(casos)

    monkeypatch.setattr(calibration, "calibrar_senales", observar)
    monkeypatch.chdir(tmp_path)
    argv = [str(copia)] + (["--etiquetas", str(etiquetas)] if etiquetado else [])
    assert calibration.main(argv) == 0
    assert [c["id"] for c in recibidos] == [str(i) for i in range(1, 11)]
    for i, caso in enumerate(recibidos, 1):
        assert (caso["cv"], caso["pitch"], caso["subject"], caso["body"]) == (
            f"CV privado {i}", "SQL — API", f"Asunto privado {i}", f"Cuerpo privado {i}")
        assert caso["falsas_positivas"] == (["raya"] if etiquetado and i == 1 else [])
    informe = (tmp_path / "_ref/calibracion.md").read_text(encoding="utf-8")
    assert "JOIN postulaciones" in informe and "LISTO" in informe
    assert "ORDER BY" in informe and "LIMIT 10" in informe
    assert "| raya | 10 | " + ("1 | 0.10" if etiquetado else "0 | 0.00") in informe
    assert all(f"caso-{i:02d}" in informe for i in range(1, 11))
    assert "privado" not in informe and str(copia) not in informe
    if not etiquetado:
        assert "Sin etiquetas humanas" in informe
