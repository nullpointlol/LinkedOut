"""Tests rápidos locales: la suite completa corre solo en el CI de GitHub; en la PC se corren los tests de los
archivos tocados, con un worker y en prioridad baja, para que el portal real no se entere."""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import tests_rapidos  # noqa: E402
import verificar  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "radar_store.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_radar_store.py").write_text("from core import radar_store\n", encoding="utf-8")
    (tmp_path / "tests" / "test_otra_cosa.py").write_text("radar_store.get_post(1)\n", encoding="utf-8")
    (tmp_path / "tests" / "test_nada.py").write_text("import json\n", encoding="utf-8")
    (tmp_path / "tests" / "test_mapa.py").write_text("x = 'static/radar.js'\n", encoding="utf-8")
    return tmp_path


def test_un_test_tocado_se_corre_el_mismo(repo):
    assert tests_rapidos.tests_para(["tests/test_nada.py"], repo) == ["tests/test_nada.py"]


def test_un_modulo_tocado_corre_los_tests_que_lo_usan(repo):
    assert tests_rapidos.tests_para(["core/radar_store.py"], repo) == ["tests/test_otra_cosa.py",
                                                                     "tests/test_radar_store.py"]


def test_un_archivo_estatico_corre_los_tests_que_lo_nombran(repo):
    assert tests_rapidos.tests_para(["static/radar.js"], repo) == ["tests/test_mapa.py"]


def test_un_modulo_usado_por_demasiados_tests_corre_solo_los_suyos(repo, monkeypatch):
    monkeypatch.setattr(tests_rapidos, "TOPE_POR_ARCHIVO", 1)
    assert tests_rapidos.tests_para(["core/radar_store.py"], repo) == ["tests/test_radar_store.py"]


def test_lo_borrado_o_sin_tests_no_corre_nada(repo):
    assert tests_rapidos.tests_para(["docs/algo.md", "tests/test_borrado.py"], repo) == []


def test_corre_con_un_worker_en_prioridad_baja_y_sin_navegador(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    orden = []
    monkeypatch.setattr(tests_rapidos, "archivos_tocados", lambda: ["tests/test_tests_rapidos.py"])
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: orden.append("prioridad"))
    monkeypatch.setattr(tests_rapidos, "_pytest", lambda args: orden.append(args) or 0)
    assert tests_rapidos.main([]) == 0
    assert orden[0] == "prioridad"
    args = orden[1]
    assert args[args.index("-n") + 1] == "1"
    assert "not ui and not rendimiento" in args and "tests/test_tests_rapidos.py" in args


def test_sin_archivos_tocados_no_corre_nada(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    monkeypatch.setattr(tests_rapidos, "archivos_tocados", lambda: [])
    monkeypatch.setattr(tests_rapidos, "_pytest", lambda args: pytest.fail("no debía correr pytest"))
    assert tests_rapidos.main([]) == 0


def test_la_suite_completa_no_corre_fuera_del_ci(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "argv", ["verificar.py"])
    monkeypatch.setattr(verificar, "_correr", lambda nombre, args: pytest.fail("corrió la suite en la PC"))
    with pytest.raises(SystemExit) as salida:
        verificar.main()
    assert salida.value.code == 2
    assert "tests_rapidos.py" in capsys.readouterr().out


@pytest.mark.parametrize("argumento, etapas", [
    ([], ["Suite (paralelo, con cobertura)", "Interfaz con navegador real", "Rendimiento"]),
    (["--rapido"], ["Suite (paralelo, con cobertura)"]),
    (["--sin-suite"], ["Interfaz con navegador real", "Rendimiento"]),
])
def test_el_ci_puede_partir_la_compuerta_en_dos_jobs(monkeypatch, tmp_path, argumento, etapas):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: None)
    corridas = []
    monkeypatch.setattr(verificar, "_correr", lambda nombre, args: corridas.append(nombre))
    monkeypatch.setattr(sys, "argv", ["verificar.py", *argumento])
    verificar.main()
    assert corridas == etapas


@pytest.mark.parametrize("etapa, esperado", [
    ("linux", [("Suite en Linux (sin los tests de Windows)", "not ui and not rendimiento and not windows")]),
    ("windows", [("Tests de Windows", "(windows or tambien_windows) and not ui and not rendimiento")]),
    ("navegador", [("Interfaz con navegador real", "ui and not rendimiento"), ("Rendimiento", "rendimiento")]),
])
def test_etapas_del_ci_por_sistema(monkeypatch, tmp_path, etapa, esperado):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("LINKEDOUT_WORKERS", "auto")
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: None)
    corridas = []
    monkeypatch.setattr(verificar, "_correr", lambda nombre, args: corridas.append((nombre, args)))
    monkeypatch.setattr(sys, "argv", ["verificar.py", "--etapa", etapa])
    verificar.main()
    assert [(n, a[a.index("-m") + 1]) for n, a in corridas] == esperado
    if etapa != "navegador":
        args = corridas[0][1]
        assert "--cov=core" in args and not [a for a in args if a.startswith("--cov-fail-under")]
    if etapa == "linux":
        assert corridas[0][1][corridas[0][1].index("-n") + 1] == "auto"


def test_la_etapa_de_cobertura_combina_y_exige_el_piso(monkeypatch, tmp_path):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: None)
    llamadas = []

    class R:
        returncode = 0

    monkeypatch.setattr(verificar.subprocess, "run", lambda cmd, **kw: llamadas.append(cmd[3:]) or R())
    monkeypatch.setattr(sys, "argv", ["verificar.py", "--etapa", "cobertura"])
    verificar.main()
    assert llamadas == [["combine"], ["report", "--skip-covered", f"--fail-under={verificar.COBERTURA_MINIMA}"]]
