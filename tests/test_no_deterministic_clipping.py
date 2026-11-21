import pytest
from core.writer import ensure_cv_length


def test_ensure_cv_length_does_not_mechanically_clip_sentences_or_words():
    raw_cv = """# CANDIDATO
ANALISTA FUNCIONAL
Contacto

## PERFIL
Perfil profesional enfocado en análisis funcional y diseño de soluciones transaccionales.

## EXPERIENCIA
### Empresa Uno | 2020 - 2024
• **Relevamiento Funcional:** Lideré el relevamiento de procesos operativos complejos para la implementación de un sistema transaccional de facturación electrónica masiva con clientes corporativos.
• **Pruebas de Integración:** Coordiné las pruebas de integración continua sobre servicios web y contratos de APIs REST y SOAP para certificar la consistencia de datos de extremo a extremo.

## HABILIDADES TÉCNICAS
• **Herramientas:** SQL, Jira, Python.

## EDUCACIÓN
• Licenciatura en Sistemas.

## IDIOMAS
• Español: Nativo.
"""
    # 1. Verificación básica sin compresión
    shortened = ensure_cv_length(raw_cv)
    for line in shortened.splitlines():
        if line.strip().startswith(("•", "*", "-")):
            assert not line.strip().endswith("..."), "No debe truncar viñetas mecánicamente con puntos suspensivos"
            if any(k in line for k in ["Relevamiento", "Pruebas", "Herramientas"]):
                assert "**" in line, "Debe preservar la etiqueta temática intacta"

    # 2. Verificación bajo compresión donde antes se aplicaba shorten_max_len
    shortened_tight = ensure_cv_length(raw_cv, max_chars=500)
    for line in shortened_tight.splitlines():
        if line.strip().startswith(("•", "*", "-")):
            assert not line.strip().endswith("..."), "No debe truncar viñetas mecánicamente con puntos suspensivos"
            if any(k in line for k in ["Relevamiento", "Pruebas", "Herramientas"]):
                assert "**" in line, "Debe preservar la etiqueta temática intacta"


def test_ensure_cv_length_preserves_whole_bullets_under_tight_max_chars():
    """Bajo compresión agresiva, ensure_cv_length debe preservar viñetas gramaticalmente íntegras y no recortar caracteres."""
    raw_cv = """# CANDIDATO
ANALISTA FUNCIONAL
Contacto

## PERFIL
Perfil profesional enfocado en análisis funcional y diseño de soluciones transaccionales.

## EXPERIENCIA
### Empresa Uno | 2020 - 2024
• **Relevamiento Funcional:** Lideré el relevamiento de procesos operativos complejos para la implementación de un sistema transaccional de facturación electrónica masiva con clientes corporativos.
• **Pruebas de Integración:** Coordiné las pruebas de integración continua sobre servicios web y contratos de APIs REST y SOAP para certificar la consistencia de datos de extremo a extremo.

## HABILIDADES TÉCNICAS
• **Herramientas:** SQL, Jira, Python.

## EDUCACIÓN
• Licenciatura en Sistemas.

## IDIOMAS
• Español: Nativo.
"""
    # Forzamos compresión ajustada que antes disparaba shorten_max_len (175, 140, 110, 85)
    shortened = ensure_cv_length(raw_cv, max_chars=480)

    # Las viñetas presentes deben ser oraciones completas originales, sin amputaciones mecánicas
    original_bullets = [
        "• **Relevamiento Funcional:** Lideré el relevamiento de procesos operativos complejos para la implementación de un sistema transaccional de facturación electrónica masiva con clientes corporativos.",
        "• **Pruebas de Integración:** Coordiné las pruebas de integración continua sobre servicios web y contratos de APIs REST y SOAP para certificar la consistencia de datos de extremo a extremo.",
    ]

    for line in shortened.splitlines():
        st = line.strip()
        if st.startswith(("•", "*", "-")) and "Relevamiento Funcional" in st:
            assert st == original_bullets[0], f"La viñeta fue amputada o mutilada: '{st}'"
        if st.startswith(("•", "*", "-")) and "Pruebas de Integración" in st:
            assert st == original_bullets[1], f"La viñeta fue amputada o mutilada: '{st}'"
        if st.startswith(("•", "*", "-")):
            assert not st.endswith("..."), "No debe tener elipsis mecánica"


def test_ensure_cv_length_no_sections_fallback_does_not_append_ellipsis():
    """Si no hay secciones ##, ensure_cv_length no debe amputar palabras con .rsplit ni inyectar '...'."""
    raw_text = "Texto plano de prueba para verificar que no hay recorte determinístico con puntos suspensivos. " * 30
    out = ensure_cv_length(raw_text, max_chars=100)
    assert not out.endswith("..."), "No debe inyectar puntos suspensivos mecánicos"
    assert "..." not in out, "No debe contener puntos suspensivos generados por recorte"
