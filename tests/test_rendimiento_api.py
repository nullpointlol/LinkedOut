"""Rendimiento con una base del tamaño de la real (~4600 posts del Radar con análisis de ~10 KB y ~170
postulaciones). Cada endpoint de lectura tiene un tope de tiempo estricto: si una consulta o un render del lado del
servidor se vuelve lento, el test falla.

Los topes salen de medir en la PC del usuario (Windows) con un margen para CI. Se informa la mediana de 3 llamadas.
"""
import json
import statistics
import time

import pytest
from fastapi.testclient import TestClient

import core.database
import server
from core import radar_store

pytestmark = [pytest.mark.rendimiento, pytest.mark.timeout(300)]

N_POSTS = 4600
N_POSTULACIONES = 170
ESTADOS = ["ANALIZADA"] * 50 + ["DESCARTADO"] * 30 + ["IGNORADA"] * 10 + ["LISTO"] * 4 + ["ENVIADO"] * 4 + ["ERROR"] * 2

# Topes en milisegundos (mediana de 3): lo medido el 11/11 en la PC de desarrollo por ~2,5 (margen para CI).
# Medido: status 26, posts 780 (todos), posts/{id} 9, procesos 510, lecturas 6, ofertas 48, ofertas/{id} 7, / 3.
TOPES_MS = {
    "GET /api/radar/status": 300,
    "GET /api/radar/posts": 1500,
    "GET /api/radar/posts/{id}": 150,
    "GET /api/radar/procesos": 1200,
    "GET /api/radar/lecturas": 150,
    "GET /api/ofertas": 400,
    "GET /api/ofertas/{id}": 150,
    "GET /": 150,
}


def _analisis(i):
    reqs = [{"nombre": f"Requisito {k} del aviso {i}", "tipo": "REQUISITO_EXCLUYENTE",
             "estado": ("MATCH_DIRECTO", "TRANSFERIBLE", "BRECHA_REAL")[k % 3], "approved": k % 3 != 2,
             "justificacion": "Justificación de prueba con algo de texto para pesar como un análisis real. " * 3}
            for k in range(14)]
    return {"es_oferta": True, "empresa": f"Empresa {i % 300}", "puesto": f"Analista {i % 40}",
            "dolores_empresa": ["Ordenar procesos y documentar"] * 3, "requisitos": reqs,
            "match_score": float(i % 101), "desglose_afinidad": {"brechas": [], "puntos_faltantes": 0}}


@pytest.fixture
def base_grande():
    conn = radar_store._connect()
    texto = "Buscamos Analista Funcional con SQL, Jira y relevamiento. Enviar CV a rrhh@empresa.test. " * 12
    filas = []
    for i in range(N_POSTS):
        filas.append((f"urn:li:activity:{100000 + i}", f"https://www.linkedin.com/feed/update/urn:li:activity:{100000 + i}/",
                      f"Autor {i}", "Recruiter", texto, "2 h", "consulta", json.dumps(["rrhh@empresa.test"]),
                      ESTADOS[i % len(ESTADOS)], json.dumps(_analisis(i), ensure_ascii=False),
                      json.dumps({"oferta": 1, "puesto": "Analista", "canal": "email"}),
                      "busqueda", "2026-11-10 10:00:00", "2026-11-10 10:00:00"))
    with conn:
        conn.executemany(
            "INSERT INTO linkedin_posts (urn, url, autor, cargo_autor, texto, fecha_rel, query, emails, estado, "
            "analisis, triage, fuente, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", filas)
    conn.close()
    ids = [core.database.create_postulacion(titulo=f"Analista {i}", empresa=f"Empresa {i}",
                                            contenido=texto, url="", origen="manual")
           for i in range(N_POSTULACIONES)]
    return ids


def _mediana_ms(client, metodo, url):
    tiempos = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = client.request(metodo, url)
        tiempos.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, f"{metodo} {url}: {r.status_code} {r.text[:200]}"
    return statistics.median(tiempos)


@pytest.mark.aislado
def test_endpoints_de_lectura_dentro_de_su_tope(base_grande, monkeypatch):
    from unittest.mock import AsyncMock
    from core.radar import RadarLoop
    stub = AsyncMock(spec=RadarLoop)
    stub.estado_cuentas = lambda: []
    monkeypatch.setattr(server, "radar_loop", stub)
    client = TestClient(server.app)
    post_id = radar_store.list_posts(limit=1)[0]["id"]
    urls = {
        "GET /api/radar/status": "/api/radar/status",
        "GET /api/radar/posts": "/api/radar/posts?limite_descartados=500",  # la llamada de la pantalla
        "GET /api/radar/posts/{id}": f"/api/radar/posts/{post_id}",
        "GET /api/radar/procesos": "/api/radar/procesos",
        "GET /api/radar/lecturas": "/api/radar/lecturas",
        "GET /api/ofertas": "/api/ofertas",
        "GET /api/ofertas/{id}": f"/api/ofertas/{base_grande[0]}",
        "GET /": "/",
    }
    medidos = {nombre: _mediana_ms(client, "GET", url) for nombre, url in urls.items()}
    informe = "\n".join(f"  {n}: {ms:.0f} ms (tope {TOPES_MS[n]} ms)" for n, ms in medidos.items())
    print("\nRendimiento con base grande:\n" + informe)
    lentos = {n: round(ms) for n, ms in medidos.items() if ms > TOPES_MS[n]}
    assert not lentos, f"superan su tope: {lentos}\n{informe}"
