"""Script de pruebas de sintaxis en LinkedIn (solo cuenta 2, de a una, dentro del presupuesto). Sin LinkedIn real."""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.linkedin_harvester import HarvestResult
from core.paths import MARCA_SESION, MARCA_SESION_VIEJA

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sintaxis_linkedin", RAIZ / "tools" / "sintaxis_linkedin.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)

AHORA = datetime(2026, 11, 11, 15, 0, 0)
CASOS = [{"id": "uno", "mide": "Frase exacta", "consulta": '"cv a"'},
         {"id": "dos", "mide": "OR simple", "consulta": '"cv a" OR "enviar cv"'}]


class Harvester:
    def __init__(self, resultado):
        self.resultado = resultado
        self.consultas, self.dumps, self.cerrado = [], [], False

    async def harvest(self, query, max_posts=30, max_scrolls=8, conocidos=None, paciencia=1):
        self.consultas.append(query)
        return self.resultado

    async def dump_debug(self, carpeta):
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "page.html").write_text("<html/>", encoding="utf-8")
        (carpeta / "screenshot.png").write_bytes(b"png")
        self.dumps.append(carpeta)

    async def close(self):
        self.cerrado = True


def con_posts(n):
    return HarvestResult(posts=[{"urn": f"urn:li:activity:{i}"} for i in range(n)],
                         lectura={"motivo_fin": "max_posts", "urns_parseados": [f"u{i}" for i in range(n)]})


@pytest.fixture
def entorno(tmp_path):
    perfil = tmp_path / "linkedin_profile_2"
    perfil.mkdir()
    (perfil / MARCA_SESION).write_text("x", encoding="utf-8")
    return {"perfil": perfil, "salida": tmp_path / "sintaxis"}


def correr(entorno, caso, harvester, ahora=AHORA):
    return sl.correr_caso(caso, perfil=entorno["perfil"], salida=entorno["salida"],
                          harvester_factory=lambda profile_dir: harvester, ahora=ahora)


def test_guarda_captura_html_y_resultado(entorno):
    h = Harvester(con_posts(4))
    r = correr(entorno, CASOS[0], h)
    assert h.consultas == ['"cv a"'] and h.cerrado
    assert r["posts"] == 4 and r["funciona"] is True and r["motivo_fin"] == "max_posts"
    carpeta = Path(r["carpeta"])
    assert (carpeta / "page.html").is_file() and (carpeta / "screenshot.png").is_file()
    guardados = sl.leer_resultados(entorno["salida"])
    assert [g["id"] for g in guardados] == ["uno"]


def test_sin_resultados_no_funciona(entorno):
    r = correr(entorno, CASOS[0], Harvester(HarvestResult(lectura={"motivo_fin": "sin_resultados"})))
    assert r["funciona"] is False and r["posts"] == 0


def test_solo_con_la_sesion_de_la_cuenta_2(entorno):
    (entorno["perfil"] / MARCA_SESION).unlink()
    with pytest.raises(sl.NoSePuede, match="cuenta 2"):
        correr(entorno, CASOS[0], Harvester(con_posts(1)))


def test_la_marca_de_sesion_vieja_tambien_vale(entorno):
    # compatibilidad: se borra tras el deploy (la cuenta 2 de producción tiene la marca con el nombre viejo)
    (entorno["perfil"] / MARCA_SESION).unlink()
    (entorno["perfil"] / MARCA_SESION_VIEJA).write_text("x", encoding="utf-8")
    assert correr(entorno, CASOS[0], Harvester(con_posts(1)))["funciona"] is True


def test_respeta_seis_por_hora_y_sesenta_por_dia(entorno):
    for i in range(6):
        correr(entorno, CASOS[0], Harvester(con_posts(1)), ahora=AHORA - timedelta(minutes=50 - i))
    with pytest.raises(sl.NoSePuede, match="por hora"):
        correr(entorno, CASOS[1], Harvester(con_posts(1)))
    assert sl.presupuesto_restante(sl.leer_resultados(entorno["salida"]), AHORA + timedelta(minutes=60)) == 6
    viejos = [{"id": "x", "fecha": (AHORA - timedelta(hours=2 + i / 10)).isoformat()} for i in range(60)]
    assert sl.presupuesto_restante(viejos, AHORA) == 0


def test_un_bloqueo_corta(entorno):
    with pytest.raises(sl.NoSePuede, match="bloque"):
        correr(entorno, CASOS[0], Harvester(HarvestResult(lectura={"motivo_fin": "bloqueo"})))
    assert sl.leer_resultados(entorno["salida"])[0]["motivo_fin"] == "bloqueo"


def test_siguiente_pendiente_y_informe(entorno):
    assert sl.siguiente(CASOS, []) == CASOS[0]
    correr(entorno, CASOS[0], Harvester(con_posts(3)))
    resultados = sl.leer_resultados(entorno["salida"])
    assert sl.siguiente(CASOS, resultados) == CASOS[1]
    texto = sl.informe(CASOS, resultados)
    assert "| Frase exacta | `\"cv a\"` | funciona | 3 | 2026-11-11 15:00 |" in texto
    assert "| OR simple | `\"cv a\" OR \"enviar cv\"` | pendiente |" in texto


def test_los_casos_del_repo_son_validos():
    casos = json.loads((RAIZ / "tools" / "sintaxis_linkedin_casos.json").read_text(encoding="utf-8"))
    ids = [c["id"] for c in casos]
    assert len(ids) == len(set(ids)) and all(c["mide"] and c["consulta"] for c in casos)


def test_caso_de_una_busqueda_del_usuario(tmp_path, monkeypatch):
    import core.paths
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    (tmp_path / "candidato").mkdir()
    (tmp_path / "candidato" / "busquedas.json").write_text(json.dumps({"busquedas": [
        {"nombre": "Mía", "activa": True, "consulta": '"cv a" propia'}]}), encoding="utf-8")
    caso = sl.caso_de_busqueda("mía")
    assert caso == {"id": "busqueda:Mía", "mide": "Búsqueda del usuario «Mía»", "consulta": '"cv a" propia'}
    assert sl.caso_de_busqueda("otra") is None
    # Las búsquedas del usuario no van al informe público
    assert "propia" not in sl.informe(CASOS, [dict(caso, fecha=AHORA.isoformat(), motivo_fin="max_posts",
                                                     posts=1, funciona=True)])
