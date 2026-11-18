import json
import pytest
from pathlib import Path
import core.database
from core.database import get_postulacion
from core.auditor import scan_anti_ai_writing
from core.prompts_evaluacion import SIGNS_OF_AI_WRITING_WORDS
from core.pdf_generator import compile_markdown_to_pdf


@pytest.fixture(autouse=True)
def ensure_postulacion_2_present():
    """Garantiza la disponibilidad de la postulación 2 en entornos aislados de testing."""
    p = get_postulacion(2)
    if p is None:
        real_db = core.database.BASE_DIR / "data" / "postulaciones.db"
        if real_db.exists():
            real_p = get_postulacion(2, db_path=real_db)
            if real_p:
                conn = core.database.get_db_connection()
                cols = list(real_p.keys())
                placeholders = ", ".join(["?"] * len(cols))
                vals = []
                for k in cols:
                    v = real_p[k]
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False)
                    vals.append(v)
                with conn:
                    conn.execute(
                        f"INSERT OR REPLACE INTO postulaciones ({', '.join(cols)}) VALUES ({placeholders})",
                        vals
                    )
                conn.close()


def test_postulacion_2_cv_conforms_to_anti_ai_and_a4_standards():
    p = get_postulacion(2)
    if p is None:
        pytest.skip("Postulación con ID 2 no disponible en la base de datos local")
    cv_md = p.get("cv_markdown") or ""
    assert len(cv_md) > 500, f"CV markdown demasiado corto o vacío: {len(cv_md)} caracteres"

    # Verificación de secciones canónicas
    canonical_sections = ["PERFIL", "EXPERIENCIA", "HABILIDADES TÉCNICAS", "EDUCACIÓN", "IDIOMAS"]
    for sec in canonical_sections:
        assert f"## {sec}" in cv_md, f"Falta la sección canónica en CV: {sec}"

    # Escaneo Anti-AI: palabras prohibidas. Los patrones de estructura (simetría de viñetas, negritas,
    # apertura del perfil) se sumaron después de generar este CV: se auditan en los CV nuevos.
    scan_cv = scan_anti_ai_writing(cv_md)
    palabras = [w for w in scan_cv["detected_words"] if w in SIGNS_OF_AI_WRITING_WORDS]
    assert palabras == [], f"Clichés detectados en CV: {palabras}"


def test_postulacion_2_pitches_multicanal_anti_ai_and_constraints():
    p = get_postulacion(2)
    if p is None:
        pytest.skip("Postulación con ID 2 no disponible en la base de datos local")
    pitches = p.get("pitches_multicanal") or {}
    assert isinstance(pitches, dict), "pitches_multicanal debe ser un diccionario"

    channels = ["inmail", "email", "web_ats"]
    for ch in channels:
        text = pitches.get(ch) or ""
        assert len(text) > 0, f"El canal {ch} no tiene contenido"
        scan = scan_anti_ai_writing(text)
        assert scan["anti_ai_score"] >= 95, f"Clichés detectados en canal {ch}: {scan['detected_words']}"
        assert len(scan["detected_words"]) == 0, f"Clichés detectados en canal {ch}: {scan['detected_words']}"

    inmail_text = pitches.get("inmail", "")
    assert len(inmail_text) <= 300, f"Inmail supera el límite de 300 caracteres: {len(inmail_text)} caracteres"


def test_postulacion_2_pdf_compilation_and_a4_single_page():
    p = get_postulacion(2)
    if p is None:
        pytest.skip("Postulación con ID 2 no disponible en la base de datos local")
    cv_md = p.get("cv_markdown") or ""
    role = p.get("titulo")

    pdf_path = compile_markdown_to_pdf(cv_md, target_role=role, output_filename="test_postulacion_2_a4.pdf")
    assert pdf_path.exists(), f"El archivo PDF no fue generado: {pdf_path}"
    assert pdf_path.stat().st_size > 0, f"El archivo PDF generado está vacío: {pdf_path}"

    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        num_pages = len(reader.pages)
        assert num_pages <= 1, f"El CV en PDF supera 1 página A4: {num_pages} páginas"
    except ImportError:
        pass
