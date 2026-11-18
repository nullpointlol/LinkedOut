"""Unitarios de `core/procesos_windows.py` con un kernel32 simulado: cada llamada a la API de Job
Objects que falla se convierte en OSError (y se libera el handle). No se crea ningún proceso."""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects solo existen en Windows")

if sys.platform == "win32":
    from core import procesos_windows as pw


class _Kernel:
    """kernel32 falso: cada función devuelve lo configurado y anota las llamadas."""

    def __init__(self, **resultados):
        self.resultados = {"CreateJobObjectW": 77, "SetInformationJobObject": 1, "AssignProcessToJobObject": 1,
                           "TerminateJobObject": 1, "QueryInformationJobObject": 1, "CloseHandle": 1,
                           **resultados}
        self.llamadas = []
        self.activos = [0]

    def __getattr__(self, nombre):
        def funcion(*args):
            self.llamadas.append(nombre)
            if nombre == "QueryInformationJobObject" and self.resultados[nombre]:
                args[2]._obj.ActiveProcesses = self.activos.pop(0) if self.activos else 0
            return self.resultados[nombre]
        return funcion


def _job(monkeypatch, **resultados):
    kernel = _Kernel(**resultados)
    monkeypatch.setattr(pw, "_kernel", lambda: kernel)
    return kernel


def test_crear_job_que_falla_lanza_oserror(monkeypatch):
    """Si CreateJobObjectW devuelve 0, se lanza OSError."""
    _job(monkeypatch, CreateJobObjectW=0)
    with pytest.raises(OSError):
        pw._JobWindows()


def test_configurar_job_que_falla_cierra_el_handle(monkeypatch):
    """Si no se puede configurar KILL_ON_JOB_CLOSE, se cierra el handle antes de lanzar."""
    kernel = _job(monkeypatch, SetInformationJobObject=0)
    with pytest.raises(OSError):
        pw._JobWindows()
    assert kernel.llamadas[-1] == "CloseHandle"


@pytest.mark.parametrize("funcion, accion", [
    ("AssignProcessToJobObject", lambda job: job.asignar(5)),
    ("TerminateJobObject", lambda job: job.terminar()),
    ("CloseHandle", lambda job: job.close()),
])
def test_operaciones_que_fallan_lanzan_oserror(monkeypatch, funcion, accion):
    """Asignar, terminar o cerrar con la API en error lanza OSError."""
    job = (_job(monkeypatch, **{funcion: 0}), pw._JobWindows())[1]
    with pytest.raises(OSError):
        accion(job)


@pytest.mark.asyncio
async def test_esperar_vacio_sondea_hasta_cero_procesos(monkeypatch):
    """`esperar_vacio` repite la consulta mientras queden procesos activos."""
    kernel = _job(monkeypatch)
    kernel.activos = [2, 1, 0]
    await pw._JobWindows().esperar_vacio()
    assert kernel.llamadas.count("QueryInformationJobObject") == 3


@pytest.mark.asyncio
async def test_esperar_vacio_con_consulta_fallida(monkeypatch):
    """Si la consulta del Job falla, `esperar_vacio` lanza OSError."""
    _job(monkeypatch, QueryInformationJobObject=0)
    with pytest.raises(OSError):
        await pw._JobWindows().esperar_vacio()


def test_popen_contenido_rechaza_shell(monkeypatch):
    """El proceso contenido no admite `shell=True` (se ejecuta directo, sin intérprete)."""
    _job(monkeypatch)
    with pytest.raises(ValueError, match="ejecución directa"):
        pw._PopenEnJob(["cmd"], job=pw._JobWindows(), shell=True)


@pytest.mark.asyncio
async def test_terminar_arbol_que_falla_cierra_el_job(monkeypatch):
    """Si TerminateJobObject falla, se cierra el handle (KILL_ON_JOB_CLOSE) y se propaga el error."""
    kernel = _job(monkeypatch, TerminateJobObject=0)
    proceso = object.__new__(pw.ProcesoWindows)
    proceso.job = pw._JobWindows()
    with pytest.raises(OSError):
        await proceso.terminar_arbol()
    assert kernel.llamadas[-1] == "CloseHandle"
    assert proceso.job.handle is None
