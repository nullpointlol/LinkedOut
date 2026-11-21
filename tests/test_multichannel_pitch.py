import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from core.database import init_db, create_postulacion, get_postulacion, update_postulacion
from core.writer import WriterAgent, synthesize_multichannel_pitches
from server import app


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
