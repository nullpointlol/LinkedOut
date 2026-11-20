"""Compuerta nocturna: la suite completa corre de noche en la PC sobre develop y sobre las ramas pendientes de
merge, de a una, y se corta si el portal real se pone lento. Todo simulado: un repo git temporal con su remoto, un
portal HTTP falso y una compuerta falsa. Nunca el repo, el portal ni los datos reales."""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import compuerta_nocturna as cn  # noqa: E402

GUARDA = 60


def _git(carpeta, *args):
    r = subprocess.run(["git", "-C", str(carpeta), *args], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _commit(carpeta, archivo, texto, fecha=None):
    (Path(carpeta) / archivo).write_text(texto, encoding="utf-8")
    _git(carpeta, "add", archivo)
    entorno = {}
    if fecha:
        entorno = {"GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha}
    r = subprocess.run(["git", "-C", str(carpeta), "commit", "-q", "-m", f"cambio {archivo}"], capture_output=True,
                       text=True, env={**__import__("os").environ, **entorno})
    assert r.returncode == 0, r.stderr


@pytest.fixture
def repo(tmp_path):
    """Un remoto con develop, main, una rama pendiente reciente, una ya mergeada (rebasada) y una vieja."""
    remoto = tmp_path / "remoto.git"
    _git(tmp_path, "init", "-q", "--bare", str(remoto))
    trabajo = tmp_path / "trabajo"
    _git(tmp_path, "clone", "-q", str(remoto), str(trabajo))
    _git(trabajo, "config", "user.email", "prueba@example.org")
    _git(trabajo, "config", "user.name", "Prueba")
    _git(trabajo, "checkout", "-q", "-b", "develop")
    _commit(trabajo, "base.txt", "base")
    _git(trabajo, "push", "-q", "origin", "develop")
    _git(trabajo, "push", "-q", "origin", "develop:main")
    # pendiente y reciente
    _git(trabajo, "checkout", "-q", "-b", "pendiente", "develop")
    _commit(trabajo, "nuevo.txt", "nuevo")
    _git(trabajo, "push", "-q", "origin", "pendiente")
    # mergeada rebasada: el mismo cambio entra a develop con otro commit
    _git(trabajo, "checkout", "-q", "-b", "mergeada", "develop")
    _commit(trabajo, "mergeado.txt", "mergeado")
    _git(trabajo, "push", "-q", "origin", "mergeada")
    _git(trabajo, "checkout", "-q", "develop")
    _git(trabajo, "cherry-pick", "mergeada")
    _git(trabajo, "push", "-q", "origin", "develop")
    # vieja: commit de hace 30 días
    _git(trabajo, "checkout", "-q", "-b", "vieja", "develop")
    hace_un_mes = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    _commit(trabajo, "viejo.txt", "viejo", fecha=hace_un_mes)
    _git(trabajo, "push", "-q", "origin", "vieja")
    _git(trabajo, "checkout", "-q", "develop")
    return trabajo


def test_ramas_a_verificar_develop_primero_y_solo_las_pendientes_recientes(repo):
    ramas = cn.ramas_a_verificar(repo)
    assert [r.nombre for r in ramas] == ["develop", "pendiente"]
    assert all(len(r.sha) == 40 for r in ramas)


# ---------------------------------------------------------------- portal falso

class _Portal:
    def __init__(self):
        self.demora = 0.0
        portal = self

        class Manejador(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(portal.demora)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.servidor.server_port}/api/radar/status"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def cerrar(self):
        self.servidor.shutdown()


@pytest.fixture
def portal():
    p = _Portal()
    yield p
    p.cerrar()


def test_el_vigia_corta_ante_dos_respuestas_lentas_seguidas(portal):
    vigia = cn.VigiaPortal(portal.url, lento_s=0.3)
    assert vigia.medir() is False
    portal.demora = 0.6
    assert vigia.medir() is False  # una lenta sola no corta
    assert vigia.medir() is True   # dos seguidas, sí
    assert vigia.lentas == 2 and vigia.peor >= 0.3


def test_una_lenta_suelta_no_corta(portal):
    vigia = cn.VigiaPortal(portal.url, lento_s=0.3)
    portal.demora = 0.6
    assert vigia.medir() is False
    portal.demora = 0.0
    assert vigia.medir() is False
    portal.demora = 0.6
    assert vigia.medir() is False


def test_portal_apagado_no_corta_la_compuerta():
    vigia = cn.VigiaPortal("http://127.0.0.1:9/api/radar/status", lento_s=0.3)  # nadie escucha
    assert vigia.medir() is False and vigia.medir() is False


# ---------------------------------------------------------------- una rama

COMPUERTA_VERDE = [sys.executable, "-c", "print('3 passed in 1.00s'); print('Compuerta de calidad en verde.')"]
COMPUERTA_ROJA = [sys.executable, "-c",
                  "print('FAILED tests/test_x.py::test_uno - assert 1 == 2'); print('1 failed, 2 passed in 1.00s');"
                  " raise SystemExit(1)"]
COMPUERTA_LENTA = [sys.executable, "-c", "import time; print('arranca', flush=True); time.sleep(60)"]


def _config(tmp_path, comando, portal_url, **extra):
    return cn.Config(salida=tmp_path / "compuerta", comando=comando, portal_url=portal_url, intervalo_s=0.2,
                     lento_s=0.3, **extra)


def test_una_rama_verde(repo, tmp_path, portal):
    rama = cn.ramas_a_verificar(repo)[0]
    r = cn.verificar_rama(repo, rama, _config(tmp_path, COMPUERTA_VERDE, portal.url))
    assert r["resultado"] == "verde" and r["resumen"] == "3 passed in 1.00s" and r["fallados"] == []
    assert r["duracion_s"] >= 0 and Path(r["log"]).is_file()
    assert "worktree" not in _git(repo, "worktree", "list").split("\n")[-1] or len(
        _git(repo, "worktree", "list").splitlines()) == 1  # el worktree temporal se borró


def test_una_rama_roja_lista_los_fallados(repo, tmp_path, portal):
    rama = cn.ramas_a_verificar(repo)[1]
    r = cn.verificar_rama(repo, rama, _config(tmp_path, COMPUERTA_ROJA, portal.url))
    assert r["resultado"] == "rojo"
    assert r["fallados"] == ["tests/test_x.py::test_uno"] and r["resumen"] == "1 failed, 2 passed in 1.00s"


def test_el_portal_lento_corta_la_suite_y_lo_anota(repo, tmp_path, portal):
    portal.demora = 0.6
    rama = cn.ramas_a_verificar(repo)[0]
    inicio = time.monotonic()
    r = cn.verificar_rama(repo, rama, _config(tmp_path, COMPUERTA_LENTA, portal.url))
    assert time.monotonic() - inicio < 30, "no cortó la suite"
    assert r["resultado"] == "cortada" and "portal" in r["motivo"]
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


# ---------------------------------------------------------------- noche completa e informe

def test_la_noche_deja_el_informe_y_ultimo_json(repo, tmp_path, portal):
    config = _config(tmp_path, COMPUERTA_VERDE, portal.url)
    cn.correr_noche(repo, config, ahora=datetime(2026, 11, 14, 3, 0))
    informe = (config.salida / "20261114.md").read_text(encoding="utf-8")
    assert "develop" in informe and "pendiente" in informe and "verde" in informe
    ultimo = json.loads((config.salida / "ultimo.json").read_text(encoding="utf-8"))
    assert ultimo["fecha"] == "2026-11-14" and [r["rama"] for r in ultimo["ramas"]] == ["develop", "pendiente"]
    assert all(r["resultado"] == "verde" and len(r["sha"]) == 40 for r in ultimo["ramas"])


def test_un_commit_ya_verificado_en_verde_no_se_repite(repo, tmp_path, portal):
    config = _config(tmp_path, COMPUERTA_VERDE, portal.url)
    cn.correr_noche(repo, config, ahora=datetime(2026, 11, 14, 3, 0))
    config_roja = _config(tmp_path, COMPUERTA_ROJA, portal.url)  # si corriera, daría rojo
    cn.correr_noche(repo, config_roja, ahora=datetime(2026, 11, 15, 3, 0))
    ultimo = json.loads((config.salida / "ultimo.json").read_text(encoding="utf-8"))
    assert all(r["resultado"] == "verde" and r.get("reutilizado") for r in ultimo["ramas"])


def test_el_tope_de_la_noche_deja_las_ramas_restantes_sin_correr(repo, tmp_path, portal):
    config = _config(tmp_path, COMPUERTA_VERDE, portal.url, tope_s=0.0)
    cn.correr_noche(repo, config, ahora=datetime(2026, 11, 14, 3, 0))
    ultimo = json.loads((config.salida / "ultimo.json").read_text(encoding="utf-8"))
    assert {r["resultado"] for r in ultimo["ramas"]} == {"sin correr"}


def test_las_reglas_son_constantes_con_nombre():
    assert cn.LENTO_S == 3 and cn.INTERVALO_S == 15 and cn.LENTAS_PARA_CORTAR == 2
    assert cn.WORKERS == "4" and cn.DIAS_RAMA_ACTIVA == 7 and cn.TOPE_NOCHE_S == 3 * 3600


# ---------------------------------------------------------------- tarea programada (solo simulada)

windows = pytest.mark.skipif(sys.platform != "win32" or not __import__("shutil").which("powershell"),
                             reason="tarea programada de Windows")


@windows
def test_registrar_simulado_muestra_la_tarea_sin_registrarla(tmp_path):
    from tests import powershell_utf8
    hogar = tmp_path / "hogar"
    hogar.mkdir()
    entorno = dict(__import__("os").environ, USERPROFILE=str(hogar), HOME=str(hogar))
    codigo, salida = powershell_utf8.correr(RAIZ / "tools" / "compuerta_nocturna.ps1",
                                            ["-Registrar", "-Simular", "-NombreTarea", "LinkedOut-compuerta-prueba"],
                                            entorno)
    assert codigo == 0, salida
    for esperado in ("LinkedOut-compuerta-prueba", "03:00", "conhost.exe", "--headless", "3 h",
                     str(hogar / "LinkedOut" / "compuerta" / "herramienta")):
        assert esperado in salida, (esperado, salida)
    assert "Simulado: no se registró nada" in salida
    assert not (hogar / "LinkedOut").exists()
    r = __import__("subprocess").run(["powershell", "-NoProfile", "-Command",
                                      "Get-ScheduledTask -TaskName LinkedOut-compuerta-prueba -ErrorAction SilentlyContinue"],
                                     capture_output=True, text=True)
    assert "LinkedOut-compuerta-prueba" not in r.stdout


def test_el_script_tiene_bom_para_powershell_5():
    assert (RAIZ / "tools" / "compuerta_nocturna.ps1").read_bytes()[:3] == b"\xef\xbb\xbf"


# ---------------------------------------------------------------- exclusiones y orden

def test_las_ramas_de_excluir_txt_no_se_corren(repo, tmp_path):
    excluir = tmp_path / "excluir.txt"
    excluir.write_text("# reemplazadas\n\npendiente\n  otra/que-no-existe  \n", encoding="utf-8")
    assert [r.nombre for r in cn.ramas_a_verificar(repo, excluir=excluir)] == ["develop"]


def test_sin_excluir_txt_no_se_excluye_nada(repo, tmp_path):
    assert [r.nombre for r in cn.ramas_a_verificar(repo, excluir=tmp_path / "no-existe.txt")] == ["develop", "pendiente"]


def test_develop_primero_y_despues_de_la_mas_reciente_a_la_mas_vieja(repo):
    hace_tres_dias = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    _git(repo, "checkout", "-q", "-b", "anterior", "develop")
    _commit(repo, "anterior.txt", "anterior", fecha=hace_tres_dias)
    _git(repo, "push", "-q", "origin", "anterior")
    _git(repo, "checkout", "-q", "-b", "nueva", "develop")
    _commit(repo, "nueva.txt", "nueva")
    _git(repo, "push", "-q", "origin", "nueva")
    assert [r.nombre for r in cn.ramas_a_verificar(repo)] == ["develop", "nueva", "pendiente", "anterior"]


def test_la_noche_usa_el_excluir_txt_de_su_carpeta(repo, tmp_path, portal):
    config = _config(tmp_path, COMPUERTA_VERDE, portal.url)
    config.salida.mkdir(parents=True)
    (config.salida / "excluir.txt").write_text("pendiente\n", encoding="utf-8")
    cn.correr_noche(repo, config, ahora=datetime(2026, 11, 15, 3, 0))
    ultimo = json.loads((config.salida / "ultimo.json").read_text(encoding="utf-8"))
    assert [r["rama"] for r in ultimo["ramas"]] == ["develop"]
    assert "pendiente" in ultimo["excluidas"]
