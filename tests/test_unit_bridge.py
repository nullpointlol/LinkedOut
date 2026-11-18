"""Unitarios de `core/bridge.py`: extracción de JSON, nombres de modelos, resolución del binario y
del esfuerzo, salida del proceso de `agy` (simulado: nunca se ejecuta el CLI), terminación y avisos."""
import asyncio
import logging
from pathlib import Path

import pytest

from core import bridge
from core.bridge import AntigravityBridge, extract_json_payload

# Original del registro de errores: conftest lo reemplaza en cada test, la colección es anterior
_REGISTRAR_ORIGINAL = bridge._registrar_error_agy


# --------------------------------------------------------------------------- extract_json_payload

def test_extract_json_payload_casos():
    """Wrapper con response dict, wrapper sin respuesta útil, bloque markdown con lista, objeto
    incrustado en texto y textos sin JSON válido."""
    for texto, esperado in [
        ("", {}),
        ("   ", {}),
        ('{"response": {"ok": 1}}', {"ok": 1}),
        ('{"status": "SUCCESS", "conversation_id": "c1"}', {"status": "SUCCESS", "conversation_id": "c1"}),
        ('Mirá:\n```json\n[1, 2]\n```', {"items": [1, 2]}),
        ('Mirá:\n```json\n{roto\n```\nfin', {"raw": 'Mirá:\n```json\n{roto\n```\nfin'}),
        ('Texto previo {"a": 1} texto posterior', {"a": 1}),
        ('Texto {sin json} final', {"raw": "Texto {sin json} final"}),
    ]:
        assert extract_json_payload(texto) == esperado


# --------------------------------------------------------------------------- modelos y binario

def test_nombre_visible_de_modelos():
    """Cada familia tiene su nombre legible; un id desconocido se muestra tal cual."""
    for modelo, nombre in [
        ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
        ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
        ("modelo-desconocido", "modelo-desconocido"),
    ]:
        assert AntigravityBridge._format_display_name(modelo) == nombre


def test_resolver_esfuerzo_de_modelos_sin_familia():
    """Fuera de Gemini/Claude, solo se manda --effort si coincide con el sufijo del modelo."""
    for modelo, esfuerzo, esperado in [
        ("gpt-oss-120b-medium", "medium", ("gpt-oss-120b-medium", "medium")),
        ("gpt-oss-120b-medium", "low", ("gpt-oss-120b-medium", None)),
        ("otro-modelo", "high", ("otro-modelo", "high")),
    ]:
        assert AntigravityBridge.resolve_model_and_effort(modelo, esfuerzo) == esperado


def test_resolver_binario_usa_ruta_por_defecto_o_none(monkeypatch, tmp_path):
    """Sin `agy` en el PATH se prueban las rutas estándar; si no existe ninguna, None."""
    monkeypatch.setattr(bridge.shutil, "which", lambda nombre: None)
    instalado = tmp_path / "agy.exe"
    instalado.write_bytes(b"")
    monkeypatch.setattr(bridge, "DEFAULT_AGY_PATHS", [tmp_path / "no_esta.exe", instalado])
    assert AntigravityBridge()._resolve_binary() == instalado
    monkeypatch.setattr(bridge, "DEFAULT_AGY_PATHS", [tmp_path / "no_esta.exe"])
    assert AntigravityBridge()._resolve_binary() is None


@pytest.mark.asyncio
async def test_ejecutar_sin_binario_avisa_donde_instalarlo(monkeypatch, tmp_path):
    """Si el ejecutable no existe, la llamada falla con FileNotFoundError sin crear procesos."""
    b = AntigravityBridge(agy_path=tmp_path / "agy.exe")
    b.agy_path = tmp_path / "no_existe.exe"
    with pytest.raises(FileNotFoundError, match="Antigravity CLI"):
        await b._ejecutar_prompt("hola")


# --------------------------------------------------------------------------- proceso simulado

class _ProcesoFalso:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self._salida = (stdout, stderr)
        self.returncode = returncode
        self.pid = 4321

    async def communicate(self):
        return self._salida


def _bridge_con_proceso(monkeypatch, tmp_path, proceso, cerrar=None):
    binario = tmp_path / "agy.exe"
    binario.write_bytes(b"")
    llamadas = []

    async def crear(*cmd, **kwargs):
        llamadas.append((cmd, kwargs))
        return proceso

    monkeypatch.setattr(bridge, "_crear_proceso", crear)
    monkeypatch.setattr(bridge, "_cerrar_proceso", cerrar or (lambda p: None))
    return AntigravityBridge(agy_path=binario), llamadas


@pytest.mark.asyncio
async def test_codigo_de_error_con_salida_devuelve_la_salida(monkeypatch, tmp_path):
    """Con código distinto de 0 pero JSON en stdout, se usa la respuesta (en JSON o en texto)."""
    proceso = _ProcesoFalso(stdout=b'{"ok": true}', returncode=1)
    b, _ = _bridge_con_proceso(monkeypatch, tmp_path, proceso)
    assert await b._ejecutar_prompt("hola", json_mode=True) == {"ok": True}
    assert await b._ejecutar_prompt("hola", json_mode=False) == '{"ok": true}'


@pytest.mark.asyncio
async def test_codigo_de_error_sin_salida_lanza_con_stderr(monkeypatch, tmp_path):
    """Con código de error y stdout vacío, se lanza RuntimeError con el stderr."""
    proceso = _ProcesoFalso(stdout=b"", stderr=b"falla interna", returncode=2)
    b, _ = _bridge_con_proceso(monkeypatch, tmp_path, proceso)
    with pytest.raises(RuntimeError, match=r"código 2\): falla interna"):
        await b._ejecutar_prompt("hola")


@pytest.mark.asyncio
async def test_cwd_se_pasa_al_proceso_y_un_error_al_cerrar_solo_se_registra(monkeypatch, tmp_path, caplog):
    """El `cwd` llega al proceso; si cerrar sus recursos falla, se registra y la respuesta sigue."""
    def cerrar_roto(p):
        raise OSError("handle inválido")

    proceso = _ProcesoFalso(stdout=b'{"ok": 1}')
    b, llamadas = _bridge_con_proceso(monkeypatch, tmp_path, proceso, cerrar=cerrar_roto)
    with caplog.at_level(logging.ERROR, logger="core.bridge"):
        assert await b._ejecutar_prompt("hola", cwd=tmp_path) == {"ok": 1}
    assert llamadas[0][1]["cwd"] == str(tmp_path)
    assert "cierre de los recursos" in caplog.text


@pytest.mark.asyncio
async def test_en_posix_el_proceso_arranca_en_sesion_nueva(monkeypatch, tmp_path):
    """Fuera de Windows el proceso se crea en una sesión propia (para matar su grupo)."""
    proceso = _ProcesoFalso(stdout=b'{"ok": 1}')
    b, llamadas = _bridge_con_proceso(monkeypatch, tmp_path, proceso)
    monkeypatch.setattr(bridge.sys, "platform", "linux")
    await b._ejecutar_prompt("hola")
    assert llamadas[0][1].get("start_new_session") is True
    assert "creationflags" not in llamadas[0][1]


# --------------------------------------------------------------------------- terminación

class _ProcesoRebelde:
    def __init__(self, error_kill, error_wait=None):
        self.error_kill = error_kill
        self.error_wait = error_wait
        self.pid = 999999

    async def terminar_arbol(self):
        return None

    def kill(self):
        raise self.error_kill

    async def wait(self):
        if self.error_wait:
            raise self.error_wait


@pytest.mark.asyncio
async def test_terminar_tolera_proceso_ya_muerto(monkeypatch):
    """Si el padre ya no existe (ProcessLookupError) la limpieza termina sin errores."""
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    await bridge._terminar_proceso(_ProcesoRebelde(ProcessLookupError()))


@pytest.mark.asyncio
async def test_terminar_registra_fallos_de_kill_y_wait(monkeypatch, caplog):
    """Errores inesperados al matar o esperar al padre se registran y no cortan la limpieza."""
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    with caplog.at_level(logging.ERROR, logger="core.bridge"):
        await bridge._terminar_proceso(_ProcesoRebelde(PermissionError("x"), error_wait=OSError("y")))
    assert "terminación del proceso padre" in caplog.text
    assert "espera del proceso padre" in caplog.text


@pytest.mark.asyncio
async def test_terminar_arbol_en_posix_ignora_grupo_inexistente(monkeypatch):
    """En POSIX se mata el grupo del proceso; si ya no existe, no falla."""
    llamadas = []

    def killpg(pid, senal):
        llamadas.append(pid)
        raise ProcessLookupError()

    monkeypatch.setattr(bridge.sys, "platform", "linux")
    monkeypatch.setattr(bridge.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(bridge.signal, "SIGKILL", 9, raising=False)
    await bridge._terminar_arbol(_ProcesoRebelde(ProcessLookupError()))
    assert llamadas == [999999]


@pytest.mark.asyncio
async def test_crear_proceso_en_posix_usa_asyncio(monkeypatch):
    """Fuera de Windows el proceso se crea con `asyncio.create_subprocess_exec`."""
    recibido = {}

    async def crear(*cmd, **kwargs):
        recibido.update(cmd=cmd, kwargs=kwargs)
        return "proceso"

    monkeypatch.setattr(bridge.sys, "platform", "linux")
    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", crear)
    assert await bridge._crear_proceso("agy", "-p", "hola", cwd="x") == "proceso"
    assert recibido == {"cmd": ("agy", "-p", "hola"), "kwargs": {"cwd": "x"}}


# --------------------------------------------------------------------------- cuota y registro

def test_agotada_con_turno_lo_suelta():
    """Quien tenía el turno y choca con el límite lo suelta para que otra llamada pruebe."""
    estado = bridge._EstadoCuota()
    estado._probando = True
    estado.agotada("quota exceeded", soltar_turno=True)
    assert estado._probando is False
    assert estado.estado()["agotada"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.TimeoutError(), RuntimeError("otra cosa")])
async def test_sondeo_de_cuota_no_propaga_errores(monkeypatch, error):
    """El sondeo manual devuelve el estado de la cuota aunque la llamada mínima falle o tarde."""
    async def falla(self, *a, **k):
        raise error

    monkeypatch.setattr(AntigravityBridge, "run_prompt", falla)
    estado = await bridge.sondear_cuota()
    assert set(estado) == {"agotada", "desde", "reintento", "renueva", "mensaje"}
    assert estado["agotada"] is False


def test_registrar_error_agrega_una_linea_al_log(monkeypatch, tmp_path):
    """El error de AGY queda en una sola línea (saltos como « | ») en agy_errores.log."""
    import core.paths
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    _REGISTRAR_ORIGINAL("linea 1\nlinea 2")
    contenido = (tmp_path / "agy_errores.log").read_text(encoding="utf-8")
    assert contenido.endswith(" linea 1 | linea 2\n")
    assert contenido.count("\n") == 1


def test_registrar_error_sin_carpeta_no_falla(monkeypatch, tmp_path):
    """Si no se puede escribir el log, el registro se omite en silencio."""
    import core.paths
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path / "no" / "existe")
    _REGISTRAR_ORIGINAL("algo")
    assert not (tmp_path / "no").exists()


# --------------------------------------------------------------------------- admisión

def test_aviso_a_loop_cerrado_entre_operaciones_no_falla():
    """Si el loop de una admisión se cierra justo al avisar, el RuntimeError se ignora."""
    class _Loop:
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, fn):
            raise RuntimeError("Event loop is closed")

    class _Admision:
        loop = _Loop()

        def despertar(self):
            pass

    admision = _Admision()
    bridge._ADMISIONES.add(admision)
    try:
        bridge._avisar_admisiones()
    finally:
        bridge._ADMISIONES.discard(admision)


def test_modo_serie_desde_un_hilo_sin_loop_persiste():
    """Activar el modo serie fuera de un event loop usa la capacidad configurada y lo guarda."""
    bridge.configurar_modo_serie(True)
    try:
        estado = bridge.estado_admision()
        assert estado["modo_serie"] is True
        assert estado["limite"] == 1
    finally:
        bridge.configurar_modo_serie(False)
    assert bridge.estado_admision()["modo_serie"] is False


@pytest.mark.asyncio
async def test_despertar_con_cola_notifica_a_los_que_esperan():
    """Con pedidos en cola, `despertar` agenda una notificación que libera a quien espera."""
    admision = bridge._AdmisionAGY(asyncio.get_running_loop())
    admision.cola.append((1, 1))
    liberado = asyncio.Event()

    async def esperar():
        async with admision.condition:
            await admision.condition.wait()
            liberado.set()

    tarea = asyncio.create_task(esperar())
    await asyncio.sleep(0)
    admision.despertar()
    await asyncio.wait_for(liberado.wait(), timeout=60)
    await tarea
    assert liberado.is_set()
