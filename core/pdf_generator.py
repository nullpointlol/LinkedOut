"""Compilador de CV Markdown a PDF (A4 Estándar ISO 216 con geometría corporativa)."""
import os
import re
import shutil
import html
import tempfile
import subprocess
import time
from pathlib import Path
from typing import Optional, List

from core.docx_generator import summarize_role_slug, get_candidate_name, clean_text, is_contact_line
from core.text_normalization import normalizar_guiones
from core.paths import DATA_DIR
from core.idioma import idioma_de_cv

BASE_DIR = Path(__file__).resolve().parent.parent
EXPORTS_DIR = DATA_DIR / "exports"


def find_browser_executable() -> Optional[str]:
    """Detecta Microsoft Edge, Google Chrome o Brave (instalación estándar de Windows o PATH) para imprimir el PDF."""
    candidatos = []
    for carpeta in (("Microsoft", "Edge", "Application", "msedge.exe"),
                    ("Google", "Chrome", "Application", "chrome.exe"),
                    ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")):
        for var in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
            base = os.environ.get(var)
            if base:
                candidatos.append(Path(base).joinpath(*carpeta))
    for candidato in candidatos:
        if candidato.is_file():
            return str(candidato)
    for nombre in ("msedge", "chrome", "brave"):
        encontrado = shutil.which(nombre)
        if encontrado:
            return encontrado
    return None
# El lanzador de Edge devuelve 0 antes de escribir el PDF (sobre todo con otro Edge abierto): se espera el archivo
# hasta este plazo; sin esperar, se caía a ReportLab y después Edge pisaba el archivo
ESPERA_PDF_EDGE_S = 20.0
INTERVALO_PDF_EDGE_S = 0.15


def _esperar_pdf(ruta: Path, plazo_s: float = ESPERA_PDF_EDGE_S) -> bool:
    """True cuando el PDF existe y su tamaño no cambia entre dos lecturas (Edge terminó de escribirlo)."""
    limite = time.monotonic() + plazo_s
    previo = -1
    while time.monotonic() < limite:
        tam = ruta.stat().st_size if ruta.exists() else 0
        if tam > 0 and tam == previo:
            return True
        previo = tam
        time.sleep(INTERVALO_PDF_EDGE_S)
    return False

# Paleta cromática corporativa (fiel al manual y al template DOCX)
COLOR_NAVY_HEX = "#1A365D"
COLOR_TEAL_HEX = "#1A7580"
COLOR_SLATE_HEX = "#4A5568"
COLOR_BODY_HEX = "#222222"
COLOR_BORDER_HEX = "#E2E8F0"


def generate_cv_pdf_filename(
    target_role: Optional[str] = None,
    candidate_name: Optional[str] = None,
    markdown_text: Optional[str] = None
) -> str:
    """Genera el nombre formal del archivo PDF con patrón 'CV - <Candidate Name> - <Target Role Slug>.pdf'."""
    raw_name = candidate_name or (get_candidate_name(markdown_text) if markdown_text else get_candidate_name())
    name = clean_text(raw_name) if raw_name else "Candidato"
    if not (target_role or "").strip():
        # CV general sin titular: el archivo no inventa un puesto («… - PROFESIONAL.pdf»)
        return f"CV - {name}.pdf"
    return f"CV - {name} - {summarize_role_slug(target_role)}.pdf"


_RE_WA_ME = re.compile(r"(?:https?://)?wa\.me/\+?(\d+)", re.IGNORECASE)
_RE_TELEFONO = re.compile(r"\+?\d[\d \-()]{6,}\d")


def _contacto_con_whatsapp(linea: str) -> str:
    """El teléfono de la línea de contacto pasa a ser el link wa.me (`[tel](https://wa.me/n)`) y el
    link wa.me suelto, si lo había, se quita: el número se ve una sola vez y es clickeable."""
    items = [it.strip() for it in linea.split("|") if it.strip()]
    wa = next((m for m in (_RE_WA_ME.search(it) for it in items) if m), None)
    sin_wa = [it for it in items if not _RE_WA_ME.search(it)]
    i_tel = next((i for i, it in enumerate(sin_wa) if "@" not in it and "linkedin" not in it.lower()
                  and _RE_TELEFONO.fullmatch(it)), None)
    if i_tel is not None:
        tel = sin_wa[i_tel]
        digitos = wa.group(1) if wa else re.sub(r"\D", "", tel)
        sin_wa[i_tel] = f"[{tel}](https://wa.me/{digitos})"
    elif wa:
        sin_wa.insert(min(1, len(sin_wa)), f"[+{wa.group(1)}](https://wa.me/{wa.group(1)})")
    # El perfil sin https:// (como suele escribirlo el Redactor) también queda clickeable
    sin_wa = [f"[{it}](https://{it})" if re.fullmatch(r"(?:www\.)?linkedin\.com/in/\S+", it, re.I) else it
              for it in sin_wa]
    return " | ".join(sin_wa)


def _format_inline_markdown(text: str) -> str:
    """Convierte negritas, cursivas y enlaces en HTML seguro."""
    # Escapar entidades HTML base excepto etiquetas que nosotros insertemos
    out = html.escape(text)

    # Negrita: **texto**
    out = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', out)
    # Cursiva: *texto* o _texto_
    out = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', out)

    # Enlaces markdown [texto](url)
    def _link_sub(m):
        label, url = m.group(1), m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
    out = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', _link_sub, out)

    # Enlaces planos https://...
    def _raw_url_sub(m):
        url = m.group(0)
        # Quitar puntuación al final si la hay
        trail = ""
        if url.endswith(('.', ',', ';')):
            trail = url[-1]
            url = url[:-1]
        display = url.replace("https://", "").replace("http://", "").replace("www.", "")
        if len(display) > 40:
            display = display[:37] + "..."
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{display}</a>{trail}'
    out = re.sub(r'(?<!href=")(https?://[^\s<]+)', _raw_url_sub, out)

    # Mailto
    out = re.sub(
        r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)',
        r'<a href="mailto:\1">\1</a>',
        out
    )

    return out


def puesto_del_cv(markdown_text: str) -> str:
    """Línea de puesto objetivo del CV (la que va bajo el nombre); "" si no hay."""
    nombre = False
    for line in (l.strip() for l in (markdown_text or "").splitlines()):
        if not line:
            continue
        if line.startswith("# ") and not nombre:
            nombre = True
            continue
        if nombre and not line.startswith("#") and "|" not in line and "@" not in line:
            return line.replace("**", "").strip()
        return ""
    return ""


def render_cv_html(
    markdown_text: str,
    target_role: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    candidate_name: Optional[str] = None,
) -> str:
    """Convierte CV Markdown en HTML limpio y optimizado para impresión A4 executive."""
    markdown_text = normalizar_guiones(markdown_text)
    target_role = normalizar_guiones(target_role) if target_role else target_role
    lines = [line.strip() for line in markdown_text.strip().splitlines()]
    
    parsed_candidate_name = ""
    detected_target_role = ""
    contact_info = ""
    body_lines = []

    # Parsear cabecera inicial
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line:
            idx += 1
            continue
        if line.startswith("# ") and not parsed_candidate_name:
            parsed_candidate_name = line.replace("# ", "").strip()
            idx += 1
            continue
        if parsed_candidate_name and not detected_target_role and not line.startswith("#") and "|" not in line and not is_contact_line(line):
            detected_target_role = line.strip()
            idx += 1
            continue
        if parsed_candidate_name and ("|" in line or is_contact_line(line)) and not contact_info and not line.startswith("#"):
            contact_info = line.strip()
            idx += 1
            continue
        # Resto de líneas forman el cuerpo
        body_lines = lines[idx:]
        break

    final_candidate_name = candidate_name or parsed_candidate_name or get_candidate_name(markdown_text)
    candidate_name = final_candidate_name

    final_target_role = target_role or detected_target_role

    # Renderizar cuerpo con secciones
    sections_html = []
    current_section = None
    in_exp_block = False
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            sections_html.append("  </ul>")
            in_list = False

    def close_exp_block():
        nonlocal in_exp_block
        close_list()
        if in_exp_block:
            sections_html.append("</div><!-- /experience-block -->")
            in_exp_block = False

    for line in body_lines:
        if not line:
            continue

        # Encabezado Nivel 2 (Secciones principales)
        if line.startswith("## "):
            close_exp_block()
            current_section = line.replace("## ", "").strip().upper()
            sections_html.append(f'<h2 class="section-title">{html.escape(current_section)}</h2>')
            continue

        # Encabezado Nivel 3 (Puesto / Empresa / Período o Subsección)
        if line.startswith("### "):
            close_exp_block()
            in_exp_block = True
            subheading = line.replace("### ", "").strip()
            # Si tiene separadores '|', formatear con flex
            if "|" in subheading:
                parts = [p.strip() for p in subheading.split("|")]
                title = parts[0]
                meta = " | ".join(parts[1:])
                sections_html.append('<div class="experience-block">')
                sections_html.append(
                    f'  <div class="job-header">'
                    f'    <span class="job-title">{_format_inline_markdown(title)}</span>'
                    f'    <span class="job-meta">{_format_inline_markdown(meta)}</span>'
                    f'  </div>'
                )
            else:
                sections_html.append('<div class="experience-block">')
                sections_html.append(f'  <div class="job-header"><span class="job-title">{_format_inline_markdown(subheading)}</span></div>')
            continue

        # Viñetas de lista
        if line.startswith(("\u2022", "-", "*", "\u00b7")):
            bullet_text = re.sub(r"^[\u2022\-\*\u00b7]\s*", "", line).strip()
            if not in_list:
                sections_html.append('  <ul class="cv-bullets">')
                in_list = True
            sections_html.append(f'    <li>{_format_inline_markdown(bullet_text)}</li>')
            continue

        # Líneas normales de texto / párrafos
        close_list()
        sections_html.append(f'  <p class="cv-paragraph">{_format_inline_markdown(line)}</p>')

    close_exp_block()

    body_content_html = "\n".join(sections_html)

    # Formatear la línea de contacto si existe
    contact_html = ""
    if contact_info:
        formatted_contact = _format_inline_markdown(_contacto_con_whatsapp(contact_info))
        contact_html = f'<div class="contact-line">{formatted_contact}</div>'

    # Formatear Puesto Objetivo
    role_html = ""
    if final_target_role:
        role_html = f'<div class="target-role">{html.escape(final_target_role.upper())}</div>'

    if isinstance(keywords, list):
        kw_clean_list = [(k.get("nombre") if isinstance(k, dict) else str(k)).strip() for k in keywords if k]
    else:
        kw_clean_list = [k.strip() for k in str(keywords).split(",") if k.strip()] if keywords else []
    kw_clean_list = [normalizar_guiones(kw) for kw in kw_clean_list]
    kw_str = ", ".join(kw_clean_list)

    # Filtrar palabras clave delta (ausentes en el cuerpo del CV) para el apéndice stealth invisible
    cv_lower = markdown_text.lower()
    delta_keywords = [kw for kw in kw_clean_list if kw and kw.lower() not in cv_lower]
    delta_kw_str = ", ".join(delta_keywords)

    html_doc = f"""<!DOCTYPE html>
<html lang="{idioma_de_cv(markdown_text)}">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(candidate_name)} - CV{f" - {html.escape(final_target_role)}" if final_target_role else ""}</title>
  <meta name="author" content="{html.escape(candidate_name)}">
  <meta name="subject" content="Currículum Vitae para la posición de {html.escape(final_target_role or 'Profesional')}">
  {f'<meta name="keywords" content="{html.escape(kw_str)}">' if kw_str else ''}
  <style>
    @page {{
      size: A4 portrait;
      margin: 15mm;
    }}
    @media print {{
      body {{
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }}
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: Arial, Helvetica, sans-serif;
      color: {COLOR_BODY_HEX};
      background-color: #ffffff;
      font-size: 9.5pt;
      line-height: 1.35;
      margin: 0;
      padding: 0;
    }}
    .cv-container {{
      width: 100%;
      margin: 0 auto;
    }}
    h1.candidate-name {{
      color: {COLOR_NAVY_HEX};
      font-size: 20pt;
      font-weight: 700;
      letter-spacing: 0.5px;
      margin: 0 0 2px 0;
      line-height: 1.1;
      text-transform: uppercase;
    }}
    .target-role {{
      color: {COLOR_TEAL_HEX};
      font-size: 10.5pt;
      font-weight: 700;
      letter-spacing: 0.8px;
      margin: 2px 0 6px 0;
      text-transform: uppercase;
    }}
    .contact-line {{
      font-size: 8.5pt;
      color: {COLOR_SLATE_HEX};
      margin: 0 0 10px 0;
      padding-bottom: 5px;
      border-bottom: 1px solid {COLOR_BORDER_HEX};
      line-height: 1.35;
    }}
    .contact-line a {{
      color: {COLOR_NAVY_HEX};
      text-decoration: none;
    }}
    h2.section-title {{
      color: {COLOR_NAVY_HEX};
      font-size: 11pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1.5px solid {COLOR_TEAL_HEX};
      padding-bottom: 2px;
      margin: 12px 0 6px 0;
    }}
    .experience-block {{
      page-break-inside: avoid;
      break-inside: avoid;
      margin-bottom: 7px;
    }}
    .job-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-size: 9.5pt;
      margin-bottom: 2px;
    }}
    .job-title {{
      font-weight: 700;
      color: {COLOR_NAVY_HEX};
    }}
    .job-meta {{
      font-weight: 600;
      color: {COLOR_SLATE_HEX};
      font-size: 8.5pt;
    }}
    ul.cv-bullets {{
      margin: 2px 0 4px 0;
      padding-left: 15px;
    }}
    ul.cv-bullets li {{
      font-size: 9.5pt;
      line-height: 1.35;
      margin-bottom: 2.5px;
      color: {COLOR_BODY_HEX};
    }}
    p.cv-paragraph {{
      margin: 3px 0;
      font-size: 9.5pt;
      line-height: 1.35;
      color: {COLOR_BODY_HEX};
    }}
    strong {{
      color: #111111;
      font-weight: 700;
    }}
    .ats-appendix {{
      font-size: 0.5pt;
      color: #FFFFFF;
      line-height: 1.0;
      word-break: break-word;
      margin: 0;
      padding: 0;
    }}
  </style>
</head>
<body>
  <div class="cv-container">
    <h1 class="candidate-name">{html.escape(candidate_name)}</h1>
    {role_html}
    {contact_html}
    {body_content_html}
    {f'<div class="ats-appendix">{html.escape(delta_kw_str)}</div>' if delta_kw_str else ''}
  </div>
</body>
</html>"""
    return html_doc


def _compile_with_reportlab(
    markdown_text: str,
    target_role: Optional[str],
    output_path: Path,
    keywords: Optional[List[str]] = None,
    candidate_name: Optional[str] = None
) -> Path:
    """Compilador de contingencia basado en ReportLab si no hay navegador headless disponible."""
    markdown_text = normalizar_guiones(markdown_text)
    target_role = normalizar_guiones(target_role) if target_role else target_role
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    cand_name = candidate_name or get_candidate_name(markdown_text)
    candidate_name = cand_name
    if isinstance(keywords, list):
        kw_clean_list = [(k.get("nombre") if isinstance(k, dict) else str(k)).strip() for k in keywords if k]
    else:
        kw_clean_list = [k.strip() for k in str(keywords).split(",") if k.strip()] if keywords else []
    kw_clean_list = [normalizar_guiones(kw) for kw in kw_clean_list]
    kw_str = ", ".join(kw_clean_list) if kw_clean_list else None

    margin_pt = 15 * 2.83465  # 15mm en puntos tipográficos (~42.5 pt)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=margin_pt,
        rightMargin=margin_pt,
        topMargin=margin_pt,
        bottomMargin=margin_pt,
        title=f"CV - {candidate_name} - {target_role or 'Profesional'}",
        author=candidate_name,
        subject=f"Currículum Vitae para la posición de {target_role or 'Profesional'}",
        keywords=kw_str,
    )

    styles = getSampleStyleSheet()

    navy_col = colors.HexColor(COLOR_NAVY_HEX)
    teal_col = colors.HexColor(COLOR_TEAL_HEX)
    slate_col = colors.HexColor(COLOR_SLATE_HEX)
    body_col = colors.HexColor(COLOR_BODY_HEX)

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=navy_col,
        spaceAfter=2,
    )
    role_style = ParagraphStyle(
        'DocRole',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=teal_col,
        spaceAfter=4,
    )
    contact_style = ParagraphStyle(
        'DocContact',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=slate_col,
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        'DocH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=navy_col,
        spaceBefore=10,
        spaceAfter=4,
    )
    h3_style = ParagraphStyle(
        'DocH3',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=12,
        textColor=navy_col,
        spaceBefore=4,
        spaceAfter=2,
    )
    bullet_style = ParagraphStyle(
        'DocBullet',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=body_col,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=body_col,
        spaceAfter=4,
    )

    story = []
    lines = [l.strip() for l in markdown_text.strip().splitlines() if l.strip()]

    candidate_name = cand_name
    idx = 0
    if lines and lines[0].startswith("# "):
        idx = 1

    story.append(Paragraph(candidate_name, title_style))

    original_role = ""
    if idx < len(lines) and not lines[idx].startswith("#") and "|" not in lines[idx] and not is_contact_line(lines[idx]):
        original_role = lines[idx]
        idx += 1

    final_role = target_role or original_role
    if final_role:
        story.append(Paragraph(final_role.upper(), role_style))

    if idx < len(lines) and ("|" in lines[idx] or is_contact_line(lines[idx])) and not lines[idx].startswith("#"):
        contacto = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>',
                          html.escape(_contacto_con_whatsapp(lines[idx]), quote=False))
        story.append(Paragraph(contacto, contact_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(COLOR_BORDER_HEX), spaceAfter=6))
        idx += 1

    for line in lines[idx:]:
        if line.startswith("## "):
            sec_title = line.replace("## ", "").strip().upper()
            story.append(Paragraph(sec_title, h2_style))
            story.append(HRFlowable(width="100%", thickness=1, color=teal_col, spaceAfter=4))
        elif line.startswith("### "):
            subheading = line.replace("### ", "").strip()
            story.append(Paragraph(subheading, h3_style))
        elif line.startswith(("\u2022", "-", "*", "\u00b7")):
            bullet_clean = re.sub(r"^[\u2022\-\*\u00b7]\s*", "", line).strip()
            # Convertir **negrita** a <b>negrita</b> para ReportLab
            clean_markup = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', bullet_clean)
            story.append(Paragraph(f"&bull; {clean_markup}", bullet_style))
        else:
            clean_markup = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
            story.append(Paragraph(clean_markup, body_style))

    cv_lower = markdown_text.lower()
    delta_keywords = [kw for kw in kw_clean_list if kw and kw.lower() not in cv_lower]
    delta_kw_str = ", ".join(delta_keywords)
    if delta_kw_str:
        ats_style = ParagraphStyle(
            'DocATSAppendix',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=0.5,
            leading=1.0,
            textColor=colors.HexColor("#FFFFFF"),
            spaceBefore=0,
            spaceAfter=0,
        )
        story.append(Paragraph(html.escape(delta_kw_str), ats_style))

    doc.build(story)
    return output_path


def compile_markdown_to_pdf(
    markdown_text: str,
    target_role: Optional[str] = None,
    output_filename: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    candidate_name: Optional[str] = None
) -> Path:
    """Compila el CV Markdown a formato PDF estándar A4.
    
    Utiliza el navegador headless (Edge/Chrome) para pixel-perfect layout A4.
    Si el navegador no está disponible o falla, aplica fallback automático a ReportLab.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    cand_name = candidate_name or get_candidate_name(markdown_text)

    if not output_filename:
        output_filename = generate_cv_pdf_filename(target_role, candidate_name=cand_name)

    output_path = EXPORTS_DIR / output_filename

    # 1. Intentar renderizado headless mediante navegador (Edge / Chrome)
    browser_exe = find_browser_executable()
    if browser_exe:
        html_content = render_cv_html(markdown_text, target_role=target_role, keywords=keywords, candidate_name=cand_name)
        temp_html = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
                f.write(html_content)
                temp_html = Path(f.name)

            cmd = [
                browser_exe,
                "--headless",
                "--disable-gpu",
                "--run-all-compositor-stages-before-draw",
                "--no-pdf-header-footer",
                f"--print-to-pdf={output_path.resolve()}",
                temp_html.resolve().as_uri(),
            ]

            output_path.unlink(missing_ok=True)  # un PDF anterior no cuenta como el de esta compilación
            res = subprocess.run(cmd, capture_output=True, timeout=20)
            if res.returncode == 0 and _esperar_pdf(output_path):
                return output_path
        except Exception:
            # Fall through to reportlab fallback
            pass
        finally:
            if temp_html and temp_html.exists():
                try:
                    temp_html.unlink()
                except OSError:
                    pass

    # 2. Contingencia robusta con ReportLab
    return _compile_with_reportlab(markdown_text, target_role, output_path, keywords=keywords, candidate_name=cand_name)
