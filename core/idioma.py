"""Idioma del CV: inglés si la oferta está escrita en inglés o pide el CV en inglés; si no, español.

Las secciones del CV se reconocen en los dos idiomas (`seccion_canonica`), así el saneado, la
poda de largo, el Auditor y la exportación funcionan igual con «## EXPERIENCIA» que con
«## EXPERIENCE». El idioma de un CV ya redactado se deduce de sus encabezados (`idioma_de_cv`).
"""
import re
import unicodedata
from typing import Dict

ES, EN = "es", "en"

# Palabras frecuentes que no existen en el otro idioma
_FRECUENTES = {
    EN: set("the and to of with for you we our your will are is in on as be an this that who have "
            "experience team role skills work years".split()),
    ES: set("de la el en y con para los las del que por un una se sus al es tu nuestro nuestra "
            "experiencia equipo años conocimientos".split()),
}
MIN_PALABRAS_INGLES = 8
MIN_PALABRAS_INGLES_SOLO = 4

# Pedido explícito: «enviá tu CV en inglés», «CV (en idioma inglés)», «resume in English», «English CV»
_RE_PIDE_INGLES = re.compile(
    r"\b(?:cv|c\.v\.|curr[ií]cul(?:um|o)s?|hoja de vida|resume|résumé)\b[^.\n]{0,40}?"
    r"\b(?:en\s+(?:idioma\s+)?ingl[eé]s|in\s+english)\b"
    r"|\benglish\s+(?:cv|resume|résumé)\b",
    re.IGNORECASE,
)

SECCIONES = ("PERFIL", "EXPERIENCIA", "HABILIDADES", "EDUCACION", "IDIOMAS")
TITULOS: Dict[str, Dict[str, str]] = {
    ES: {"PERFIL": "PERFIL", "EXPERIENCIA": "EXPERIENCIA", "HABILIDADES": "HABILIDADES TÉCNICAS",
         "EDUCACION": "EDUCACIÓN", "IDIOMAS": "IDIOMAS"},
    EN: {"PERFIL": "SUMMARY", "EXPERIENCIA": "EXPERIENCE", "HABILIDADES": "TECHNICAL SKILLS",
         "EDUCACION": "EDUCATION", "IDIOMAS": "LANGUAGES"},
}
_CLAVES_SECCION = (
    ("PERFIL", ("PERFIL", "RESUMEN", "SOBRE MI", "ACERCA DE", "SUMMARY", "PROFILE", "ABOUT")),
    ("EXPERIENCIA", ("EXPERIENCIA", "HISTORIAL", "TRAYECTORIA", "EXPERIENCE", "EMPLOYMENT", "WORK HISTORY")),
    ("HABILIDADES", ("HABILIDAD", "COMPETENCIA", "TECNOLOG", "SKILL")),
    ("EDUCACION", ("EDUCACI", "FORMACI", "ESTUDIO", "ACADEMIC", "EDUCATION")),
    ("IDIOMAS", ("IDIOMA", "LENGUAJE", "LANGUAGE")),
)
_PALABRAS_SECCION_EN = ("SUMMARY", "PROFILE", "EXPERIENCE", "SKILLS", "EDUCATION", "LANGUAGES")


def idioma_cv_de_oferta(texto: str) -> str:
    """`en` si la oferta está escrita en inglés o pide el CV en inglés; si no, `es`."""
    # NFKC: las letras «negrita» de Unicode de LinkedIn (𝗘𝘅𝗽𝗹𝗼𝗿𝗲) cuentan como letras (oferta 8201, 15/11)
    texto = unicodedata.normalize("NFKC", texto or "")
    if _RE_PIDE_INGLES.search(texto):
        return EN
    return EN if escrita_en_ingles(texto) else ES


def escrita_en_ingles(texto: str) -> bool:
    """Si el texto está escrito en inglés (pedir el CV en inglés no cuenta: eso es `idioma_cv_de_oferta`)."""
    palabras = re.findall(r"[a-záéíóúñü]+", unicodedata.normalize("NFKC", texto or "").lower())
    en = sum(p in _FRECUENTES[EN] for p in palabras)
    es = sum(p in _FRECUENTES[ES] for p in palabras)
    # Una oferta corta en inglés no llega al mínimo: alcanza con 4 sin ninguna castellana (oferta 1792, 16/11)
    if en >= MIN_PALABRAS_INGLES_SOLO and es == 0:
        return True
    return en >= MIN_PALABRAS_INGLES and en >= 2 * es


def _sin_tildes(texto: str) -> str:
    return texto.upper().translate(str.maketrans("ÁÉÍÓÚÜ", "AEIOUU"))


def seccion_canonica(titulo: str) -> str:
    """Categoría de un encabezado de sección, en cualquiera de los dos idiomas (`OTHER` si no es una)."""
    norm = _sin_tildes(titulo.replace("#", " ").strip())
    for seccion, claves in _CLAVES_SECCION:
        if any(k in norm for k in claves):
            return seccion
    return "OTHER"


def idioma_de_cv(cv_md: str) -> str:
    """`en` si los encabezados `##` del CV están en inglés."""
    titulos = [_sin_tildes(l[2:]) for l in (cv_md or "").splitlines() if l.startswith("## ")]
    en = sum(any(p in t for p in _PALABRAS_SECCION_EN) for t in titulos)
    return EN if en and en * 2 >= len(titulos) else ES


def idioma_de_texto(texto: str) -> str:
    """Idioma de un CV (por sus encabezados) o de un texto corrido como el pitch (por sus palabras)."""
    if any(l.startswith("## ") for l in (texto or "").splitlines()):
        return idioma_de_cv(texto)
    palabras = re.findall(r"[a-záéíóúñü]+", unicodedata.normalize("NFKC", texto or "").lower())
    en = sum(p in _FRECUENTES[EN] for p in palabras)
    es = sum(p in _FRECUENTES[ES] for p in palabras)
    return EN if en > es else ES
