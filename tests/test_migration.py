from pathlib import Path
import docx

def test_candidato_files_exist():
    """El perfil de ejemplo versionado existe; el real vive en <DATA_DIR>/candidato y no se versiona."""
    base_dir = Path(__file__).resolve().parent.parent
    ejemplo = base_dir / "config" / "ejemplo" / "candidato"
    exp_path = ejemplo / "experiencia.txt"
    per_path = ejemplo / "personales.txt"

    assert exp_path.exists(), "config/ejemplo/candidato/experiencia.txt no existe"
    assert per_path.exists(), "config/ejemplo/candidato/personales.txt no existe"
    assert "Analista Técnico Funcional" in exp_path.read_text(encoding="utf-8")
    assert per_path.read_text(encoding="utf-8").strip()

def test_template_executive_exists():
    base_dir = Path(__file__).resolve().parent.parent
    template_path = base_dir / "templates" / "cv_template_executive.docx"
    
    assert template_path.exists(), "templates/cv_template_executive.docx no existe"
    doc = docx.Document(str(template_path))
    assert len(doc.paragraphs) > 0

def test_prompts_evaluacion_heuristics():
    from core.prompts_evaluacion import (
        SIGNS_OF_AI_WRITING_WORDS,
        ATS_CRITERIA,
        RECRUITER_CRITIC_DIRECTIVES,
        STRATEGIST_PROMPT,
        WRITER_PROMPT,
    )
    assert len(SIGNS_OF_AI_WRITING_WORDS) >= 15
    assert "delve" in SIGNS_OF_AI_WRITING_WORDS
    assert "tapestry" in SIGNS_OF_AI_WRITING_WORDS
    assert "pivotal" in SIGNS_OF_AI_WRITING_WORDS
    assert "fostering" in SIGNS_OF_AI_WRITING_WORDS
    
    assert "keywords" in ATS_CRITERIA
    assert "sections" in ATS_CRITERIA
    assert "10 a 15 segundos" in RECRUITER_CRITIC_DIRECTIVES
    assert "XYZ" in WRITER_PROMPT
