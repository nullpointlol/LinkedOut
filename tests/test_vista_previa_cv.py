"""Vista previa del CV en el editor (Manual): las viñetas «•» que escribe el redactor se ven como lista, una por
renglón, igual que en el PDF. Antes, Markdown las tomaba como un solo párrafo («• a • b • c» de corrido).

Con el `marked` local (static/vendor) y app.js en node, sin navegador.
Medidas: cada «•» al principio de renglón es un <li>; las negritas de la etiqueta («• **Frontend:** …») se
mantienen; un «•» en medio del texto no se toca; el HTML sigue escapado (markdownSeguro).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")

CV = """# SAM ORTEGA
Ciudad Ejemplo | sam.ortega@example.com

## EXPERIENCIA LABORAL
### Desarrolladora Frontend | 2021 - 2025
• Construí interfaces en React.
• Medí el rendimiento con Lighthouse.
  • Con sangría también.

## HABILIDADES TÉCNICAS
• **Frontend:** React, TypeScript.
• **Calidad:** Jest • Playwright.
• <img src=x onerror=alert(1)>

Texto con un • en el medio.
"""


def _render(md: str) -> str:
    script = f"""
      global.window = global;
      global.document = {{ addEventListener() {{}}, getElementById() {{ return null; }}, querySelectorAll() {{ return []; }} }};
      global.escapeHtml = (s) => s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
      global.marked = require({json.dumps(str(RAIZ / "static" / "vendor" / "marked-18.2.1" / "marked.umd.js"))});
      require({json.dumps(str(RAIZ / "static" / "app.js"))});
      console.log(JSON.stringify(window.markdownCv({json.dumps(md)})));
    """
    salida = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(salida.stdout.strip().splitlines()[-1])


def test_cada_vineta_es_un_item_de_lista():
    html = _render(CV)
    assert html.count("<li>") == 6
    assert "<li>Construí interfaces en React.</li>" in html
    assert "<li>Medí el rendimiento con Lighthouse.</li>" in html
    assert "<li>Con sangría también.</li>" in html
    # Ningún renglón ni párrafo empieza con «•»: todas las viñetas quedaron en la lista
    assert not [linea for linea in html.splitlines() if linea.lstrip().startswith(("•", "<p>•"))]


def test_negritas_de_la_etiqueta_y_puntos_en_medio_del_texto():
    html = _render(CV)
    assert "<li><strong>Frontend:</strong> React, TypeScript.</li>" in html
    assert "<li><strong>Calidad:</strong> Jest • Playwright.</li>" in html
    assert "<p>Texto con un • en el medio.</p>" in html


def test_el_html_del_cv_sigue_escapado():
    html = _render(CV)
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_el_editor_usa_la_vista_previa_del_cv():
    app = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")
    assert "cvContainer.innerHTML = markdownCv(cvMarkdown);" in app
