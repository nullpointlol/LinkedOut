"""Tests TDD para Task 1: Desambiguación de 'IA Expand' y Saneamiento Estético de Dolores de Empresa."""
from pathlib import Path
import re
import subprocess
import pytest

INDEX_PATH = Path("static/index.html")
APP_JS_PATH = Path("static/app.js")
STRATEGIST_PATH = Path("core/strategist.py")
SERVER_PATH = Path("server.py")


def test_btn_abrir_expandir_perfil_text_and_title():
    """El botón superior (#btnAbrirExpandirPerfil) fue purgado en favor de los tags interactivos."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert "btnAbrirExpandirPerfil" not in content


def test_btn_expandir_brechas_text_and_title():
    """El botón de afinidad (#btnExpandirBrechas) fue purgado en favor de los chips interactivos."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert "btnExpandirBrechas" not in content


def test_modal_expandir_perfil_title_and_subtitle():
    """El modal #modalExpandirPerfil fue purgado en favor de la matriz interactiva."""
    content = INDEX_PATH.read_text(encoding="utf-8")
    assert "modalExpandirPerfil" not in content


def test_strategist_and_server_utf8_encoding_clean():
    """Tanto core/strategist.py como server.py deben tener codificación UTF-8 limpia y sin caracteres corruptos."""
    for path in [STRATEGIST_PATH, SERVER_PATH]:
        raw_bytes = path.read_bytes()
        assert b"\xef\xbf\xbd" not in raw_bytes, f"{path} contiene caracteres de reemplazo U+FFFD"
        content = raw_bytes.decode("utf-8")
        assert "\ufffd" not in content, f"{path} contiene \\ufffd tras decodificar"

    # Verificar dolores_empresa en core/strategist.py
    strategist_content = STRATEGIST_PATH.read_text(encoding="utf-8")
    expected_dolores = [
        "Reducción sistemática de tareas manuales operativas y aceleración de entregas.",
        "Consolidación de calidad de código y observabilidad de incidentes en tiempo real.",
        "Articulación fluida entre requerimientos de negocio y equipos técnicos.",
    ]
    for dolor in expected_dolores:
        assert dolor in strategist_content, f"Falta o está corrupto el dolor determinístico: {dolor}"

    # Verificar server.py METODOLOGIAS_KW y palabras de responsabilidad
    server_content = SERVER_PATH.read_text(encoding="utf-8")
    assert '"ágil"' in server_content or "'ágil'" in server_content
    for kw in ["diseño", "coordinación", "documentación", "análisis", "gestión"]:
        assert f'"{kw}"' in server_content or f"'{kw}'" in server_content, f"Falta keyword UTF-8 limpia: {kw}"


def test_lista_dolores_aesthetic_styling_in_app_js():
    """Cada dolor de empresa en app.js debe renderizarse como tarjeta Dark Mode sin viñetas toscas ni chevrones rotos."""
    content = APP_JS_PATH.read_text(encoding="utf-8")

    # Extraer el bloque donde se renderiza listaDolores
    match = re.search(r'// Dolores de empresa[\s\S]*?doloresList\.appendChild\(li\);', content)
    assert match is not None, "No se encontró el bloque de renderizado de listaDolores en app.js"
    block = match.group(0)

    # Rediseño E4: tarjeta con .editor-dolor (editor.css): fondo, borde, esquinas redondeadas e interlineado amplio
    assert "editor-dolor" in block, "Falta la clase .editor-dolor en las tarjetas de dolores"
    css = Path("static/css/editor.css").read_text(encoding="utf-8")
    regla = css[css.index(".editor-dolor {"):]
    regla = regla[:regla.index("}")]
    for propiedad in ("background: var(--bg-", "border: 1px solid var(--border-subtle)", "border-radius: var(--radius-md)",
                      "line-height: var(--lh-normal)"):
        assert propiedad in regla, propiedad

    # No debe tener chevrones ni viñetas toscas
    assert "fa-chevron-right" not in block, "No debe incluir fa-chevron-right en las tarjetas de dolores"

    # Validar en Node.js que el elemento generado tenga la clase correcta
    node_script = """
    const jsdom = require('jsdom');
    // Si jsdom no está instalado, simplemente verificamos la cadena de clases
    """
    # Verificamos sintaxis completa de app.js con node
    syntax_check = subprocess.run(["node", "--check", "static/app.js"], capture_output=True, text=True)
    assert syntax_check.returncode == 0, f"Error de sintaxis en static/app.js: {syntax_check.stderr}"


def test_cv_theme_and_dropdown_alignment():
    """#dropdownTelemetria (menú Ajustes) no debe desbordar a 375 px y .prose-cv debe adoptar una paleta pastel sin colores neón."""
    content = INDEX_PATH.read_text(encoding="utf-8")

    # 1. Menú Ajustes: fijo con márgenes en celular, anclado a la derecha desde sm (el engranaje queda a la derecha)
    match_dropdown = re.search(r'<div\s+id="dropdownTelemetria"[^>]*class="([^"]*)"', content)
    assert match_dropdown is not None, "#dropdownTelemetria no encontrado en static/index.html"
    # Rediseño E4b: la posición la da .ajustes en modales.css (fijo con márgenes; anclado a la derecha desde 640 px)
    assert "ajustes" in match_dropdown.group(1).split()
    modales = Path("static/css/modales.css").read_text(encoding="utf-8")
    base = modales[modales.index(".ajustes {"):]
    assert "position: fixed" in base[:base.index("}")] and "right: var(--sp-3)" in base[:base.index("}")]
    ancha = modales[modales.index("@media (min-width: 640px)"):]
    assert "position: absolute" in ancha[:ancha.index("}")] and "right: 0" in ancha[:ancha.index("}")]

    # 2. Extraer el bloque CSS de .prose-cv
    match_css = re.search(r'/\* Formato visual refinado para secciones del CV web \*/([\s\S]*?)</style>', content)
    assert match_css is not None, "Bloque CSS de .prose-cv no encontrado"
    css_cv = match_css.group(1)

    # 3. Verificar eliminación de colores estridentes/neón
    assert "#fef08a" not in css_cv, "El color amarillo neón #fef08a debe ser eliminado de .prose-cv"
    assert "3b82f6" not in css_cv, "Los tonos azules chillones 3b82f6 deben ser eliminados de .prose-cv"
    assert "#f59e0b" not in css_cv, "El ámbar brillante #f59e0b debe ser eliminado de .prose-cv"

    # 4. Verificar nueva paleta pastel suave
    assert re.search(r'\.prose-cv\s+strong\s*\{[^}]*color:\s*#f1f5f9', css_cv), (
        "El selector .prose-cv strong debe usar color: #f1f5f9"
    )
    assert re.search(r'\.prose-cv\s+h2\s*\{[^}]*color:\s*#cbd5e1', css_cv), (
        "El selector .prose-cv h2 debe usar color: #cbd5e1"
    )
    assert re.search(r'\.prose-cv\s+li::before\s*\{[^}]*color:\s*#64748b', css_cv), (
        "El selector .prose-cv li::before debe usar color: #64748b"
    )

