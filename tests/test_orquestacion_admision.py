"""Contratos de admisión global, 503, modo serie y prioridad por carril.

Feature: orquestacion-prioridades (Misión 03 - S4-concurrencia).
Garantiza los invariantes I1 a I6 sobre la base actual y fija en rojo
los contratos de tope 2, prioridad de generación, freno 503, modo serie y config parcial.
"""
import asyncio
import collections
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import server
from core import database
from core import bridge as modulo_bridge
from core import radar_store as store
from core.bridge import AntigravityBridge
from core.radar import RadarLoop
from tests.test_orquestacion_prioridades import comprobar_invariantes_actuales


# =====================================================================
# Context managers y utilidades para pruebas deterministas
# =====================================================================

@asynccontextmanager
async def tareas_controladas():
    """Drena tareas hijas en finally incluso ante aserción fallida o CancelledError."""
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


def drenar_loop_secundario(loop):
    """Cancela y reúne todas las tareas pendientes de un loop secundario antes de cerrarlo."""
    try:
        pendientes = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pendientes:
            t.cancel()
        if pendientes:
            loop.run_until_complete(asyncio.gather(*pendientes, return_exceptions=True))
    finally:
        loop.close()


async def esperar_en_cola(admision, n=1, timeout=5.0):
    """Espera de forma observable que haya al menos n llamadas esperando cupo en admisión."""
    t0 = asyncio.get_running_loop().time()
    while True:
        # 1. Si admision tiene atributo o método 'esperando'
        esp = getattr(admision, "esperando", None)
        if callable(esp):
            esp = esp()
        if isinstance(esp, int) and esp >= n:
            return
        if isinstance(esp, (list, tuple, set)) and len(esp) >= n:
            return

        # 2. Si admision tiene atributo 'cola' o 'pendientes'
        for attr in ("cola", "pendientes", "waiters", "_cola", "_pendientes"):
            c = getattr(admision, attr, None)
            if c is not None and (len(c) if hasattr(c, "__len__") else c) >= n:
                return

        # 3. Si admision usa convención estándar asyncio _waiters (Semaphore / Queue / Condition)
        waiters = getattr(admision, "_waiters", None)
        if waiters is not None and len(waiters) >= n:
            return

        if asyncio.get_running_loop().time() - t0 > timeout:
            raise AssertionError(f"Admisión no alcanzó {n} elementos en cola tras {timeout}s")
        await asyncio.sleep(0.001)


def _run_node_isolated(script: str):
    """Ejecuta un script Node de prueba de forma aislada con timeout estricto de 5s."""
    if shutil.which("node") is None:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        nombre = f.name
    try:
        res = subprocess.run(
            ["node", nombre],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
        )
        salida = res.stdout.strip()
        return json.loads(salida) if salida else None
    finally:
        try:
            os.unlink(nombre)
        except OSError:
            pass


# =====================================================================
# Tests de admisión, 503 y modo serie
# =====================================================================

@pytest.mark.asyncio
async def test_invariantes_bridge_actual(monkeypatch, tmp_path):
    """Mide los 6 invariantes I1-I6 y comprueba que N llamadas exitosas equivalen a N ejecuciones físicas."""
    # invariante: base actual cumple I1-I6 medido por el helper compartido de misión 01/02
    await comprobar_invariantes_actuales(monkeypatch, tmp_path)

    # invariante: I4 en bridge: N solicitudes exitosas = N intentos físicos exactos sin sondeos ni retries
    bridge = AntigravityBridge()
    ejecutadas = []

    async def mock_ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
        ejecutadas.append((prompt, json_mode, timeout_seconds, effort, cwd))
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar)

    res1 = await bridge.run_prompt("prompt_uno", json_mode=True, timeout_seconds=180)
    res2 = await bridge.run_prompt("prompt_dos", json_mode=False, timeout_seconds=60)
    assert res1 == {"ok": "prompt_uno"}
    assert res2 == {"ok": "prompt_dos"}
    assert len(ejecutadas) == 2
    assert ejecutadas[0] == ("prompt_uno", True, 180, None, None)
    assert ejecutadas[1] == ("prompt_dos", False, 60, None, None)

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: core.bridge reexporta carril_agy y expone estado_admision() según contrato
    assert callable(getattr(modulo_bridge, "carril_agy", None)), "Falta reexportar carril_agy en core.bridge"
    assert callable(getattr(modulo_bridge, "estado_admision", None)), "Falta contrato estado_admision() en core.bridge"

    adm = modulo_bridge.estado_admision()
    assert adm["limite"] == 2, f"Tope global por defecto debe ser 2, obtenido {adm.get('limite')}"
    assert adm["modo_serie"] is False
    assert adm["serie_hasta"] is None
    assert adm["historial"] == []

    # defecto: límite físico por defecto sin variable ambiental es 2 (en base actual es 3)
    loop = asyncio.get_running_loop()
    monkeypatch.delattr(loop, "_linkedout_agy_admision", raising=False)
    monkeypatch.delenv("LINKEDOUT_AGY_MAX_CONCURRENTES", raising=False)
    admision = modulo_bridge._admision_agy()
    capacidad = getattr(admision, "_value", None)
    assert capacidad == 2, f"La capacidad física por defecto en bridge debe ser 2, no {capacidad}"


@pytest.mark.asyncio
async def test_tope_dos_y_prioridad(monkeypatch, tmp_path):
    """Tope global de 2 llamadas simultáneas, preferencia de generación sobre análisis liberando un solo ocupante."""
    # invariante: base ejecuta llamadas concurrentes y secuenciales sin deadlock
    bridge_inv = AntigravityBridge()
    llamadas_inv = []

    async def mock_ejecutar_inv(self, prompt, *a, **kw):
        llamadas_inv.append(prompt)
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_inv)
    assert await bridge_inv.run_prompt("inv_1") == {"ok": "inv_1"}
    assert await bridge_inv.run_prompt("inv_2") == {"ok": "inv_2"}
    assert llamadas_inv == ["inv_1", "inv_2"]

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: tope de 2 llamadas, prioridad generación en cola y avance de análisis tras liberación
    assert callable(getattr(modulo_bridge, "carril_agy", None)), "Falta carril_agy en core.bridge"
    carril_agy = modulo_bridge.carril_agy

    bridge1 = AntigravityBridge()
    bridge2 = AntigravityBridge()

    gate_ocupante_1 = asyncio.Event()
    gate_ocupante_2 = asyncio.Event()
    gate_gen = asyncio.Event()
    gate_ana = asyncio.Event()

    empezo = {
        "ocupante_1": asyncio.Event(),
        "ocupante_2": asyncio.Event(),
        "trabajo_gen": asyncio.Event(),
        "trabajo_ana": asyncio.Event(),
    }

    activos = 0
    pico = 0
    iniciados = []
    terminados = []

    async def mock_ejecutar(self, prompt, *a, **kw):
        nonlocal activos, pico
        activos += 1
        pico = max(pico, activos)
        iniciados.append(prompt)
        if prompt in empezo:
            empezo[prompt].set()
        try:
            if prompt == "ocupante_1":
                await asyncio.wait_for(gate_ocupante_1.wait(), timeout=60)
            elif prompt == "ocupante_2":
                await asyncio.wait_for(gate_ocupante_2.wait(), timeout=60)
            elif prompt == "trabajo_gen":
                await asyncio.wait_for(gate_gen.wait(), timeout=60)
            elif prompt == "trabajo_ana":
                await asyncio.wait_for(gate_ana.wait(), timeout=60)
            return {"ok": prompt}
        finally:
            activos -= 1
            terminados.append(prompt)

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar)

    admision = modulo_bridge._admision_agy()

    async with tareas_controladas() as lanzar:
        # Iniciar dos ocupantes para llenar el tope de 2
        t1 = lanzar(bridge1.run_prompt("ocupante_1"))
        t2 = lanzar(bridge2.run_prompt("ocupante_2"))

        await asyncio.wait_for(
            asyncio.gather(empezo["ocupante_1"].wait(), empezo["ocupante_2"].wait()),
            timeout=60,
        )

        assert pico == 2, f"Pico debe alcanzar 2, actual {pico}"
        assert activos == 2
        assert len(iniciados) == 2

        # Encolar primero un análisis
        async def run_analisis():
            with carril_agy("analisis"):
                return await bridge1.run_prompt("trabajo_ana")

        t_ana = lanzar(run_analisis())
        # Sincronización observable: esperar que el análisis quede en cola antes de avanzar
        await esperar_en_cola(admision, 1)
        assert empezo["trabajo_ana"].is_set() is False, "Análisis en cola no debe ingresar con tope lleno"
        assert len(iniciados) == 2

        # Encolar después una generación
        async def run_generacion():
            with carril_agy("generacion"):
                return await bridge2.run_prompt("trabajo_gen")

        t_gen = lanzar(run_generacion())
        # Sincronización observable: esperar que ambos estén esperando en cola
        await esperar_en_cola(admision, 2)
        assert empezo["trabajo_gen"].is_set() is False, "Generación en cola no debe ingresar con tope lleno"
        assert len(iniciados) == 2

        # Liberamos UN SOLO ocupante: la generación debe tener prioridad e ingresar antes que el análisis
        gate_ocupante_1.set()
        await asyncio.wait_for(t1, timeout=60)

        # Esperamos a que la llamada con prioridad inicie de forma observable
        await asyncio.wait_for(empezo["trabajo_gen"].wait(), timeout=60)

        assert pico == 2, f"Pico físico nunca debe superar 2, observado {pico}"
        assert iniciados[2] == "trabajo_gen", f"Generación debía tener preferencia en cola, inició: {iniciados[2]}"
        assert empezo["trabajo_ana"].is_set() is False, "Análisis no debe haber empezado con solo un cupo liberado"
        assert activos == 2, "Con ocupante_2 y trabajo_gen activos debe haber exactamente 2"

        # Liberamos la generación
        gate_gen.set()
        await asyncio.wait_for(t_gen, timeout=60)

        # Ahora el análisis debe avanzar tras la liberación del cupo
        await asyncio.wait_for(empezo["trabajo_ana"].wait(), timeout=60)
        assert "trabajo_ana" in iniciados, "Análisis debe avanzar al liberarse cupo"
        assert pico == 2

        # Liberamos el análisis y el segundo ocupante
        gate_ana.set()
        gate_ocupante_2.set()
        await asyncio.wait_for(asyncio.gather(t2, t_ana), timeout=60)

    assert pico == 2
    assert activos == 0

    # defecto: dos loops consecutivos sin objetos ligados y con contención real fuera del loop activo
    def correr_loops_consecutivos():
        for idx in range(2):
            nl = asyncio.new_event_loop()
            try:
                async def tarea_contencion():
                    b1 = AntigravityBridge()
                    b2 = AntigravityBridge()
                    activos_l = 0
                    pico_l = 0
                    gate_l = asyncio.Event()

                    async def mock_ej(self, prompt, *a, **kw):
                        nonlocal activos_l, pico_l
                        activos_l += 1
                        pico_l = max(pico_l, activos_l)
                        try:
                            await asyncio.wait_for(gate_l.wait(), timeout=60)
                            return {"ok": prompt}
                        finally:
                            activos_l -= 1

                    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ej)
                    async with tareas_controladas() as lanzar_l:
                        t_a = lanzar_l(b1.run_prompt(f"c_{idx}_1"))
                        t_b = lanzar_l(b2.run_prompt(f"c_{idx}_2"))
                        t_c = lanzar_l(b1.run_prompt(f"c_{idx}_3"))

                        adm_l = modulo_bridge._admision_agy()
                        await esperar_en_cola(adm_l, 1)

                        assert pico_l == 2, f"En loop {idx}: contención debe alcanzar pico 2, obtenido {pico_l}"
                        gate_l.set()
                        res = await asyncio.wait_for(asyncio.gather(t_a, t_b, t_c), timeout=60)
                        assert len(res) == 3

                nl.run_until_complete(tarea_contencion())
            finally:
                drenar_loop_secundario(nl)

    running_loop = asyncio.get_running_loop()
    await asyncio.wait_for(running_loop.run_in_executor(None, correr_loops_consecutivos), timeout=60)


@pytest.mark.asyncio
async def test_cancelacion_no_pierde_permisos(monkeypatch, tmp_path):
    """Cancelación en cola o durante intento libera el cupo sin perder permisos ni duplicar."""
    # invariante: base cancelación en vuelo propaga CancelledError y no bloquea llamadas posteriores
    bridge_inv = AntigravityBridge()
    empezo_inv = asyncio.Event()

    async def mock_ejecutar_inv(self, prompt, *a, **kw):
        if prompt == "cancelar_base":
            empezo_inv.set()
            await asyncio.sleep(10)
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_inv)
    t_inv = asyncio.create_task(bridge_inv.run_prompt("cancelar_base"))
    await asyncio.wait_for(empezo_inv.wait(), timeout=60)
    t_inv.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_inv
    assert await asyncio.wait_for(bridge_inv.run_prompt("sucesor_inv"), timeout=60) == {"ok": "sucesor_inv"}

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: cancelación en cola y en intento bajo tope 2 no pierde permisos
    assert callable(getattr(modulo_bridge, "carril_agy", None)), "Falta carril_agy"
    carril_agy = modulo_bridge.carril_agy

    bridge1 = AntigravityBridge()
    bridge2 = AntigravityBridge()

    intentos_por_prompt = collections.defaultdict(int)

    # Caso A: Cancelación en cola
    gate_espera_a = asyncio.Event()
    activos_a = 0
    pico_a = 0
    empezo_a = {
        "activo_1": asyncio.Event(),
        "activo_2": asyncio.Event(),
        "ana_cancelable": asyncio.Event(),
        "gen_espera": asyncio.Event(),
    }

    async def mock_ejecutar_a(self, prompt, *a, **kw):
        nonlocal activos_a, pico_a
        intentos_por_prompt[prompt] += 1
        activos_a += 1
        pico_a = max(pico_a, activos_a)
        if prompt in empezo_a:
            empezo_a[prompt].set()
        try:
            if prompt in ("activo_1", "activo_2"):
                await asyncio.wait_for(gate_espera_a.wait(), timeout=60)
            return {"ok": prompt}
        finally:
            activos_a -= 1

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_a)

    admision = modulo_bridge._admision_agy()

    async with tareas_controladas() as lanzar:
        t1 = lanzar(bridge1.run_prompt("activo_1"))
        t2 = lanzar(bridge2.run_prompt("activo_2"))

        await asyncio.wait_for(
            asyncio.gather(empezo_a["activo_1"].wait(), empezo_a["activo_2"].wait()),
            timeout=60,
        )
        assert activos_a == 2
        assert pico_a == 2

        async def run_ana_cancel():
            with carril_agy("analisis"):
                return await bridge1.run_prompt("ana_cancelable")

        async def run_gen_espera():
            with carril_agy("generacion"):
                return await bridge2.run_prompt("gen_espera")

        t_ana = lanzar(run_ana_cancel())
        t_gen = lanzar(run_gen_espera())

        # Sincronización observable: esperar que ambos estén esperando en cola
        await esperar_en_cola(admision, 2)
        assert empezo_a["ana_cancelable"].is_set() is False
        assert empezo_a["gen_espera"].is_set() is False

        # Cancelamos el análisis mientras espera en cola
        t_ana.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(t_ana, timeout=60)

        # Liberamos los ocupantes
        gate_espera_a.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=60)

        # La generación en espera debe ingresar y completarse
        await asyncio.wait_for(t_gen, timeout=60)
        assert empezo_a["gen_espera"].is_set() is True
        assert empezo_a["ana_cancelable"].is_set() is False
        assert intentos_por_prompt["ana_cancelable"] == 0, "Llamada cancelada en cola debe tener 0 intentos"

        # Demostrar que tras la cancelación en cola se recuperan ambos permisos:
        # Retenemos dos sucesoras simultáneamente y medimos un nuevo pico de 2
        gate_retenidas_a = asyncio.Event()
        activos_suc_a = 0
        pico_suc_a = 0
        empezo_suc_a = {"suc_a1": asyncio.Event(), "suc_a2": asyncio.Event()}

        async def mock_ejecutar_suc_a(self, prompt, *a, **kw):
            nonlocal activos_suc_a, pico_suc_a
            intentos_por_prompt[prompt] += 1
            activos_suc_a += 1
            pico_suc_a = max(pico_suc_a, activos_suc_a)
            if prompt in empezo_suc_a:
                empezo_suc_a[prompt].set()
            try:
                await asyncio.wait_for(gate_retenidas_a.wait(), timeout=60)
                return {"ok": prompt}
            finally:
                activos_suc_a -= 1

        monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_suc_a)
        sa1 = lanzar(bridge1.run_prompt("suc_a1"))
        sa2 = lanzar(bridge2.run_prompt("suc_a2"))

        await asyncio.wait_for(
            asyncio.gather(empezo_suc_a["suc_a1"].wait(), empezo_suc_a["suc_a2"].wait()),
            timeout=60,
        )
        assert activos_suc_a == 2, "Ambas sucesoras deben estar activas simultáneamente tras cancelación en cola"
        assert pico_suc_a == 2, f"Pico nuevo tras cancelación en cola debe ser 2, obtenido {pico_suc_a}"
        gate_retenidas_a.set()
        await asyncio.wait_for(asyncio.gather(sa1, sa2), timeout=60)

    # Caso B: Cancelación en intento activo
    gate_espera_b = asyncio.Event()
    activos_b = 0
    pico_b = 0
    empezo_b = {
        "cancelar_en_intento": asyncio.Event(),
        "activo_otro": asyncio.Event(),
    }

    async def mock_ejecutar_b(self, prompt, *a, **kw):
        nonlocal activos_b, pico_b
        intentos_por_prompt[prompt] += 1
        activos_b += 1
        pico_b = max(pico_b, activos_b)
        if prompt in empezo_b:
            empezo_b[prompt].set()
        try:
            await asyncio.wait_for(gate_espera_b.wait(), timeout=60)
            return {"ok": prompt}
        finally:
            activos_b -= 1

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_b)

    async with tareas_controladas() as lanzar:
        t_intento = lanzar(bridge1.run_prompt("cancelar_en_intento"))
        t_otro = lanzar(bridge2.run_prompt("activo_otro"))

        await asyncio.wait_for(
            asyncio.gather(empezo_b["cancelar_en_intento"].wait(), empezo_b["activo_otro"].wait()),
            timeout=60,
        )
        assert activos_b == 2
        assert pico_b == 2

        # Cancelamos durante el intento activo
        t_intento.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(t_intento, timeout=60)

        # Liberamos el otro proceso activo
        gate_espera_b.set()
        await asyncio.wait_for(t_otro, timeout=60)

        # Demostrar que tras la cancelación en intento se recuperan ambos permisos:
        # Retenemos dos sucesoras simultáneamente y medimos un nuevo pico de 2
        gate_retenidas_b = asyncio.Event()
        activos_suc_b = 0
        pico_suc_b = 0
        empezo_suc_b = {"suc_b1": asyncio.Event(), "suc_b2": asyncio.Event()}

        async def mock_ejecutar_suc_b(self, prompt, *a, **kw):
            nonlocal activos_suc_b, pico_suc_b
            intentos_por_prompt[prompt] += 1
            activos_suc_b += 1
            pico_suc_b = max(pico_suc_b, activos_suc_b)
            if prompt in empezo_suc_b:
                empezo_suc_b[prompt].set()
            try:
                await asyncio.wait_for(gate_retenidas_b.wait(), timeout=60)
                return {"ok": prompt}
            finally:
                activos_suc_b -= 1

        monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_suc_b)
        sb1 = lanzar(bridge1.run_prompt("suc_b1"))
        sb2 = lanzar(bridge2.run_prompt("suc_b2"))

        await asyncio.wait_for(
            asyncio.gather(empezo_suc_b["suc_b1"].wait(), empezo_suc_b["suc_b2"].wait()),
            timeout=60,
        )
        assert activos_suc_b == 2, "Ambas sucesoras deben estar activas simultáneamente tras cancelación en intento"
        assert pico_suc_b == 2, f"Pico nuevo tras cancelación en intento debe ser 2, obtenido {pico_suc_b}"
        gate_retenidas_b.set()
        await asyncio.wait_for(asyncio.gather(sb1, sb2), timeout=60)

    # Verificación de solicitudes únicas e intentos exactos (sin retries ni duplicados)
    assert intentos_por_prompt["activo_1"] == 1
    assert intentos_por_prompt["activo_2"] == 1
    assert intentos_por_prompt["ana_cancelable"] == 0
    assert intentos_por_prompt["gen_espera"] == 1
    assert intentos_por_prompt["suc_a1"] == 1
    assert intentos_por_prompt["suc_a2"] == 1
    assert intentos_por_prompt["cancelar_en_intento"] == 1
    assert intentos_por_prompt["activo_otro"] == 1
    assert intentos_por_prompt["suc_b1"] == 1
    assert intentos_por_prompt["suc_b2"] == 1


@pytest.mark.asyncio
async def test_503_drena_extiende_y_expira(monkeypatch, tmp_path):
    """503 pasa a modo serie (límite 1), drena activas, extiende ante reiteración y expira a los 900s."""
    # invariante: base propaga error sin cuota y mantiene bridge funcional
    bridge_inv = AntigravityBridge()

    async def mock_ejecutar_inv(self, prompt, *a, **kw):
        if prompt == "fallo_base":
            raise RuntimeError("fallo controlado")
        return {"ok": prompt}

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_inv)
    with pytest.raises(RuntimeError, match="fallo controlado"):
        await bridge_inv.run_prompt("fallo_base")
    assert await bridge_inv.run_prompt("exito_base") == {"ok": "exito_base"}

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: sustituir reloj en bridge.time sin alterar time.time global
    t_sim = [1_000_000.0]
    reloj_simulado = SimpleNamespace(
        time=lambda: t_sim[0],
        monotonic=lambda: t_sim[0],
        sleep=lambda s: None,
    )
    monkeypatch.setattr(modulo_bridge, "time", reloj_simulado)

    assert callable(getattr(modulo_bridge, "estado_admision", None)), "Falta estado_admision"

    bridge1 = AntigravityBridge()
    bridge2 = AntigravityBridge()

    gate_bloqueo = asyncio.Event()
    gate_disparo_503 = asyncio.Event()
    gate_serie_1 = asyncio.Event()
    gate_serie_2 = asyncio.Event()

    empezo = {
        "llamada_activa": asyncio.Event(),
        "llamada_503": asyncio.Event(),
        "llamada_serie_1": asyncio.Event(),
        "llamada_serie_2": asyncio.Event(),
    }

    activos = 0
    pico = 0
    inicios = []

    async def mock_ejecutar(self, prompt, *a, **kw):
        nonlocal activos, pico
        activos += 1
        pico = max(pico, activos)
        inicios.append((prompt, t_sim[0]))
        if prompt in empezo:
            empezo[prompt].set()
        try:
            if prompt == "llamada_activa":
                await asyncio.wait_for(gate_bloqueo.wait(), timeout=60)
            elif prompt == "llamada_503":
                await asyncio.wait_for(gate_disparo_503.wait(), timeout=60)
                raise RuntimeError("503 Service Unavailable: overloaded")
            elif prompt == "llamada_serie_1":
                await asyncio.wait_for(gate_serie_1.wait(), timeout=60)
            elif prompt == "llamada_serie_2":
                await asyncio.wait_for(gate_serie_2.wait(), timeout=60)
            elif prompt == "segundo_503":
                raise RuntimeError("503 Service Unavailable: still overloaded")
            return {"ok": prompt}
        finally:
            activos -= 1

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar)

    admision = modulo_bridge._admision_agy()

    async with tareas_controladas() as lanzar:
        # Confirmar dos llamadas ya activas antes de disparar el error
        t_activa = lanzar(bridge1.run_prompt("llamada_activa"))
        t_503 = lanzar(bridge2.run_prompt("llamada_503"))

        await asyncio.wait_for(
            asyncio.gather(empezo["llamada_activa"].wait(), empezo["llamada_503"].wait()),
            timeout=60,
        )
        assert activos == 2, "Deben estar ambas llamadas activas simultáneamente antes de disparar el 503"
        assert pico == 2

        # Ahora disparamos el error 503 en la segunda llamada
        gate_disparo_503.set()
        with pytest.raises(RuntimeError) as exc_info:
            await asyncio.wait_for(t_503, timeout=60)
        assert "503" in str(exc_info.value), "Debe fallar con mensaje explícito de 503"

        # 503 activa freno: límite pasa a 1, serie_hasta = t_sim + 900
        adm = modulo_bridge.estado_admision()
        assert adm["limite"] == 1, f"Límite debe ser 1 ante 503, obtenido {adm['limite']}"
        assert adm["serie_hasta"] == 1_000_000.0 + 900, f"Vencimiento esperado {1_000_000.0 + 900}, obtenido {adm['serie_hasta']}"
        assert any(ev.get("motivo") == "Saturación AGY (503)" for ev in adm["historial"])

        # La llamada activa sigue corriendo: no se cancela, se drena
        assert activos == 1

        # Encolar llamadas suficientes para medir serie física: llamada_serie_1 y llamada_serie_2
        t_s1 = lanzar(bridge1.run_prompt("llamada_serie_1"))
        t_s2 = lanzar(bridge2.run_prompt("llamada_serie_2"))

        # Sincronización observable: esperar que ambas llamadas en serie estén esperando en cola bajo límite 1
        await esperar_en_cola(admision, 2)

        # Mientras se drena llamada_activa bajo límite 1, ninguna llamada nueva puede ingresar
        assert empezo["llamada_serie_1"].is_set() is False, "No debe ingresar bajo límite 1 con llamada activa"
        assert empezo["llamada_serie_2"].is_set() is False
        assert activos == 1

        # Liberamos la llamada activa drenada
        gate_bloqueo.set()
        await asyncio.wait_for(t_activa, timeout=60)

        # Ahora ingresa la primera llamada en serie
        await asyncio.wait_for(empezo["llamada_serie_1"].wait(), timeout=60)
        assert activos == 1, "En modo serie el cupo activo debe ser exactamente 1 (pico 1)"
        assert empezo["llamada_serie_2"].is_set() is False, "Segunda llamada en serie debe esperar a la primera"

        # Liberamos llamada_serie_1
        gate_serie_1.set()
        await asyncio.wait_for(t_s1, timeout=60)

        # Ahora ingresa llamada_serie_2 de forma serial
        await asyncio.wait_for(empezo["llamada_serie_2"].wait(), timeout=60)
        assert activos == 1
        gate_serie_2.set()
        await asyncio.wait_for(t_s2, timeout=60)

        # Interacción manual/automática con freno 503 vigente:
        # Activar serie manual conserva el freno y registra la transición, aunque el efectivo siga en 1
        store.update_radar_state(modo_serie=True)
        adm_man_act = modulo_bridge.estado_admision()
        assert adm_man_act["modo_serie"] is True
        assert adm_man_act["limite"] == 1
        assert adm_man_act["serie_hasta"] == 1_000_000.0 + 900, "Activar serie manual debe conservar freno 503"
        assert any(ev.get("motivo") == "Modo serie activado" and ev.get("limite") == 1 for ev in adm_man_act["historial"])

        # Desactivar serie manual conserva el freno y registra la transición, límite efectivo sigue en 1
        store.update_radar_state(modo_serie=False)
        adm_man_des = modulo_bridge.estado_admision()
        assert adm_man_des["modo_serie"] is False
        assert adm_man_des["limite"] == 1, "Desactivar serie manual con freno 503 vigente mantiene límite 1"
        assert adm_man_des["serie_hasta"] == 1_000_000.0 + 900, "Desactivar serie manual debe conservar vencimiento del freno 503"
        assert any(ev.get("motivo") == "Modo serie desactivado" and ev.get("limite") == 1 for ev in adm_man_des["historial"])

        # Segundo 503 a los 300 segundos: extiende vencimiento a último 503 + 900
        t_sim[0] = 1_000_300.0
        t_503_2 = lanzar(bridge1.run_prompt("segundo_503"))
        with pytest.raises(RuntimeError) as exc_info2:
            await asyncio.wait_for(t_503_2, timeout=60)
        assert "503" in str(exc_info2.value)

        adm_ext = modulo_bridge.estado_admision()
        assert adm_ext["limite"] == 1
        assert adm_ext["serie_hasta"] == 1_000_300.0 + 900, "Vencimiento debe extenderse a último 503 + 900"

        # A los 899s desde el segundo 503 sigue en modo serie (límite 1)
        t_sim[0] = 1_000_300.0 + 899.0
        assert modulo_bridge.estado_admision()["limite"] == 1

        # A los 900s exactos expira el freno automático y vuelve a límite 2
        t_sim[0] = 1_000_300.0 + 900.0
        adm_fin = modulo_bridge.estado_admision()
        assert adm_fin["limite"] == 2, f"Tras 900s debe volver a límite 2, obtenido {adm_fin['limite']}"
        assert any(ev.get("motivo") == "Fin del modo serie automático" and ev.get("limite") == 2 for ev in adm_fin["historial"])

        # Medir recuperación física tras vencer: ambas pueden correr simultáneamente (pico 2)
        gate_recup = asyncio.Event()
        activos_recup = 0
        pico_recup = 0
        empezo_recup = {"recup_1": asyncio.Event(), "recup_2": asyncio.Event()}

        async def mock_ejecutar_recup(self, prompt, *a, **kw):
            nonlocal activos_recup, pico_recup
            activos_recup += 1
            pico_recup = max(pico_recup, activos_recup)
            if prompt in empezo_recup:
                empezo_recup[prompt].set()
            try:
                await asyncio.wait_for(gate_recup.wait(), timeout=60)
                return {"ok": prompt}
            finally:
                activos_recup -= 1

        monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_recup)
        r1 = lanzar(bridge1.run_prompt("recup_1"))
        r2 = lanzar(bridge2.run_prompt("recup_2"))

        await asyncio.wait_for(
            asyncio.gather(empezo_recup["recup_1"].wait(), empezo_recup["recup_2"].wait()),
            timeout=60,
        )
        assert activos_recup == 2, "Tras vencer el freno debe admitir 2 llamadas simultáneas"
        assert pico_recup == 2
        gate_recup.set()
        await asyncio.wait_for(asyncio.gather(r1, r2), timeout=60)

        # Comprobar serie manual tras vencimiento del freno automático:
        store.update_radar_state(modo_serie=True)
        t_sim[0] = 1_002_000.0
        monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar)
        t_503_3 = lanzar(bridge1.run_prompt("segundo_503"))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(t_503_3, timeout=60)

        # Pasados 900s expira el freno automático pero el modo serie manual mantiene el límite en 1
        t_sim[0] = 1_002_000.0 + 1000.0
        adm_man = modulo_bridge.estado_admision()
        assert adm_man["limite"] == 1, "Modo serie manual debe mantener límite 1 tras expirar el freno automático"


@pytest.mark.asyncio
async def test_503_cli_no_es_cuota(monkeypatch, tmp_path):
    """503 en stderr, stdout o JSON con returncode 0 activa serie y propaga fallo; 429 separado."""
    # invariante: base reconoce 429 como cuota y activa CUOTA.agotada
    modulo_bridge.CUOTA.restablecida()
    err_429 = modulo_bridge.texto_de_error("", "Error 429: Too Many Requests", 1)
    assert bool(modulo_bridge._RE_CUOTA.search(err_429)) is True
    modulo_bridge.CUOTA.agotada(err_429)
    assert modulo_bridge.CUOTA.estado()["agotada"] is True
    modulo_bridge.CUOTA.restablecida()

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: doblar _crear_proceso y verificar parseo de 503 sin confundir con cuota
    fake_exe = tmp_path / "fake_agy.exe"
    fake_exe.write_text("dummy", encoding="utf-8")

    bridge = AntigravityBridge(agy_path=fake_exe)
    monkeypatch.setattr(bridge, "agy_path", fake_exe)

    class FakeProcess:
        def __init__(self, stdout=b"", stderr=b"", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

        async def communicate(self):
            return (self.stdout, self.stderr)

        async def wait(self):
            return self.returncode

        def kill(self):
            pass

    procesos_creados = []
    proceso_actual = [None]

    async def fake_crear_proceso(*cmd, **kwargs):
        proc = proceso_actual[0]
        procesos_creados.append(cmd)
        return proc

    monkeypatch.setattr(modulo_bridge, "_crear_proceso", fake_crear_proceso)

    def restablecer_freno_aislado():
        store.update_radar_state(modo_serie=False, agy_serie_hasta=None)

    # 1. 429 aislado: activa CUOTA, NO activa serie_hasta de 503
    restablecer_freno_aislado()
    proceso_actual[0] = FakeProcess(stderr=b"Error 429: Rate limit exceeded", returncode=1)
    t_sim = [1_000_000.0]
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: t_sim[0], monotonic=lambda: t_sim[0]))

    async with tareas_controladas() as lanzar:
        t_429 = lanzar(bridge.run_prompt("test_429"))
        t0 = asyncio.get_running_loop().time()
        while not modulo_bridge.CUOTA.estado()["agotada"]:
            if asyncio.get_running_loop().time() - t0 > 5.0:
                raise TimeoutError("CUOTA no se marcó como agotada en 5s")
            await asyncio.sleep(0.01)

        assert modulo_bridge.CUOTA.estado()["agotada"] is True
        adm_429 = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
        assert adm_429.get("serie_hasta") is None, "429 no debe activar freno por 503"
        t_429.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_429
        modulo_bridge.CUOTA.restablecida()

    # 2. 503 en stderr con returncode != 0 (Aislado: verifica que no hay freno previo)
    restablecer_freno_aislado()
    t_sim[0] = 1_005_000.0
    adm_prev_err = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {"limite": 2}
    assert adm_prev_err.get("serie_hasta") is None, "Debe iniciar aislado sin freno previo"

    procesos_creados.clear()
    proceso_actual[0] = FakeProcess(stderr=b"HTTP 503 Service Unavailable: model overloaded", returncode=1)
    with pytest.raises(RuntimeError) as exc_stderr:
        await asyncio.wait_for(bridge.run_prompt("p_stderr"), timeout=60)
    assert "503" in str(exc_stderr.value)
    assert len(procesos_creados) == 1, "503 no debe reintentar ni sondear"
    assert modulo_bridge.CUOTA.estado()["agotada"] is False, "503 no es agotamiento de cuota"
    adm_503 = modulo_bridge.estado_admision()
    assert adm_503["limite"] == 1
    assert adm_503["serie_hasta"] == 1_005_000.0 + 900

    # 3. 503 en JSON con returncode 0 (Aislado: restablece estado para no aprovechar el caso anterior)
    restablecer_freno_aislado()
    t_sim[0] = 1_010_000.0
    adm_prev_json = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {"limite": 2}
    assert adm_prev_json.get("serie_hasta") is None, "Debe iniciar aislado sin freno previo"

    procesos_creados.clear()
    proceso_actual[0] = FakeProcess(
        stdout=b'{"status": "ERROR", "error": "503 The model is overloaded. Please try again later."}',
        returncode=0,
    )
    with pytest.raises(RuntimeError) as exc_json:
        await asyncio.wait_for(bridge.run_prompt("p_json0"), timeout=60)
    assert "503" in str(exc_json.value)
    assert len(procesos_creados) == 1
    assert modulo_bridge.CUOTA.estado()["agotada"] is False
    adm_json = modulo_bridge.estado_admision()
    assert adm_json["limite"] == 1
    assert adm_json["serie_hasta"] == 1_010_000.0 + 900

    # 4. 503 en stdout con returncode != 0 (Aislado: restablece estado previo)
    restablecer_freno_aislado()
    t_sim[0] = 1_015_000.0
    adm_prev_stdout = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {"limite": 2}
    assert adm_prev_stdout.get("serie_hasta") is None, "Debe iniciar aislado sin freno previo"

    procesos_creados.clear()
    proceso_actual[0] = FakeProcess(stdout=b"503 Service Unavailable", returncode=1)
    with pytest.raises(RuntimeError) as exc_stdout:
        await asyncio.wait_for(bridge.run_prompt("p_stdout"), timeout=60)
    assert "503" in str(exc_stdout.value)
    assert len(procesos_creados) == 1
    assert modulo_bridge.CUOTA.estado()["agotada"] is False
    adm_stdout = modulo_bridge.estado_admision()
    assert adm_stdout["limite"] == 1
    assert adm_stdout["serie_hasta"] == 1_015_000.0 + 900


@pytest.mark.asyncio
async def test_config_parcial_persistente(monkeypatch, tmp_path):
    """PATCH /api/radar/config vincula admisión activa 1/2, mide efecto físico y recupera capacidad."""
    # invariante: base status responde 200 y contiene umbral_match
    client = TestClient(server.app)
    r_inv = client.get("/api/radar/status")
    assert r_inv.status_code == 200
    st_inv = r_inv.json()
    assert "umbral_match" in st_inv
    assert "generacion_pausada" in st_inv

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: mock observador de RadarLoop para verificar que no se detienen workers ni se despierta con pausa
    mock_loop = AsyncMock(spec=RadarLoop)
    mock_loop.is_running = True
    monkeypatch.setattr(server, "radar_loop", mock_loop)

    store.update_radar_state(
        generacion_pausada=True,
        analisis_pausado=True,
        envios_pausados=True,
        umbral_match=75,
        desired_running=True,
    )

    # 1. Activar modo serie vía PATCH
    r_patch = client.patch("/api/radar/config", json={"modo_serie": True})
    assert r_patch.status_code == 200, f"PATCH debe responder 200, obtenido {r_patch.status_code}"
    st_patch = r_patch.json()
    assert st_patch.get("modo_serie") is True, "status debe incluir modo_serie: True"
    assert st_patch.get("umbral_match") == 75, "PATCH parcial no debe pisar umbral_match"

    # I2: Todas las pausas se respetan intactas
    assert st_patch.get("generacion_pausada") is True, "I2: generacion_pausada debe permanecer True"
    assert st_patch.get("analisis_pausado") is True, "I2: analisis_pausado debe permanecer True"
    assert st_patch.get("envios_pausados") is True, "I2: envios_pausados debe permanecer True"

    # I2: Con generacion_pausada no arranca ningún trabajo nuevo de generación
    mock_loop.wake_generation.assert_not_awaited()

    # I5: La cosecha no se detiene; desired_running permanece activo y no se detienen workers
    st_store = store.get_radar_state()
    assert st_store.get("desired_running") is True, "I5: modo serie no altera desired_running de cosecha"
    mock_loop.stop.assert_not_awaited()
    mock_loop.stop_for_shutdown.assert_not_awaited()

    # Vinculación con admisión activa: modo_serie activo produce límite 1
    adm_activa = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_activa.get("limite") == 1, "Activar modo serie debe producir límite de admisión activo 1"
    assert adm_activa.get("modo_serie") is True

    # Medir efecto físico directo del modo serie activado: retener llamadas y comprobar pico 1
    bridge1 = AntigravityBridge()
    bridge2 = AntigravityBridge()
    gate_p1 = asyncio.Event()
    gate_p2 = asyncio.Event()
    empezo_p = {"p1": asyncio.Event(), "p2": asyncio.Event()}
    activos_p = 0
    pico_p = 0

    async def mock_ejecutar_patch(self, prompt, *a, **kw):
        nonlocal activos_p, pico_p
        activos_p += 1
        pico_p = max(pico_p, activos_p)
        if prompt in empezo_p:
            empezo_p[prompt].set()
        try:
            if prompt == "p1":
                await asyncio.wait_for(gate_p1.wait(), timeout=60)
            elif prompt == "p2":
                await asyncio.wait_for(gate_p2.wait(), timeout=60)
            return {"ok": prompt}
        finally:
            activos_p -= 1

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_patch)

    async with tareas_controladas() as lanzar:
        t_p1 = lanzar(bridge1.run_prompt("p1"))
        t_p2 = lanzar(bridge2.run_prompt("p2"))

        await asyncio.wait_for(empezo_p["p1"].wait(), timeout=60)
        # Con modo serie activo, pico físico debe ser exactamente 1; la segunda llamada espera
        assert activos_p == 1
        assert pico_p == 1
        assert empezo_p["p2"].is_set() is False, "Segunda llamada no debe entrar simultáneamente con modo serie activo"

        gate_p1.set()
        await asyncio.wait_for(t_p1, timeout=60)

        # Tras completarse la primera, ingresa la segunda llamada en serie
        await asyncio.wait_for(empezo_p["p2"].wait(), timeout=60)
        assert activos_p == 1
        assert pico_p == 1
        gate_p2.set()
        await asyncio.wait_for(t_p2, timeout=60)

    # 2. Desactivar modo serie vía PATCH y verificar vuelta a capacidad 2
    r_off = client.patch("/api/radar/config", json={"modo_serie": False})
    assert r_off.status_code == 200
    st_off = r_off.json()
    assert st_off.get("modo_serie") is False

    adm_vuelta = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_vuelta.get("limite") == 2, "Desactivar modo serie debe volver el límite activo a 2"
    assert adm_vuelta.get("modo_serie") is False

    # Medir recuperación física de capacidad 2 en la misma admisión tras desactivar serie
    gate_r1 = asyncio.Event()
    gate_r2 = asyncio.Event()
    empezo_r = {"r1": asyncio.Event(), "r2": asyncio.Event()}
    activos_r = 0
    pico_r = 0

    async def mock_ejecutar_recup(self, prompt, *a, **kw):
        nonlocal activos_r, pico_r
        activos_r += 1
        pico_r = max(pico_r, activos_r)
        if prompt in empezo_r:
            empezo_r[prompt].set()
        try:
            if prompt == "r1":
                await asyncio.wait_for(gate_r1.wait(), timeout=60)
            elif prompt == "r2":
                await asyncio.wait_for(gate_r2.wait(), timeout=60)
            return {"ok": prompt}
        finally:
            activos_r -= 1

    monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ejecutar_recup)

    async with tareas_controladas() as lanzar:
        t_r1 = lanzar(bridge1.run_prompt("r1"))
        t_r2 = lanzar(bridge2.run_prompt("r2"))

        # Ambas deben poder entrar concurrentemente (pico 2)
        await asyncio.wait_for(
            asyncio.gather(empezo_r["r1"].wait(), empezo_r["r2"].wait()),
            timeout=60,
        )
        assert activos_r == 2, "Al desactivar modo serie se debe recuperar la concurrencia física de 2"
        assert pico_r == 2
        gate_r1.set()
        gate_r2.set()
        await asyncio.wait_for(asyncio.gather(t_r1, t_r2), timeout=60)

    # Verificación en historial de procesos
    r_proc = client.get("/api/radar/procesos")
    assert r_proc.status_code == 200
    proc_data = r_proc.json()
    assert "agy" in proc_data, "GET /api/radar/procesos debe incluir clave 'agy'"
    historial = proc_data["agy"].get("historial", [])
    assert any(ev.get("motivo") == "Modo serie activado" for ev in historial)
    assert any(ev.get("motivo") == "Modo serie desactivado" for ev in historial)

    # PATCH con umbral_match inválido debe rechazar con 422
    r_invalido = client.patch("/api/radar/config", json={"umbral_match": 150})
    assert r_invalido.status_code == 422


def test_historial_y_control_serie(monkeypatch, tmp_path):
    """UI: control checkbox #radarModoSerie, label visible >= 12px, flujo DOM, captura PATCH y aviso de saturación."""
    # invariante: #vista-radar oculto y panel de procesos existente
    html_path = Path("static/index.html")
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    vista = soup.find(id="vista-radar")
    assert vista is not None
    assert vista.has_attr("hidden"), "#vista-radar debe iniciar oculta con atributo hidden"
    panel = vista.find(id="radarProcesosPanel")
    assert panel is not None, "Debe existir #radarProcesosPanel"

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # El contrato crea el control desde JS: observar el DOM tras init, no el HTML fuente.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                content_type="application/json", body="[]" if "/posts" in route.request.url else "{}"))
            # Quitar scripts del documento para ejecutar solo radar.js, sin CDN ni app.js.
            html = html_path.read_text(encoding="utf-8")
            import re
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
            page.set_content(html)
            page.add_script_tag(content="window.escapeHtml = x => String(x ?? '');")
            page.add_script_tag(path=str(Path("static/radar.js").resolve()))
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.wait_for_selector("#radarModoSerie", state="attached", timeout=3000)
            panel = BeautifulSoup(page.locator("#radarProcesosPanel").evaluate(
                "el => el.outerHTML"), "html.parser")
        finally:
            browser.close()

    # defecto: control checkbox #radarModoSerie en panel de procesos con label visible
    checkbox = panel.find(id="radarModoSerie")
    assert checkbox is not None, "Falta control checkbox #radarModoSerie en #radarProcesosPanel"
    assert checkbox.get("type") == "checkbox"

    label = panel.find("label", attrs={"for": "radarModoSerie"}) or checkbox.find_parent("label")
    assert label is not None, "Falta label asociado al checkbox #radarModoSerie"
    assert "Modo serie" in label.get_text()

    classes = set(label.get("class", []))
    assert not {"text-zinc-500", "text-zinc-600"}.intersection(classes), "No usar text-zinc-500/600 por regla de legibilidad"
    # Rediseño E2: .proc-serie de radar.css con un tamaño de texto de tokens (>= 12 px)
    css = Path("static/css/radar.css").read_text(encoding="utf-8")
    regla = css[css.index(".proc-serie {"):]
    assert "proc-serie" in classes and "font-size: var(--fs-1" in regla[:regla.index("}")], "Texto del label debe ser >= 12px"

    # defecto: comportamiento DOM observable en Node (captura PATCH, persistencia y renderizado de historial)
    if shutil.which("node") is not None:
        js_file = json.dumps(str(Path("static/radar.js").resolve()))
        resultado = _run_node_isolated(r"""
global.window = global;

// Registrar los intervalos de init para liberarlos al terminar la medición DOM.
const crearIntervalo = global.setInterval;
const intervalos = new Set();
global.setInterval = (...args) => {
  const timer = crearIntervalo(...args);
  intervalos.add(timer);
  return timer;
};

let peticionCapturada = null;
const listeners = {};
const docListeners = {};
let nextId = 0;

const crearNodo = (id, tag = 'div') => ({
  id,
  tagName: tag.toUpperCase(),
  className: '',
  textContent: '',
  children: [],
  checked: false,
  hidden: false,
  disabled: false,
  style: {},
  dataset: {},
  attributes: {},
  classList: {
    add(...cc) {},
    remove(...cc) {},
    toggle(c, v) {},
    contains(c) { return false; },
  },
  setAttribute(k, v) { this.attributes[k] = String(v); },
  getAttribute(k) { return this.attributes[k]; },
  hasAttribute(k) { return k in this.attributes; },
  removeAttribute(k) { delete this.attributes[k]; },
  appendChild(c) { this.children.push(c); return c; },
  append(...cc) { for (const c of cc) this.children.push(typeof c === 'string' ? crearNodo('text', 'span') : c); },
  removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
  remove() {},
  replaceChildren(...cc) { this.children = [...cc]; },
  insertBefore(n, ref) { this.children.push(n); return n; },
  querySelector(sel) { return null; },
  querySelectorAll(sel) { return []; },
  closest(sel) { return null; },
  addEventListener(ev, fn) {
    if (!listeners[id]) listeners[id] = {};
    if (!listeners[id][ev]) listeners[id][ev] = [];
    listeners[id][ev].push(fn);
  },
  async dispatchEvent(evName) {
    const arr = (listeners[id] && listeners[id][evName]) || [];
    for (const fn of arr) {
      const res = fn({ target: this });
      if (res && typeof res.then === 'function') await res;
    }
  }
});

const nodos = {
  'vista-radar': crearNodo('vista-radar'),
  'radarToggleBtn': crearNodo('radarToggleBtn', 'button'),
  'radarMostrarNavegadorBtn': crearNodo('radarMostrarNavegadorBtn', 'button'),
  'radarReanudarEnviosBtn': crearNodo('radarReanudarEnviosBtn', 'button'),
  'radarAprobarTodosBtn': crearNodo('radarAprobarTodosBtn', 'button'),
  'radarAprobarConfirmarBtn': crearNodo('radarAprobarConfirmarBtn', 'button'),
  'radarAprobarCancelarBtn': crearNodo('radarAprobarCancelarBtn', 'button'),
  'radarAprobarLista': crearNodo('radarAprobarLista'),
  'radarAprobarModal': crearNodo('radarAprobarModal'),
  'radarMensajeCerrar': crearNodo('radarMensajeCerrar', 'button'),
  'radarUmbralInput': crearNodo('radarUmbralInput', 'input'),
  'radarFiltros': crearNodo('radarFiltros'),
  'radarFiltroAlcance': crearNodo('radarFiltroAlcance'),
  'radarDetallePost': crearNodo('radarDetallePost'),
  'radarEmailTo': crearNodo('radarEmailTo', 'input'),
  'radarEmailSubject': crearNodo('radarEmailSubject', 'input'),
  'radarEmailBody': crearNodo('radarEmailBody', 'textarea'),
  'radarAprobarBtn': crearNodo('radarAprobarBtn', 'button'),
  'radarDescartarBtn': crearNodo('radarDescartarBtn', 'button'),
  'radarRegenerarBtn': crearNodo('radarRegenerarBtn', 'button'),
  'radarAbrirEditorBtn': crearNodo('radarAbrirEditorBtn', 'button'),
  'radarDescargarCvBtn': crearNodo('radarDescargarCvBtn', 'button'),
  'radarModoSerie': crearNodo('radarModoSerie', 'input'),
  'radarProcesosPanel': crearNodo('radarProcesosPanel'),
  'radarProcesosLista': crearNodo('radarProcesosLista'),
  'radarProcesosAvisos': crearNodo('radarProcesosAvisos'),  // la hoja rediseñada avisa la saturación arriba
  'radarProcesosTopes': crearNodo('radarProcesosTopes'),
  'radarProcesosCuota': crearNodo('radarProcesosCuota'),
  'radarBacklogQuedan': crearNodo('radarBacklogQuedan'),
  'radarBacklogBtn': crearNodo('radarBacklogBtn', 'button'),
  'radarEstadoPill': crearNodo('radarEstadoPill'),
  'radarProcesosFlecha': crearNodo('radarProcesosFlecha', 'button'),
  'radarProcesosCerrar': crearNodo('radarProcesosCerrar', 'button'),
  'radarGeneracionBtn': crearNodo('radarGeneracionBtn', 'button'),
  'radarAnalisisBtn': crearNodo('radarAnalisisBtn', 'button'),
  'radarLista': crearNodo('radarLista'),
  'radarListaFeed': crearNodo('radarListaFeed'),
  'radarTabs': crearNodo('radarTabs'),
};

global.document = {
  addEventListener(ev, fn) {
    if (!docListeners[ev]) docListeners[ev] = [];
    docListeners[ev].push(fn);
  },
  getElementById(id) {
    if (!nodos[id]) {
      nextId++;
      nodos[id] = crearNodo(id);
    }
    return nodos[id];
  },
  createElement(tag) {
    nextId++;
    return crearNodo(`auto_${nextId}`, tag);
  },
  createTextNode(txt) {
    return { textContent: txt, nodeType: 3 };
  },
  querySelectorAll() { return []; },
  querySelector() { return null; },
};

global.fetch = async (url, opts = {}) => {
  if (opts.method === 'PATCH' && url.includes('/config')) {
    peticionCapturada = {
      url,
      method: opts.method,
      body: JSON.parse(opts.body || '{}'),
    };
    return {
      ok: true,
      status: 200,
      json: async () => ({ modo_serie: peticionCapturada.body.modo_serie }),
    };
  }
  if (url.includes('/procesos')) {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        en_curso: [],
        cola_generacion: [],
        cola_analisis: [],
        backlog: { quedan: 0, items: [] },
        terminados: [],
        agy: {
          limite: 1,
          modo_serie: true,
          serie_hasta: 1800000000,
          historial: [{ fecha: '2026-11-08T10:00:00Z', limite: 1, motivo: 'Saturación AGY (503)' }]
        }
      }),
    };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

require(RUTA_JS);

(async () => {
  try {
  // Inicializar eventos de documento (DOMContentLoaded / init)
  if (docListeners['DOMContentLoaded']) {
    for (const fn of docListeners['DOMContentLoaded']) {
      const res = fn();
      if (res && typeof res.then === 'function') await res;
    }
  } else if (window.Radar && typeof window.Radar.init === 'function') {
    await window.Radar.init();
  }

  // 1. Simular interacción del usuario en el control checkbox para activar modo serie
  nodos.radarModoSerie.checked = true;
  await nodos.radarModoSerie.dispatchEvent('change');

  // 2. Esperar operaciones asincrónicas de carga de procesos devuelta por el servidor
  if (window.Radar && window.Radar._test && typeof window.Radar._test.cargarProcesos === 'function') {
    await window.Radar._test.cargarProcesos();
  }

  function recorrerTexto(n) {
    let s = n.textContent || '';
    if (Array.isArray(n.children)) {
      for (const c of n.children) s += ' ' + recorrerTexto(c);
    }
    return s;
  }

  const textoLista = recorrerTexto(nodos.radarProcesosAvisos) + ' ' + recorrerTexto(nodos.radarProcesosLista);

  console.log(JSON.stringify({
    checked: nodos.radarModoSerie.checked,
    patch: peticionCapturada,
    texto_lista: textoLista,
    listener_registrado: Boolean(listeners.radarModoSerie && listeners.radarModoSerie.change && listeners.radarModoSerie.change.length),
  }));
  } finally {
    for (const timer of intervalos) clearInterval(timer);
    global.setInterval = crearIntervalo;
  }
})();
""".replace("RUTA_JS", js_file))
        assert resultado is not None, "Node debe devolver resultado JSON"
        assert resultado.get("listener_registrado") is True, "El checkbox #radarModoSerie debe tener listener change enlazado por init()"
        assert resultado.get("patch") is not None, "Disparar el cambio en radarModoSerie debe enviar PATCH /config"
        assert resultado["patch"].get("body", {}).get("modo_serie") is True
        assert resultado.get("checked") is True, "El control debe reflejar el estado retornado por el servidor"
        assert "IA saturada: una llamada por vez" in resultado.get("texto_lista", "") or "Modo serie automático por saturación" in resultado.get("texto_lista", "") or "Saturación AGY (503)" in resultado.get("texto_lista", "")


def test_reinicio_preserva_freno(monkeypatch, tmp_path):
    """Reinicio preserva estado_admision, separa freno 503 de serie manual e historial sin tocar aprobaciones."""
    # invariante: base reconcile_on_startup funciona sobre tabla inicializada
    db_file = tmp_path / "reinicio.db"
    conn = sqlite3.connect(db_file)
    try:
        store.init_radar_tables(conn)
    finally:
        conn.close()

    store.update_radar_state(db_path=db_file, generacion_pausada=True)
    store.reconcile_on_startup(db_path=db_file)
    st_inv = store.get_radar_state(db_path=db_file)
    assert st_inv["generacion_pausada"] is True

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: conectar la base predeterminada en core.database.DB_PATH para que estado_admision consulte esta base
    monkeypatch.setattr(database, "DB_PATH", db_file)

    t_sim = [1_800_000_000.0]
    monkeypatch.setattr(modulo_bridge, "time", SimpleNamespace(time=lambda: t_sim[0], monotonic=lambda: t_sim[0]))

    # Fase A: Freno automático solo (sin modo serie manual)
    futuro_freno = 1_800_000_900.0
    historial_freno = [{"fecha": "2026-11-08T10:00:00Z", "limite": 1, "motivo": "Saturación AGY (503)"}]
    store.update_radar_state(
        db_path=db_file,
        modo_serie=False,
        agy_serie_hasta=futuro_freno,
        agy_historial=historial_freno,
    )

    store.reconcile_on_startup(db_path=db_file)

    adm_freno = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_freno.get("limite") == 1, "Freno automático activo tras reinicio debe fijar límite 1"
    assert adm_freno.get("modo_serie") is False, "Freno automático no debe activar modo serie manual"
    assert adm_freno.get("serie_hasta") == futuro_freno
    assert adm_freno.get("historial") == historial_freno

    # Al vencer el freno automático solo, debe volver a límite 2
    t_sim[0] = futuro_freno + 1.0
    adm_freno_vencido = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_freno_vencido.get("limite") == 2, "Al expirar freno automático tras reinicio, debe volver a 2"

    # Fase B: Modo serie manual solo (sin freno automático)
    historial_manual = [{"fecha": "2026-11-08T10:05:00Z", "limite": 1, "motivo": "Modo serie activado"}]
    store.update_radar_state(
        db_path=db_file,
        modo_serie=True,
        agy_serie_hasta=None,
        agy_historial=historial_manual,
    )

    store.reconcile_on_startup(db_path=db_file)

    adm_manual = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_manual.get("limite") == 1, "Modo serie manual tras reinicio debe fijar límite 1"
    assert adm_manual.get("modo_serie") is True
    assert adm_manual.get("serie_hasta") == None
    assert adm_manual.get("historial") == historial_manual

    # Modo serie manual no vence por tiempo
    t_sim[0] = 1_900_000_000.0
    adm_manual_t = modulo_bridge.estado_admision() if hasattr(modulo_bridge, "estado_admision") else {}
    assert adm_manual_t.get("limite") == 1, "Modo serie manual se mantiene en 1 sin importar el tiempo"

    # I1: Nada se aprueba al reiniciar
    pid = store.insert_post_if_new({"urn": "reinicio:post_1", "texto": "Oferta para recuperación"}, "q", db_path=db_file)
    store.update_post(pid, db_path=db_file, estado="ANALIZANDO")
    store.reconcile_on_startup(db_path=db_file)
    post = store.get_post(pid, db_path=db_file)
    assert post["estado"] == "OFERTA", "ANALIZANDO debe volver a OFERTA tras reinicio"
    assert post["aprobado_at"] is None, "Reinicio no debe fijar aprobado_at"

    # I3: Un post recuperado se toma una sola vez
    store.update_post(
        pid,
        db_path=db_file,
        estado="ANALIZADA",
        texto="A" * 400,
        triage={"puesto": "Analista Funcional"},
        analisis={"match_score": 85.0},
    )
    assert store.tomar_automatico(pid, db_path=db_file) is True
    assert store.tomar_automatico(pid, db_path=db_file) is False


def test_env_no_supera_dos(monkeypatch, tmp_path):
    """LINKEDOUT_AGY_MAX_CONCURRENTES reduce a 1, acota valores mayores a 2 e inválidos usan 2."""
    # invariante: base LINKEDOUT_AGY_MAX_CONCURRENTES=1 reduce capacidad a 1 (ejecutado sin loop anidado)
    monkeypatch.setenv("LINKEDOUT_AGY_MAX_CONCURRENTES", "1")
    loop_inv = asyncio.new_event_loop()
    try:
        setattr(loop_inv, "_linkedout_agy_admision", None)

        async def inv():
            adm = modulo_bridge._admision_agy()
            return getattr(adm, "_value", None)

        cap_inv = loop_inv.run_until_complete(inv())
        assert cap_inv == 1
    finally:
        drenar_loop_secundario(loop_inv)

    if os.environ.get("INVARIANT_CHECK") == "1":
        return

    # defecto: acotar a 2 cualquier valor > 2 e inválidos usan 2
    casos = [
        (None, 2),     # ausente -> 2 (base era 3)
        ("1", 1),      # 1 -> 1
        ("2", 2),      # 2 -> 2
        ("3", 2),      # 3 -> acotado a 2 (no 3)
        ("4", 2),      # 4 -> acotado a 2 (no 4)
        ("10", 2),     # 10 -> acotado a 2
        ("", 2),       # vacio -> fallback a 2
        ("abc", 2),    # invalido -> fallback a 2
        ("0", 2),      # 0 -> fallback a 2
        ("-2", 2),     # negativo -> fallback a 2
    ]

    for valor, limite_esperado in casos:
        loop_test = asyncio.new_event_loop()
        try:
            async def medir():
                if valor is None:
                    monkeypatch.delenv("LINKEDOUT_AGY_MAX_CONCURRENTES", raising=False)
                else:
                    monkeypatch.setenv("LINKEDOUT_AGY_MAX_CONCURRENTES", valor)

                setattr(loop_test, "_linkedout_agy_admision", None)
                adm = modulo_bridge._admision_agy()
                capacidad = getattr(adm, "_value", None)
                if hasattr(modulo_bridge, "estado_admision"):
                    limite_adm = modulo_bridge.estado_admision().get("limite")
                else:
                    limite_adm = capacidad
                return capacidad, limite_adm

            cap, lim = loop_test.run_until_complete(medir())
            assert cap == limite_esperado, f"Env {valor!r}: capacidad esperada {limite_esperado}, obtenida {cap}"
            assert lim == limite_esperado, f"Env {valor!r}: limite esperado {limite_esperado}, obtenido {lim}"
        finally:
            drenar_loop_secundario(loop_test)

    # Medir entradas físicas para los valores ambientales (no solo consultar _value)
    def medir_entradas_fisicas(env_val):
        loop_env = asyncio.new_event_loop()
        try:
            async def run_medicion():
                if env_val is None:
                    monkeypatch.delenv("LINKEDOUT_AGY_MAX_CONCURRENTES", raising=False)
                else:
                    monkeypatch.setenv("LINKEDOUT_AGY_MAX_CONCURRENTES", env_val)
                setattr(loop_env, "_linkedout_agy_admision", None)

                b1 = AntigravityBridge()
                b2 = AntigravityBridge()
                gate_m1 = asyncio.Event()
                gate_m2 = asyncio.Event()
                gate_m3 = asyncio.Event()
                empezo_m = {"m1": asyncio.Event(), "m2": asyncio.Event(), "m3": asyncio.Event()}
                activos_m = 0
                pico_m = 0

                async def mock_ej(self, prompt, *a, **kw):
                    nonlocal activos_m, pico_m
                    activos_m += 1
                    pico_m = max(pico_m, activos_m)
                    if prompt in empezo_m:
                        empezo_m[prompt].set()
                    try:
                        if prompt == "m1":
                            await asyncio.wait_for(gate_m1.wait(), timeout=60)
                        elif prompt == "m2":
                            await asyncio.wait_for(gate_m2.wait(), timeout=60)
                        elif prompt == "m3":
                            await asyncio.wait_for(gate_m3.wait(), timeout=60)
                        return {"ok": prompt}
                    finally:
                        activos_m -= 1

                monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ej)

                async with tareas_controladas() as lanzar_m:
                    t_m1 = lanzar_m(b1.run_prompt("m1"))
                    t_m2 = lanzar_m(b2.run_prompt("m2"))
                    t_m3 = lanzar_m(b1.run_prompt("m3"))

                    adm_env = modulo_bridge._admision_agy()
                    if env_val == "1":
                        await asyncio.wait_for(empezo_m["m1"].wait(), timeout=60)
                        await esperar_en_cola(adm_env, 1)
                        assert activos_m == 1
                        assert pico_m == 1
                        assert empezo_m["m2"].is_set() is False
                        assert empezo_m["m3"].is_set() is False
                        gate_m1.set()
                        await asyncio.wait_for(t_m1, timeout=60)

                        await asyncio.wait_for(empezo_m["m2"].wait(), timeout=60)
                        assert activos_m == 1
                        assert pico_m == 1
                        gate_m2.set()
                        await asyncio.wait_for(t_m2, timeout=60)

                        await asyncio.wait_for(empezo_m["m3"].wait(), timeout=60)
                        assert activos_m == 1
                        assert pico_m == 1
                        gate_m3.set()
                        await asyncio.wait_for(t_m3, timeout=60)
                    else:
                        await asyncio.wait_for(
                            asyncio.gather(empezo_m["m1"].wait(), empezo_m["m2"].wait()),
                            timeout=60,
                        )
                        await esperar_en_cola(adm_env, 1)
                        assert activos_m == 2
                        assert pico_m == 2
                        assert empezo_m["m3"].is_set() is False, "Con env > 2 la tercera llamada no debe entrar simultáneamente"
                        gate_m1.set()
                        await asyncio.wait_for(t_m1, timeout=60)

                        await asyncio.wait_for(empezo_m["m3"].wait(), timeout=60)
                        assert pico_m == 2
                        gate_m2.set()
                        gate_m3.set()
                        await asyncio.wait_for(asyncio.gather(t_m2, t_m3), timeout=60)

                return pico_m

            return loop_env.run_until_complete(run_medicion())
        finally:
            drenar_loop_secundario(loop_env)

    pico_env_1 = medir_entradas_fisicas("1")
    assert pico_env_1 == 1

    pico_env_4 = medir_entradas_fisicas("4")
    assert pico_env_4 == 2

    # defecto: dos loops consecutivos sin objetos ligados y con contención real
    def correr_loop_con_contencion():
        nl = asyncio.new_event_loop()
        try:
            async def contencion():
                b1 = AntigravityBridge()
                b2 = AntigravityBridge()
                activos = 0
                pico = 0
                gate = asyncio.Event()

                async def mock_ej(self, prompt, *a, **kw):
                    nonlocal activos, pico
                    activos += 1
                    pico = max(pico, activos)
                    try:
                        await asyncio.wait_for(gate.wait(), timeout=60)
                        return {"ok": prompt}
                    finally:
                        activos -= 1

                monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", mock_ej)
                async with tareas_controladas() as lanzar_c:
                    t1 = lanzar_c(b1.run_prompt("cnt_1"))
                    t2 = lanzar_c(b2.run_prompt("cnt_2"))
                    t3 = lanzar_c(b1.run_prompt("cnt_3"))

                    adm = modulo_bridge._admision_agy()
                    await esperar_en_cola(adm, 1)

                    assert pico == 2, f"Pico en contención debe ser 2, obtenido {pico}"
                    gate.set()
                    await asyncio.wait_for(asyncio.gather(t1, t2, t3), timeout=60)
                    return pico

            return nl.run_until_complete(contencion())
        finally:
            drenar_loop_secundario(nl)

    monkeypatch.setenv("LINKEDOUT_AGY_MAX_CONCURRENTES", "2")
    p1 = correr_loop_con_contencion()
    p2 = correr_loop_con_contencion()
    assert p1 == 2 and p2 == 2
