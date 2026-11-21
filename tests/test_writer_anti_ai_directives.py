import pytest
from core.prompts_evaluacion import WRITER_PROMPT
from core.writer import synthesize_multichannel_pitches
from core.auditor import scan_anti_ai_writing

def test_writer_prompt_contains_a4_single_page_instruction_and_spanish_anti_ai_directives():
    prompt_lower = WRITER_PROMPT.lower()
    # Directiva de 1 página A4 máximo y síntesis inteligente
    assert "máximo" in prompt_lower and ("1 página a4" in prompt_lower or "una sola página a4" in prompt_lower or "1 página" in prompt_lower)
    assert "sintetizar" in prompt_lower or "condensar" in prompt_lower
    # Directivas Anti-AI en español
    assert "con foco en" in prompt_lower or "clichés corporativos" in prompt_lower
    assert "sólida trayectoria" in prompt_lower or "lenguaje inflado" in prompt_lower or "peacock" in prompt_lower
    assert "simetría" in prompt_lower or "variación sintáctica" in prompt_lower

def test_synthesize_multichannel_pitches_free_of_ai_cliches():
    pitches = synthesize_multichannel_pitches(
        pitch="Desarrollé sistemas transaccionales y lideré especificaciones de negocio.",
        puesto="Analista Funcional",
        empresa="Empresa Demo",
        profile={"nombre": "Juan Pérez", "contacto": "Buenos Aires | 123456"}
    )
    for channel, text in pitches.items():
        assert "sólida trayectoria" not in text.lower()
        scan = scan_anti_ai_writing(text)
        assert scan["clean"], f"El canal {channel} contiene clichés detectados: {scan['detected_words']}"
