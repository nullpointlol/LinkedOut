"""Contención desde el nacimiento y limpieza si falla el arranque del proceso."""
import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects de Windows")

if sys.platform == "win32":
    from core import procesos_windows as pw


@pytest.fixture
def nacimiento(monkeypatch):
    eventos = []
    handles = []
    original_crear = pw._winapi.CreateProcess
    original_asignar = pw._JobWindows.asignar
    original_kernel = pw._kernel

    def crear(*args):
        assert args[5] & 0x4, "el hijo debe nacer suspendido"
        assert args[5] & 0x08000000, "no debe abrir ventanas"
        resultado = original_crear(*args)
        eventos.append("crear")
        hp = resultado[0]
        actual = pw._winapi.GetCurrentProcess()
        copia = pw._winapi.DuplicateHandle(actual, hp, actual, 0, False, 2)
        handles.append(copia)
        return resultado

    def asignar(job, hp):
        assert eventos == ["crear"], "asignar antes de habilitar ejecución"
        original_asignar(job, hp)
        eventos.append("asignar")

    def kernel():
        api = original_kernel()
        resume = api.ResumeThread

        def reanudar(ht):
            assert eventos == ["crear", "asignar"]
            eventos.append("reanudar")
            return resume(ht)

        api.ResumeThread = reanudar
        return api

    monkeypatch.setattr(pw._winapi, "CreateProcess", crear)
    monkeypatch.setattr(pw._JobWindows, "asignar", asignar)
    monkeypatch.setattr(pw, "_kernel", kernel)
    try:
        yield eventos, handles
    finally:
        # Solo handles duplicados de procesos creados por este test.
        for handle in handles:
            if pw._winapi.WaitForSingleObject(handle, 0) != 0:
                pw._winapi.TerminateProcess(handle, 1)
                pw._winapi.WaitForSingleObject(handle, 5000)
            pw._winapi.CloseHandle(handle)


@pytest.mark.asyncio
async def test_asigna_job_antes_de_ejecutar_y_cerrar_mata_el_arbol(tmp_path, nacimiento):
    eventos, _ = nacimiento
    pidfile = tmp_path / "hijo.pid"
    codigo = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); "
        "time.sleep(60)"
    )
    proceso = None
    hijo_handle = None
    try:
        proceso = pw.ProcesoWindows([sys.executable, "-c", codigo], creationflags=0x08000000)
        assert eventos == ["crear", "asignar", "reanudar"]
        async with asyncio.timeout(2):
            while not pidfile.exists():
                await asyncio.sleep(0.005)
        api = proceso.job.kernel
        api.OpenProcess.argtypes = [pw.wintypes.DWORD, pw.wintypes.BOOL, pw.wintypes.DWORD]
        api.OpenProcess.restype = pw.wintypes.HANDLE
        hijo_handle = api.OpenProcess(0x00100000, False, int(pidfile.read_text()))
        assert hijo_handle
        proceso.job.close()  # ejercita KILL_ON_JOB_CLOSE independientemente de TerminateJobObject
        await asyncio.wait_for(proceso.wait(), 60)
        assert proceso.returncode is not None
        assert pw._winapi.WaitForSingleObject(hijo_handle, 1000) == 0
    finally:
        if proceso is not None:
            proceso.job.close()
            await proceso.wait()
            proceso.close()
        if hijo_handle:
            pw._winapi.CloseHandle(hijo_handle)


@pytest.mark.parametrize("fase", ["asignar", "reanudar"])
def test_fallo_al_arrancar_termina_y_espera_el_proceso_suspendido(tmp_path, monkeypatch, nacimiento, fase):
    _, handles = nacimiento
    marca = tmp_path / "ejecutado"
    codigo = f"import pathlib; pathlib.Path({str(marca)!r}).touch()"
    if fase == "asignar":
        def fallar(*args):
            raise PermissionError("asignación denegada simulada")
        monkeypatch.setattr(pw._JobWindows, "asignar", fallar)
    else:
        original = pw._kernel

        def kernel():
            api = original()
            api.ResumeThread = lambda ht: 0xFFFFFFFF
            return api

        monkeypatch.setattr(pw, "_kernel", kernel)
    with pytest.raises(OSError):
        pw.ProcesoWindows([sys.executable, "-c", codigo], creationflags=0x08000000)
    assert len(handles) == 1
    assert pw._winapi.WaitForSingleObject(handles[0], 0) == 0
    assert not marca.exists(), "se ejecutó código sin contención"
