"""Contrato A: admisión compartida y cuota, sin procesos ni proveedores reales."""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import logging
import os
from types import SimpleNamespace

import pytest

from core import bridge as modulo_bridge
from core.auditor import AuditorAgent
from core.bridge import AntigravityBridge, CuotaAgotada, _EstadoCuota


# conftest sustituye esta lente antes de cada test; conservar la implementación real.
REVISION_REAL = AuditorAgent.revisar_senales_ia
VARIABLE = "LINKEDOUT_AGY_MAX_CONCURRENTES"
GUARDA = 60  # solo guarda contra bloqueos (con la PC cargada 10 s no alcanzaban)
LIBRE = {"agotada": False, "desde": None, "reintento": None,
         "renueva": None, "mensaje": ""}
SENAL = {"frase": "Experiencia sintética verificable.", "patron": "prueba",
         "reescritura": "Experiencia de prueba."}


@asynccontextmanager
async def tareas_controladas():
    """Drenar también si una aserción falla o una hija arroja CancelledError."""
    tareas = []

    def lanzar(coro, nombre=None):
        tarea = asyncio.create_task(coro, name=nombre)
        tareas.append(tarea)
        return tarea

    try:
        yield lanzar
    finally:
        for tarea in tareas:
            if not tarea.done():
                tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)


async def esperar(awaitable):
    # Sólo guarda contra bloqueos: el reloj real no decide ninguna aserción.
    return await asyncio.wait_for(awaitable, timeout=GUARDA)


async def asentar_cola():
    """Ceder turnos de event loop, incluido el hijo de wait_for de sondear_cuota."""
    for _ in range(3):
        turno = asyncio.Event()
        asyncio.get_running_loop().call_soon(turno.set)
        await turno.wait()


@pytest.fixture
def cuota_controlada(monkeypatch):
    cuota = _EstadoCuota()
    monkeypatch.setattr(modulo_bridge, "CUOTA", cuota)
    monkeypatch.delenv(VARIABLE, raising=False)
    ahora = [1_800_000_000.0]
    monotonic = modulo_bridge.time.monotonic
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(
        time=lambda: ahora[0], monotonic=lambda: monotonic() + ahora[0] - 1_800_000_000.0))
    entradas = asyncio.Queue()
    pendientes = []
    original = cuota.esperar_turno

    async def dormir(segundos):
        liberar = asyncio.Event()
        pendientes.append(liberar)
        entradas.put_nowait(asyncio.current_task())
        await liberar.wait()

    async def esperar_turno():
        return await original(dormir=dormir)

    def despertar():
        copia = list(pendientes)
        pendientes.clear()
        for liberar in copia:
            liberar.set()

    def avanzar(segundos):
        ahora[0] += segundos

    monkeypatch.setattr(cuota, "esperar_turno", esperar_turno)
    return SimpleNamespace(cuota=cuota, entradas=entradas, despertar=despertar,
                           avanzar=avanzar)


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["exito_viejo", "error_viejo", "seguidor", "sondeo_seguidor",
                                  "sondeo", "dueno_en_espera", "recuperacion"])
async def test_invariante_generacion_cuota_y_cancelacion(monkeypatch, cuota_controlada,
                                                       tmp_path, caso):
    control = cuota_controlada
    cuota = control.cuota
    iniciado = asyncio.Event()
    terminar = asyncio.Event()
    llamadas = []

    async def ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        llamadas.append((prompt, json_mode, timeout_seconds, effort, cwd))
        if prompt == "viejo" or prompt == "dueno" or prompt == modulo_bridge.PROMPT_SONDEO:
            iniciado.set()
            await terminar.wait()
        if caso == "error_viejo" and prompt == "viejo":
            raise RuntimeError("fallo sintético sin cuota")
        if caso == "recuperacion" and prompt == "recuperar" and len(llamadas) == 1:
            raise CuotaAgotada("RESOURCE_EXHAUSTED: cuota sintética")
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    async with tareas_controladas() as lanzar:
        if caso in ("exito_viejo", "error_viejo"):
            vieja = lanzar(AntigravityBridge().run_prompt("viejo"))
            await esperar(iniciado.wait())
            cuota.agotada("agotamiento posterior")
            estado = dict(cuota.estado())
            terminar.set()
            if caso == "error_viejo":
                with pytest.raises(RuntimeError, match="fallo sintético"):
                    await esperar(vieja)
            else:
                # invariante: base, la respuesta en vuelo se conserva.
                assert await esperar(vieja) == {"ok": "viejo"}
            # invariante: base, éxito/error viejo no borra el agotamiento posterior.
            assert cuota.estado() == estado and estado["agotada"] is True
            cuota.reintentar_ya()
            assert await esperar(AntigravityBridge().run_prompt("sucesor")) == {"ok": "sucesor"}
            # invariante: base, una respuesta de la generación nueva recupera la cuota.
            assert cuota.estado() == LIBRE
        elif caso in ("seguidor", "sondeo_seguidor"):
            cuota.agotada("agotamiento previo")
            cuota.reintentar_ya()
            dueno = lanzar(AntigravityBridge().run_prompt("dueno"))
            await esperar(iniciado.wait())
            estado = dict(cuota.estado())
            seguidor = lanzar(modulo_bridge.sondear_cuota() if caso == "sondeo_seguidor"
                              else AntigravityBridge().run_prompt("seguidor"))
            esperando = await esperar(control.entradas.get())
            # invariante: base, el seguidor (o el hijo del sondeo) espera sin ejecutar.
            assert esperando is not dueno
            if caso == "seguidor":
                assert esperando is seguidor
            seguidor.cancel()
            with pytest.raises(asyncio.CancelledError):
                await esperar(seguidor)
            tercero = lanzar(AntigravityBridge().run_prompt("tercero"))
            assert await esperar(control.entradas.get()) is tercero  # invariante: sólo un dueño.
            # invariante: base, cancelar al seguidor no libera el turno ajeno ni cambia el aviso.
            assert cuota.estado() == estado
            assert [x[0] for x in llamadas] == ["dueno"]
            terminar.set()
            assert await esperar(dueno) == {"ok": "dueno"}
            control.despertar()
            assert await esperar(tercero) == {"ok": "tercero"}
            assert cuota.estado() == LIBRE  # invariante: no se perdió la llamada pendiente.
        elif caso in ("sondeo", "dueno_en_espera"):
            cuota.agotada("cuota cancelable")
            estado = dict(cuota.estado())
            if caso == "sondeo":
                cancelada = lanzar(modulo_bridge.sondear_cuota())
                await esperar(iniciado.wait())
                estado = dict(cuota.estado())  # sondear adelanta el reintento.
            else:
                cancelada = lanzar(AntigravityBridge().run_prompt("dueno"))
                assert await esperar(control.entradas.get()) is cancelada  # invariante.
            cancelada.cancel()
            with pytest.raises(asyncio.CancelledError):
                await esperar(cancelada)
            assert cuota.estado() == estado  # invariante: cancelar no borra el agotamiento.
            cuota.reintentar_ya()
            assert await esperar(AntigravityBridge().run_prompt("sucesor")) == {"ok": "sucesor"}
            assert cuota.estado() == LIBRE  # invariante: el dueño cancelado libera su turno.
        else:
            argumentos = ("recuperar", False, 1, "low", tmp_path)
            pendiente = lanzar(AntigravityBridge().run_prompt(*argumentos))
            assert await esperar(control.entradas.get()) is pendiente  # invariante: pausa tras cuota.
            seguidor = lanzar(AntigravityBridge().run_prompt("seguidor"))
            assert await esperar(control.entradas.get()) is seguidor  # invariante: no intenta todavía.
            assert llamadas == [argumentos]  # invariante: base, un solo intento agotado.
            control.avanzar(10_000)  # supera el plazo de trabajo, sin esperar reloj real.
            control.despertar()
            assert await esperar(pendiente) == {"ok": "recuperar"}
            assert await esperar(seguidor) == {"ok": "seguidor"}
            # invariante: base, espera de cuota no consume plazo ni pierde argumentos/trabajo.
            assert llamadas == [argumentos, argumentos, ("seguidor", True, 180, None, None)]
            assert cuota.estado() == LIBRE
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


async def rafaga(monkeypatch, limite):
    """Siete instancias/etapas; el auditor y el sondeo usan sus caminos reales."""
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)
    liberar = asyncio.Event()
    todos = asyncio.Event()
    capacidad = asyncio.Event()
    llamadas = []
    instancias = []
    activos = 0
    pico = 0
    iniciados = 0
    etapas = ("analisis", "redactor", "auditoria", "remediacion", "completar", "mail", "sondeo")

    async def ejecutar(self, prompt, *args, **kwargs):
        nonlocal activos, pico
        llamadas.append(prompt)
        instancias.append(self)
        activos += 1
        pico = max(pico, activos)
        if activos >= limite:
            capacidad.set()
        try:
            await liberar.wait()
            return {"ok": prompt, "senales": [SENAL]}
        finally:
            activos -= 1

    async def invocar(i):
        nonlocal iniciados
        iniciados += 1
        if iniciados == 7:
            todos.set()
        with modulo_bridge.etapa(etapas[i]):
            if i == 6:
                return await modulo_bridge.sondear_cuota()
            bridge = AntigravityBridge()
            if i == 2:
                return await AuditorAgent(bridge=bridge).revisar_senales_ia(SENAL["frase"])
            return await bridge.run_prompt(etapas[i])

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    async with tareas_controladas() as lanzar:
        tareas = [lanzar(invocar(i), etapas[i]) for i in range(7)]
        await esperar(todos.wait())
        await esperar(capacidad.wait())
        await asentar_cola()
        # defecto: la base admite siete; el contrato exige un único cupo compartido.
        assert pico == limite, f"pico físico {pico}, límite global {limite}"
        assert len(llamadas) == limite
        liberar.set()
        resultados = await esperar(asyncio.gather(*tareas))
        assert len(llamadas) == 7 and len({id(x) for x in instancias}) == 7
        assert len(set(llamadas)) == 7 and activos == 0 and pico == limite
        assert resultados[2] == [SENAL], "la lente real debe atravesar el bridge"
        assert resultados[6] == LIBRE, "el sondeo también termina dentro del cupo global"
        for i in (0, 1, 3, 4, 5):
            assert resultados[i]["ok"] == etapas[i]


@pytest.mark.asyncio
@pytest.mark.parametrize("valor,limite,advertir", [
    (None, 2, True), ("1", 1, False), ("2", 2, False), ("4", 2, False),
    ("", 2, True), ("abc", 2, True), ("1.5", 2, True), ("0", 2, True), ("-2", 2, True),
])
async def test_limite_global_y_configuracion(monkeypatch, cuota_controlada, caplog,
                                            valor, limite, advertir):
    assert cuota_controlada.cuota.estado() == LIBRE  # invariante: base, cuota inicial libre.
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    if valor is not None:
        monkeypatch.setenv(VARIABLE, valor)
    with caplog.at_level(logging.WARNING, logger=modulo_bridge.__name__):
        await rafaga(monkeypatch, limite)
    if advertir:
        assert any(r.name == modulo_bridge.__name__ and r.levelno >= logging.WARNING
                   for r in caplog.records), "la configuración ausente/inválida debe avisar"
    # La capacidad se fija al primer uso del loop; no se redimensiona en el siguiente lote.
    monkeypatch.setenv(VARIABLE, "4" if limite != 4 else "1")
    await rafaga(monkeypatch, limite)


@pytest.mark.asyncio
@pytest.mark.parametrize("momento", ["admision", "cuota", "intento"])
async def test_cancelacion_no_pierde_permisos(monkeypatch, cuota_controlada, momento):
    control = cuota_controlada
    assert control.cuota.estado() == LIBRE  # invariante: base, no hay agotamiento heredado.
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    monkeypatch.setenv(VARIABLE, "1")
    inicio = asyncio.Event()
    liberar = asyncio.Event()
    llamadas = []

    async def ejecutar(self, prompt, *args, **kwargs):
        llamadas.append(prompt)
        if prompt == "ocupante":
            inicio.set()
            await liberar.wait()
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    async with tareas_controladas() as lanzar:
        if momento == "cuota":
            control.cuota.agotada("espera cancelable")
            cancelada = lanzar(AntigravityBridge().run_prompt("cancelada"))
            assert await esperar(control.entradas.get()) is cancelada
            cancelada.cancel()
            with pytest.raises(asyncio.CancelledError):
                await esperar(cancelada)
            # Tres esperas de cuota no deben reservar el único cupo físico.
            pendientes = []
            for i in range(3):
                pendientes.append(lanzar(AntigravityBridge().run_prompt(f"cuota-{i}")))
                await esperar(control.entradas.get())
            control.cuota.restablecida()
            assert await esperar(AntigravityBridge().run_prompt("libre")) == {"ok": "libre"}
            assert llamadas == ["libre"]
            control.despertar()
            assert await esperar(asyncio.gather(*pendientes)) == [
                {"ok": f"cuota-{i}"} for i in range(3)]
            assert Counter(llamadas) == Counter(["libre", "cuota-0", "cuota-1", "cuota-2"])
        else:
            ocupante = lanzar(AntigravityBridge().run_prompt("ocupante"))
            await esperar(inicio.wait())
            if momento == "admision":
                cancelada = lanzar(AntigravityBridge().run_prompt("cancelada"))
                await asentar_cola()
                assert llamadas == ["ocupante"], "la cancelada debe estar esperando admisión"
            else:
                cancelada = ocupante
            cancelada.cancel()
            with pytest.raises(asyncio.CancelledError):
                await esperar(cancelada)
            liberar.set()
            if momento == "admision":
                assert await esperar(ocupante) == {"ok": "ocupante"}
            assert await esperar(AntigravityBridge().run_prompt("sucesor")) == {"ok": "sucesor"}
        assert "cancelada" not in llamadas
    # Repetir exige tanto permisos completos como ausencia de liberaciones dobles.
    await rafaga(monkeypatch, 1)


@pytest.mark.asyncio
async def test_cuota_cambia_mientras_espera_admision(monkeypatch, cuota_controlada):
    control = cuota_controlada
    cuota = control.cuota
    assert cuota.estado() == LIBRE  # invariante: base, cuota inicial disponible.
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    monkeypatch.setenv(VARIABLE, "1")
    inicio = asyncio.Event()
    liberar = asyncio.Event()
    llamadas = []

    async def ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        llamadas.append((prompt, timeout_seconds))
        if prompt == "viejo":
            inicio.set()
            await liberar.wait()
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    async with tareas_controladas() as lanzar:
        vieja = lanzar(AntigravityBridge().run_prompt("viejo"))
        await esperar(inicio.wait())
        pendiente = lanzar(AntigravityBridge().run_prompt("pendiente", timeout_seconds=1))
        await asentar_cola()
        assert llamadas == [("viejo", 180)], "el pendiente debe esperar cupo"
        cuota.agotada("agotamiento mientras espera admisión")
        estado = dict(cuota.estado())
        control.avanzar(100)  # sólo admisión; aún no venció el reintento de cuota.
        liberar.set()
        assert await esperar(vieja) == {"ok": "viejo"}
        assert await esperar(control.entradas.get()) is pendiente
        # Debe revalidar CUOTA y soltar admisión antes de entrar en esta espera.
        assert llamadas == [("viejo", 180)] and cuota.estado() == estado
        assert not pendiente.done()
        # Simular recuperación administrativa sin despertar todavía al pendiente.
        cuota.restablecida()
        assert await esperar(AntigravityBridge().run_prompt("independiente")) == {"ok": "independiente"}
        assert llamadas == [("viejo", 180), ("independiente", 180)]
        # Nuevo agotamiento obliga a capturar la generación vigente, no la de la cola inicial.
        cuota.agotada("segunda generación de cuota")
        cuota.reintentar_ya()
        control.despertar()
        assert await esperar(pendiente) == {"ok": "pendiente"}
        assert llamadas[-1] == ("pendiente", 1), "la espera no consume plazo del proveedor"
        assert cuota.estado() == LIBRE, "el intento nuevo debe recuperar la generación actual"
    await rafaga(monkeypatch, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError, RuntimeError])
async def test_timeout_y_error_liberan_cupo(monkeypatch, cuota_controlada, error):
    assert cuota_controlada.cuota.estado() == LIBRE  # invariante: base, cuota sin pausa.
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    monkeypatch.setenv(VARIABLE, "1")
    inicio = asyncio.Event()
    fallar = asyncio.Event()
    llamadas = []

    async def ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        llamadas.append(prompt)
        if prompt == "falla":
            assert timeout_seconds == 1
            inicio.set()
            await fallar.wait()
            raise error("fallo de proveedor sintético")
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)
    async with tareas_controladas() as lanzar:
        fallida = lanzar(AntigravityBridge().run_prompt("falla", timeout_seconds=1))
        await esperar(inicio.wait())
        pendientes = [lanzar(AntigravityBridge().run_prompt(f"pendiente-{i}")) for i in range(6)]
        await asentar_cola()
        assert llamadas == ["falla"], "las otras seis deben esperar cupo"
        fallar.set()
        with pytest.raises(error, match="fallo de proveedor sintético"):
            await esperar(fallida)
        resultados = await esperar(asyncio.gather(*pendientes))
        assert resultados == [{"ok": f"pendiente-{i}"} for i in range(6)]
        assert Counter(llamadas) == Counter(["falla"] + [f"pendiente-{i}" for i in range(6)])
        assert cuota_controlada.cuota.estado() == LIBRE
    await rafaga(monkeypatch, 1)
