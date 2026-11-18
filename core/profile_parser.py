"""Módulo agnóstico de extracción y parseo de perfil de candidato."""
import re
from datetime import date
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATO_DIR = DATA_DIR / "candidato"

RESERVED_SECTIONS = {
    "experiencia",
    "experiencia laboral",
    "experiencia profesional",
    "educacion",
    "educación",
    "idiomas",
    "perfil",
    "perfil profesional",
    "habilidades",
    "habilidades técnicas",
    "habilidades tecnicas",
    "habilidades interpersonales",
    "habilidades blandas",
    "competencias",
    "proyectos",
    "referencias",
    "certificaciones",
    "contacto",
}


def _labor_lines(exp_text: str):
    """Omite secciones no laborales, incluidos sus subtítulos anidados."""
    excluded_level = None
    for line in exp_text.splitlines():
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        title = (heading.group(2) if heading else line.strip()).casefold().rstrip(":")
        non_work = (
            title in RESERVED_SECTIONS - {
                "experiencia", "experiencia laboral", "experiencia profesional"}
            or re.match(r"^(?:educaci[oó]n|education|formaci[oó]n|academic|studies|"
                        r"certifications|training)(?:\b|$)", title)
        )
        if heading and excluded_level is not None and len(heading.group(1)) <= excluded_level:
            excluded_level = None
        if non_work:
            if excluded_level is None:
                excluded_level = len(heading.group(1)) if heading else 1
            continue
        if not heading and title in {"experiencia", "experiencia laboral",
                                     "experiencia profesional", "experience", "work experience"}:
            excluded_level = None
        if excluded_level is None:
            yield line


def calcular_anios_experiencia(exp_text: str, hoy: date | None = None) -> int | None:
    """Años de experiencia SOLO desde las fechas del historial (decisión del 08/11: una frase suelta
    nunca da la cifra, porque no se puede saber con certeza de quién es); meses y luego años (Q2).

    Los extremos son límites de mes: 01/2018 a 01/2025 son 84 meses.
    Sin meses, usa los extremos anuales más antiguos/recientes del historial.
    Fechas inválidas, futuras o invertidas se descartan, sin estimar duración.
    """
    hoy = hoy if hoy is not None else date.today()
    mes_actual = hoy.year * 12 + hoy.month - 1
    fecha = r"(?:\d{2}/)?\d{4}"
    rango = re.compile(
        rf"(?<![\d/])({fecha})\s*(?:[-–—]|\ba\b)\s*"
        rf"({fecha}|presente|actualidad|present|current)(?![\w/])",
        re.IGNORECASE,
    )
    intervalos = []
    anuales = []
    mixtos = False
    for line in _labor_lines(exp_text):
        for match in rango.finditer(line):
            extremos = []
            incompleto = False
            invalido = False
            for valor in match.groups():
                if valor.casefold() in {"presente", "actualidad", "present", "current"}:
                    extremos.append(mes_actual)
                elif "/" not in valor:
                    incompleto = True
                else:
                    mes, anio = map(int, valor.split("/"))
                    if not 1 <= mes <= 12 or anio < 1:
                        invalido = True
                    extremos.append(anio * 12 + mes - 1)
            if invalido or any(mes > mes_actual for mes in extremos):
                continue
            if incompleto:
                desde, hasta = match.groups()
                if "/" in desde or "/" in hasta:
                    mixtos = True
                    continue
                inicio_anio = int(desde)
                fin_anio = hoy.year if not hasta.isdigit() else int(hasta)
                if 1 <= inicio_anio <= fin_anio <= hoy.year:
                    anuales.append((inicio_anio, fin_anio))
                continue
            inicio, fin = extremos
            if inicio <= fin:
                intervalos.append((inicio, fin))
    if mixtos or (intervalos and anuales):
        # No fabricar precisión mensual ni presentar un total parcial.
        return None
    if not intervalos:
        if not anuales:
            return None
        # Años contiguos (2010-2013 y 2013-2016, o 2016 y 2017) se unen; un hueco real
        # (un empleo viejo y otro años después) no suma (creador, 09/11).
        anuales.sort()
        total, (inicio, fin) = 0, anuales[0]
        for siguiente_inicio, siguiente_fin in anuales[1:]:
            if siguiente_inicio <= fin + 1:
                fin = max(fin, siguiente_fin)
            else:
                total += fin - inicio
                inicio, fin = siguiente_inicio, siguiente_fin
        return total + fin - inicio
    intervalos.sort()
    inicio, fin = intervalos[0]
    meses = 0
    for siguiente_inicio, siguiente_fin in intervalos[1:]:
        if siguiente_inicio <= fin:
            fin = max(fin, siguiente_fin)
        else:
            meses += fin - inicio
            inicio, fin = siguiente_inicio, siguiente_fin
    return (meses + fin - inicio) // 12


_RE_PUESTO = re.compile(r"^\s*(?:puesto|rol|titular)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_RE_RANGO_ROL = re.compile(
    r"((?:\d{2}/)?\d{4})\s*(?:[-–—]|\ba\b)\s*((?:\d{2}/)?\d{4}|presente|actualidad|present|current)",
    re.IGNORECASE,
)


def _extract_puesto(per_text: str) -> str:
    """Titular declarado en personales.txt («Puesto: <titular>»)."""
    match = _RE_PUESTO.search(per_text or "")
    return match.group(1).strip() if match else ""


def parse_roles(exp_text: str) -> List[Dict[str, Any]]:
    """Roles del historial en orden: `## Empresa` + `### Rol | AAAA - AAAA` (varios por empresa).

    Cada rol: {empresa, rol, desde, hasta, vinetas}. Un `###` sin fechas con el mismo título que el
    rol anterior de la empresa continúa ese rol; las secciones reservadas (habilidades, educación…)
    no son roles.
    """
    roles: List[Dict[str, Any]] = []
    empresa = ""
    for line in _labor_lines(exp_text or ""):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            candidatas = _extract_companies(stripped)
            empresa = candidatas[0] if candidatas else ""
            continue
        if not empresa:
            continue
        if stripped.startswith("### "):
            contenido = stripped[4:].strip()
            titulo = contenido.split("|")[0].strip()
            rango = _RE_RANGO_ROL.search(contenido)
            previo = roles[-1] if roles and roles[-1]["empresa"] == empresa else None
            if not rango and previo and previo["rol"].casefold() == titulo.casefold():
                continue
            if titulo and titulo.casefold() != empresa.casefold():
                roles.append({
                    "empresa": empresa,
                    "rol": titulo,
                    "desde": rango.group(1) if rango else "",
                    "hasta": rango.group(2) if rango else "",
                    "vinetas": [],
                })
            continue
        if re.match(r"^[•*\-]\s+", stripped) and roles and roles[-1]["empresa"] == empresa:
            roles[-1]["vinetas"].append(re.sub(r"^[•*\-]\s+", "", stripped))
    return roles


def get_candidate_roles(exp_text: Optional[str] = None, candidato_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Roles del candidato (ver `parse_roles`), del texto dado o de `experiencia.txt`."""
    if exp_text is None:
        base = Path(candidato_dir) if candidato_dir else DEFAULT_CANDIDATO_DIR
        archivo = base / "experiencia.txt"
        exp_text = archivo.read_text(encoding="utf-8") if archivo.exists() else ""
    return parse_roles(exp_text)


def secciones_de_habilidades(exp_text: str) -> Dict[str, List[str]]:
    """Viñetas de `## HABILIDADES TÉCNICAS` e `## HABILIDADES INTERPERSONALES` de la base."""
    salida: Dict[str, List[str]] = {"tecnicas": [], "interpersonales": []}
    actual = None
    for line in (exp_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            titulo = stripped[3:].strip().casefold()
            actual = ("interpersonales" if "interpersonal" in titulo or "blanda" in titulo
                      else "tecnicas" if titulo.startswith("habilidades") or titulo.startswith("competencias")
                      else None)
            continue
        if actual and re.match(r"^[•*\-]\s+", stripped):
            salida[actual].append(re.sub(r"^[•*\-]\s+", "", stripped))
    return salida


def _extract_role(exp_text: str, empresas: List[str]) -> str:
    """Toma el primer título explícito del historial, nunca una empresa."""
    company_names = {empresa.casefold() for empresa in empresas}
    for line in _labor_lines(exp_text):
        match = re.match(r"^\s*###\s+([^|]+)\|", line)
        if match:
            rol = match.group(1).strip()
            if rol and rol.casefold() not in company_names | RESERVED_SECTIONS:
                return rol
    return ""


def parse_candidate_profile(exp_text: str, per_text: str) -> Dict[str, Any]:
    """
    Extrae dinámicamente del perfil de cualquier candidato:
    - nombre: str (de la 1ª línea de per_text o del primer # H1)
    - contacto: str (línea con teléfono / email / ciudad)
    - empresas: List[str] (parseadas dinámicamente de '## [Nombre Empresa]' en exp_text)
    - educacion: List[str]
    - idiomas: List[str]
    - rol: str (título explícito del historial, vacío si falta)
    - anios_experiencia: int | None (declaración personal o duración del historial)
    """
    nombre = _extract_name(per_text, exp_text)
    contacto = _extract_contact(per_text)
    empresas = _extract_companies(exp_text)
    educacion = _extract_section_items(per_text, r"(?:##?\s*EDUCACI[ÓO]N|EDUCACI[ÓO]N)")
    idiomas = _extract_section_items(per_text, r"(?:##?\s*IDIOMAS|IDIOMAS)")

    return {
        "nombre": nombre,
        "contacto": contacto,
        "empresas": empresas,
        "educacion": educacion,
        "idiomas": idiomas,
        # El titular declarado manda: el primer `###` puede ser un rol freelance que no lo representa
        "rol": _extract_puesto(per_text) or _extract_role(exp_text, empresas),
        "anios_experiencia": calcular_anios_experiencia(exp_text),
        "roles": parse_roles(exp_text),
    }


def _extract_name(per_text: str, exp_text: str) -> str:
    """Extrae el nombre del candidato desde per_text o exp_text."""
    if per_text:
        lines = [line.strip() for line in per_text.splitlines() if line.strip()]
        for line in lines:
            cleaned = re.sub(r"^#+\s*", "", line).strip()
            if cleaned and cleaned.lower() not in RESERVED_SECTIONS:
                # Si no es una línea puramente de contacto
                if "@" not in cleaned and "wa.me" not in cleaned.lower():
                    return cleaned

    if exp_text:
        for line in exp_text.splitlines():
            line_s = line.strip()
            if line_s.startswith("# ") and not line_s.startswith("## "):
                cleaned = line_s[2:].strip()
                if cleaned and cleaned.lower() not in RESERVED_SECTIONS:
                    return cleaned

    return "Candidato"


def _extract_contact(per_text: str) -> str:
    """Extrae la línea de datos de contacto de per_text."""
    if not per_text:
        return ""
    lines = [line.strip() for line in per_text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if "@" in line or "wa.me" in lower or "linkedin.com" in lower or ("|" in line and any(c.isdigit() for c in line)):
            return line
    return ""


def _extract_companies(exp_text: str) -> List[str]:
    """Extrae dinámicamente los nombres de empresas de las cabeceras ## en exp_text."""
    if not exp_text:
        return []

    companies: List[str] = []
    seen = set()

    for line in exp_text.splitlines():
        line_s = line.strip()
        if line_s.startswith("## ") and not line_s.startswith("### "):
            raw_comp = line_s[3:].strip()
            # Limpiar posible tubería o rango de fechas, ej. ## Hospital | 2020 - 2023
            comp_candidate = raw_comp.split("|")[0].split("—")[0].split("–")[0].strip()
            # Limpiar fecha con guión simple ej. "Empresa - 2020"
            comp_candidate = re.split(r"\s+-\s+(?:\d{4}|presente|actualidad)", comp_candidate, flags=re.IGNORECASE)[0].strip()

            if comp_candidate and comp_candidate.lower() not in RESERVED_SECTIONS:
                if comp_candidate.lower() not in seen:
                    seen.add(comp_candidate.lower())
                    companies.append(comp_candidate)

    return companies


def _extract_section_items(text: str, section_header_regex: str) -> List[str]:
    """Extrae elementos de una sección (ej. Educación, Idiomas) delimitada por cabeceras."""
    if not text:
        return []

    pattern = rf"(?:^|\n)\s*{section_header_regex}\s*\n([\s\S]*?)(?=(?:\n\s*##?[A-ZÁÉÍÓÚ\s]+|\n\s*[A-ZÁÉÍÓÚ\s]{{4,}}\n|\Z))"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return []

    raw_block = match.group(1).strip()
    items = []
    for line in raw_block.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        cleaned = re.sub(r"^[•*–\-]\s*", "", cleaned).strip()
        if cleaned and cleaned.lower() not in RESERVED_SECTIONS:
            items.append(cleaned)

    return items


def load_candidate_profile(candidato_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Carga y parsea el perfil del candidato desde el directorio de datos."""
    base = Path(candidato_dir) if candidato_dir else DEFAULT_CANDIDATO_DIR
    exp_file = base / "experiencia.txt"
    per_file = base / "personales.txt"

    exp_text = exp_file.read_text(encoding="utf-8") if exp_file.exists() else ""
    per_text = per_file.read_text(encoding="utf-8") if per_file.exists() else ""

    from core.perfil_areas import anios_por_area_en_cache

    profile = parse_candidate_profile(exp_text, per_text)
    areas = anios_por_area_en_cache(base)
    # Sin resultados vigentes, conservar el formato del parser puro.
    # Los consumidores leen este dato opcional con .get("anios_por_area", []).
    if areas:
        profile["anios_por_area"] = areas
    return profile


_CAMPOS_VOZ = ("disponibilidad", "ubicacion", "disponibilidad_en", "ubicacion_en", "voz")


def load_voz_email(candidato_dir: Optional[Path] = None) -> Dict[str, str]:
    """Voz del candidato para los mails (`voz_email.md`): las frases fijas de disponibilidad y
    ubicación (en castellano y en inglés) y el texto modelo. Campos vacíos si falta el archivo."""
    base = Path(candidato_dir) if candidato_dir else DEFAULT_CANDIDATO_DIR
    archivo = base / "voz_email.md"
    voz = {campo: "" for campo in _CAMPOS_VOZ}
    if not archivo.exists():
        return voz
    seccion = ""
    lineas_voz: List[str] = []
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        titulo = re.match(r"^##\s*(.+?)\s*$", linea)
        if titulo:
            seccion = titulo.group(1).upper()
            continue
        if seccion == "VOZ":
            lineas_voz.append(linea)
            continue
        dato = re.match(r"^\s*(disponibilidad|ubicaci[oó]n)\s*:\s*(.+?)\s*$", linea, re.IGNORECASE)
        if dato and seccion.startswith("DATOS FIJOS"):
            campo = "disponibilidad" if dato.group(1).lower().startswith("disp") else "ubicacion"
            voz[campo + ("_en" if "(EN)" in seccion else "")] = dato.group(2)
    voz["voz"] = "\n".join(lineas_voz).strip()
    return voz


def get_candidate_companies(exp_text: Optional[str] = None, candidato_dir: Optional[Path] = None) -> List[str]:
    """Retorna la lista de nombres de empresas de la trayectoria del candidato."""
    if exp_text:
        return _extract_companies(exp_text)
    profile = load_candidate_profile(candidato_dir)
    return profile.get("empresas", [])
