"""Procesos contenidos desde su creación en un Job Object propio (Windows/CPython).

La creación suspendida permite asignar el handle antes de ejecutar código del hijo.
No se abren procesos por PID ni se habilita breakaway. Popen conserva la gestión
de tuberías y communicate; solo se especializa su punto de creación en Windows.
"""
import asyncio
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import _winapi


class _LimitesBasicos(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _LimitesExtendidos(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _LimitesBasicos),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Contabilidad(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    firmas = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for nombre, (argumentos, resultado) in firmas.items():
        funcion = getattr(kernel, nombre)
        funcion.argtypes, funcion.restype = argumentos, resultado
    return kernel


class _JobWindows:
    def __init__(self):
        self.kernel = _kernel()
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limites = _LimitesExtendidos()
        limites.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limites), ctypes.sizeof(limites)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def asignar(self, handle):
        if not self.kernel.AssignProcessToJobObject(self.handle, handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminar(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    async def esperar_vacio(self):
        while True:
            info = _Contabilidad()
            if not self.kernel.QueryInformationJobObject(
                    self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.ActiveProcesses == 0:
                return
            await asyncio.sleep(0.01)

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


class _PopenEnJob(subprocess.Popen):
    def __init__(self, args, *, job, **kwargs):
        self.job = job
        super().__init__(args, **kwargs)

    def _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds,
                       cwd, env, startupinfo, creationflags, shell,
                       p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
                       *unused):
        if shell or preexec_fn or pass_fds:
            raise ValueError("El proceso contenido requiere ejecución directa")
        startupinfo = startupinfo.copy() if startupinfo else subprocess.STARTUPINFO()
        if -1 not in (p2cread, c2pwrite, errwrite):
            startupinfo.dwFlags |= _winapi.STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite
            startupinfo.lpAttributeList = {"handle_list": self._filter_handle_list(
                [int(p2cread), int(c2pwrite), int(errwrite)])}
        command = args if isinstance(args, str) else subprocess.list2cmdline(args)
        executable = os.fsdecode(executable) if executable is not None else None
        cwd = os.fsdecode(cwd) if cwd is not None else None
        sys.audit("subprocess.Popen", executable, command, cwd, env)
        try:
            hp, ht, pid, _ = _winapi.CreateProcess(
                executable, command, None, None, True,
                creationflags | 0x4,  # CREATE_SUSPENDED: todavía no puede crear hijos
                env, cwd, startupinfo)
        finally:
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
        self._child_created = True
        self._handle = subprocess.Handle(hp)
        self.pid = pid
        try:
            self.job.asignar(hp)
            if self.job.kernel.ResumeThread(ht) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            # El proceso sigue suspendido: nadie puede quedar fuera del Job.
            _winapi.TerminateProcess(hp, 1)
            _winapi.WaitForSingleObject(hp, _winapi.INFINITE)
            self.returncode = _winapi.GetExitCodeProcess(hp)
            self._handle.Close()
            raise
        finally:
            _winapi.CloseHandle(ht)


class ProcesoWindows:
    """Interfaz async de communicate/kill/wait con propiedad explícita del Job."""
    def __init__(self, args, **kwargs):
        self.job = _JobWindows()
        self._comunicacion = None
        try:
            self._process = _PopenEnJob(args, job=self.job, **kwargs)
        except BaseException:
            self.job.close()
            raise

    @property
    def pid(self):
        return self._process.pid

    @property
    def returncode(self):
        return self._process.poll()

    async def communicate(self, input=None):
        if self._comunicacion is None:
            self._comunicacion = asyncio.create_task(asyncio.to_thread(self._process.communicate, input))
        return await asyncio.shield(self._comunicacion)

    def kill(self):
        self._process.kill()

    async def wait(self):
        resultado = await asyncio.to_thread(self._process.wait)
        if self._comunicacion is not None:
            await asyncio.shield(self._comunicacion)
        return resultado

    async def terminar_arbol(self):
        try:
            self.job.terminar()
        except Exception:
            # Cerrar el único handle dispara KILL_ON_JOB_CLOSE como segunda vía.
            self.job.close()
            raise
        await self.job.esperar_vacio()

    def close(self):
        try:
            self.job.close()
        finally:
            for stream in (self._process.stdout, self._process.stderr):
                if stream is not None:
                    stream.close()
            self._process._handle.Close()
