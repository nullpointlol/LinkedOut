import inspect
from pathlib import Path
import docx
import pytest

from core.profile_parser import parse_candidate_profile, load_candidate_profile, get_candidate_companies
from core.auditor import evaluate_ats_compliance, AuditorAgent
from core.docx_generator import (
    get_candidate_name,
    generate_cv_filename,
    render_contact_paragraph,
    compile_markdown_to_docx,
)
from core.pdf_generator import (
    generate_cv_pdf_filename,
    compile_markdown_to_pdf,
)


SAMPLE_PER_TEXT = """María Fernández

Córdoba, Argentina | +54 9 351 123-4567 | https://wa.me/5493511234567 | maria.fernandez@example.com | https://www.linkedin.com/in/persona-ficticia-04/

EDUCACIÓN
Médica Cirujana - Universidad Nacional de Córdoba, 2012 - 2018
Especialización en Gestión Hospitalaria - Universidad Católica, 2020

IDIOMAS
• Inglés: Avanzado
• Español: Nativo
"""

SAMPLE_EXP_TEXT = """# Experiencia profesional

## Hospital Privado
### Coordinadora de Triage y Guardia | 2022 - Presente
• Lideré el equipo de guardia médica reduciendo los tiempos de espera en 35%.
• Digitalicé el protocolo de triage mediante software interno.

## Clínica del Sol
### Médica de Planta | 2018 - 2022
• Atendí más de 500 pacientes mensuales en servicio ambulatorio.
• Coordiné guardias y auditorías de historias clínicas.
"""


def test_parse_candidate_profile_arbitrary_candidate():
    profile = parse_candidate_profile(SAMPLE_EXP_TEXT, SAMPLE_PER_TEXT)

    assert profile["nombre"] == "María Fernández"
    assert "+54 9 351 123-4567" in profile["contacto"]
    assert "maria.fernandez@example.com" in profile["contacto"]
    assert profile["empresas"] == ["Hospital Privado", "Clínica del Sol"]
    assert len(profile["educacion"]) >= 2
    assert any("Médica Cirujana" in e for e in profile["educacion"])
    assert len(profile["idiomas"]) >= 2
    assert any("Inglés" in i for i in profile["idiomas"])


def test_parse_candidate_profile_markdown_h1_name():
    per_h1 = """# Dr. Juan Pérez
Mendoza, Argentina | juan@example.com

EDUCACIÓN
Bioquímico - UNCUYO
"""
    exp = """## Laboratorio Central
Bioquímico analista
"""
    profile = parse_candidate_profile(exp, per_h1)
    assert profile["nombre"] == "Dr. Juan Pérez"
    assert profile["empresas"] == ["Laboratorio Central"]


def test_evaluate_ats_compliance_with_dynamic_expected_companies():
    cv_text = """# MARÍA FERNÁNDEZ
Córdoba, Argentina | maria.fernandez@example.com

## PERFIL
Médica con experiencia en gestión y guardia.

## EXPERIENCIA
### Coordinadora de Guardia | Hospital Privado | 2022 - Presente
• Reduje tiempos de espera en 35% en más de 200 guardias.

## HABILIDADES
• Triage, Auditoría, Gestión hospitalaria.

## EDUCACIÓN
• Médica Cirujana
"""
    keywords = ["Triage", "Auditoría"]

    # Caso 1: Se espera "Hospital Privado" -> presente -> has_all_experiences True
    res_ok = evaluate_ats_compliance(cv_text, keywords, expected_companies=["Hospital Privado"])
    assert res_ok["has_all_experiences"] is True
    assert res_ok["missing_experiences"] == []
    assert res_ok["found_experiences"] == ["Hospital Privado"]

    # Caso 2: Se espera "Hospital Privado" y "Clínica del Sol" -> falta "Clínica del Sol"
    res_fail = evaluate_ats_compliance(cv_text, keywords, expected_companies=["Hospital Privado", "Clínica del Sol"])
    assert res_fail["has_all_experiences"] is False
    assert "Clínica del Sol" in res_fail["missing_experiences"]
    assert "Hospital Privado" in res_fail["found_experiences"]

    # No debe requerir ni evaluar Freelance, VELORA SISTEMAS ni KORVA cuando se pasan empresas dinámicas
    assert "VELORA SISTEMAS" not in res_ok.get("missing_experiences", [])
    assert "KORVA" not in res_ok.get("missing_experiences", [])
    assert "FREELANCE" not in res_ok.get("missing_experiences", [])


def test_evaluate_ats_compliance_empty_expected_companies_not_penalized():
    cv_text = """# Candidato
## PERFIL
Profesional.
## EXPERIENCIA
• Logro 1 en 50 proyectos con 30% mejora.
## HABILIDADES
• Python
## EDUCACIÓN
• Universidad
"""
    res = evaluate_ats_compliance(cv_text, ["Python"], expected_companies=[])
    assert res["has_all_experiences"] is True
    assert res["missing_experiences"] == []


def test_auditor_agent_audit_uses_expected_companies():
    agent = AuditorAgent()
    cv_text = """# MARÍA FERNÁNDEZ
Córdoba, Argentina | maria.fernandez@example.com

## PERFIL
Médica de urgencias.

## EXPERIENCIA
### Hospital Privado | 2022
• 10 proyectos con 40% de mejora y 15 semanas.

## HABILIDADES
• Triage, Urgencias

## EDUCACIÓN
• Medicina
"""
    report = agent.audit(
        cv_markdown=cv_text,
        pitch="Propuesta de valor clara.",
        keywords=["Triage", "Urgencias"],
        expected_companies=["Hospital Privado", "Sanatorio Allende"],
    )

    ats = report["ats"]
    assert ats["has_all_experiences"] is False
    assert "Sanatorio Allende" in ats["missing_experiences"]

    # Las deducciones no deben referenciar empresas fijas canónicas (VELORA SISTEMAS / KORVA)
    deducciones_str = " ".join(report["deducciones"])
    assert "VELORA SISTEMAS" not in deducciones_str
    assert "KORVA" not in deducciones_str
    assert "Sanatorio Allende" in deducciones_str
    assert "empresas esperadas de la trayectoria" in deducciones_str


def test_docx_get_candidate_name_and_filename_agnostic(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docx_generator.EXPORTS_DIR", tmp_path)

    # 1. Extracción desde markdown
    md = """# CLAUDIA RUIZ
claudia@example.com

## PERFIL
Arquitecta de Soluciones Cloud.
"""
    assert get_candidate_name(md) == "CLAUDIA RUIZ"
    filename = generate_cv_filename("Cloud Architect", candidate_name="Claudia Ruiz")
    assert filename == "CV_CLOUD-ARCHITECT_CLAUDIA-RUIZ.docx"

    # 2. Fallback cuando no hay archivo de personales ni markdown
    fake_dir = tmp_path / "empty_dir"
    fake_dir.mkdir()
    monkeypatch.setattr("core.docx_generator.DATA_DIR", fake_dir)
    fallback_name = get_candidate_name(None)
    assert fallback_name == "Candidato"
    assert "Alex" not in fallback_name


def test_docx_render_contact_paragraph_does_not_inject_hardcoded_defaults():
    doc = docx.Document()
    # Contacto minimalista sin URL de linkedin ni teléfono
    contact_line = "Rosario, Argentina | laura@empresa.test"
    p = render_contact_paragraph(doc, contact_line)
    
    assert "laura@empresa.test" in p.text
    assert "5555-0100" not in p.text
    assert "alex-rivera" not in p.text
    # Ningún hyperlink debe apuntar al perfil de Alex
    for rel in doc.part.rels.values():
        if "hyperlink" in rel.reltype:
            assert "alex-rivera" not in rel.target_ref


def test_pdf_generate_filename_agnostic():
    # Con candidate_name explícito (normalizado sin tildes para seguridad HTTP/ASCII)
    fn1 = generate_cv_pdf_filename("Data Scientist", candidate_name="Sofía Rossi")
    assert fn1 == "CV - Sofia Rossi - DATA-SCIENTIST.pdf"

    # Con markdown provisto
    md = "# CARLOS PAEZ\ncarlos@example.com"
    fn2 = generate_cv_pdf_filename("Tech Lead", markdown_text=md)
    assert fn2 == "CV - CARLOS PAEZ - TECH-LEAD.pdf"


def test_no_hardcoded_canonical_companies_or_names_in_source_code():
    import core.auditor as auditor_mod
    import core.docx_generator as docx_mod
    import core.pdf_generator as pdf_mod

    auditor_src = inspect.getsource(auditor_mod)
    docx_src = inspect.getsource(docx_mod)
    pdf_src = inspect.getsource(pdf_mod)

    # Auditor no debe tener CANONICAL_COMPANIES
    assert "CANONICAL_COMPANIES" not in auditor_src
    assert '["FREELANCE", "VELORA SISTEMAS", "KORVA"]' not in auditor_src

    # Generadores no deben tener hardcoded el nombre del candidato
    assert '"Alex Rivera"' not in docx_src
    assert '"Alex Rivera"' not in pdf_src
    assert 'alex-rivera-ejemplo' not in docx_src


def test_load_voz_email_reads_fixed_phrases_and_template(tmp_path):
    from core.profile_parser import load_voz_email
    assert load_voz_email(tmp_path) == {"disponibilidad": "", "ubicacion": "", "disponibilidad_en": "",
                                        "ubicacion_en": "", "voz": ""}
    (tmp_path / "voz_email.md").write_text(
        "## DATOS FIJOS\nDisponibilidad: Lunes a viernes.\nUbicación: Soy de Lanús.\n\n"
        "## DATOS FIJOS (EN)\nDisponibilidad: Monday to Friday.\nUbicación: Based in Lanús.\n\n"
        "## VOZ\nHola, buenas.\n\nSaludos,\n", encoding="utf-8")
    assert load_voz_email(tmp_path) == {"disponibilidad": "Lunes a viernes.", "ubicacion": "Soy de Lanús.",
                                        "disponibilidad_en": "Monday to Friday.", "ubicacion_en": "Based in Lanús.",
                                        "voz": "Hola, buenas.\n\nSaludos,"}
