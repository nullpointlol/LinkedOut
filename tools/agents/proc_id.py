#!/usr/bin/env python3
"""Identificación temporal y resolución de WINPID para procesos del kit.
Uso:
  python proc_id.py <pid>
    Imprime la hora de creación del proceso como un entero estable.
    Sale con 1 y sin salida si el proceso no existe o no se puede leer.
  python proc_id.py --self-winpid <msys_pid>
    Imprime el WINPID (lee /proc/<msys_pid>/winpid si existe; si no, devuelve <msys_pid>).
"""
import os
from pathlib import Path
import subprocess
import sys


def get_proc_time_windows(pid: int):
    if pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes as wt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    kernel.GetProcessTimes.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wt.BOOL

    h_proc = kernel.OpenProcess(0x1000, False, pid)
    if not h_proc:
        h_proc = kernel.OpenProcess(0x0400, False, pid)
    if not h_proc:
        return None

    try:
        creation = wt.FILETIME()
        exit_time = wt.FILETIME()
        kernel_time = wt.FILETIME()
        user_time = wt.FILETIME()
        if not kernel.GetProcessTimes(
            h_proc,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        val = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return val if val > 0 else None
    finally:
        kernel.CloseHandle(h_proc)


def get_proc_time_linux(pid: int):
    if pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
            content = f.read()
        rpar = content.rfind(")")
        if rpar == -1:
            return None
        fields = content[rpar + 1:].split()
        if len(fields) < 20:
            return None
        return int(fields[19])
    except Exception:
        return None


def get_proc_time(pid: int):
    if os.name == "nt":
        return get_proc_time_windows(pid)
    return get_proc_time_linux(pid)


def kill_proc_tree(pid: int):
    if pid <= 0:
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as wt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class Entry(ctypes.Structure):
            _fields_ = [
                ("size", wt.DWORD),
                ("usage", wt.DWORD),
                ("pid", wt.DWORD),
                ("heap", ctypes.c_size_t),
                ("module", wt.DWORD),
                ("threads", wt.DWORD),
                ("parent", wt.DWORD),
                ("priority", wt.LONG),
                ("flags", wt.DWORD),
                ("exe", ctypes.c_char * 260),
            ]

        kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
        kernel.Process32First.argtypes = kernel.Process32Next.argtypes = [
            wt.HANDLE,
            ctypes.POINTER(Entry),
        ]
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
        kernel.TerminateProcess.restype = wt.BOOL
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        kernel.CloseHandle.restype = wt.BOOL

        pids = [pid]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot != wt.HANDLE(-1).value and snapshot != 0:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            parents = {}
            more = kernel.Process32First(snapshot, ctypes.byref(entry))
            while more:
                parents.setdefault(entry.parent, []).append(entry.pid)
                more = kernel.Process32Next(snapshot, ctypes.byref(entry))
            kernel.CloseHandle(snapshot)

            for p in pids:
                for child in parents.get(p, []):
                    if child not in pids:
                        pids.append(child)

        try:
            out = subprocess.check_output(["ps", "-W"], stderr=subprocess.DEVNULL).decode("utf-8", "replace")
            win_to_msys = {}
            ppid_to_children = {}
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        mpid = int(parts[0])
                        mppid = int(parts[1])
                        wpid = int(parts[3])
                        win_to_msys[wpid] = mpid
                        ppid_to_children.setdefault(mppid, []).append(wpid)
                    except ValueError:
                        continue
            for p in list(pids):
                mp = win_to_msys.get(p)
                if mp and mp in ppid_to_children:
                    for cw in ppid_to_children[mp]:
                        if cw not in pids:
                            pids.append(cw)
        except Exception:
            pass

        for p in reversed(pids):
            h = kernel.OpenProcess(0x0001, False, p)
            if h:
                try:
                    kernel.TerminateProcess(h, 1)
                finally:
                    kernel.CloseHandle(h)
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def get_winpid(msys_pid: str) -> str:
    winpid_path = Path(f"/proc/{msys_pid}/winpid")
    if winpid_path.exists():
        try:
            val = winpid_path.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass

    if os.name == "nt":
        try:
            out = subprocess.check_output(["ps", "-W"], stderr=subprocess.DEVNULL).decode("utf-8", "replace")
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[0] == msys_pid:
                    return parts[3]
        except Exception:
            pass

    return msys_pid


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-winpid":
        msys_pid = sys.argv[2]
        print(get_winpid(msys_pid))
        sys.exit(0)

    if len(sys.argv) == 3 and sys.argv[1] == "--kill":
        try:
            pid = int(sys.argv[2])
            kill_proc_tree(pid)
        except Exception:
            sys.exit(1)
        sys.exit(0)

    if len(sys.argv) == 2 and sys.argv[1] != "--self-winpid":
        try:
            pid = int(sys.argv[1])
        except ValueError:
            sys.exit(1)
        t = get_proc_time(pid)
        if t is None:
            sys.exit(1)
        print(t)
        sys.exit(0)

    sys.exit(1)


if __name__ == "__main__":
    main()
