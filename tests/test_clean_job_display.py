import pytest
from pathlib import Path
from core.scraper import clean_html_text, filter_job_boilerplate

def test_filter_job_boilerplate_removes_cookies_and_portal_clutter():
    """filter_job_boilerplate debe purgar avisos de cookies, botones de compartir y disclaimers legales."""
    raw_scraped = """
    Aceptar todas las cookies para continuar navegando.
    Política de privacidad y configuración de cookies.
    Buscamos un Ingeniero de Software Sénior con 5 años de experiencia en Python y AWS.
    Responsabilidades:
    - Diseñar APIs REST escalables
    - Optimizar bases de datos PostgreSQL
    Compartir este empleo en LinkedIn o Twitter.
    Todos los derechos reservados. Copyright 2026 Portal de Empleos.
    """
    cleaned = filter_job_boilerplate(raw_scraped)

    assert "Aceptar todas las cookies" not in cleaned
    assert "Política de privacidad" not in cleaned
    assert "Compartir este empleo" not in cleaned
    assert "Todos los derechos reservados" not in cleaned

    assert "Ingeniero de Software Sénior" in cleaned
    assert "Diseñar APIs REST escalables" in cleaned
    assert "Optimizar bases de datos PostgreSQL" in cleaned

def test_clean_html_text_with_filter():
    """clean_html_text debe invocar filter_job_boilerplate asegurando que no quede ruido residual."""
    html_sample = """
    <html>
      <body>
        <div class="cookie-banner">Este sitio utiliza cookies para mejorar la experiencia. Aceptar cookies</div>
        <h1>Tech Lead</h1>
        <p>Liderazgo de equipo y desarrollo en Python.</p>
        <footer>Copyright 2026. Todos los derechos reservados.</footer>
      </body>
    </html>
    """
    text = clean_html_text(html_sample)
    assert "Tech Lead" in text
    assert "Liderazgo de equipo" in text
    assert "Todos los derechos reservados" not in text

def test_ui_contains_clean_vacancy_mode_toggle():
    """static/index.html debe contener el toggle de visualización [Limpio / Original] para la vacante."""
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "btnModoVacanteLimpio" in html, "Falta botón Limpio en la cabecera de la vacante"
    assert "btnModoVacanteCrudo" in html, "Falta botón Original/Crudo en la cabecera de la vacante"

def test_app_js_implements_formatear_descripcion_vacante():
    """static/app.js debe implementar formatearDescripcionVacante y cambiarModoTextoVacante."""
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert "formatearDescripcionVacante" in js
    assert "cambiarModoTextoVacante" in js


def test_api_returns_contenido_limpio_and_filters_portal_clutter(monkeypatch):
    """GET /api/ofertas/{id} debe exponer contenido_limpio libre de ruido de navegación."""
    from fastapi.testclient import TestClient
    from server import app
    from core.database import create_postulacion

    client = TestClient(app)

    raw_text = """
    Pasar al contenido principal
    Functional Analyst Jr./Ssr. (R2)
    Acciona IT
    Hace 19 horas
    120 solicitudes
    Descubre a quién ha contratado Acciona IT para este puesto
    Guardar
    Analista Funcional Jr./Semi Senior
    Ubicación: Belgrano, Buenos Aires, Argentina
    Modalidad: Presencial (4 días por semana)
    Principales desafíos:
    - Relevamiento y especificación funcional
    - Traducir necesidades del negocio en soluciones tecnológicas
    Buscamos:
    - Al menos 2 años de experiencia como Analista Funcional
    - Experiencia previa en Sistemas / IT
    Aceptar y unirse a LinkedIn
    Al hacer clic en «Continuar» para unirse
    """
    post_id = create_postulacion(
        empresa="Acciona IT",
        titulo="Analista Funcional",
        contenido=raw_text,
        url="https://linkedin.com/jobs/view/999"
    )

    res = client.get(f"/api/ofertas/{post_id}")
    assert res.status_code == 200
    data = res.json()

    assert "contenido_limpio" in data, "La respuesta debe incluir la clave contenido_limpio"
    clean = data["contenido_limpio"]
    assert "Pasar al contenido principal" not in clean
    assert "Descubre a quién ha contratado" not in clean
    assert "Guardar" not in clean
    assert "120 solicitudes" not in clean
    assert "Aceptar y unirse a LinkedIn" not in clean
    assert "Analista Funcional Jr./Semi Senior" in clean
    assert "Relevamiento y especificación funcional" in clean


def test_filter_job_boilerplate_truncates_recommendation_blocks():
    """filter_job_boilerplate debe truncar tajantemente al encontrar cabeceras de sugerencias/alertas."""
    text_with_recommendations = """
    Analista Funcional Jr./Semi Senior
    Ubicación: Belgrano, Buenos Aires, Argentina
    Modalidad: Presencial
    Requisitos:
    - SQL
    - Metodologías ágiles
    Las recomendaciones duplican tus probabilidades de conseguir una entrevista con Acciona IT
    Mira a quién conoces
    Recibe una notificación cuando se publique un nuevo empleo
    Crear alerta
    Otros perfiles vistos
    Analista de Transformación y Tecnología - Gador
    Analista de Soporte de Aplicaciones
    """
    cleaned = filter_job_boilerplate(text_with_recommendations)

    assert "Analista Funcional Jr./Semi Senior" in cleaned
    assert "Requisitos:" in cleaned
    assert "SQL" in cleaned
    assert "Las recomendaciones duplican" not in cleaned
    assert "Mira a quién conoces" not in cleaned
    assert "Recibe una notificación" not in cleaned
    assert "Crear alerta" not in cleaned
    assert "Otros perfiles vistos" not in cleaned
    assert "Analista de Transformación y Tecnología" not in cleaned
    assert "Analista de Soporte de Aplicaciones" not in cleaned


