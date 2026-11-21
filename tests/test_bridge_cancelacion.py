"""Regresiones de limpieza: procesos Python locales, sin invocar AGY."""
import asyncio
import contextlib
import ctypes
import os
from pathlib import Path
import signal
import sys
from unittest.mock import AsyncMock

import pytest

from core.bridge import AntigravityBridge
from core import bridge as modulo_bridge


@pytest.mark.asyncio
@pytest.mark.parametrize("falla_arbol", [False, True])
async def test_cancelacion_preserva_excepcion_y_limpia_padre(monkeypatch, caplog, falla_arbol):
    orden = []
    empezo = asyncio.Event()

    class Proceso:
        pid = 123
        returncode = None

        async def communicate(self):
            empezo.set()
            await asyncio.Future()

        def kill(self):
            orden.append("kill")

        async def wait(self):
            orden.append("wait")

    def arbol(*args):
        orden.append("arbol")
        if falla_arbol:
            raise PermissionError("fallo de limpieza simulado")

    async def terminar_arbol(process):
        arbol()

    monkeypatch.setattr(modulo_bridge, "_terminar_arbol", terminar_arbol)
    monkeypatch.setattr(modulo_bridge, "_crear_proceso", AsyncMock(return_value=Proceso()))
    bridge = AntigravityBridge(agy_path=sys.executable)
    tarea = asyncio.create_task(bridge._ejecutar_prompt("prueba"))
    try:
        await asyncio.wait_for(empezo.wait(), 60)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert orden == ["arbol", "kill", "wait"]
        if falla_arbol:
            assert "fallo de limpieza simulado" in caplog.text
    finally:
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelacion_durante_limpieza_por_timeout_se_propaga(monkeypatch):
    esperando = asyncio.Event()
    liberar = asyncio.Event()
    orden = []

    class Proceso:
        pid = 123
        returncode = None

        async def communicate(self):
            await asyncio.Future()

        def kill(self):
            orden.append("kill")

        async def wait(self):
            esperando.set()
            await liberar.wait()
            orden.append("wait")

    monkeypatch.setattr(modulo_bridge, "_terminar_arbol", AsyncMock())
    monkeypatch.setattr("core.bridge._crear_proceso", AsyncMock(return_value=Proceso()))
    bridge = AntigravityBridge(agy_path=sys.executable)
    tarea = asyncio.create_task(bridge._ejecutar_prompt("prueba", timeout_seconds=0.01))
    try:
        await asyncio.wait_for(esperando.wait(), 60)
        tarea.cancel()
        await asyncio.sleep(0)
        assert not tarea.done(), "debe terminar la limpieza antes de propagar cancelación"
        liberar.set()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert orden == ["kill", "wait"]
    finally:
        liberar.set()
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("motivo", ["cancelacion", "timeout"])
async def test_interrupcion_termina_y_espera_proceso_y_descendiente(tmp_path, monkeypatch, motivo):
    pidfile = tmp_path / "hijo.pid"
    codigo = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); "
        "time.sleep(60)"
    )
    bridge = AntigravityBridge(agy_path=sys.executable)
    monkeypatch.setattr(bridge, "_build_command", lambda *a, **kw: [sys.executable, "-c", codigo])
    original = modulo_bridge._crear_proceso
    procesos = []
    esperados = []

    async def crear(*args, **kwargs):
        proc = await original(*args, **kwargs)
        # Observar también la espera efectiva de terminación, además del estado del proceso.
        if args[0] == sys.executable:
            procesos.append(proc)
            wait = proc.wait

            async def esperar():
                resultado = await wait()
                esperados.append(proc.pid)
                return resultado

            monkeypatch.setattr(proc, "wait", esperar)
        return proc

    monkeypatch.setattr("core.bridge._crear_proceso", crear)
    tarea = None
    hijo = None
    handle = None
    ajeno = None
    try:
        opciones = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        ajeno = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)", **opciones)
        tarea = asyncio.create_task(bridge._ejecutar_prompt("prueba", timeout_seconds=2))
        # El hijo crea el archivo antes de escribir el PID: se espera a que tenga contenido (carrera en el CI)
        async with asyncio.timeout(1.5):
            while not (pidfile.exists() and pidfile.read_text().strip()):
                await asyncio.sleep(0.005)
        hijo = int(pidfile.read_text())
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, hijo)
            assert handle
        if motivo == "cancelacion":
            tarea.cancel()
        done, _ = await asyncio.wait({tarea}, timeout=3)
        assert tarea in done, "la limpieza no terminó: el descendiente conserva las tuberías abiertas"
        with pytest.raises(asyncio.CancelledError if motivo == "cancelacion" else TimeoutError):
            await tarea
        assert procesos[0].returncode is not None, "el proceso sigue vivo"
        assert esperados == [procesos[0].pid], "no se esperó la terminación del proceso"
        assert ajeno.returncode is None, "se terminó un proceso ajeno al trabajo"
        if handle:
            assert kernel.WaitForSingleObject(handle, 1000) == 0, "el descendiente sigue vivo"
        else:
            # Si el hijo ya fue recogido, /proc/<pid> desaparece entre el chequeo y la lectura (carrera en el CI)
            try:
                estado = Path(f"/proc/{hijo}/stat").read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                estado = None
            if estado is None:
                with pytest.raises(ProcessLookupError):
                    os.kill(hijo, 0)
            else:
                assert estado == "Z", f"el descendiente sigue vivo (estado {estado})"
    finally:
        # Limpieza independiente del código probado, incluso durante RED.
        if hijo is not None:
            if sys.platform == "win32":
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                terminar = kernel.OpenProcess(1, False, hijo)
                if terminar:
                    kernel.TerminateProcess(terminar, 1)
                    kernel.CloseHandle(terminar)
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(hijo, signal.SIGKILL)
        for proc in procesos:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        if tarea is not None:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)
        for proc in procesos:
            await proc.communicate()
            await proc.wait()
        if handle:
            kernel.CloseHandle(handle)
        if ajeno is not None:
            with contextlib.suppress(ProcessLookupError):
                ajeno.kill()
            await ajeno.wait()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Contención nativa de Windows")
async def test_arbol_windows_no_depende_de_instantaneas_por_pid(tmp_path, monkeypatch):
    def instantanea_prohibida(pid):
        raise PermissionError("No se permite recorrer procesos por PID")

    monkeypatch.setattr(modulo_bridge, "_terminar_arbol_windows", instantanea_prohibida, raising=False)
    await test_interrupcion_termina_y_espera_proceso_y_descendiente(tmp_path, monkeypatch, "cancelacion")


def test_grupo_corriendo_ignora_zombis_y_otros_grupos(tmp_path):
    for pid, estado, grupo in ((10, "Z", 77), (11, "R", 99), (12, "S", 77)):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (py thon) {estado} 1 {grupo} {grupo} 0")
    assert modulo_bridge._grupo_corriendo(77, tmp_path)
    (tmp_path / "12" / "stat").write_text("12 (py thon) Z 1 77 77 0")
    assert not modulo_bridge._grupo_corriendo(77, tmp_path)


@pytest.mark.asyncio
async def test_esperar_grupo_espera_a_que_deje_de_correr(monkeypatch):
    respuestas = iter([True, True, False])
    monkeypatch.setattr(modulo_bridge, "_grupo_corriendo", lambda pgid: next(respuestas))
    await modulo_bridge._esperar_grupo(77)
    with pytest.raises(StopIteration):
        next(respuestas)
