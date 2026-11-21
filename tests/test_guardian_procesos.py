"""Guardián de procesos («que no haya procesos inútiles e innecesarios NUNCA»).

La decisión es una función pura sobre una lista de procesos inventada: ningún test apaga nada real."""
import json
from datetime import datetime, timedelta

import pytest

from tools import guardian_procesos as g
from tools import temporal

AHORA = datetime(2026, 11, 16, 18, 0)
EDGE_DEV = r'"msedge.exe" --user-data-dir=C:\Users\usuario\LinkedOut\dev-datos\linkedin_profile_2 --no-first-run'


def p(pid, ppid, nombre, cmd="", minutos=5):
    return {"pid": pid, "ppid": ppid, "nombre": nombre, "cmd": cmd, "inicio": AHORA - timedelta(minutes=minutos)}


def portal(pid, puerto, ppid=1, minutos=5):
    return p(pid, ppid, "python.exe", f'"python.exe" -m uvicorn server:app --host 127.0.0.1 --port {puerto}', minutos)


def apagados(procesos, temporales=(), prod_activa=False, dev_activo=True):
    return {pid: motivo for pid, motivo in g.decidir(procesos, list(temporales), AHORA, prod_activa=prod_activa,
                                                     dev_activo=dev_activo)}


def test_el_dev_y_lo_que_cuelga_de_el_no_se_tocan():
    procesos = [portal(10, 1112), p(11, 10, "node.exe", "playwright run-driver"), p(12, 11, "msedge.exe", EDGE_DEV),
                p(13, 10, "agy.exe", "agy.EXE --model x", minutos=3)]
    assert apagados(procesos) == {}


def test_un_portal_en_otro_puerto_se_apaga():
    assert list(apagados([portal(10, 1112), portal(20, 1199, minutos=3000)])) == [20]


@pytest.mark.parametrize("activo,esperado", [(False, [10]), (True, [])])
def test_el_dev_solo_con_su_marca(activo, esperado):
    # producción siempre prendida y el dev apagado hasta que haga falta (dev.ps1 -Iniciar)
    procesos = [portal(10, 1112), p(12, 10, "msedge.exe", EDGE_DEV)]
    assert list(apagados(procesos, dev_activo=activo)) == esperado


@pytest.mark.parametrize("activa,esperado", [(False, [30]), (True, [])])
def test_produccion_solo_con_la_marca(activa, esperado):
    assert list(apagados([portal(30, 1111)], prod_activa=activa)) == esperado


def test_un_temporal_se_respeta_hasta_que_vence():
    vigente = {"pid": 40, "vence": (AHORA + timedelta(minutes=5)).isoformat(), "motivo": "prueba"}
    vencido = {"pid": 41, "vence": (AHORA - timedelta(minutes=1)).isoformat(), "motivo": "prueba"}
    procesos = [portal(40, 1197), portal(41, 1198), p(42, 1, "bash.exe", "sleep 600")]
    assert sorted(apagados(procesos, [vigente, vencido])) == [41]


def test_edge_de_linkedout_sin_portal_vivo_se_apaga():
    procesos = [p(50, 999, "msedge.exe", EDGE_DEV), p(51, 50, "msedge.exe", EDGE_DEV + " --type=renderer")]
    assert list(apagados(procesos)) == [50]  # se cierra el árbol desde la raíz


def test_agy_huerfano_o_vencido_se_apaga():
    procesos = [portal(10, 1112), p(60, 10, "agy.exe", "agy.EXE", minutos=20), p(61, 999, "agy.exe", "agy.EXE"),
                p(62, 10, "agy.exe", "agy.EXE", minutos=2)]
    assert sorted(apagados(procesos)) == [60, 61]


def test_pytest_de_mas_de_45_minutos_se_apaga():
    procesos = [p(70, 1, "python.exe", "python -m pytest -q", minutos=50), p(71, 1, "python.exe", "python -m pytest", minutos=10)]
    assert list(apagados(procesos)) == [70]


def test_nunca_toca_programas_del_usuario():
    procesos = [p(80, 1, "Antigravity.exe", "Antigravity.exe", minutos=9999), p(81, 1, "chrome.exe", "chrome", minutos=9999),
                p(82, 1, "pwsh.exe", "pwsh -NoExit", minutos=9999), p(83, 1, "claude.exe", "claude", minutos=9999),
                p(84, 1, "msedge.exe", '"msedge.exe" --profile-directory=Default', minutos=9999),
                p(85, 1, "python.exe", "python script_del_usuario.py", minutos=9999)]
    assert apagados(procesos) == {}


def test_temporal_anota_y_limpia_lo_que_murio(tmp_path, monkeypatch):
    registro = tmp_path / "procesos-temporales.json"
    monkeypatch.setattr(temporal, "REGISTRO", registro)
    temporal.anotar(123, minutos=30, motivo="portal de prueba 1197", ahora=AHORA)
    datos = json.loads(registro.read_text(encoding="utf-8"))
    assert datos[0]["pid"] == 123 and datos[0]["motivo"] == "portal de prueba 1197"
    assert datos[0]["vence"] == (AHORA + timedelta(minutes=30)).isoformat()
    temporal.limpiar(vivos={999})
    assert json.loads(registro.read_text(encoding="utf-8")) == []
