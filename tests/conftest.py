import json
import os
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

# `server` inicializa SQLite y otros archivos al importarse, antes de los fixtures.
# No heredar la carpeta configurada por el usuario para esa inicialización.
_DATOS_DE_IMPORTACION = TemporaryDirectory(prefix="linkedout_pytest_", ignore_cleanup_errors=True)
_DATA_DIR_TEST = Path(_DATOS_DE_IMPORTACION.name) / "data"
# Solo para el test de la guarda (test_guardas_produccion): fuerza otra carpeta y comprueba que la suite se corta
_DATA_DIR_TEST = Path(os.environ.get("LINKEDOUT_FORZAR_DATOS_DE_PRUEBA") or _DATA_DIR_TEST)
os.environ["LINKEDOUT_DATA_DIR"] = str(_DATA_DIR_TEST)
# Ninguna variable del nombre viejo heredada de la consola llega a los tests (core.paths la leería si falta la nueva)
for _nombre in [k for k in os.environ if k.upper().startswith("BUSCATRABAJO_")]:  # compatibilidad: se borra tras el deploy
    del os.environ[_nombre]
# .env de los tests: uno vacío propio. Nunca el .env real (Gmail) de la raíz del repo ni el de producción.
_ENV_TEST = Path(_DATOS_DE_IMPORTACION.name) / "tests.env"
_ENV_TEST.write_text("", encoding="utf-8")
os.environ["LINKEDOUT_ENV_FILE"] = str(_ENV_TEST)

# Guardas de producción: si los datos, el .env o un puerto apuntan a producción, la suite no corre; y
# conectarse al portal real (127.0.0.1:1111) falla en cualquier test.
from tests import guardas_produccion as _guardas  # noqa: E402

_PROBLEMAS = _guardas.problemas(
    _DATA_DIR_TEST, _ENV_TEST, os.environ,
    _guardas.rutas_de_produccion(Path.home(), _guardas.repo_principal(Path(__file__).resolve().parent)))
if _PROBLEMAS:
    pytest.exit("La suite apunta a producción: " + " | ".join(_PROBLEMAS), returncode=3)
_guardas.bloquear_portal_real()
# Perfil sintético explícito: conserva los nombres que esperan los tests heredados,
# con contactos de ejemplo y experiencia de prueba, sin leer data/candidato real.
shutil.copytree(Path(__file__).parent / "fixtures" / "candidato", _DATA_DIR_TEST / "candidato")

# CLI de Antigravity: los tests nunca llaman a la IA real. Un `agy` de prueba va primero en el PATH,
# también en la PC donde está el real: el bridge lo encuentra al crearse y, si un test llega a ejecutarlo sin simular
# la IA, falla enseguida con un mensaje claro (antes, en la PC, esos tests llamaban al agy real; en el CI de Linux, un
# archivo vacío daba «Exec format error»).
_AGY_FALSO = Path(_DATOS_DE_IMPORTACION.name) / "bin"
_AGY_FALSO.mkdir(parents=True, exist_ok=True)
MENSAJE_AGY_DE_PRUEBA = "agy de prueba: los tests no llaman a la IA real"
if os.name == "nt":
    (_AGY_FALSO / "agy.cmd").write_text(f"@echo {MENSAJE_AGY_DE_PRUEBA} 1>&2\r\n@exit /b 1\r\n", encoding="ascii")
else:
    (_AGY_FALSO / "agy").write_text(f"#!/bin/sh\necho '{MENSAJE_AGY_DE_PRUEBA}' >&2\nexit 1\n", encoding="ascii")
    (_AGY_FALSO / "agy").chmod(0o755)
os.environ["PATH"] = str(_AGY_FALSO) + os.pathsep + os.environ.get("PATH", "")

import core.database
import server
from core.expander import CapabilityExpanderAgent

VOZ_TEST = {
    "disponibilidad": "Mi disponibilidad para entrevistas es de lunes a viernes de 14 a 17 hs.",
    "ubicacion": "Soy de Villa Test, Buenos Aires.",
    "disponibilidad_en": "My availability for interviews is Monday to Friday, 2 to 5 pm (Argentina time).",
    "ubicacion_en": "I'm based in Villa Test, Buenos Aires.",
    "voz": "Hola, buenas tardes.\nVi la publicación y me gustaría conocer más.\n\nQuedo a disposición,\nSaludos,",
}


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, tmp_path_factory, monkeypatch):
    """Aisla completamente la base de datos y archivos de prueba para que los tests

    nunca toquen ni contaminen data/postulaciones.db ni los datos de producción/desarrollo.
    """
    # 1. Base de datos SQLite aislada en carpeta temporal
    test_db = tmp_path / "test_isolated_postulaciones.db"
    monkeypatch.setattr(core.database, "DB_PATH", test_db)
    # Los tests antiguos que buscan un respaldo en BASE_DIR/data quedan aislados también.
    monkeypatch.setattr(core.database, "BASE_DIR", tmp_path)
    core.database.init_db(test_db)

    # Respaldo sintético en BASE_DIR/data/postulaciones.db para tests aislados que lo requieran
    fixture_json = Path(__file__).parent / "fixtures" / "postulacion_2.json"
    if fixture_json.exists():
        respaldo_dir = tmp_path / "data"
        respaldo_dir.mkdir(parents=True, exist_ok=True)
        respaldo_db = respaldo_dir / "postulaciones.db"
        core.database.init_db(respaldo_db)
        with open(fixture_json, "r", encoding="utf-8") as f:
            fixture_p = json.load(f)
        conn = core.database.get_db_connection(respaldo_db)
        cols = list(fixture_p.keys())
        placeholders = ", ".join(["?"] * len(cols))
        vals = [
            json.dumps(fixture_p[k], ensure_ascii=False) if isinstance(fixture_p[k], (dict, list)) else fixture_p[k]
            for k in cols
        ]
        with conn:
            conn.execute(
                f"INSERT OR REPLACE INTO postulaciones ({', '.join(cols)}) VALUES ({placeholders})",
                vals
            )
        conn.close()

    # 2. Agente expansor de perfil aislado en carpeta temporal
    test_expand_file = tmp_path / "test_competencias_expandidas.json"
    test_expander = CapabilityExpanderAgent(storage_path=test_expand_file)
    monkeypatch.setattr(server, "expander_agent", test_expander)

    # 3. TestClient se presenta como cliente "testclient" y Host "testserver": el guard de solo-local
    #    del Radar los acepta únicamente a través de este hook explícito (en producción no existe).
    from core.radar_api import ESTADO_HOSTS_DE_TEST
    monkeypatch.setattr(server.app.state, ESTADO_HOSTS_DE_TEST, {"testclient", "testserver"}, raising=False)

    # 4. La lente LLM de señales de IA del Auditor no llama a agy en los tests (sus propios tests la
    #    prueban con un bridge falso vía `REVISION_IA_REAL`)
    from core.auditor import AuditorAgent

    async def _sin_senales(self, cv_markdown):
        return []

    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", _sin_senales)

    # 5. La voz del candidato para los mails es fija en los tests (no lee data/candidato/voz_email.md)
    from core import email_composer
    monkeypatch.setattr(email_composer, "load_voz_email", lambda: dict(VOZ_TEST))

    # 6. Tickets de errores: base y archivos propios de cada test, fuera de tmp_path (hay tests que comparan
    #    todo tmp_path para probar «sin efectos», y un 4xx genera un ticket)
    from core import tickets as _tickets
    monkeypatch.setattr(_tickets, "CARPETA", tmp_path_factory.mktemp("tickets"))

    # 7. Cuota de AGY: cada test arranca con cuota y sin escribir data/agy_errores.log
    from core import bridge as _bridge
    _bridge.CUOTA.restablecida()
    monkeypatch.setattr(_bridge, "_registrar_error_agy", lambda texto: None)

    yield

    # 8. El Radar por defecto del portal corre en su propio hilo (core/radar_hilo.py): si un test lo arrancó
    #    y no lo apagó, se apaga acá (nunca queda un Radar vivo entre tests)
    from core.radar_hilo import RadarEnHilo
    hilo_radar = getattr(server, "radar_loop", None)
    if isinstance(hilo_radar, RadarEnHilo) and hilo_radar.hilo_vivo:
        hilo_radar.cerrar_ya()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "presupuesto_real: el Radar lee las reglas_radar.json del DATA_DIR de prueba (presupuesto real)",
    )
    config.addinivalue_line("markers", "ui: abre un navegador real (Playwright) contra el portal en un puerto libre")
    config.addinivalue_line("markers", "rendimiento: mide tiempos contra topes estrictos")
    config.addinivalue_line(
        "markers",
        "busquedas_por_defecto: deja las búsquedas del Radar como arrancan en una base nueva (apagadas)",
    )
    config.addinivalue_line(
        "markers",
        "windows: solo tiene sentido en Windows (PowerShell, prioridades, Job Objects). Se agrega sola a todo test "
        "con un skipif cuyo motivo nombra Windows; el CI los corre en un job chico de Windows (-m windows)",
    )
    config.addinivalue_line(
        "markers", "tambien_windows: test portable que además corre en el job de Windows (TAMBIEN_EN_WINDOWS)")


# Tests portables que además corren en el job de Windows del CI: en Linux el PDF sale por ReportLab (no hay
# Edge) y las ramas de Windows del bridge no se ejecutan. En Windows prueban el motor real.
TAMBIEN_EN_WINDOWS = frozenset({
    "test_pdf_export.py", "test_cv_formatting_and_a4_e2e.py", "test_verify_offer_2_anti_ai_and_a4.py",
    "test_cv_density_and_ats_metadata.py", "test_anti_ai_replacements_and_typography.py",
    "test_candidate_profile_agnostic.py", "test_cv_mail_normalization.py",
    "test_bridge.py", "test_unit_bridge.py", "test_bridge_cancelacion.py", "test_bridge_no_window.py",
})


def pytest_collection_modifyitems(config, items):
    """Marca `windows` a los tests que solo corren en Windows: la suite principal corre en Linux en el CI.
    Y `tambien_windows` a los de TAMBIEN_EN_WINDOWS, que corren en los dos sistemas."""
    for item in items:
        if any("windows" in str(m.kwargs.get("reason", "")).lower() for m in item.iter_markers("skipif")):
            item.add_marker(pytest.mark.windows)
        elif item.path.name in TAMBIEN_EN_WINDOWS:
            item.add_marker(pytest.mark.tambien_windows)


@pytest.fixture(autouse=True)
def _busquedas_encendidas_en_tests_heredados(request, isolate_test_environment):
    """Las búsquedas del Radar arrancan apagadas, pero los tests del motor anteriores las suponen
    encendidas: en la base aislada de cada test se encienden, salvo con `busquedas_por_defecto`."""
    if request.node.get_closest_marker("busquedas_por_defecto"):
        return
    from core import radar_store
    radar_store.update_radar_state(busquedas_activas=True)


@pytest.fixture(autouse=True)
def _desactivar_presupuesto_en_tests_heredados(request, monkeypatch):
    """El Radar de los tests usa las reglas reales del perfil (fixture) con presupuesto de búsquedas sin límite
    práctico, salvo con la marca `presupuesto_real` (lee el reglas_radar.json del DATA_DIR de prueba)."""
    if request.node.get_closest_marker("presupuesto_real"):
        return
    from tests import reglas_prueba
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(busquedas_por_hora=10**6, busquedas_por_dia=10**6))

