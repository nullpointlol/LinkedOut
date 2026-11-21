"""Normalización determinista de prosa, sin modificar referencias ni contacto."""
import re


_GUIONES = str.maketrans({"\u2013": "-", "\u2014": "-"})
_TOKENS = re.compile(
    r"(?<=\]\()(?:\\.|[^()\\]|\([^()]*\))*(?=\))"
    r"|`+[^`\n]*`+"
    r"|(?:https?://|www\.)[^\s<>]+"
    r"|[^\s<>()[\],;]+@[^\s<>()[\],;]+"
    r"|\b[\w./-]+[–—][\w./–—-]+\b",
    re.IGNORECASE,
)


def transformar_fragmentos(texto, transformar, protegidos=(), *, ocultar_protegidos=False):
    """Transforma prosa entre tokens y literales, sin proteger toda la línea."""
    intervalos = []
    for match in _TOKENS.finditer(texto):
        token = match.group()
        identificador = (
            any(c.isalpha() for c in token)
            and (any(c.isdigit() for c in token) or any(c in token for c in "_/."))
            and not re.fullmatch(r"(?:\d{2}/)?\d{4}[–—](?:Presente|Actualidad|Present|Current)\.?", token, re.I)
        )
        if (texto[max(0, match.start() - 2):match.start()] == "](" or token.startswith("`")
                or "@" in token or re.match(r"(?:https?://|www\.)", token, re.I) or identificador):
            intervalos.append(match.span())
    for literal in protegidos:
        if literal:
            intervalos.extend(m.span() for m in re.finditer(re.escape(literal), texto))
    partes = []
    inicio = 0
    for desde, hasta in sorted(intervalos):
        if desde > inicio:
            partes.append(transformar(texto[inicio:desde]))
        if hasta > inicio:
            partes.append(" " if ocultar_protegidos else texto[max(inicio, desde):hasta])
            inicio = hasta
    partes.append(transformar(texto[inicio:]))
    return "".join(partes)


def normalizar_guiones(texto: str, protegidos=()) -> str:
    """Pasa rayas de prosa/rangos a '-', conservando espacios y tokens de Q1.

    En CV Markdown, nombre, encabezados de sección y contacto de cabecera
    conservan su identidad literal. Un identificador sin formato de código
    debe combinar letras con cifras o separadores técnicos para protegerse.
    """
    def normalizar_linea(linea):
        return transformar_fragmentos(linea, lambda parte: parte.translate(_GUIONES), protegidos)

    cabecera = False
    resultado = []
    for linea in texto.splitlines(keepends=True):
        stripped = linea.lstrip()
        if stripped.startswith("# "):
            cabecera = True
            resultado.append(linea)
            continue
        if re.match(r"##\s", stripped):
            cabecera = False
            resultado.append(linea)
            continue
        contacto = cabecera and (
            "@" in linea or "|" in linea
            or re.search(r"https?://|www\.|linkedin\.com|wa\.me|^\s*\+?\d[\d ()–—-]{6,}", linea, re.I)
        )
        resultado.append(linea if contacto else normalizar_linea(linea))
    return "".join(resultado)
