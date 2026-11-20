import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from core.database import init_db, create_postulacion, get_postulacion, update_postulacion
from core.writer import WriterAgent, synthesize_multichannel_pitches
from server import app


def test_writer_fallback_generates_all_three_formats():
    """WriterAgent._deterministic_fallback must produce cv_markdown, pitch, and pitches_multicanal with 3 formats."""
    writer = WriterAgent()
    profile = writer.load_candidate_profile()
    analysis = {
        "puesto": "Analista Técnico Funcional",
        "empresa": "Tech Global Inc",
        "dolores_empresa": ["Falta de observabilidad en pipelines CI/CD"],
        "requisitos": [
            {"nombre": "Python", "estado": "MATCH_DIRECTO", "approved": True},
            {"nombre": "Jenkins", "estado": "MATCH_DIRECTO", "approved": True},
        ],
        "match_score": 85.0,
    }

    result = writer._deterministic_fallback(
        puesto=analysis["puesto"],
        empresa=analysis["empresa"],
        analysis=analysis,
        profile=profile,
    )

    assert "cv_markdown" in result
    assert "pitch" in result
    assert "pitches_multicanal" in result

    multichannel = result["pitches_multicanal"]
    assert isinstance(multichannel, dict)
    assert "inmail" in multichannel
    assert "email" in multichannel
    assert "web_ats" in multichannel

    assert len(multichannel["inmail"].strip()) > 0
    assert len(multichannel["email"].strip()) > 0
    assert len(multichannel["web_ats"].strip()) > 0


def test_inmail_length_constraint_under_300_chars():
    """LinkedIn InMail pitch must strictly satisfy length <= 300 characters."""
    writer = WriterAgent()
    profile = writer.load_candidate_profile()

    # Test with standard role and company
    analysis_standard = {
        "puesto": "Analista Técnico Funcional",
        "empresa": "Tech Global Inc",
        "requisitos": [],
    }
    result_std = writer._deterministic_fallback("Analista Técnico Funcional", "Tech Global Inc", analysis_standard, profile)
    inmail_std = result_std["pitches_multicanal"]["inmail"]
    assert len(inmail_std) <= 300, f"InMail length {len(inmail_std)} exceeds 300 characters: '{inmail_std}'"

    # Test with very long title and company name
    long_puesto = "Senior Lead Cloud Solutions Architect & DevOps Specialist de Infraestructura Crítica"
    long_empresa = "Corporación Multinacional de Telecomunicaciones e Innovación Digital S.A."
    analysis_long = {"puesto": long_puesto, "empresa": long_empresa, "requisitos": []}
    result_long = writer._deterministic_fallback(long_puesto, long_empresa, analysis_long, profile)
    inmail_long = result_long["pitches_multicanal"]["inmail"]
    assert len(inmail_long) <= 300, f"Long InMail length {len(inmail_long)} exceeds 300 characters: '{inmail_long}'"


def test_email_pitch_structure():
    """Email pitch must have an explicit Subject line and contact signature."""
    writer = WriterAgent()
    profile = writer.load_candidate_profile()
    analysis = {"puesto": "Tech Lead", "empresa": "Acme Corp", "requisitos": []}
    result = writer._deterministic_fallback("Tech Lead", "Acme Corp", analysis, profile)
    email = result["pitches_multicanal"]["email"]

    # Subject line check
    assert "Asunto:" in email
    assert "Postulación" in email
    assert "Sam Ortega" in email

    # Signature and contact check
    assert "Sam Ortega" in email
    assert ("+54" in email or "sam.ortega@example.com" in email or "LinkedIn" in email)


def test_web_ats_pitch_structure():
    """Web / ATS pitch must be first-person narrative without email headers or greeting."""
    writer = WriterAgent()
    profile = writer.load_candidate_profile()
    analysis = {"puesto": "Analista Funcional", "empresa": "Banco Galicia", "requisitos": []}
    result = writer._deterministic_fallback("Analista Funcional", "Banco Galicia", analysis, profile)
    web_ats = result["pitches_multicanal"]["web_ats"]

    # Must not contain email headers
    assert "Asunto:" not in web_ats
    assert not web_ats.startswith("Estimado")
    assert not web_ats.startswith("Hola ")

    # Must be first-person narrative
    assert any(verb in web_ats.lower() for verb in ["postulo", "cuento", "aportando", "mi trayectoria", "me especialicé"])


def test_synthesize_multichannel_pitches_helper():
    """synthesize_multichannel_pitches must derive all 3 formats from a generic pitch."""
    raw_pitch = (
        "Hola equipo de selección de Globant,\n\n"
        "Me pongo en contacto respecto a la vacante de Tech Lead. Cuento con 12 años en automatización y observabilidad.\n\n"
        "Lideré proyectos clave reduciendo incidentes un 30%.\n\n"
        "Quedo a su disposición para coordinar una reunión."
    )

    synthesized = synthesize_multichannel_pitches(
        pitch=raw_pitch,
        puesto="Tech Lead",
        empresa="Globant"
    )

    assert isinstance(synthesized, dict)
    assert "inmail" in synthesized
    assert "email" in synthesized
    assert "web_ats" in synthesized

    assert len(synthesized["inmail"]) <= 300
    assert "Asunto:" in synthesized["email"]
    assert "Asunto:" not in synthesized["web_ats"]


def test_database_persistence_and_retrieval(tmp_path):
    """Database schema must store and retrieve pitches_multicanal as a parsed JSON dictionary."""
    db_file = tmp_path / "test_pitch.db"
    init_db(db_file)

    post_id = create_postulacion(
        titulo="Backend Engineer",
        empresa="Mercado Libre",
        contenido="Requisitos: Python, APIs",
        db_path=db_file
    )

    # Initially empty pitches_multicanal
    initial = get_postulacion(post_id, db_path=db_file)
    assert initial is not None
    assert isinstance(initial.get("pitches_multicanal"), dict)

    # Update with multichannel pitches dict
    payload = {
        "inmail": "Hola! Vi la vacante de Backend Engineer en Mercado Libre...",
        "email": "Asunto: Postulación Backend Engineer - Sam Ortega\n\nEstimado equipo...",
        "web_ats": "Postulo a la posición de Backend Engineer aportando experiencia en Python...",
    }
    update_postulacion(post_id, db_path=db_file, pitches_multicanal=payload)

    retrieved = get_postulacion(post_id, db_path=db_file)
    assert retrieved is not None
    assert retrieved["pitches_multicanal"] == payload
    assert retrieved["pitches_multicanal"]["inmail"] == payload["inmail"]


def test_api_no_sintetiza_pitches_multicanal_en_el_detalle(tmp_path, monkeypatch):
    """GET /api/ofertas/{id} ya no arma pitches multicanal (no se generan más)."""
    db_file = tmp_path / "test_api_pitch.db"
    init_db(db_file)
    monkeypatch.setattr("core.database.DB_PATH", db_file)

    post_id = create_postulacion(
        titulo="SRE Lead",
        empresa="Telecom",
        contenido="SRE con Python y Linux",
        db_path=db_file
    )
    # Put a traditional pitch without multichannel pitches
    update_postulacion(
        post_id,
        db_path=db_file,
        pitch="Hola Telecom, me postulo a la vacante de SRE Lead con experiencia en monitoreo y Linux.",
        status="READY"
    )

    client = TestClient(app)
    response = client.get(f"/api/ofertas/{post_id}")
    assert response.status_code == 200
    data = response.json()

    assert not (data.get("pitches_multicanal") or {}).get("inmail")
    assert not (data.get("pitches_multicanal") or {}).get("web_ats")


def test_frontend_sin_pitch_ni_canales():
    """el pitch y los pitches multicanal (InMail, web_ats) ya no se generan: la pantalla no los muestra."""
    html_content = Path("static/index.html").read_text(encoding="utf-8")
    js_content = Path("static/app.js").read_text(encoding="utf-8")
    for elemento in ("rightTab-pitch", "subtab-pitch", "pitchTab-inmail", "pitchTab-web_ats", "pitchCharCounter",
                     "btnCopyPitch"):
        assert elemento not in html_content
    for funcion in ("setPitchChannel", "updatePitchView", "copiarPitch", "currentPitchChannel"):
        assert funcion not in js_content
