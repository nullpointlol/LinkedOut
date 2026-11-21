"""Tests para verificar que subprocess y asyncio.create_subprocess_exec usen CREATE_NO_WINDOW en Windows."""
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from core.bridge import AntigravityBridge, CREATE_NO_WINDOW


def test_create_no_window_flag_value():
    """En Windows CREATE_NO_WINDOW debe ser 0x08000000, en otros SO debe ser 0."""
    if sys.platform == "win32":
        assert CREATE_NO_WINDOW == 0x08000000
    else:
        assert CREATE_NO_WINDOW == 0


def test_get_model_info_uses_no_window_and_caches():
    """get_model_info debe llamar a subprocess.run con creationflags y cachear el resultado."""
    bridge = AntigravityBridge()
    AntigravityBridge._cached_available_models = None

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = "gemini-3.8-flash-high\ngpt-oss-120b-medium\n"

    with patch("subprocess.run", return_value=mock_res) as mock_run:
        info1 = bridge.get_model_info()
        assert "gemini-3.8-flash-high" in info1["available_models"]

        if sys.platform == "win32":
            _, kwargs = mock_run.call_args
            assert kwargs.get("creationflags") == 0x08000000

        # Segunda llamada: debe usar caché en memoria y NO llamar nuevamente a subprocess.run
        info2 = bridge.get_model_info()
        assert info2["available_models"] == info1["available_models"]
        assert mock_run.call_count == 1, "get_model_info no usó la caché de modelos disponibles"


@pytest.mark.asyncio
async def test_run_prompt_uses_create_no_window():
    """run_prompt solicita ejecución sin ventana al creador de procesos propio."""
    bridge = AntigravityBridge()

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b'{"status": "SUCCESS", "response": "ok"}', b"")
    mock_proc.returncode = 0

    with patch("core.bridge._crear_proceso", return_value=mock_proc) as mock_exec:
        await bridge.run_prompt("test prompt", json_mode=False)

        if sys.platform == "win32":
            _, kwargs = mock_exec.call_args
            assert kwargs.get("creationflags") == 0x08000000


@pytest.mark.asyncio
async def test_run_prompt_passes_cwd_only_when_given(tmp_path):
    """`cwd` llega al creador de procesos; sin `cwd` se hereda el directorio."""
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_bytes(b"")
    bridge = AntigravityBridge()
    bridge.agy_path = fake_agy

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b'{"status": "SUCCESS", "response": "ok"}', b"")
    mock_proc.returncode = 0

    aislado = tmp_path / "aislado"
    aislado.mkdir()
    with patch("core.bridge._crear_proceso", return_value=mock_proc) as mock_exec:
        await bridge.run_prompt("p", json_mode=False, cwd=aislado)
        _, kwargs = mock_exec.call_args
        assert kwargs["cwd"] == str(aislado)
        args, _ = mock_exec.call_args
        assert "--dangerously-skip-permissions" in args  # comportamiento por defecto sin cambios

        await bridge.run_prompt("p", json_mode=False)
        _, kwargs = mock_exec.call_args
        assert "cwd" not in kwargs


def test_radar_agy_cwd_is_empty_temp_dir_outside_repo():
    import tempfile
    from pathlib import Path
    from core.bridge import radar_agy_cwd
    repo = Path(__file__).resolve().parent.parent
    with radar_agy_cwd() as d:
        assert d.is_dir() and list(d.iterdir()) == []
        assert Path(tempfile.gettempdir()).resolve() in d.resolve().parents
        assert repo not in d.resolve().parents
    assert not d.exists()
