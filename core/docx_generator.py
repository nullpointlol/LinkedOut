"""Compilador de CV Markdown a DOCX Ejecutivo (fiel al template y geometría A4)."""
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Union
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from core.paths import DATA_DIR
from core import candidato_datos
from core.text_normalization import normalizar_guiones

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = BASE_DIR / "templates" / "cv_template_executive.docx"
EXPORTS_DIR = DATA_DIR / "exports"

# Paleta cromática corporativa (Navy & Teal)
COLOR_NAVY = RGBColor(36, 75, 110)      # #244B6E (Títulos, Posiciones)
COLOR_TEAL = RGBColor(26, 117, 128)     # #1A7580 (Bordes divisores, Puesto Objetivo)
COLOR_SLATE = RGBColor(77, 89, 103)     # #4D5967 (Línea de contacto, subtítulos)
COLOR_BODY = RGBColor(34, 34, 34)       # #222222 (Texto regular)
HEX_NAVY = "244B6E"
HEX_TEAL = "1A7580"
HEX_BORDER_CONTACT = "A8BBC4"

STOPWORDS = {
    'de', 'del', 'la', 'el', 'los', 'las', 'en', 'para', 'por', 'y', 'e', 'a', 'con',
    'sobre', 'un', 'una', 'of', 'in', 'for', 'and', 'the', 'to', 'at', 'with'
}
SENIORITY = {
    'senior', 'sr', 'junior', 'jr', 'semisenior', 'semi-senior', 'ssr', 'trainee', 'staff', 'principal'
}


def clean_text(text: str) -> str:
    """Normaliza texto eliminando tildes y caracteres diacríticos."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def summarize_role_slug(role: Optional[str]) -> str:
    """Genera un slug interpretativo y conciso del rol de máximo 3 palabras (2 ideal)."""
    if not role or not role.strip():
        return "PROFESIONAL"

    r = clean_text(role)
    # Limpiar sufijos de género /a, /o, (a), (o)
    r = re.sub(r'/(?:a|o|as|os)\b', '', r, flags=re.IGNORECASE)
    r = re.sub(r'\((?:a|o|as|os)\)', '', r, flags=re.IGNORECASE)

    # Separar por delimitadores mayores como '/', '|', '–', '—'
    chunks = re.split(r'[/|–—]', r)
    primary = chunks[0].strip() if chunks and len(chunks[0].strip().split()) >= 1 else r

    # Separar por guiones espaciados (ej. 'Analista Funcional - Banca')
    hyphen_chunks = re.split(r'\s+-\s+', primary)
    if hyphen_chunks:
        primary = hyphen_chunks[0].strip()

    tokens = re.findall(r'[A-Za-z0-9]+', primary)
    meaningful = [t for t in tokens if t.lower() not in STOPWORDS]

    # Si hay más de 2 palabras, omitir nivel de seniority para destacar el rol sustantivo
    if len(meaningful) > 2:
        without_sr = [t for t in meaningful if t.lower() not in SENIORITY]
        if len(without_sr) >= 2:
            meaningful = without_sr

    # Limitar a 3 palabras como mucho
    if len(meaningful) > 3:
        meaningful = meaningful[:3]

    slug = '-'.join(t.upper() for t in meaningful)
    return slug or "PROFESIONAL"


def get_candidate_name(cv_markdown: Optional[str] = None) -> str:
    """Obtiene el nombre completo del candidato dinámicamente desde el markdown o data/candidato/personales.txt."""
    if cv_markdown:
        for line in cv_markdown.strip().splitlines():
            line_s = line.strip()
            if line_s.startswith("# ") and not line_s.startswith("## "):
                extracted = line_s[2:].strip()
                if extracted:
                    return extracted
    personales_path = DATA_DIR / "candidato" / "personales.txt"
    if personales_path.exists():
        try:
            with open(personales_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    return first_line
        except Exception:
            pass
    return "Candidato"


def generate_cv_filename(target_role: Optional[str] = None, candidate_name: Optional[str] = None) -> str:
    """Genera el nombre formal del archivo DOCX con patrón CV_ROL_NOMBRE-APELLIDO.docx."""
    role_slug = summarize_role_slug(target_role)
    name = candidate_name or get_candidate_name()
    name_clean = clean_text(name)
    name_tokens = re.findall(r'[A-Za-z0-9]+', name_clean)
    name_slug = '-'.join(t.upper() for t in name_tokens) or "CANDIDATO"
    return f"CV_{role_slug}_{name_slug}.docx"


def add_bottom_border(paragraph, color_hex: str = HEX_TEAL, sz: str = "7", space: str = "4"):
    """Agrega una línea horizontal divisoria inferior al párrafo mediante XML de Word."""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), sz)
    bottom.set(qn('w:space'), space)
    bottom.set(qn('w:color'), color_hex)
    pBdr.append(bottom)
    pPr.append(pBdr)


def add_hyperlink(paragraph, url: str, text: str, color_hex: str = HEX_NAVY, font_size_pt: float = 9.0):
    """Inserta un hipervínculo clicable en el párrafo."""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True
    )
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    hyperlink.set(qn('w:tooltip'), url)
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    c = OxmlElement('w:color')
    c.set(qn('w:val'), color_hex)
    rPr.append(c)
    fonts = OxmlElement('w:rFonts')
    fonts.set(qn('w:ascii'), 'Arial')
    fonts.set(qn('w:hAnsi'), 'Arial')
    rPr.append(fonts)
    sz = OxmlElement('w:sz')
    sz.set(qn('w:val'), str(int(font_size_pt * 2)))
    rPr.append(sz)
    new_run.append(rPr)
    text_el = OxmlElement('w:t')
    text_el.text = text
    new_run.append(text_el)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def _add_formatted_runs(paragraph, text: str, font_size: Pt = Pt(9.5), default_bold: bool = False):
    """Convierte texto con negritas markdown **palabra** en runs de python-docx, saneando backticks y caracteres extraños."""
    # Blindaje contra micro-fuentes: prohibir reducción artificial de fuente en cuerpo a menos de 8.5pt
    effective_font_size = font_size
    if hasattr(font_size, 'pt') and font_size.pt < 8.5:
        effective_font_size = Pt(9.5)

    tokens = re.split(r'(\*\*.*?\*\*)', text)
    for token in tokens:
        if not token:
            continue
        is_bold = default_bold or (token.startswith('**') and token.endswith('**'))
        content = token[2:-2] if (token.startswith('**') and token.endswith('**')) else token
        # Sanear backticks markdown (`código` -> código)
        content = content.replace('`', '')
        
        run = paragraph.add_run(content)
        run.bold = is_bold
        run.font.name = 'Arial'
        run.font.size = effective_font_size
        run.font.color.rgb = COLOR_BODY


def is_contact_line(text: str) -> bool:
    """Detecta si una línea corresponde a los datos de contacto del candidato."""
    t = text.lower()
    indicators = ["@", "linkedin", "wa.me", "buenos aires", "+54"]
    return (any(ind in t for ind in indicators)
            or ("|" in t and any(c.isdigit() for c in t))
            or bool(re.fullmatch(r"(?:https?://|www\.)\S+", t))
            or bool(re.fullmatch(r"\+?\d[\d ()–—-]{6,}\d", t)))


def render_contact_paragraph(doc: Document, line: str):
    """Renderiza la línea de contacto con iconos y enlaces clicables (WhatsApp, Mailto, LinkedIn)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.keep_with_next = True

    raw_items = [it.strip() for it in line.split("|") if it.strip()]
    
    location_str = ""
    phone_str = ""
    whatsapp_url = ""
    email_str = ""
    linkedin_url = ""

    for item in raw_items:
        lower = item.lower()
        if "linkedin" in lower:
            m = re.search(r'https?://[^\s]+', item)
            if m:
                linkedin_url = m.group(0)
            elif "linkedin.com" in lower:
                m_no_http = re.search(r'(?:www\.)?linkedin\.com/[^\s]+', item)
                if m_no_http:
                    linkedin_url = f"https://{m_no_http.group(0)}"
        elif "@" in item:
            m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', item)
            email_str = m.group(0) if m else item
        elif "wa.me" in lower:
            m = re.search(r'https?://wa\.me/[^\s]+|wa\.me/[^\s]+', item)
            whatsapp_url = m.group(0) if m else item
            if not whatsapp_url.startswith("http"):
                whatsapp_url = "https://" + whatsapp_url
        elif any(c.isdigit() for c in item) and ("+" in item or "-" in item or "11" in item or "15" in item):
            phone_str = item
        else:
            if not location_str:
                location_str = item

    if phone_str and not whatsapp_url:
        digits = re.sub(r'\D', '', phone_str)
        if digits:
            whatsapp_url = f"https://wa.me/{digits}"
    if whatsapp_url and not phone_str:
        digits_match = re.search(r'wa\.me/(\+?\d+)', whatsapp_url)
        if digits_match:
            phone_str = f"+{digits_match.group(1).lstrip('+')}"

    first = True

    def add_separator():
        nonlocal first
        if not first:
            sep = p.add_run(" • ")
            sep.font.name = "Arial"
            sep.font.size = Pt(10.0)
            sep.font.color.rgb = COLOR_SLATE
        first = False

    if location_str:
        add_separator()
        r = p.add_run(location_str)
        r.font.name = "Arial"
        r.font.size = Pt(10.0)
        r.font.color.rgb = COLOR_SLATE

    if whatsapp_url and phone_str:
        add_separator()
        add_hyperlink(p, whatsapp_url, phone_str, color_hex=HEX_NAVY, font_size_pt=10.0)

    if email_str:
        add_separator()
        add_hyperlink(p, f"mailto:{email_str}", email_str, color_hex=HEX_NAVY, font_size_pt=10.0)

    if linkedin_url:
        add_separator()
        add_hyperlink(p, linkedin_url, "LinkedIn", color_hex=HEX_NAVY, font_size_pt=10.0)

    if first:
        # Si ningún patrón coincidió, renderizar como texto regular
        _add_formatted_runs(p, line, font_size=Pt(10.0))

    return p


def add_target_role_subtitle(doc: Document, role_title: str):
    """Inserta el puesto objetivo en Teal corporativo 11.5pt destacado bajo el nombre."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    clean_title = (role_title or "").replace("`", "").upper()
    run = p.add_run(clean_title)
    run.bold = True
    run.font.name = "Arial"
    run.font.size = Pt(11.5)
    run.font.color.rgb = COLOR_TEAL
    return p


def _safe_meta_string(val: Union[str, List[str], None], max_len: int = 250) -> str:
    """Trunca de forma segura cadenas o listas de metadatos DOCX para respetar el límite estricto de 255 caracteres de OpenXML."""
    if not val:
        return ""

    if isinstance(val, list):
        accumulated = []
        current_len = 0
        for item in val:
            item_str = (item.get("nombre") if isinstance(item, dict) else str(item)).strip()
            if not item_str:
                continue
            addition_len = len(item_str) if not accumulated else len(item_str) + 2  # ", "
            if current_len + addition_len <= max_len:
                accumulated.append(item_str)
                current_len += addition_len
            else:
                break
        if accumulated:
            return ", ".join(accumulated)
        first_item = str(val[0]).strip() if val else ""
        return first_item[:max_len]

    text = str(val).strip()
    if len(text) <= max_len:
        return text

    truncated = text[:max_len]
    last_break = max(truncated.rfind(' '), truncated.rfind(','))
    if last_break > int(max_len * 0.6):
        return truncated[:last_break].rstrip(' ,')
    return truncated


def compile_markdown_to_docx(
    cv_markdown: str,
    target_role: Optional[str] = None,
    output_filename: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    candidate_name: Optional[str] = None
) -> Path:
    """Compila el CV redactado en Markdown a un documento DOCX profesional con soporte de metadatos y micro-indexación ATS."""
    cv_markdown = normalizar_guiones(cv_markdown)
    target_role = normalizar_guiones(target_role) if target_role else target_role
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    
    if TEMPLATE_PATH.exists():
        doc = Document(str(TEMPLATE_PATH))
        for p in list(doc.paragraphs):
            p._element.getparent().remove(p._element)
    else:
        doc = Document()

    # Metadatos oficiales de documento DOCX para motores ATS
    c_name = candidate_name or get_candidate_name(cv_markdown)
    role_title = target_role or candidato_datos.puesto()
    doc.core_properties.author = doc.core_properties.last_modified_by = _safe_meta_string(c_name, max_len=250)
    doc.core_properties.comments = ""  # la plantilla de python-docx trae su firma y fechas de 2013
    doc.core_properties.created = doc.core_properties.modified = datetime.now()
    doc.core_properties.title = _safe_meta_string(" - ".join(filter(None, ("CV", c_name, role_title))), max_len=250)
    doc.core_properties.subject = _safe_meta_string(f"Currículum Vitae para la posición de {role_title}", max_len=250)
    if keywords:
        doc.core_properties.keywords = normalizar_guiones(_safe_meta_string(keywords, max_len=250))

    # Configuración de página A4 y márgenes estándar
    section = doc.sections[0]
    section.page_width = Pt(595.3)   # A4 Width
    section.page_height = Pt(841.9)  # A4 Height
    section.top_margin = Pt(72)      # 1 pulgada
    section.bottom_margin = Pt(72)
    section.left_margin = Pt(54)     # 0.75 pulgada
    section.right_margin = Pt(54)

    lines = cv_markdown.strip().splitlines()
    has_inserted_target_role = False
    passed_name = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped in ('---', '***'):
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if level >= 2:
                has_inserted_target_role = True

            if level == 1:
                # Nombre del candidato
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(2)
                p.paragraph_format.keep_with_next = True
                run = p.add_run(heading_text.upper())
                run.bold = True
                run.font.name = "Arial"
                run.font.size = Pt(20)
                run.font.color.rgb = COLOR_NAVY
                passed_name = True

                # Si se pasó target_role explícito y la siguiente línea no parece ser el puesto,
                # lo dejamos para insertar inmediatamente o tras detectar si la siguiente línea es el puesto.
                continue

            elif level == 2:
                # Encabezado de Sección (PERFIL, EXPERIENCIA, etc.)
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(14)
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.keep_with_next = True
                run = p.add_run(heading_text.upper())
                run.bold = True
                run.font.name = "Arial"
                run.font.size = Pt(10.5)
                run.font.color.rgb = COLOR_NAVY

            elif level >= 3:
                # Puesto o subsección (ej. Cargo | Empresa | Fechas)
                if '|' in heading_text:
                    parts = [pt.strip() for pt in heading_text.split('|')]
                    role = parts[0].replace('**', '')
                    comp_date = " | ".join(p.replace('**', '') for p in parts[1:]) if len(parts) > 1 else ""

                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(8)
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.keep_with_next = True
                    # Run del Rol
                    r_role = p.add_run(role)
                    r_role.bold = True
                    r_role.font.name = 'Arial'
                    r_role.font.size = Pt(10)
                    r_role.font.color.rgb = COLOR_NAVY
                    # Separador y Empresa | Fechas en el mismo renglón
                    if comp_date:
                        sep_run = p.add_run(" | ")
                        sep_run.font.name = 'Arial'
                        sep_run.font.size = Pt(9.5)
                        sep_run.font.color.rgb = COLOR_SLATE
                        r_sub = p.add_run(comp_date)
                        r_sub.font.name = 'Arial'
                        r_sub.font.size = Pt(9.5)
                        r_sub.font.color.rgb = COLOR_SLATE
                else:
                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(8)
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.keep_with_next = True
                    _add_formatted_runs(p, heading_text, font_size=Pt(10), default_bold=True)

        elif re.match(r"^[*\-\u2022]\s+", stripped):
            # Viñeta de logro normalizada (sin sangría colgante ni indent desalineado)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(0)
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1.5)
            p.paragraph_format.line_spacing = 1.35

            bullet_content = re.sub(r'^[*\-•]\s+', '', stripped)
            _add_formatted_runs(p, f"\u2022 {bullet_content}", font_size=Pt(9.5))

        else:
            # Caso especial: Línea inmediatamente posterior al nombre
            if passed_name and not has_inserted_target_role:
                if is_contact_line(stripped):
                    # No había subtítulo en markdown; si pasaron target_role, insertarlo antes del contacto
                    if target_role:
                        add_target_role_subtitle(doc, target_role)
                        has_inserted_target_role = True
                    render_contact_paragraph(doc, stripped)
                    continue
                else:
                    # La línea es el título/puesto objetivo
                    role_to_display = target_role or stripped
                    add_target_role_subtitle(doc, role_to_display)
                    has_inserted_target_role = True
                    continue

            # Contact line en cualquier otra posición
            if is_contact_line(stripped):
                render_contact_paragraph(doc, stripped)
                continue

            # Párrafo normal
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.35
            _add_formatted_runs(p, stripped, font_size=Pt(9.5))

    # Micro-Indexación Técnica Stealth al pie (Apéndice ATS invisible: 1.0pt, blanco, <w:vanish/>, delta-only)
    if keywords:
        if isinstance(keywords, list):
            kw_list = [(k.get("nombre") if isinstance(k, dict) else str(k)).strip() for k in keywords if k]
        else:
            kw_list = [k.strip() for k in str(keywords).split(",") if k.strip()]
        cv_lower = cv_markdown.lower()
        kw_list = [normalizar_guiones(kw) for kw in kw_list]
        delta_keywords = [kw for kw in kw_list if kw and kw.lower() not in cv_lower]
        if delta_keywords:
            p_ats = doc.add_paragraph()
            p_ats.paragraph_format.space_before = Pt(0)
            p_ats.paragraph_format.space_after = Pt(0)
            p_ats.paragraph_format.line_spacing = 1.0
            run_ats = p_ats.add_run(", ".join(delta_keywords))
            run_ats.font.name = "Arial"
            run_ats.font.size = Pt(1.0)
            run_ats.font.color.rgb = RGBColor(255, 255, 255)
            rPr = run_ats._r.get_or_add_rPr()
            rPr.append(OxmlElement('w:vanish'))

    filename = output_filename or generate_cv_filename(target_role=target_role, candidate_name=c_name)
    output_path = EXPORTS_DIR / filename
    doc.save(str(output_path))
    return output_path
