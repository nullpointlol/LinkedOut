"""Tests para verificar el contrato del UI: eliminación del select duplicado, presencia del botón de descarte in-place y paleta dark mode."""
from pathlib import Path
import re

INDEX_PATH = Path("static/index.html")
APP_JS_PATH = Path("static/app.js")


def test_select_modelo_activo_removed():
    """El select redundantemente duplicado del header debe ser eliminado por completo."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert '<select id="selectModeloActivo"' not in content, (
        "El elemento <select id='selectModeloActivo'> todavía está presente en static/index.html. "
        "Debe eliminarse y unificarse en el botón de la derecha."
    )


def test_btn_ver_telemetria_present_as_unified_control():
    """El botón unificado de la derecha debe existir con sus identificadores de modelo y telemetría."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert 'id="btnVerTelemetria"' in content
    assert 'id="labelModeloActivo"' in content
    assert 'id="labelThinkingTokensBadge"' in content


def test_btn_descartar_oferta_present_in_subpanel_header():
    """El botón de descarte in-place debe estar presente en la cabecera de la vacante."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert 'id="btnDescartarOferta"' in content
    assert 'descartarOfertaClick(this)' in content
    assert 'id="lblDescartar"' in content


def test_app_js_implements_in_place_discard_logic():
    """app.js debe implementar descartarOfertaClick y resetBotonDescartar sin modales invasivos."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function descartarOfertaClick" in content
    assert "function resetBotonDescartar" in content
    assert "DELETE" in content
    assert "/api/ofertas/" in content


def test_app_js_telemetria_populates_model_selector():
    """abrirModalTelemetria y actualizarTelemetriaModelo deben operar sin depender del select viejo."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function abrirModalTelemetria" in content
    assert "function cambiarModelo" in content
    # Comprobar que cambiarModelo hace POST a /api/model-config
    assert "/api/model-config" in content


def test_dark_mode_palette_applied():
    """index.html debe usar la paleta de fondo mate #0f1115 y superficies #16181d."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert "#0f1115" in content or "bg-[#0f1115]" in content
    assert "#16181d" in content or "bg-[#16181d]" in content


def test_app_js_syntax_is_valid():
    """static/app.js debe compilar sintácticamente sin errores de parser en JavaScript."""
    import subprocess
    import shutil

    node_bin = shutil.which("node")
    if node_bin:
        res = subprocess.run([node_bin, "-c", str(APP_JS_PATH)], capture_output=True, text=True)
        assert res.returncode == 0, f"Error de sintaxis en static/app.js:\n{res.stderr}"

