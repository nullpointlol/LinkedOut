"""Script de palabras clave: base SQLite temporal, nunca data/ real."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.palabras_clave import lift, normalizar, reporte, tokens

RAIZ = Path(__file__).resolve().parent.parent


def _base(tmp_path):
    ruta = tmp_path / "p.db"
    conn = sqlite3.connect(ruta)
    conn.execute("CREATE TABLE linkedin_posts (id INTEGER PRIMARY KEY, query TEXT, texto TEXT, estado TEXT, "
                 "analisis TEXT, emails TEXT, created_at TEXT)")
    filas = []
    for i in range(4):
        filas.append(('"cv a" funcional', f"Analista funcional en CABA, enviar CV a rrhh{i}@ejemplo.com.ar",
                      "ENVIADO", None, '["a@ejemplo.com.ar"]'))
    filas.append(('"cv a" funcional', "Funcional SQL Jira CABA", "ANALIZADA", json.dumps({"match_score": 80}), "[]"))
    filas.append(('"cv a" funcional', "Funcional SQL Jira CABA", "ANALIZADA", json.dumps({"match_score": 40}), "[]"))
    for i in range(10):
        filas.append(('"cv a"', f"Hiring engineer in Dubai, send CV at hr{i}@gmail.com", "DESCARTADO", None,
                      '["x@correo-ficticio.test"]'))
    conn.executemany("INSERT INTO linkedin_posts (query, texto, estado, analisis, emails, created_at) "
                     "VALUES (?, ?, ?, ?, ?, datetime('now'))", filas)
    conn.commit()
    conn.close()
    return ruta


def test_normalizar_y_tokens():
    assert normalizar("Técnico ÁREA") == "tecnico area"
    t = tokens("Buscamos Analista Funcional en CABA: cv a juan@ejemplo.com.ar")
    assert {"analista", "funcional", "analista funcional", "caba", "ejemplo.com.ar", ".com.ar"} <= t
    assert "en" not in t and "a" not in t


def test_lift():
    assert lift(10, 0, 10, 100) > lift(1, 50, 10, 100)
    assert lift(0, 0, 0, 0) == 1.0


def test_reporte_ordena_por_lift(tmp_path):
    conn = sqlite3.connect(_base(tmp_path))
    texto = reporte(conn, dias=14, top=10)
    conn.close()
    seccion = texto.split("## Rendimiento")[0]
    lineas = [l for l in seccion.splitlines()[2:] if l.strip()]
    lifts = [float(l.split()[-1]) for l in lineas]
    assert lifts == sorted(lifts, reverse=True)
    assert any(l.startswith("funcional") for l in lineas)
    assert "dubai" not in seccion
    rend = texto.split("## Rendimiento")[1]
    assert '"cv a" funcional' in rend


def test_script_no_modifica_la_base(tmp_path):
    ruta = _base(tmp_path)
    antes = hashlib.sha256(ruta.read_bytes()).hexdigest()
    salida = subprocess.run([sys.executable, str(RAIZ / "scripts" / "palabras_clave.py"), "--db", str(ruta)],
                            capture_output=True, text=True, encoding="utf-8", cwd=RAIZ)
    assert salida.returncode == 0, salida.stderr
    assert "Palabras que separan" in salida.stdout
    assert hashlib.sha256(ruta.read_bytes()).hexdigest() == antes
