"""Radar con dos cuentas de LinkedIn. Desde el 13/11 cada lectura usa la cuenta del perfil:
`cuenta_feed` para el feed y `cuenta_busquedas` para las búsquedas (hoy, principal y secundaria). Si esa cuenta no
puede (sin sesión, pausada o sin presupuesto), no se lee: nunca se cae en la otra. Nunca hay dos navegadores
abiertos."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.linkedin_harvester import HarvestResult
from core.radar import RadarLoop
from tests import reglas_prueba
from tests.test_radar_loop import FakeHarvester, make_radar

pytestmark = pytest.mark.presupuesto_real

PRINCIPAL, SECUNDARIA = radar_store.CUENTA_PRINCIPAL, radar_store.CUENTA_SECUNDARIA
BLOQUEO = HarvestResult(lectura={"motivo_fin": "bloqueo", "scrolls": 0, "pantallas": []})


def _lectura(cuenta=PRINCIPAL, motivo="max_posts", min_atras=10, query="q"):
    t = (datetime.now(timezone.utc) - timedelta(minutes=min_atras)).isoformat()
    return radar_store.registrar_lectura(
        query=query, fuente="busqueda", inicio=t, fin=t, scrolls=1, motivo_fin=motivo,
        pantallas=[], parseados=0, guardados=0, destinos={}, cuenta=cuenta,
    )


def _cuentas_de_lecturas():
    conn = radar_store._connect()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT query, fuente, motivo_fin, cuenta FROM radar_lecturas ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _cuentas_del_perfil(monkeypatch):
    """Las reglas reales (6/h y 60/día) con las cuentas que pidió el usuario: feed principal, búsquedas secundaria."""
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(cuenta_feed=PRINCIPAL, cuenta_busquedas=SECUNDARIA))


def _radar_dos_cuentas(tmp_path, principal=None, secundaria=None, con_sesion=True, queries=("q1",)):
    """Radar con un navegador falso por cuenta. `eventos` registra aperturas y cierres en orden."""
    principal = principal or FakeHarvester()
    secundaria = secundaria or FakeHarvester()
    eventos = []
    radar = make_radar(tmp_path, harvester=principal, queries=queries, now=datetime.now)

    def factory(profile_dir=None):
        cuenta = SECUNDARIA if profile_dir is not None else PRINCIPAL
        eventos.append(("abre", cuenta))
        return secundaria if profile_dir is not None else principal

    for nombre, fake in ((PRINCIPAL, principal), (SECUNDARIA, secundaria)):
        original = fake.close

        async def close(original=original, nombre=nombre):
            eventos.append(("cierra", nombre))
            await original()
        fake.close = close

    radar._harvester_factory = factory
    radar._perfil_secundario = tmp_path / "linkedin_profile_2"
    if con_sesion:
        radar._perfil_secundario.mkdir(parents=True, exist_ok=True)
        radar._marca_secundaria.write_text("ok", encoding="utf-8")
    radar._test_eventos = eventos
    return radar, principal, secundaria


# ---------------------------------------------------------------- una sola cuenta: nada cambia

@pytest.mark.asyncio
async def test_con_una_cuenta_configurada_para_todo_nada_cambia(tmp_path, monkeypatch):
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(cuenta_feed=PRINCIPAL, cuenta_busquedas=PRINCIPAL))
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path, con_sesion=False)
    assert radar.cuentas() == [PRINCIPAL]
    await radar.run_cycle()
    assert principal.calls == ["feed", "q1"] and secundaria.calls == []
    assert radar._test_eventos == [("abre", PRINCIPAL)]
    assert {fila[3] for fila in _cuentas_de_lecturas()} == {PRINCIPAL}


def test_lecturas_viejas_sin_cuenta_cuentan_como_principal(tmp_path):
    make_radar(tmp_path)
    conn = radar_store._connect()
    with conn:
        conn.execute("INSERT INTO radar_lecturas (query, fuente, inicio, motivo_fin, cuenta) VALUES (?,?,?,?,NULL)",
                     ("vieja", "busqueda", datetime.now(timezone.utc).isoformat(), "bloqueo"))
    conn.close()
    hace_una_hora = datetime.now(timezone.utc) - timedelta(hours=1)
    assert radar_store.contar_busquedas(hace_una_hora, cuenta=PRINCIPAL) == 1
    assert radar_store.contar_busquedas(hace_una_hora, cuenta=SECUNDARIA) == 0
    assert len(radar_store.inicios_bloqueo(hace_una_hora, cuenta=PRINCIPAL)) == 1


# ---------------------------------------------------------------- presupuesto y pausa por cuenta

def test_cada_cuenta_tiene_su_presupuesto(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    for i in range(6):
        _lectura(PRINCIPAL, min_atras=5 + i, query=f"q{i}")
    assert radar._presupuesto_busquedas(PRINCIPAL) == 0
    assert radar._presupuesto_busquedas(SECUNDARIA) == 6
    assert radar._cuenta_para_buscar() == SECUNDARIA
    for i in range(6):
        _lectura(SECUNDARIA, min_atras=5 + i, query=f"s{i}")
    assert radar._cuenta_para_buscar() is None  # sin presupuesto en la secundaria, nunca la principal


def test_ninguna_cuenta_supera_su_tope(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    for cuenta in (PRINCIPAL, SECUNDARIA):
        for i in range(6):
            _lectura(cuenta, min_atras=5 + i, query=f"{cuenta}{i}")
    assert radar._cuenta_para_buscar() is None


def test_la_pausa_es_de_cada_cuenta(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    _lectura(PRINCIPAL, motivo="bloqueo")
    assert radar._pausa_de(PRINCIPAL) is not None and radar._pausa_de(SECUNDARIA) is None
    # La pausa que cuenta es la de la cuenta de búsquedas
    assert radar._busquedas_pausadas_hasta() is None
    assert radar._cuenta_para_buscar() == SECUNDARIA
    _lectura(SECUNDARIA, motivo="bloqueo", min_atras=5)
    assert radar._busquedas_pausadas_hasta() is not None
    assert radar._cuenta_para_buscar() is None


def test_si_linkedin_pauso_a_las_dos_el_presupuesto_baja_un_tercio(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    assert radar._presupuesto_busquedas(PRINCIPAL) == 6
    for cuenta in (PRINCIPAL, SECUNDARIA):
        _lectura(cuenta, motivo="bloqueo", min_atras=23 * 60)
    assert radar._presupuesto_busquedas(PRINCIPAL) == 4
    assert radar._presupuesto_busquedas(SECUNDARIA) == 4


# ---------------------------------------------------------------- turnos y cambio de cuenta

@pytest.mark.asyncio
async def test_con_las_dos_cuentas_ninguna_busqueda_usa_la_principal(tmp_path):
    """con las dos cuentas con sesión, ninguna búsqueda usa la principal."""
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path, queries=("q1", "q2", "q3"))
    await radar.run_cycle()
    assert principal.calls == ["feed"]
    assert secundaria.calls == ["q1", "q2", "q3"]
    assert {(f[1], f[3]) for f in _cuentas_de_lecturas()} == {("feed", PRINCIPAL), ("busqueda", SECUNDARIA)}


@pytest.mark.asyncio
@pytest.mark.parametrize("motivo", ["sin_sesion", "pausada", "sin_presupuesto"])
async def test_si_la_secundaria_no_puede_no_se_busca_y_el_feed_sigue(tmp_path, motivo):
    """la secundaria sin sesión, pausada o sin presupuesto → 0 búsquedas (nunca la
    principal) y el feed sigue con la principal."""
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path, con_sesion=motivo != "sin_sesion",
                                                      queries=("q1", "q2"))
    if motivo == "pausada":
        _lectura(SECUNDARIA, motivo="bloqueo")
    if motivo == "sin_presupuesto":
        for i in range(6):
            _lectura(SECUNDARIA, min_atras=5 + i, query=f"s{i}")
    await radar.run_cycle()
    assert principal.calls == ["feed"] and secundaria.calls == []
    assert [f for f in _cuentas_de_lecturas() if f[1] == "busqueda" and f[3] == PRINCIPAL] == []


@pytest.mark.asyncio
async def test_un_bloqueo_no_pasa_la_busqueda_a_la_principal(tmp_path):
    secundaria = FakeHarvester({"q1": [BLOQUEO]})
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path, secundaria=secundaria, queries=("q1", "q2"))
    await radar.run_cycle()
    assert principal.calls == ["feed"]
    assert secundaria.calls == ["q1"]  # bloqueada: corta el ciclo, sin repetir con la principal
    assert _cuentas_de_lecturas() == [
        ("feed", "feed", "max_scrolls", PRINCIPAL),
        ("q1", "busqueda", "bloqueo", SECUNDARIA),
    ]
    assert (tmp_path / "radar_debug" / "bloqueo" / "page.html").is_file()
    assert radar._busquedas_pausadas_hasta() is not None


@pytest.mark.asyncio
async def test_nunca_hay_dos_navegadores_abiertos(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    await radar.run_cycle()
    abiertos = set()
    for accion, cuenta in radar._test_eventos:
        if accion == "abre":
            assert not abiertos, f"se abrió {cuenta} con {abiertos} todavía abierto"
            abiertos.add(cuenta)
        else:
            abiertos.discard(cuenta)
    assert radar._test_eventos == [("abre", PRINCIPAL), ("cierra", PRINCIPAL), ("abre", SECUNDARIA)]


@pytest.mark.asyncio
async def test_con_las_dos_pausadas_solo_se_lee_el_feed_con_la_principal(tmp_path):
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path)
    _lectura(PRINCIPAL, motivo="bloqueo")
    _lectura(SECUNDARIA, motivo="bloqueo")
    await radar.run_cycle()
    assert principal.calls == ["feed"] and secundaria.calls == []


@pytest.mark.asyncio
async def test_el_feed_vuelve_a_la_principal_en_el_ciclo_siguiente(tmp_path):
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path)
    _lectura(PRINCIPAL, motivo="bloqueo")
    await radar.run_cycle()
    assert principal.calls == ["feed"] and secundaria.calls == ["q1"]
    await radar.run_cycle()
    assert principal.calls == ["feed", "feed"]
    eventos = radar._test_eventos
    vuelta = [i for i in range(len(eventos) - 1)
              if eventos[i:i + 2] == [("cierra", SECUNDARIA), ("abre", PRINCIPAL)]]
    assert vuelta, f"el feed del segundo ciclo tiene que cerrar la cuenta 2 y abrir la principal: {eventos}"


# ---------------------------------------------------------------- inicio de sesión de la cuenta 2

@pytest.mark.asyncio
async def test_login_de_la_cuenta_2_deja_la_marca_y_la_habilita(tmp_path):
    secundaria = FakeHarvester(logged_in_seq=[False, True])
    radar, principal, secundaria = _radar_dos_cuentas(tmp_path, secundaria=secundaria, con_sesion=False)
    assert radar.cuentas() == [PRINCIPAL]
    radar.pedir_login_secundaria()
    await radar.run_cycle()
    assert secundaria.show_calls == 1, "tiene que mostrar la ventana para que el usuario inicie sesión"
    assert radar._marca_secundaria.is_file()
    assert radar.cuentas() == [PRINCIPAL, SECUNDARIA]
    assert radar._login_secundaria_pedido is False


def test_estado_de_las_cuentas_para_el_panel(tmp_path):
    radar, _, _ = _radar_dos_cuentas(tmp_path)
    _lectura(SECUNDARIA, motivo="bloqueo")
    estado = {c["cuenta"]: c for c in radar.estado_cuentas()}
    assert estado[PRINCIPAL] == {"cuenta": PRINCIPAL, "con_sesion": True, "en_uso": True, "pausada_hasta": None}
    assert estado[SECUNDARIA]["con_sesion"] is True and estado[SECUNDARIA]["en_uso"] is False
    assert estado[SECUNDARIA]["pausada_hasta"] is not None


# ---------------------------------------------------------------- API y panel

@pytest.fixture
def cliente(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    stub.estado_cuentas = lambda: [
        {"cuenta": PRINCIPAL, "con_sesion": True, "en_uso": True, "pausada_hasta": None},
        {"cuenta": SECUNDARIA, "con_sesion": False, "en_uso": False, "pausada_hasta": None},
    ]
    llamadas = []
    stub.pedir_login_secundaria = lambda: llamadas.append("login")
    monkeypatch.setattr(server, "radar_loop", stub)
    return TestClient(server.app), stub, llamadas


def test_status_informa_las_cuentas(cliente):
    client, _, _ = cliente
    cuentas = client.get("/api/radar/status").json()["cuentas"]
    assert [c["cuenta"] for c in cuentas] == [PRINCIPAL, SECUNDARIA]


def test_el_portal_no_tiene_boton_ni_endpoint_de_login(cliente):
    """el inicio de sesión de la cuenta 2 se hace a mano, no un botón del portal."""
    client, _, _ = cliente
    assert client.post("/api/radar/cuenta-2/login").status_code in (404, 405)
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    html = (raiz / "static" / "index.html").read_text(encoding="utf-8")
    js = (raiz / "static" / "radar.js").read_text(encoding="utf-8")
    assert "radarLoginCuenta2Btn" not in html and "radarLoginCuenta2Btn" not in js
    assert 'type="password"' not in html
