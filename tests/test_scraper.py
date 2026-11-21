import pytest
from core.scraper import extract_job_from_html, clean_html_text

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Senior Technical Business Analyst - Telecom Argentina | MercadoJobs</title>
    <meta property="og:title" content="Senior Technical Business Analyst">
    <meta property="og:site_name" content="Telecom Argentina">
    <meta property="og:description" content="Buscamos Analista Técnico Funcional con experiencia en Python y SQL.">
    <style>body { font-family: sans-serif; }</style>
    <script>console.log("analytics");</script>
</head>
<body>
    <header><nav><a href="/">Inicio</a></nav></header>
    <main>
        <h1>Senior Technical Business Analyst</h1>
        <div class="company">Telecom Argentina</div>
        <div class="job-description">
            <p>Buscamos profesional para liderar relevamiento técnico e integración de sistemas críticos.</p>
            <h2>Requisitos:</h2>
            <ul>
                <li>Experiencia comprobable de 5 años en análisis funcional.</li>
                <li>Automatizaciones en Python y consultas SQL.</li>
                <li>Conocimientos de pipelines CI/CD con Jenkins o GitLab.</li>
            </ul>
        </div>
    </main>
    <footer>Copyright 2026</footer>
</body>
</html>
"""

def test_clean_html_text():
    cleaned = clean_html_text(SAMPLE_HTML)
    assert "analytics" not in cleaned
    assert "Copyright 2026" not in cleaned
    assert "Senior Technical Business Analyst" in cleaned
    assert "Telecom Argentina" in cleaned
    assert "Automatizaciones en Python" in cleaned

def test_extract_job_from_html():
    data = extract_job_from_html(SAMPLE_HTML, url="https://example.com/job/123")
    assert "Senior Technical Business Analyst" in data["titulo"]
    assert "Telecom Argentina" in data["empresa"]
    assert len(data["contenido"]) > 100
    assert "Automatizaciones en Python" in data["contenido"]
    assert data["url"] == "https://example.com/job/123"

@pytest.mark.asyncio
async def test_scrape_job_url_fallback(monkeypatch):
    from core.scraper import scrape_job_url

    # Test error handling on invalid / unreachable URL
    result = await scrape_job_url("http://invalid-non-existent-domain-12345.com")
    assert "url" in result
    assert "contenido" in result
    assert result["url"] == "http://invalid-non-existent-domain-12345.com"


def test_extract_job_empleosit_structure():
    import os
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "empleosit_sample.html")
    assert os.path.exists(fixture_path), "Fixture empleosit_sample.html must exist"

    with open(fixture_path, "r", encoding="utf-8", errors="ignore") as f:
        html_content = f.read()

    job = extract_job_from_html(html_content, url="https://www.empleosit.com.ar/display-job/130603/Analista-Senior-de-Procesos.html")

    # Title must be clean, not containing location suffix or portal domain
    assert job["titulo"] == "Analista Senior de Procesos"

    # Company should be Besysoft S.A., not the portal URL https://www.empleosit.com.ar
    assert job["empresa"] == "Besysoft S.A."

    # Job content must contain the actual responsibilities and description
    assert "Nos encontramos en la búsqueda de un/a Analista Senior de Procesos" in job["contenido"]
    assert "Principales responsabilidades" in job["contenido"]
    assert "Relevar, analizar y documentar procesos" in job["contenido"]

    # Navigation, sidebar and header noise must NOT be in the extracted content
    assert "Inicio" not in job["contenido"]
    assert "¿Quiénes somos?" not in job["contenido"]
    assert "Trabajos Similares" not in job["contenido"]
    assert "\xa0" not in job["contenido"]


def test_extract_job_without_json_ld_strips_navigation_and_sidebar():
    html_without_ld = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Tech Lead Python (Buenos Aires) - Global Tech</title>
        <meta property="og:title" content="Tech Lead Python (Buenos Aires)">
        <meta property="og:site_name" content="Global Tech">
    </head>
    <body>
        <div id="navigation" class="nav-menu">
            <ul>
                <li><a href="/">Inicio</a></li>
                <li><a href="/jobs">Buscar Empleos</a></li>
                <li><a href="/contact">Contacto</a></li>
            </ul>
        </div>
        <div class="sidebar suggested-jobs">
            <h3>Trabajos Similares</h3>
            <p>DevOps Engineer</p>
        </div>
        <div class="main-content">
            <h1 class="job-title">Tech Lead Python</h1>
            <div class="displayFieldBlock job-description">
                <h2>Descripción del puesto</h2>
                <p>Liderar equipo de ingeniería backend en FastAPI y arquitecturas distribuidas.</p>
                <h3>Requisitos</h3>
                <ul>
                    <li>5+ años con Python moderno.</li>
                    <li>Experiencia en Kubernetes y AWS.</li>
                </ul>
            </div>
        </div>
        <div class="footer-links">
            <p>Todos los derechos reservados 2026</p>
        </div>
    </body>
    </html>
    """

    job = extract_job_from_html(html_without_ld, url="https://example.com/job/456")
    assert job["titulo"] == "Tech Lead Python"
    assert job["empresa"] == "Global Tech"
    assert "Liderar equipo de ingeniería backend" in job["contenido"]
    assert "5+ años con Python moderno" in job["contenido"]

    # Verify noise elements are completely omitted
    assert "Inicio" not in job["contenido"]
    assert "Buscar Empleos" not in job["contenido"]
    assert "Trabajos Similares" not in job["contenido"]
    assert "Todos los derechos reservados" not in job["contenido"]



def _bloqueado(codigo):
    def leer(url, timeout):
        raise RuntimeError(f"HTTP Error {codigo}: Forbidden")
    return leer


class _LectorFalso:
    def __init__(self, html):
        self.html_code, self.leidas = html, []

    async def html(self, url, plazo_s):
        self.leidas.append(url)
        return self.html_code


@pytest.mark.parametrize("codigo", [401, 403, 429, 500])
@pytest.mark.asyncio
async def test_sitio_que_bloquea_se_lee_con_el_navegador(monkeypatch, codigo):
    from core import navegador_portales, scraper
    lector = _LectorFalso(SAMPLE_HTML)
    monkeypatch.setattr(scraper, "_fetch_url_sync", _bloqueado(codigo))
    monkeypatch.setattr(navegador_portales, "lector", lambda: lector)
    data = await scraper.scrape_job_url("https://example.com/job/1")
    assert "error" not in data and "Automatizaciones en Python" in data["contenido"]
    assert lector.leidas == ["https://example.com/job/1"]


@pytest.mark.asyncio
async def test_aviso_caducado_en_el_navegador_es_inexistente(monkeypatch):
    from core import navegador_portales, scraper
    monkeypatch.setattr(scraper, "_fetch_url_sync", _bloqueado(403))
    monkeypatch.setattr(navegador_portales, "lector", lambda: _LectorFalso("<p>Este empleo caducó en Indeed</p>"))
    assert "HTTP Error 410" in (await scraper.scrape_job_url("https://example.com/job/2"))["error"]


@pytest.mark.asyncio
async def test_pantalla_de_ingreso_no_es_una_oferta(monkeypatch):
    from core import navegador_portales, scraper
    monkeypatch.setattr(scraper, "_fetch_url_sync", _bloqueado(403))
    monkeypatch.setattr(navegador_portales, "lector", lambda: _LectorFalso("<title>Sign In | Indeed</title><h1>Sign In</h1>"))
    assert "iniciar sesión" in (await scraper.scrape_job_url("https://example.com/job/4"))["error"]


@pytest.mark.asyncio
async def test_pagina_vacia_se_lee_con_el_navegador(monkeypatch):
    from core import navegador_portales, scraper
    lector = _LectorFalso(SAMPLE_HTML)
    monkeypatch.setattr(scraper, "_fetch_url_sync", lambda url, timeout: "<html><body></body></html>")
    monkeypatch.setattr(navegador_portales, "lector", lambda: lector)
    assert "Automatizaciones en Python" in (await scraper.scrape_job_url("https://example.com/job/5"))["contenido"]


@pytest.mark.parametrize("error", ["HTTP Error 404: Not Found", "HTTP Error 410: Gone",
                                   "destino no permitido: 10.0.0.1", "<urlopen error getaddrinfo failed>"])
@pytest.mark.asyncio
async def test_inexistente_bloqueado_o_sin_red_no_abre_el_navegador(monkeypatch, error):
    from core import navegador_portales, scraper

    def falla(url, timeout):
        raise RuntimeError(error)
    lector = _LectorFalso(SAMPLE_HTML)
    monkeypatch.setattr(scraper, "_fetch_url_sync", falla)
    monkeypatch.setattr(navegador_portales, "lector", lambda: lector)
    assert (await scraper.scrape_job_url("https://example.com/job/3"))["error"] == error
    assert lector.leidas == []
