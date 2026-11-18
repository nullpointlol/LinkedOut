"""Subagente 2: Redactor de Alto Impacto (Google XYZ format, Markdown limpio ATS, Pitch Multicanal: InMail, Email, Web/ATS)."""
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
import contextlib

from core.bridge import AntigravityBridge, radar_agy_cwd
from core.prompts_evaluacion import (
    REGLA_DATOS_DE_TERCEROS, WRITER_PROMPT, WRITER_PROMPT_COMPLETO, delimitar_datos_de_terceros, instrucciones_titulo_cv,
)
from core.profile_parser import load_candidate_profile, parse_candidate_profile, parse_roles, RESERVED_SECTIONS
from core.paths import DATA_DIR
from core import candidato_datos
import logging

from core.cv_guardas import (
    COMPLETO, UNA_PAGINA, bloque_prompt, cargar_conocimientos_ausentes, corregir_encabezados, corregir_perfil,
    encabezado_canonico, es_requisito_logistico, normalizar_formato, quitar_requisitos_de_habilidades,
    roles_por_empresa,
)
from core.idioma import EN, ES, TITULOS, idioma_de_cv, seccion_canonica
from core.titulo_cv import decidir_titulo, aplicar_titulo, asegurar_mencion_ats

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
EXP_PATH = DATA_DIR / "candidato" / "experiencia.txt"
PER_PATH = DATA_DIR / "candidato" / "personales.txt"


def synthesize_multichannel_pitches(
    pitch: str,
    puesto: str = "",
    empresa: str = "",
    dolores: Optional[List[str]] = None,
    requisitos: Optional[Any] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Genera y normaliza los 3 formatos de pitch multicanal (InMail, Email Formal, Web/ATS)
    garantizando constraints estrictos (ej: InMail <= 300 caracteres)."""
    # Si profile no vino como kwarg pero el 5to argumento positional fue un dict (profile)
    if profile is None and isinstance(requisitos, dict):
        profile = requisitos
        requisitos = None

    puesto_val = puesto.strip() if puesto else candidato_datos.puesto()
    empresa_val = empresa.strip() if empresa else "Empresa Líder"
    pitch_val = (pitch or "").strip()

    cand_name = "Candidato"
    contact_line = ""

    if profile and isinstance(profile, dict):
        if profile.get("nombre"):
            cand_name = str(profile["nombre"]).strip()
            contact_line = str(profile.get("contacto", "")).strip()
        elif profile.get("personales") or profile.get("experiencia"):
            parsed = parse_candidate_profile(
                str(profile.get("experiencia", "")),
                str(profile.get("personales", "")),
            )
            cand_name = parsed.get("nombre") or "Candidato"
            contact_line = parsed.get("contacto") or ""
    else:
        try:
            parsed = load_candidate_profile()
            cand_name = parsed.get("nombre") or "Candidato"
            contact_line = parsed.get("contacto") or ""
        except Exception:
            cand_name = "Candidato"
            contact_line = ""

    if not cand_name:
        cand_name = "Candidato"

    # 1. LinkedIn InMail (< 300 caracteres)
    inmail = (
        f"Hola! Vi la búsqueda de {puesto_val} en {empresa_val}. "
        "Aporto experiencia técnica y funcional resolviendo desafíos operativos afines a su negocio. "
        f"Me encantaría coordinar una breve charla de 10 min. Saludos! {cand_name}"
    )
    if len(inmail) > 300:
        short_p = puesto_val[:35].rsplit(" ", 1)[0] if len(puesto_val) > 35 else puesto_val
        short_e = empresa_val[:25].rsplit(" ", 1)[0] if len(empresa_val) > 25 else empresa_val
        inmail = (
            f"Hola! Vi su búsqueda de {short_p} en {short_e}. "
            "Aporto experiencia técnica resolviendo desafíos afines a su negocio. "
            f"Me gustaría tener una breve charla. Saludos! {cand_name}"
        )
        if len(inmail) > 300:
            suffix = f"... Saludos! {cand_name}"
            max_base = 300 - len(suffix)
            inmail = inmail[:max_base].rsplit(" ", 1)[0] + suffix
            if len(inmail) > 300:
                inmail = inmail[:300]

    # 2. Email Formal (con Asunto: y firma ejecutiva completa en formato minimalista y cordial)
    subject = f"Asunto: Postulación {puesto_val} - {cand_name}"
    if contact_line:
        signature = f"\n\nSaludos cordiales,\n{cand_name}\n{contact_line}"
    else:
        signature = f"\n\nSaludos cordiales,\n{cand_name}"

    if pitch_val:
        if pitch_val.startswith("Asunto:"):
            email = pitch_val
            if cand_name not in email:
                email += signature
        else:
            email_body = pitch_val
            if cand_name not in email_body:
                email = f"{subject}\n\n{email_body}{signature}"
            else:
                email = f"{subject}\n\n{email_body}"
    else:
        email = f"""{subject}

Hola equipo de {empresa_val}: les escribo con interés en la posición de {puesto_val}. Aporto experiencia técnica y funcional resolviendo desafíos operativos afines a su negocio y requerimientos del rol.

Quedo a disposición para conversar sobre cómo puedo sumar valor a su equipo.{signature}"""

    # 3. Formulario Web / ATS (narrativa directa en 1ª persona, sin encabezados de correo ni saludos)
    clean_paragraphs = []
    if pitch_val:
        for p in pitch_val.split("\n\n"):
            p_strip = p.strip()
            if not p_strip:
                continue
            if p_strip.startswith(("Asunto:", "Hola ", "Estimad", "Atentamente", "Saludos", "+54", "LinkedIn:")):
                continue
            if len(p_strip) > 30:
                clean_paragraphs.append(p_strip)

    if clean_paragraphs:
        intro = f"Postulo a la posición de {puesto_val} en {empresa_val}."
        if not clean_paragraphs[0].lower().startswith("postulo"):
            web_ats = intro + "\n\n" + "\n\n".join(clean_paragraphs)
        else:
            web_ats = "\n\n".join(clean_paragraphs)
    else:
        web_ats = (
            f"Postulo a la posición de {puesto_val} en {empresa_val} aportando experiencia técnica y funcional "
            "resolviendo desafíos operativos afines a su negocio. "
            "En mi trayectoria profesional me especialicé en conectar requerimientos del negocio "
            "con implementaciones técnicas eficientes, reduciendo incidentes y optimizando procesos clave. "
            "Cuento con disponibilidad y total alineación con las responsabilidades y tecnologías requeridas."
        )

    return {
        "inmail": inmail.strip(),
        "email": email.strip(),
        "web_ats": web_ats.strip(),
    }


def sanitize_cv_formatting(cv_md: str) -> str:
    """Sanea el Markdown del CV garantizando jerarquía tipográfica limpia de estándar ejecutivo:
    1. En ## PERFIL: erradica asteriscos y negritas dispersas, manteniendo prosa continua fluida.
    2. En ## EXPERIENCIA: asegura que la ÚNICA negrita en cada viñeta sea la etiqueta temática inicial (• **Etiqueta:**),
       removiendo negritas dispersas internas del cuerpo narrativo (ej. **Jira** -> Jira).
    3. En ## HABILIDADES TÉCNICAS: asegura que sólo el encabezado temático de cada línea sea negrita.
    """
    if not cv_md:
        return ""

    lines = cv_md.split("\n")
    cleaned_lines = []
    current_section = None

    for line in lines:
        stripped = line.strip()

        # Detección de encabezados principales
        if stripped.startswith("## "):
            current_section = seccion_canonica(stripped)
            cleaned_lines.append(line)
            continue

        if current_section == "PERFIL":
            if stripped and not stripped.startswith("#"):
                # Eliminar asteriscos de negrita/cursiva de la prosa de perfil
                clean_profile_line = line.replace("**", "").replace("*", "")
                cleaned_lines.append(clean_profile_line)
            else:
                cleaned_lines.append(line)
            continue

        if current_section == "EXPERIENCIA":
            # Detectar viñeta con etiqueta temática: • **Etiqueta:** resto
            bullet_match = re.match(r"^(\s*[•*-]\s+)\*\*([^*]+:)\*\*\s*(.*)$", line)
            if bullet_match:
                prefix = bullet_match.group(1)
                tag = bullet_match.group(2)
                body = bullet_match.group(3)
                # Purgar cualquier ** interno en el cuerpo narrativo de la viñeta
                clean_body = body.replace("**", "")
                cleaned_lines.append(f"{prefix}**{tag}** {clean_body}".rstrip())
            else:
                cleaned_lines.append(line)
            continue

        if current_section == "HABILIDADES":
            # Viñeta temática: • **Categoría:** Tech1, Tech2...
            bullet_match = re.match(r"^(\s*[•*-]\s+)\*\*([^*]+:)\*\*\s*(.*)$", line)
            if bullet_match:
                prefix = bullet_match.group(1)
                tag = bullet_match.group(2)
                body = bullet_match.group(3)
                clean_body = body.replace("**", "")
                cleaned_lines.append(f"{prefix}**{tag}** {clean_body}".rstrip())
            else:
                cleaned_lines.append(line)
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def ensure_cv_length(cv_md: str, max_chars: int = 5000) -> str:
    """Garantiza que el CV adaptado no exceda max_chars (default 5000 caracteres),
    preservando de forma innegociable el orden canónico estricto:
    1. Header (# NOMBRE DEL CANDIDATO, datos personales y de contacto)
    2. ## PERFIL (siempre arriba de Experiencia)
    3. ## EXPERIENCIA (empresas de la trayectoria del candidato en orden cronológico)
    4. ## HABILIDADES TÉCNICAS (máximo 3 líneas: encabezado + viñetas temáticas)
    5. ## EDUCACIÓN (viñetas 100% protegidas contra poda)
    6. ## IDIOMAS (viñetas 100% protegidas contra poda)
    Cualquier reducción por exceso de longitud se aplica EXCLUSIVAMENTE sobre las viñetas de Experiencia."""
    if not cv_md:
        return ""

    import copy

    # 1. Normalizar saltos de línea redundantes
    trimmed = re.sub(r'\n{3,}', '\n\n', cv_md.strip())

    # 2. Parsear el documento en bloques por encabezados de nivel 2
    lines = trimmed.splitlines()
    header_lines: List[str] = []
    raw_sections: List[tuple[str, List[str]]] = []
    current_h2: Optional[str] = None
    current_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_h2 is not None:
                raw_sections.append((current_h2, current_lines))
            current_h2 = line
            current_lines = []
        else:
            if current_h2 is None:
                header_lines.append(line)
            else:
                current_lines.append(line)

    if current_h2 is not None:
        raw_sections.append((current_h2, current_lines))

    # Fallback si no hay secciones ##
    if not raw_sections:
        return trimmed

    # Si el header principal (# ) quedó erróneamente dentro de alguna sección
    if not any(l.strip().startswith("# ") for l in header_lines):
        for idx, (sec_h, sec_l) in enumerate(raw_sections):
            found_h1 = False
            for l_idx, l in enumerate(sec_l):
                if l.strip().startswith("# ") and not l.strip().startswith("## "):
                    h1_block = []
                    rem_block = []
                    collecting = True
                    for line_item in sec_l[l_idx:]:
                        if line_item.strip().startswith("## "):
                            collecting = False
                        if collecting:
                            h1_block.append(line_item)
                        else:
                            rem_block.append(line_item)
                    header_lines = h1_block
                    raw_sections[idx] = (sec_h, sec_l[:l_idx] + rem_block)
                    found_h1 = True
                    break
            if found_h1:
                break

    def _trim_blank_lines(lines_list: List[str]) -> List[str]:
        start = 0
        while start < len(lines_list) and not lines_list[start].strip():
            start += 1
        end = len(lines_list)
        while end > start and not lines_list[end - 1].strip():
            end -= 1
        return lines_list[start:end]

    idioma = idioma_de_cv(trimmed)
    titulos = TITULOS[idioma]
    _classify_h2 = seccion_canonica

    sections: Dict[str, List[tuple[str, List[str]]]] = {
        "PERFIL": [],
        "EXPERIENCIA": [],
        "HABILIDADES": [],
        "EDUCACION": [],
        "IDIOMAS": [],
        "OTHER": [],
    }

    for h2, s_lines in raw_sections:
        cat = _classify_h2(h2)
        sections[cat].append((h2, s_lines))

    # Parsear empresas dentro de EXPERIENCIA
    companies = []
    exp_intro = []
    exp_lines = []
    for _, s_lines in sections["EXPERIENCIA"]:
        exp_lines.extend(s_lines)

    current_comp = None
    for line in exp_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            current_comp = {
                "header": stripped,
                "bullets": [],
                "pre_bullets": [],
            }
            companies.append(current_comp)
        elif current_comp is not None:
            if stripped.startswith(("•", "*", "-")):
                current_comp["bullets"].append(stripped)
            else:
                if not current_comp["bullets"]:
                    current_comp["pre_bullets"].append(stripped)
                else:
                    current_comp["bullets"].append(stripped)
        else:
            exp_intro.append(stripped)

    # Limpieza de líneas en Educación e Idiomas (proteger viñetas legítimas, purgar solo huérfanas como 'Logro Extra')
    def _clean_section_bullets(lines_list: List[str]) -> List[str]:
        cleaned = []
        for l in lines_list:
            st = l.strip()
            if not st:
                continue
            st_u = st.upper()
            if st_u.startswith(("• **LOGRO", "* **LOGRO", "- **LOGRO", "• LOGRO", "* LOGRO", "- LOGRO")):
                continue
            cleaned.append(st)
        return cleaned

    def assemble(comp_list) -> str:
        parts = []

        # 1. Header (Nombre, Puesto, Contacto)
        cleaned_header = _trim_blank_lines(header_lines)
        if cleaned_header:
            parts.append("\n".join(cleaned_header))

        # 2. PERFIL (siempre arriba de Experiencia)
        for sec_h, sec_l in sections["PERFIL"]:
            cleaned_l = _trim_blank_lines(sec_l)
            sec_text = sec_h
            if cleaned_l:
                sec_text += "\n" + "\n".join(cleaned_l)
            parts.append(sec_text)

        # 3. EXPERIENCIA
        if sections["EXPERIENCIA"] or comp_list:
            sec_h = sections["EXPERIENCIA"][0][0] if sections["EXPERIENCIA"] else f"## {titulos['EXPERIENCIA']}"
            exp_parts = [sec_h]
            if exp_intro:
                cleaned_intro = _trim_blank_lines(exp_intro)
                if cleaned_intro:
                    exp_parts.append("\n".join(cleaned_intro))

            if comp_list:
                for comp in comp_list:
                    comp_block = [comp["header"]]
                    if comp.get("pre_bullets"):
                        comp_block.extend(comp["pre_bullets"])
                    for b in comp["bullets"]:
                        comp_block.append(b)
                    exp_parts.append("\n".join(comp_block))
            elif exp_lines:
                cleaned_exp = _trim_blank_lines(exp_lines)
                if cleaned_exp:
                    exp_parts.append("\n".join(cleaned_exp))

            parts.append("\n\n".join(exp_parts))

        # 4. HABILIDADES TÉCNICAS
        for sec_h, sec_l in sections["HABILIDADES"]:
            cleaned_l = _trim_blank_lines(sec_l)
            sec_text = sec_h
            if cleaned_l:
                sec_text += "\n" + "\n".join(cleaned_l)
            parts.append(sec_text)

        # Cargar datos del candidato como fallback dinámico si las secciones vinieran vacías
        cand_profile = None
        try:
            cand_profile = load_candidate_profile()
        except Exception:
            cand_profile = None

        default_edu = []
        if cand_profile and cand_profile.get("educacion"):
            default_edu = [
                f"• {item}" if not item.startswith(("•", "*", "-")) else item
                for item in cand_profile["educacion"]
            ]
        if not default_edu:
            default_edu = ["• Formación Profesional Universitaria / Técnica."]

        default_idiomas = []
        if cand_profile and cand_profile.get("idiomas"):
            for item in cand_profile["idiomas"]:
                b = f"• {item}" if not item.startswith(("•", "*", "-")) else item
                b = re.sub(r'(?i)\bnativo\b', 'Nativo', b)
                b = re.sub(r'(?i)\bingles\b', 'Inglés', b)
                default_idiomas.append(b)
        if not default_idiomas:
            default_idiomas = ["• Español: Nativo.", "• Inglés: Técnico / Profesional (B2/C1)."]
        if idioma == EN:
            default_idiomas = [_idioma_en_ingles(b) for b in default_idiomas]

        # 5. EDUCACIÓN (con viñetas 100% protegidas y fallback dinámico si viniera vacía)
        if not sections["EDUCACION"]:
            sections["EDUCACION"] = [(f"## {titulos['EDUCACION']}", default_edu)]
        for sec_h, sec_l in sections["EDUCACION"]:
            cleaned_l = _clean_section_bullets(sec_l)
            if not any(l.strip().startswith(("•", "*", "-")) for l in cleaned_l):
                cleaned_l = default_edu
            sec_text = sec_h + "\n" + "\n".join(cleaned_l)
            parts.append(sec_text)

        # 6. IDIOMAS (con viñetas 100% protegidas y fallback dinámico si viniera vacía)
        if not sections["IDIOMAS"]:
            sections["IDIOMAS"] = [(f"## {titulos['IDIOMAS']}", default_idiomas)]
        for sec_h, sec_l in sections["IDIOMAS"]:
            cleaned_l = _clean_section_bullets(sec_l)
            if not any(l.strip().startswith(("•", "*", "-")) for l in cleaned_l):
                cleaned_l = default_idiomas
            sec_text = sec_h + "\n" + "\n".join(cleaned_l)
            parts.append(sec_text)

        # 7. OTRAS SECCIONES
        for sec_h, sec_l in sections["OTHER"]:
            cleaned_l = _trim_blank_lines(sec_l)
            sec_text = sec_h
            if cleaned_l:
                sec_text += "\n" + "\n".join(cleaned_l)
            parts.append(sec_text)

        return "\n\n".join(parts)

    comp_data = copy.deepcopy(companies)

    # 1. Si con la estructura completa ya entra en max_chars, retornar directamente
    res = assemble(comp_data)
    if len(res) <= max_chars:
        return sanitize_cv_formatting(res)

    # 2. Reducción agnóstica estructural: limitar viñetas a máx 4 por empresa
    for c in comp_data:
        if len(c["bullets"]) > 4:
            c["bullets"] = c["bullets"][:4]

    res = assemble(comp_data)
    if len(res) <= max_chars:
        return sanitize_cv_formatting(res)

    # 3. Limitar a máx 3 viñetas por empresa si aún supera max_chars
    for c in comp_data:
        if len(c["bullets"]) > 3:
            c["bullets"] = c["bullets"][:3]

    res = assemble(comp_data)
    if len(res) <= max_chars:
        return sanitize_cv_formatting(res)

    # 4. Limitar a máx 2 viñetas por empresa si aún supera max_chars
    for c in comp_data:
        if len(c["bullets"]) > 2:
            c["bullets"] = c["bullets"][:2]

    res = assemble(comp_data)
    if len(res) <= max_chars:
        return sanitize_cv_formatting(res)

    # 5. Reducir gradualmente a 1 viñeta por empresa empezando por las más antiguas
    # Preservando siempre al menos 1 viñeta íntegra por empresa (sin gaps de historial)
    for c in reversed(comp_data):
        if len(c["bullets"]) > 1:
            c["bullets"] = c["bullets"][:1]
            res = assemble(comp_data)
            if len(res) <= max_chars:
                return sanitize_cv_formatting(res)

    # 6. Retornar ensamble final saneado con viñetas gramaticalmente íntegras
    # (la concisión del texto es responsabilidad semántica del LLM)
    return sanitize_cv_formatting(res)


_IDIOMAS_EN_INGLES = (
    (r"espa[nñ]ol", "Spanish"), (r"ingl[eé]s", "English"), (r"portugu[eé]s", "Portuguese"),
    (r"franc[eé]s", "French"), (r"italiano", "Italian"), (r"alem[aá]n", "German"),
    (r"nativ[oa]", "Native"), (r"avanzado", "Advanced"), (r"intermedio", "Intermediate"),
    (r"b[aá]sico", "Basic"), (r"t[eé]cnico", "Technical"), (r"profesional", "Professional"), (r"fluido", "Fluent"),
    (r"escrit[oa]", "written"), (r"lectura", "reading"), (r"oral", "spoken"),
)


def _idioma_en_ingles(linea: str) -> str:
    """«• Inglés: Técnico / Profesional» → «• English: Technical / Professional» (fallback de IDIOMAS)."""
    for patron, ingles in _IDIOMAS_EN_INGLES:
        linea = re.sub(rf"(?i)\b{patron}\b", ingles, linea)
    return linea


# El CV completo tarda 1–3 min; con razonamiento High, en inglés o con el radar analizando en paralelo
# pasa de 5 min. Si se corta, queda el CV determinístico (en español): mejor esperar
TIMEOUT_REDACTOR_S = 600
# «Tableros BI (MATCH_DIRECTO): justificación…» → «Tableros BI»: así figura cada requisito en el prompt
_RE_SUFIJO_REQUISITO = re.compile(r"\s*\((?:MATCH_DIRECTO|TRANSFERIBLE|BRECHA_REAL)\).*$", re.DOTALL)

# Instrucciones que se agregan al prompt del Redactor cuando el CV va en inglés
INSTRUCCIONES_CV_EN_INGLES = """
IDIOMA DEL CV: INGLÉS (la oferta está en inglés o pide el CV en inglés). Esto prevalece sobre las reglas de idioma de arriba:
- Escribí TODO el CV en inglés natural, como lo escribiría un profesional que lo habla bien (no una traducción literal).
- Secciones, en este orden y con estos títulos exactos: ## SUMMARY, ## EXPERIENCE, ## TECHNICAL SKILLS, ## EDUCATION, ## LANGUAGES.
- Viñetas de experiencia en pasado y sin pronombres, empezando por el verbo («Built…», «Led…», «Automated…», «Cut…»); el puesto actual puede ir en presente. Las reglas de -icé y de 1ª persona en español no aplican.
- Traducí cargos, títulos y niveles de idioma del candidato sin inventar nada; nombres de empresas, instituciones y tecnologías van tal cual.
- Fechas con meses en inglés y «Present» para el puesto actual.
- Evitá también los clichés de CV en inglés: «results-driven», «proven track record», «passionate about», «team player», «detail-oriented», «dynamic», «fast-paced environment», «hard-working», «go-getter», «think outside the box», «highly motivated», «seasoned».
- El pitch y los pitches multicanal también van en inglés (en el email, «Subject:» en vez de «Asunto:»).
- Además devolvé "keywords_cv": un objeto que mapea el nombre de cada requisito aprobado (solo el nombre, sin el estado entre paréntesis) al término en inglés que usaste en el CV, con las palabras de la oferta si está en inglés (son las keywords que busca el ATS).
"""


def _format_bullet_with_tag(bullet_text: str, default_tag: str = "Optimización Técnica") -> str:
    cleaned = re.sub(r"^[•*–\-]\s*", "", bullet_text).strip()
    match = re.match(r"^\*\*([^*]+:)\*\*\s*(.*)$", cleaned)
    if match:
        tag = match.group(1).strip()
        body = match.group(2).strip()
    else:
        body = cleaned
        tag = ""

    body_low = body.lower()
    if not tag:
        if any(k in body_low for k in ["automatiz", "script", "bot", "pipeline", "ci/cd"]):
            tag = "Automatización & Procesos:"
        elif any(k in body_low for k in ["api", "rest", "soap", "endpoint", "servicio", "microservicio", "backend"]):
            tag = "Integración de APIs & Servicios:"
        elif any(k in body_low for k in ["calidad", "qa", "test", "prueb", "cobertura", "criterio"]):
            tag = "Garantía de Calidad & QA:"
        elif any(k in body_low for k in ["observab", "monitoreo", "grafana", "prometheus", "incidente", "soporte"]):
            tag = "Observabilidad & Soporte:"
        elif any(k in body_low for k in ["sql", "base de datos", "database", "postgres", "oracle", "powerbi", "dashboard", "metric"]):
            tag = "Datos & Analítica:"
        elif any(k in body_low for k in ["arquitectura", "diseño", "cloud", "docker", "k8s", "kubernetes", "infraestructura", "servidor"]):
            tag = "Arquitectura & Cloud:"
        elif any(k in body_low for k in ["requerimiento", "funcional", "jira", "scrum", "agil", "flujo", "negocio", "stakeholder"]):
            tag = "Gestión Funcional & Procesos:"
        else:
            tag = f"{default_tag}:"

    if not tag.endswith(":"):
        tag += ":"

    # Corregir conjugaciones en -izé a -icé según regla RAE
    body = re.sub(r"\b([a-zA-ZáéíóúÁÉÍÓÚñÑ]*)iz[éÉ]\b", r"\1icé", body)

    if body and body[0].islower():
        body = body[0].upper() + body[1:]

    if not body.endswith("."):
        body += "."

    return f"• **{tag}** {body}"


TITULO_INTERPERSONALES = {ES: "HABILIDADES INTERPERSONALES", EN: "SOFT SKILLS"}


def secciones_cv(idioma: str, formato: str, roles_base: bool = True) -> str:
    """Secciones del CV en orden. El completo suma las habilidades interpersonales de la base."""
    titulos = list(TITULOS[idioma].values())
    if normalizar_formato(formato) == COMPLETO and roles_base:
        titulos.insert(titulos.index(TITULOS[idioma]["HABILIDADES"]) + 1, TITULO_INTERPERSONALES[idioma])
    return ", ".join(titulos)


def bloque_roles(roles: List[Dict[str, Any]], formato: str) -> str:
    """Encabezados de EXPERIENCIA que el redactor tiene que copiar tal cual (base del candidato)."""
    if not roles:
        return ""
    if normalizar_formato(formato) == UNA_PAGINA:
        lineas = [
            f"- ### <uno de: {' / '.join(g['titulos'])}> | {g['empresa']}"
            + (f" ({g['desde']} - {g['hasta']})" if g["desde"] and g["hasta"] else "")
            for g in roles_por_empresa(roles)
        ]
        return ("EMPRESAS DEL CANDIDATO (CV de 1 página: un solo encabezado por empresa, con el rango completo de "
                "fechas y, como título, el rol real de esa empresa más afín a la oferta; no inventes ni combines títulos):\n"
                + "\n".join(lineas))
    lineas = [f"- {encabezado_canonico(r['rol'], r['empresa'], r['desde'], r['hasta'])}" for r in roles]
    return ("ROLES DEL CANDIDATO (copiá cada encabezado tal cual, todos y en este orden; las viñetas de cada rol "
            "salen de las de ese rol en el historial):\n" + "\n".join(lineas))


def _parse_experience_sections(exp_text: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current_company = None
    current_header = None
    current_bullets: List[str] = []

    lines = exp_text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("## ") and not stripped.startswith("### "):
            sec_title = stripped[3:].strip()
            clean_sec = sec_title.split("|")[0].split("—")[0].split("–")[0].strip()
            clean_sec = re.split(r"\s+-\s+(?:\d{4}|presente|actualidad)", clean_sec, flags=re.IGNORECASE)[0].strip()
            if clean_sec.lower() in RESERVED_SECTIONS:
                continue

            if current_company and current_bullets:
                sections.append({
                    "company": current_company,
                    "header": current_header or f"### {current_company}",
                    "bullets": current_bullets,
                })
            current_company = clean_sec
            current_header = None
            current_bullets = []
            continue

        if current_company:
            if stripped.startswith("### "):
                if current_header is None:
                    header_content = stripped[4:].strip()
                    if current_company.lower() not in header_content.lower():
                        parts = [p.strip() for p in header_content.split("|")]
                        if len(parts) >= 2:
                            current_header = f"### {parts[0]} | {current_company} | {' | '.join(parts[1:])}"
                        else:
                            current_header = f"### {header_content} | {current_company}"
                    else:
                        current_header = stripped
            elif stripped.startswith(("•", "*", "-")):
                current_bullets.append(stripped)

    if current_company and current_bullets:
        sections.append({
            "company": current_company,
            "header": current_header or f"### {current_company}",
            "bullets": current_bullets,
        })

    return sections


class WriterAgent:
    """Subagente Redactor: Produce el CV adaptado en Markdown (Google XYZ) y el Pitch Multicanal."""

    def __init__(self, bridge: Optional[AntigravityBridge] = None):
        self.bridge = bridge or AntigravityBridge()

    def load_candidate_profile(self) -> Dict[str, str]:
        exp = EXP_PATH.read_text(encoding="utf-8") if EXP_PATH.exists() else ""
        per = PER_PATH.read_text(encoding="utf-8") if PER_PATH.exists() else ""
        return {"experiencia": exp, "personales": per}

    async def generate_cv_and_pitch(
        self, analysis: Dict[str, Any], profile: Optional[Dict[str, str]] = None,
        datos_de_terceros: bool = False,
    ) -> Dict[str, Any]:
        """`datos_de_terceros=True` (ofertas del Radar LinkedIn): puesto, empresa, dolores y requisitos
        salen de un post de terceros; van delimitados en <oferta> con la regla de no obedecerlos."""
        if profile is None:
            profile = self.load_candidate_profile()
        idioma = EN if analysis.get("idioma_cv") == EN else ES
        # Sin formato explícito se conserva el CV de 1 página de antes; el pipeline pasa el de la postulación
        formato = normalizar_formato(analysis.get("formato_cv") or UNA_PAGINA)
        roles = parse_roles(str(profile.get("experiencia", "")))
        puesto = analysis.get("puesto") or candidato_datos.puesto()
        empresa = analysis.get("empresa", "Empresa Líder")
        decision = await decidir_titulo(
            {"analisis": analysis, "titulo": analysis.get("puesto", "")},
            experiencia=profile["experiencia"], personales=profile["personales"], bridge=self.bridge,
        )

        def finalizar_cv(cv):
            if not decision.titulo:
                return cv
            cv, _ = aplicar_titulo(cv, decision)
            return asegurar_mencion_ats(cv, decision.puesto_oferta)

        def fallback(reason):
            salida = self._deterministic_fallback(puesto, empresa, analysis, profile, reason=reason)
            if decision.titulo:
                # El aviso puede figurar como postulación ATS, nunca como profesión propia.
                salida["cv_markdown"] = salida["cv_markdown"].replace(
                    f"Especialista en {puesto} con", f"{decision.titulo} con", 1)
                salida["cv_markdown"] = finalizar_cv(salida["cv_markdown"])
            return salida

        dolores = "\n- ".join(analysis.get("dolores_empresa", []))

        # Requisitos filtrados por aprobación
        requisitos = analysis.get("requisitos", [])
        aprobados = [
            f"{r['nombre']} ({r['estado']}): {r.get('justificacion', '')} {f'[Equivalencia: {r.get('equivalencia')}]' if r.get('equivalencia') else ''}"
            for r in requisitos
            if r.get("approved", r.get("estado") == "MATCH_DIRECTO")
            # Residencia, modalidad o jornada no se «enfatizan»: el redactor las copiaba al perfil
            and not es_requisito_logistico(r.get("nombre", ""))
        ]
        skills_str = "\n- ".join(aprobados)

        bloque_oferta = f"""PUESTO A POSTULAR: {puesto}
EMPRESA: {empresa}

DOLORES CLAVE DE LA EMPRESA:
- {dolores}

REQUISITOS Y COMPETENCIAS A ENFATIZAR (Aprobados):
- {skills_str}"""
        if datos_de_terceros:
            bloque_oferta = (
                "DATOS DE LA VACANTE (extraídos de un post de LinkedIn de terceros):\n"
                f"{delimitar_datos_de_terceros(bloque_oferta, 'oferta')}\n\n"
                f"{REGLA_DATOS_DE_TERCEROS.format(tag='oferta')}"
            )

        ausentes = cargar_conocimientos_ausentes()
        linea_ausentes = (
            f"- Conocimientos que el candidato NO tiene (nunca van al CV, ni como «nociones», ni en viñetas ni en habilidades): {', '.join(ausentes)}.\n"
            if ausentes else ""
        )

        prompt = f"""
{WRITER_PROMPT_COMPLETO if formato == COMPLETO else WRITER_PROMPT}

{bloque_oferta}

DATOS DEL CANDIDATO:
{profile['personales']}

HISTORIAL LABORAL REAL:
{profile['experiencia']}

{bloque_roles(roles, formato)}

INSTRUCCIONES DE FORMATO:
- En cada viñeta de Experiencia, aplica fórmula Google XYZ: «Logró [X], medido por [Y], realizando [Z]».
- NO inventes puestos que el candidato no ocupó. Lo transferible de la oferta (responsabilidades, conocimientos, herramientas) va con los mismos términos de la oferta, en la viñeta y la categoría donde encaja: la ausencia en el historial no es evidencia de ausencia.
- Todo sale listo para enviar: PROHIBIDO dejar campos a completar ([Nombre], {empresa}, XX, ___). En pitches y mensajes sin nombre de destinatario, saludá sin nombre. No afirmes un nivel de idioma mayor al declarado en el perfil.
- El CV debe incluir exactamente las secciones: {secciones_cv(idioma, formato, roles_base=bool(roles))}.
- El Pitch debe cubrir los 3 formatos multicanal: inmail (<300 chars), email formal y web_ats.
{linea_ausentes}{bloque_prompt()}
{INSTRUCCIONES_CV_EN_INGLES if idioma == EN else ""}
{f'Excepción solo para el titular decidido: no lo traduzcas. Usá exactamente: «{decision.titulo}»' if idioma == EN and decision.titulo else ""}
{instrucciones_titulo_cv(decision)}
Devolvé un objeto JSON con:
- "cv_markdown": str (el contenido completo del CV en Markdown limpio)
- "pitch": str (el pitch base de 3 párrafos para el reclutador)
- "pitches_multicanal": {{
    "inmail": str (mensaje para LinkedIn InMail/DM estrictamente menor a 300 caracteres),
    "email": str (correo formal con Asunto: y firma completa),
    "web_ats": str (texto en 1ª persona sin encabezados ni asunto para formularios web/ATS)
  }}
"""
        try:
            # Con datos de terceros, agy corre en un directorio vacío: sin el repo ni los datos al alcance
            with (radar_agy_cwd() if datos_de_terceros else contextlib.nullcontext()) as cwd:
                extra = {"cwd": cwd} if cwd else {}
                try:
                    result = await self.bridge.run_prompt(
                        prompt, json_mode=True, timeout_seconds=TIMEOUT_REDACTOR_S, **extra)
                except TimeoutError:
                    result = await self.bridge.run_prompt(
                        prompt, json_mode=True, timeout_seconds=TIMEOUT_REDACTOR_S,
                        effort="medium", **extra)
            if isinstance(result, dict) and "cv_markdown" in result:
                cv_clean = sanitize_cv_formatting(result["cv_markdown"].strip())
                # Años solo del perfil del candidato y sin requisitos copiados (creador, 08/11)
                cv_clean, cambios_perfil = corregir_perfil(cv_clean)
                cv_clean, cambios_hab = quitar_requisitos_de_habilidades(cv_clean)
                cambios_perfil += cambios_hab
                if cambios_perfil:
                    logger.info("[Writer] Perfil corregido sin IA: %s", "; ".join(cambios_perfil))
                # Títulos, empresas y fechas salen de la base del candidato (creador, 09/11)
                cv_clean, cambios_enc, problemas_enc = corregir_encabezados(
                    cv_clean, roles, formato, ingles=idioma == EN)
                if cambios_enc or problemas_enc:
                    logger.info("[Writer] Encabezados de experiencia: %s", "; ".join(cambios_enc + problemas_enc))
                if formato == UNA_PAGINA and len(cv_clean) > 5000:
                    cv_clean = ensure_cv_length(cv_clean, max_chars=5000)
                else:
                    cv_clean = sanitize_cv_formatting(cv_clean)

                cv_clean = finalizar_cv(cv_clean)

                base_pitch = str(result.get("pitch", "")).strip()
                pitches_multi = result.get("pitches_multicanal")

                if (
                    not isinstance(pitches_multi, dict)
                    or not pitches_multi.get("inmail")
                    or not pitches_multi.get("email")
                    or not pitches_multi.get("web_ats")
                ):
                    pitches_multi = synthesize_multichannel_pitches(
                        pitch=base_pitch,
                        puesto=puesto,
                        empresa=empresa,
                        dolores=analysis.get("dolores_empresa"),
                        requisitos=requisitos,
                        profile=profile,
                    )
                else:
                    if len(pitches_multi.get("inmail", "")) > 300:
                        inmail_text = pitches_multi["inmail"]
                        cand_name = "Candidato"
                        if isinstance(profile, dict):
                            if profile.get("nombre"):
                                cand_name = str(profile["nombre"]).strip()
                            elif profile.get("personales"):
                                parsed_prof = parse_candidate_profile("", str(profile.get("personales", "")))
                                cand_name = parsed_prof.get("nombre") or "Candidato"
                        suffix = f"... Saludos! {cand_name}"
                        max_len = 300 - len(suffix)
                        trimmed = inmail_text[:max_len].rsplit(" ", 1)[0] + suffix
                        pitches_multi["inmail"] = trimmed[:300]

                if not base_pitch:
                    base_pitch = pitches_multi.get("email") or pitches_multi.get("web_ats") or ""

                salida = {
                    "cv_markdown": cv_clean,
                    "pitch": base_pitch,
                    "pitches_multicanal": pitches_multi,
                    "is_fallback": False,
                    "fallback_reason": None,
                    "idioma_cv": idioma_de_cv(cv_clean),
                    "formato_cv": formato,
                    "veracidad": {"cambios": cambios_enc, "problemas": problemas_enc},
                }
                if idioma == EN and isinstance(result.get("keywords_cv"), dict):
                    salida["keywords_cv"] = {
                        _RE_SUFIJO_REQUISITO.sub("", str(k)).strip(): str(v).strip()
                        for k, v in result["keywords_cv"].items()
                        if _RE_SUFIJO_REQUISITO.sub("", str(k)).strip() and str(v).strip()
                    }
                return salida
        except Exception as exc:
            return fallback(str(exc))

        return fallback("El modelo no retornó el formato de CV esperado.")

    def _deterministic_fallback(
        self, puesto: str, empresa: str, analysis: Dict[str, Any], profile: Dict[str, str], reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generador determinístico dinámico de CV y Pitch Multicanal a partir del perfil real del candidato."""
        puesto_objetivo = (puesto or candidato_datos.puesto()).upper()
        empresa_val = (empresa or "Empresa Líder").strip()

        # Parsear perfil dinámicamente
        exp_raw = profile.get("experiencia", "") if isinstance(profile, dict) else ""
        per_raw = profile.get("personales", "") if isinstance(profile, dict) else ""
        parsed = parse_candidate_profile(exp_raw, per_raw)

        cand_name = parsed.get("nombre") or "Candidato"
        contacto_line = parsed.get("contacto") or ""

        # 1. Header
        header = f"# {cand_name}\n{puesto_objetivo}"
        if contacto_line:
            header += f"\n{contacto_line}"

        # 2. Estimación de años de trayectoria y redacción de PERFIL en 1ª persona
        years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", exp_raw)]
        if years:
            min_year = min(years)
            years_diff = max(1, 2026 - min_year)
            trayectoria_str = f"más de {years_diff} años" if years_diff >= 2 else "experiencia profesional comprobable"
        else:
            trayectoria_str = "experiencia profesional comprobable"

        comp_list = parsed.get("empresas", [])
        comp_summary = f" en organizaciones como {', '.join(comp_list[:2])}" if comp_list else ""

        perfil_lines = [
            f"Especialista en {puesto} con {trayectoria_str} liderando la optimización operativa, integración técnica y observabilidad de sistemas complejos.",
            f"Cuento con experiencia profesional traduciendo requerimientos críticos del negocio y reglas operativas en soluciones técnicas escalables para {empresa_val}.",
            f"Durante mi trayectoria{comp_summary}, implementé automatizaciones y arquitecturas resilientes que redujeron tiempos de ciclo y mitigaron incidencias en entornos productivos.",
            f"Facilito la interlocución continua entre áreas funcionales e ingeniería, aportando rigor analítico y solvencia técnica para asegurar el cumplimiento de los objetivos de {empresa_val}."
        ]
        perfil_md = "\n".join(perfil_lines)

        # 3. EXPERIENCIA estructurada dinámicamente
        requisitos = analysis.get("requisitos", [])
        aprobados_reqs = [
            r["nombre"].lower() for r in requisitos
            if r.get("approved", r.get("estado") == "MATCH_DIRECTO")
        ]
        if not aprobados_reqs:
            aprobados_reqs = [r["nombre"].lower() for r in requisitos if r.get("estado") != "BRECHA_REAL"]

        dolores_list = analysis.get("dolores_empresa") or []
        pain_words = set(w.lower() for d in dolores_list for w in re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{5,}\b', str(d)))

        def _score_bullet(b_text: str) -> int:
            b_low = b_text.lower()
            s = sum(6 for req in aprobados_reqs if req in b_low)
            s += sum(4 for pw in pain_words if pw in b_low)
            if any(v in b_low for v in [
                "lideré", "diseñé", "implementé", "coordiné", "automaticé",
                "gestioné", "relevé", "desarrollé", "optimicé", "estandaricé",
                "centralicé", "resolví", "validé", "conduje", "creé", "definí"
            ]):
                s += 4
            if re.search(r"\b(?:\d+[%kKmM]?|historias de usuario|criterios de aceptación|pases? productivos?|ambientes?|consistencia|reglas de negocio)\b", b_low):
                s += 3
            return s

        formato = normalizar_formato(analysis.get("formato_cv") or UNA_PAGINA)
        roles = parse_roles(exp_raw)
        if roles:
            # Encabezados de la base: uno por rol (completo) o uno por empresa (1 página)
            def _puntaje_rol(r: Dict[str, Any]) -> int:
                return sum(_score_bullet(b) for b in r["vinetas"])

            if formato == COMPLETO:
                orden = sorted(range(len(roles)), key=lambda i: _puntaje_rol(roles[i]), reverse=True)
                cuotas = {i: (4 if pos < 2 else 2) for pos, i in enumerate(orden)}
                comp_sections = [{
                    "header": encabezado_canonico(r["rol"], r["empresa"], r["desde"], r["hasta"]),
                    "bullets": [f"• {b}" for b in r["vinetas"]] or ["• Participé en las tareas del rol."],
                    "quota": cuotas[i],
                } for i, r in enumerate(roles)]
            else:
                comp_sections = []
                for g in roles_por_empresa(roles):
                    afin = max(g["roles"], key=_puntaje_rol)
                    comp_sections.append({
                        "header": encabezado_canonico(afin["rol"], g["empresa"], g["desde"], g["hasta"]),
                        "bullets": [f"• {b}" for r in g["roles"] for b in r["vinetas"]],
                    })
        else:
            comp_sections = _parse_experience_sections(exp_raw)

        # Determinar cuotas de viñetas por empresa para un total de 6-7 viñetas
        num_comps = len(comp_sections)
        if num_comps == 3:
            quotas = [3, 2, 1]
        elif num_comps == 2:
            quotas = [3, 3]
        elif num_comps == 1:
            quotas = [6]
        elif num_comps >= 4:
            quotas = [2, 2, 1, 1] + [1] * (num_comps - 4)
        else:
            quotas = []

        exp_blocks = []
        for idx, sec in enumerate(comp_sections):
            quota = sec.get("quota") or (quotas[idx] if idx < len(quotas) else 1)
            b_list = sec["bullets"]
            # Ordenar viñetas priorizando coincidencia con requisitos
            sorted_b = sorted(b_list, key=_score_bullet, reverse=True)
            selected_b = sorted_b[:quota] if quota > 0 else sorted_b

            block_lines = [sec["header"]]
            for b in selected_b:
                block_lines.append(_format_bullet_with_tag(b))
            exp_blocks.append("\n".join(block_lines))

        if not exp_blocks:
            default_header = f"### Especialista Técnico | {comp_list[0] if comp_list else 'Consultoría Independiente'}"
            exp_blocks.append(
                f"{default_header}\n• **Optimización Técnica:** Lideré iniciativas técnicas optimizando flujos de trabajo operativos con impacto medible."
            )

        experiencia_md = "\n\n".join(exp_blocks)

        # 4. HABILIDADES TÉCNICAS en 2 líneas temáticas
        aprobados_names = [r["nombre"] for r in requisitos if r.get("approved", r.get("estado") == "MATCH_DIRECTO")]
        if not aprobados_names:
            aprobados_names = [r["nombre"] for r in requisitos if r.get("estado") != "BRECHA_REAL"]
        if not aprobados_names:
            aprobados_names = [r["nombre"] for r in requisitos]

        mgmt_keys = ["scrum", "agil", "jira", "kanban", "historia", "bpmn", "product", "qa", "testing", "prueb", "funcional", "analisis", "relevamiento", "proceso"]
        mgmt_skills = [k for k in aprobados_names if any(w in k.lower() for w in mgmt_keys)]
        dev_skills = [k for k in aprobados_names if k not in mgmt_skills]

        # Completa con las habilidades base del stack del candidato (Mi perfil); sin stack, solo lo aprobado
        base = candidato_datos.stack()["habilidades_base"]
        mgmt_techs = list(dict.fromkeys(mgmt_skills + base["gestion"]))[:5]
        dev_techs = list(dict.fromkeys(dev_skills + base["tecnicas"]))[:7]

        habilidades_md = "\n".join(
            f"• **{titulo}:** {', '.join(items)}." for titulo, items in (
                ("Gestión Funcional & Metodologías", mgmt_techs), ("Desarrollo, Datos & DevOps", dev_techs)) if items)

        # 5. EDUCACIÓN e IDIOMAS
        edu_items = parsed.get("educacion", [])
        if not edu_items:
            edu_items = ["Formación Universitaria / Técnica en Informática o Sistemas."]
        edu_bullets = [f"• {e}" if not e.startswith(("•", "*", "-")) else e for e in edu_items]
        educacion_md = "\n".join(edu_bullets)

        idiomas_items = parsed.get("idiomas", [])
        if not idiomas_items:
            idiomas_items = ["Español: Nativo.", "Inglés: Técnico / Profesional."]
        idiomas_bullets = [f"• {i}" if not i.startswith(("•", "*", "-")) else i for i in idiomas_items]
        idiomas_md = "\n".join(idiomas_bullets)

        # 6. Ensamblaje y normalización
        cv_raw = f"""{header}

## PERFIL
{perfil_md}

## EXPERIENCIA
{experiencia_md}

## HABILIDADES TÉCNICAS
{habilidades_md}

## EDUCACIÓN
{educacion_md}

## IDIOMAS
{idiomas_md}
"""
        cv_md = sanitize_cv_formatting(cv_raw)
        if formato == UNA_PAGINA or not roles:
            cv_md = ensure_cv_length(cv_md, max_chars=5000)

        # 7. Pitch formal dirigido y firmado dinámicamente
        comp_ref = f"En mi trayectoria profesional en organizaciones como {', '.join(comp_list[:2])}" if comp_list else "A lo largo de mi trayectoria profesional"
        pitch_signature = f"\n\nSaludos cordiales,\n{cand_name}"
        if contacto_line:
            pitch_signature += f"\n{contacto_line}"

        pitch = f"""Hola equipo de selección de {empresa_val},

Me pongo en contacto con gran interés respecto a la posición de {puesto}. He seguido de cerca sus desafíos en la optimización operativa y escalabilidad técnica, y considero que mi perfil profesional y experiencia práctica pueden integrarse de inmediato a sus requerimientos y objetivos estratégicos.

{comp_ref}, he liderado y colaborado en la resolución de desafíos funcionales y técnicos de alta exigencia, implementando soluciones robustas, automatización de procesos y optimización de flujos de trabajo con impacto medible en la operación.

Estoy convencido de que puedo aportar solvencia técnica y valor inmediato a los proyectos de {empresa_val}. Quedo a su entera disposición para coordinar una entrevista y profundizar sobre mi postulación. Muchas gracias por su tiempo y consideración.{pitch_signature}"""

        pitches_multi = synthesize_multichannel_pitches(
            pitch=pitch,
            puesto=puesto,
            empresa=empresa,
            dolores=analysis.get("dolores_empresa"),
            requisitos=requisitos,
            profile=profile,
        )

        fallback_reason = reason or "Indisponibilidad temporal del modelo de IA o timeout de respuesta."

        return {
            "cv_markdown": cv_md,
            "pitch": pitch,
            "pitches_multicanal": pitches_multi,
            "is_fallback": True,
            "fallback_reason": fallback_reason,
        }
