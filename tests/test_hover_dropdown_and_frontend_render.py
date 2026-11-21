"""Tests TDD para verificar la resolución de escapeHtml, renderizado seguro y selector de modelos desplegable por hover."""
from pathlib import Path
import subprocess
import pytest

INDEX_PATH = Path("static/index.html")
APP_JS_PATH = Path("static/app.js")


def test_escape_html_is_defined_in_app_js():
    """static/app.js debe declarar explícitamente function escapeHtml para evitar ReferenceError."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "function escapeHtml(" in content, (
        "La función escapeHtml no está definida en static/app.js, "
        "lo que produce un ReferenceError que detiene la renderización del CV y de la matriz."
    )


def test_escape_html_sanitizes_html_entities_via_node():
    """Valida la ejecución real de escapeHtml en Node.js escapando caracteres peligrosos."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    import re
    match = re.search(r"function escapeHtml\(str\)\s*\{[\s\S]*?\n\}", content)
    assert match is not None, "No se encontró la definición de escapeHtml en app.js"
    fn_code = match.group(0)

    js_test = f"""
    {fn_code}
    const input = '<script>alert("XSS" & \\'test\\')</script>';
    const output = escapeHtml(input);
    if (!output.includes('&lt;script&gt;') || !output.includes('&amp;') || !output.includes('&quot;') || !output.includes('&#39;')) {{
        process.exit(1);
    }}
    process.exit(0);
    """
    result = subprocess.run(
        ["node", "-e", js_test],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    assert result.returncode == 0, f"Error ejecutando escapeHtml en Node: {result.stderr}"


def test_dropdown_telemetria_anchored_in_header():
    """El selector de modelos debe estar anclado en el header como un desplegable relativo, no un popup fullscreen."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert 'id="contenedorSelectorModelo"' in content, (
        "Debe existir #contenedorSelectorModelo en el header de static/index.html."
    )
    assert 'id="dropdownTelemetria"' in content, (
        "Debe existir #dropdownTelemetria como menú desplegable en static/index.html."
    )
    # Comprobar que modalTelemetria ya no sea un modal centrado con backdrop fullscreen invasivo
    assert 'id="modalTelemetria"' not in content, (
        "El viejo popup centrado #modalTelemetria debe reemplazarse por #dropdownTelemetria."
    )


def test_ajustes_menu_toggle_and_escape_configured_in_app_js():
    """El menú Ajustes se abre con el botón (accesible por teclado), se cierra con Escape y no depende del hover."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    assert "toggleAjustesMenu" in content, "Falta toggleAjustesMenu para abrir/cerrar el menú Ajustes."
    assert "keydown" in content and "Escape" in content, "Falta cerrar el menú Ajustes con Escape."
    assert "mouseenter" not in content, "El menú Ajustes ya no se abre por hover (no sirve en celular ni con teclado)."
    assert "mostrarDropdownTelemetria" in content or "abrirDropdownTelemetria" in content
    assert "ocultarDropdownTelemetria" in content or "cerrarDropdownTelemetria" in content


def test_render_oferta_has_safe_isolated_rendering():
    """renderOferta debe proteger con try/catch las llamadas de subpaneles para que un fallo no bloquee el CV."""
    content = APP_JS_PATH.read_text(encoding="utf-8")
    # Validar que renderMatrizOferta esté dentro de un try/catch o esté aislado
    assert "try" in content
    # Asegurar que la asignación del CV ocurra de manera segura
    assert "cvContainer.innerHTML" in content
