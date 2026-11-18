"""Perfil del CV con años reales y sin requisitos copiados (creador, 08/11: «más de doce años en monitoreo»
cuando el perfil dice 6, y «Residencia CABA o Primer Cordón» copiado de la oferta)."""
import pytest

from core import cv_guardas as g

AREAS = [{"area": "Experiencia laboral total", "anios": "15"}, {"area": "análisis funcional", "anios": "más de 7"},
         {"area": "soporte e incidentes", "anios": "7"}, {"area": "monitoreo/ITSM", "anios": "6"}]
CV = """# ANA PRUEBA
ANALISTA DE MONITOREO E INCIDENTES
Ciudad Ejemplo, Buenos Aires | ana@example.com

## PERFIL
{perfil}

## EXPERIENCIA
### Empresa | Analista (2019 - 2025)
• Durante 3 años coordiné guardias con 12 años de historia.
"""


def perfil(texto):
    cv, cambios = g.corregir_perfil(CV.format(perfil=texto), AREAS)
    return cv.split("## PERFIL\n", 1)[1].split("\n\n## ", 1)[0], cambios


@pytest.mark.parametrize("texto,esperado", [
    ("Más de doce años en monitoreo de infraestructura y gestión de incidentes.", "6 años en monitoreo"),
    ("Tengo 12 años en análisis funcional.", "más de 7 años en análisis funcional"),
    ("Más de 7 años en monitoreo 24x7.", "6 años en monitoreo"),  # cifra de otra área
    ("Con +20 años de experiencia, coordino guardias.", "15 años de experiencia"),
    ("Over 12 years in incident support.", "15 years in incident support"),  # área en inglés: total
])
def test_cifra_fuera_del_perfil_se_corrige(texto, esperado):
    nuevo, cambios = perfil(texto)
    assert esperado.lower() in nuevo.lower() and cambios


@pytest.mark.parametrize("texto", [
    "Más de 7 años en análisis funcional y 6 años en monitoreo.",
    "15 años de experiencia laboral.",
    "Con +7 años de experiencia en soporte e incidentes.",
    "Analista funcional enfocado en sistemas críticos.",
])
def test_cifras_reales_no_se_tocan(texto):
    assert perfil(texto) == (texto, [])


def test_requisito_copiado_se_quita_y_experiencia_no_se_toca():
    cv, cambios = g.corregir_perfil(CV.format(perfil="6 años en monitoreo. Residencia CABA o Primer Cordón y "
                                                   "disponibilidad para esquema híbrido flexible."), AREAS)
    assert "Residencia" not in cv and "Primer Cordón" not in cv and "6 años en monitoreo." in cv
    assert "Durante 3 años coordiné guardias con 12 años de historia." in cv  # fuera del perfil no se toca
    assert len(cambios) == 1


def test_sin_cifras_del_candidato_no_inventa():
    cv = CV.format(perfil="Más de doce años en monitoreo.")
    assert g.corregir_perfil(cv, [])[0] == cv


def test_prompt_del_redactor_lleva_las_cifras():
    texto = g.bloque_prompt(AREAS)
    assert "monitoreo/ITSM: 6 años" in texto and "análisis funcional: más de 7 años" in texto
    assert "primer cordón" in texto
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    assert "{bloque_prompt()}" in (raiz / "core" / "writer.py").read_text(encoding="utf-8")
    auditor = (raiz / "core" / "auditor.py").read_text(encoding="utf-8")
    assert auditor.count("corregir_perfil(sanear_cv(") == 2


@pytest.mark.parametrize("nombre,logistico", [
    ("Residencia en CABA o Primer Cordón", True), ("Modalidad híbrida", True), ("Jornada completa", True),
    ("Estudios en Sistemas o afines", False), ("Monitoreo 24x7", False), ("Gestión de incidentes", False),
    ("Manejo de Excel", False),
])
def test_requisitos_logisticos(nombre, logistico):
    assert g.es_requisito_logistico(nombre) is logistico


def test_keywords_ats_sin_requisitos_logisticos():
    """El caso real (#2543): exigir «Residencia en CABA…» como keyword dejaba el ATS bajo el piso."""
    import server
    kws = ["Monitoreo 24x7", "Residencia en CABA o Primer Cordón", "Estudios en Sistemas o afines"]
    # Creador, 09/11: la formación tampoco es keyword del CV (iba a parar a HABILIDADES TÉCNICAS)
    assert server._keywords_del_cv(kws, {}) == ["Monitoreo 24x7"]
    assert server._keywords_del_cv(["Modalidad híbrida"], {}) == ["Modalidad híbrida"]


def test_redactor_no_enfatiza_requisitos_logisticos():
    from pathlib import Path
    writer = (Path(__file__).resolve().parent.parent / "core" / "writer.py").read_text(encoding="utf-8")
    assert 'and not es_requisito_logistico(r.get("nombre", ""))' in writer


def test_requisitos_de_postulacion_no_suman_afinidad():
    """Caso real (#1372, 91 caracteres): «Residencia…» y «Envío de CV con pretensiones…» en match daban 100%."""
    from core.strategist import calculate_match_score
    solo_logistica = [{"nombre": "Residencia en Argentina o Uruguay", "estado": "MATCH_DIRECTO"},
                      {"nombre": "Envío de CV con pretensiones salariales", "estado": "MATCH_DIRECTO"}]
    assert calculate_match_score(solo_logistica) == 0.0
    con_rol = solo_logistica + [{"nombre": "Gestión de incidentes", "estado": "BRECHA_REAL"}]
    assert calculate_match_score(con_rol) == 0.0


@pytest.mark.asyncio
async def test_links_escritos_en_el_texto_se_leen(monkeypatch):
    import server
    leidos = []

    async def leer(url):
        leidos.append(url)
        return url, "Buscamos Analista Funcional con experiencia en SQL. " * 10

    monkeypatch.setattr(server, "_leer_link_oferta", leer)
    post = {"id": None, "texto": "Nueva oportunidad 👉 https://lnkd.in/dsenY7SK.", "links": [], "intentos": 0}
    r = await server._radar_preparar_analisis(post)
    assert leidos == ["https://lnkd.in/dsenY7SK"]
    assert "Analista Funcional" in r["texto"]


def test_pdf_se_descarga_con_fetch():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "radar.js").read_text(encoding="utf-8")
    assert "async function descargarPdf(" in js and "URL.createObjectURL(blob)" in js
    assert "window.open(`/api/ofertas/${Number(p.postulacion_id)}/export/pdf`" not in js
    assert "linkPdf.onclick = (e) => descargarPdf(o.id, e)" in js
