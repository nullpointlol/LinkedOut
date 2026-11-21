"""Cobertura de requisitos en el CV: todo conocimiento o herramienta que el análisis da como
MATCH_DIRECTO o TRANSFERIBLE aparece en el CV con el término de la oferta, en HABILIDADES TÉCNICAS o integrado en la
viñeta cuyo contexto encaja. Oferta 11257: «Grafana, Dynatrace, Elastic» contaba como cubierto con solo «Elastic» y el
CV quedó LISTO al 99 % sin Grafana ni Dynatrace.

Una sola regla para el armado (`cv_maestro`), el Auditor y el medidor (`tools/medir_cobertura_cv.py`):
- un requisito compuesto se parte en términos («Grafana, Dynatrace, Elastic» → tres; «Kafka y Airflow» → dos);
- las alternativas («Python o .NET», «AWS / GCP») se cumplen con una;
- lo que va entre paréntesis como ejemplo («ej. PostgreSQL») y los años no se exigen;
- no se exigen responsabilidades, blandas, logística, idiomas, formación, rubro ni certificaciones."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.idioma import EN, ES

ESTADOS_CUBIERTOS = ("MATCH_DIRECTO", "TRANSFERIBLE")

# Palabras que no hacen al término: «Manejo de Jira» es Jira, «Conocimientos de Postman» es Postman
_RELLENO = set("""manejo dominio uso conocimiento conocimientos experiencia experiencias herramienta herramientas
formato formatos nivel avanzado avanzada avanzados intermedio intermedia basico basica basicos solido solidos solida
fluido fluida programacion lenguaje lenguajes ej eg etc idealmente preferentemente deseable excluyente comprobable
alta alto knowledge experience proficiency advanced intermediate basic strong solid tool tools""".split())
_STOP = set("""de del la las el los y e o u en con para por que se un una al a su sus como entre sobre the and of for in
with to on or at by an from as is are be into""".split())
_PREFIJO_RELLENO = re.compile(
    r"^(?i:manejo|dominio|uso|conocimientos?|experiencia|herramientas?|formatos?|programaci[oó]n|knowledge|"
    r"experience|proficiency)(?i:\s+(?:avanzad[oa]s?|s[oó]lid[oa]s?|b[aá]sic[oa]s?|fluid[oa]|intermedi[oa]))?"
    r"\s+(?i:de|en|con|del|of|in|with)\s+(?=[A-Z0-9.#])")
_RE_ANIOS = re.compile(r"\(?\s*\+?\s*\d+\s*(?:a[ñn]os?|years?)\b[^),;]*\)?", re.IGNORECASE)
_RE_EJEMPLO = re.compile(r"^\s*(?:ej\b\.?|e\.\s?g\.?|ejemplo|por ejemplo|idealmente|preferentemente|como|such as|like)",
                         re.IGNORECASE)
_RE_Y = re.compile(r"\s+(?:y|e|and|&)\s+", re.IGNORECASE)
_RE_O = re.compile(r"\s*/\s*|\s+(?:o|u|or)\s+", re.IGNORECASE)
_RE_BARRA_SIGLA = re.compile(r"\b(?:PL/SQL|CI/CD|UI/UX|TCP/IP|I/O|B2B/B2C)\b", re.IGNORECASE)
# Blandas que `cv_maestro.es_requisito_blando` no cubre (11257: oratoria, negociación, conflictos, análisis)
_RE_BLANDO = re.compile(
    r"\b(?:oratoria|negociaci[oó]n|conflictos?|capacidad (?:de )?an[aá]lisis|capacidad anal[ií]tica|"
    r"pensamiento (?:cr[ií]tico|anal[ií]tico)|atenci[oó]n al detalle|adaptabilidad|multitarea|puntualidad|"
    r"curiosidad|trabajo colaborativo|capacidad de organizaci[oó]n|orientaci[oó]n a (?:resultados|objetivos)|"
    r"negotiation|conflict resolution|critical thinking|analytical (?:skills|thinking)|attention to detail|"
    r"adaptability|public speaking|"
    # Rasgos, perfil y relación con otros: no son conocimientos («Buena presencia», «Interacción con PO, Devs, QA»)
    r"vocaci[oó]n|predisposici[oó]n|buena presencia|relaciones interpersonales|capacidad de aprendizaje|"
    r"aprendizaje r[aá]pido|liderazgo|seniority|perfil|visi[oó]n (?:integral|estrat[eé]gica|de negocio)|"
    r"interacci[oó]n con|articulaci[oó]n (?:con|entre)|coordinaci[oó]n con|colaboraci[oó]n con|"
    r"seguimiento activo|secundario|experiencia en (?:roles|posiciones|puestos|empresas|tareas|funciones)|"
    r"experiencia (?:laboral|comprobable|previa|similar|m[ií]nima)|dinamismo|proactividad|inter[eé]s en|ganas de|"
    r"disposici[oó]n|posibilidad de|apego a|criterio|minucios|profesionales|trabajo freelance|industrias?|"
    r"senior|junior|semi ?senior|\bssr\b|\bsr\b|"
    # Lugar y modalidad: análisis viejos sin la marca `logistico`
    r"presencial(?:idad)?|h[ií]brido|remoto|zona|caba|amba|barrio|turnos?|horario|disponibilidad)",
    re.IGNORECASE)
_RE_IDIOMA = re.compile(r"\b(?:ingl[eé]s|english|espa[ñn]ol|castellano|spanish|portugu[eé]s|franc[eé]s|alem[aá]n|"
                        r"italiano|idiomas?|languages?|biling[uü]e|bilingual)\b", re.IGNORECASE)
# Un nivel MCER («C2», «B2+») es de un idioma, que va en IDIOMAS
_RE_NIVEL_MCER = re.compile(r"(?<![A-Za-z0-9])[ABC][12]\+?(?![A-Za-z0-9])")
_RE_CERTIFICACION = re.compile(r"\bcertific", re.IGNORECASE)


@dataclass(frozen=True)
class Faltante:
    requisito: str
    termino: str  # el término de la oferta que tiene que aparecer (la primera alternativa)
    estado: str


def norm(texto: str) -> str:
    sin = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sin.casefold()


def es_exigible(r: Any) -> bool:
    """Un conocimiento o herramienta que el análisis da por cubierto (match o transferible aprobado)."""
    from core.cv_guardas import es_requisito_logistico, es_requisito_no_habilidad
    from core.cv_maestro import es_requisito_blando
    if not isinstance(r, dict) or not str(r.get("nombre") or "").strip():
        return False
    nombre, estado = str(r["nombre"]), str(r.get("estado") or "").upper()
    if estado not in ESTADOS_CUBIERTOS or not r.get("approved", estado == "MATCH_DIRECTO"):
        return False
    if str(r.get("tipo") or "").upper() == "RESPONSABILIDAD" or r.get("logistico"):
        return False
    return not (es_requisito_logistico(nombre) or es_requisito_no_habilidad(nombre) or es_requisito_blando(nombre)
                or _RE_BLANDO.search(nombre) or _RE_IDIOMA.search(nombre) or _RE_NIVEL_MCER.search(nombre)
                or _RE_CERTIFICACION.search(nombre))


def _palabras(texto: str) -> List[str]:
    return [p.strip(".") if p != ".net" else p for p in re.findall(r"[a-z0-9#+.]+", norm(texto))]


def nucleo(termino: str) -> List[str]:
    """Las palabras que tienen que estar en el CV (sin relleno ni conectores; si todas son relleno, todas)."""
    from core.prompts_evaluacion import GENERIC_CATEGORY_WORDS
    palabras = [p for p in _palabras(termino) if len(p.strip(".")) >= 2 and p not in _STOP]
    genericas = {norm(g) for g in GENERIC_CATEGORY_WORDS} | _RELLENO
    utiles = [p for p in palabras if p not in genericas]
    # Solo relleno («Manejo intermedio»): no hay término que exigir
    return utiles or [p for p in palabras if p not in _RELLENO]


_PREFIJO_SUELTO = re.compile(r"^(?i:formatos?|programaci[oó]n|lenguajes?|herramientas?|enfoque|ecosistema|"
                             r"plataformas?|entornos?|metodolog[ií]as?)\s+(?=[A-Z0-9.#])")


def _limpio(termino: str) -> str:
    """«Manejo de Jira» → «Jira»; «Formatos JSON» → «JSON»; «Programación Python» → «Python»."""
    termino = re.sub(r"\s+", " ", termino).strip(" :-").rstrip(".")
    return _PREFIJO_SUELTO.sub("", _PREFIJO_RELLENO.sub("", termino))


def _partir_y(parte: str) -> List[str]:
    """«Kafka y Airflow» son dos; «Manejo y configuración de APIs» es uno (lo que sigue a la «y» no es un nombre)."""
    trozos = _RE_Y.split(parte)
    salida = [trozos[0]]
    for t in trozos[1:]:
        if re.match(r"\s*[A-Z0-9.#]", t):
            salida.append(t)
        else:
            salida[-1] = f"{salida[-1]} y {t}"
    return salida


def grupos(nombre: str) -> List[List[str]]:
    """Los términos de un requisito: cada grupo se exige; dentro del grupo, alcanza una alternativa."""
    texto = _RE_ANIOS.sub(" ", nombre or "")
    parentesis = [p for p in re.findall(r"\(([^)]*)\)", texto) if not _RE_EJEMPLO.match(p)]
    principal = re.sub(r"\([^)]*\)", " ", texto)
    salida: List[List[str]] = []
    for trozo in [principal] + parentesis:
        for parte in re.split(r"[,;]", trozo):
            for sub in _partir_y(parte):
                protegido = _RE_BARRA_SIGLA.sub(lambda m: m.group(0).replace("/", "⁄"), sub)
                alternativas = [_limpio(a).replace("⁄", "/") for a in _RE_O.split(protegido)]
                alternativas = [a for a in alternativas if a and nucleo(a)]
                if alternativas:
                    salida.append(alternativas)
    return salida


def _patron(palabra: str) -> str:
    raiz = palabra
    if len(raiz) > 5 and raiz.endswith("es"):
        raiz = raiz[:-2]
    elif len(raiz) > 3 and raiz.endswith("s"):
        raiz = raiz[:-1]
    return r"(?<![a-z0-9])" + re.escape(raiz) + r"(?:es|s)?(?![a-z0-9])"


def presente(termino: str, cv: str) -> bool:
    """El término está en el CV: la frase entera o todas sus palabras núcleo (plural y tildes no cuentan)."""
    texto = norm(cv)
    if norm(termino).strip() and norm(termino).strip() in texto:
        return True
    palabras = nucleo(termino)
    return bool(palabras) and all(re.search(_patron(p), texto) for p in palabras)


def _nombre_propio(termino: str) -> bool:
    """Un nombre de producto o sigla («Grafana», «SQL Server Management Studio», «Office 365»): en un CV en inglés
    es lo único que se puede exigir con el término de una oferta en castellano."""
    nucleos = set(nucleo(termino))
    tokens = [t for t in re.findall(r"[A-Za-z0-9#+.]+", unicodedata.normalize("NFKD", termino)) if
              norm(t).strip(".") in nucleos or norm(t) in nucleos]
    return bool(tokens) and all(re.search(r"[A-Z0-9#+.]", t) for t in tokens)


def sin_perfil(cv_md: str) -> str:
    """El CV sin la sección PERFIL: nombrar una herramienta solo en el perfil no la muestra (11257: «Elastic» estaba
    solo ahí); tiene que estar en HABILIDADES TÉCNICAS o en una viñeta."""
    from core.idioma import seccion_canonica
    salida, en_perfil = [], False
    for linea in (cv_md or "").splitlines():
        if re.match(r"^##(?!#)", linea.strip()):
            en_perfil = seccion_canonica(linea) == "PERFIL"
        if not en_perfil:
            salida.append(linea)
    return "\n".join(salida)


# Siglas genéricas que solas no son una herramienta («Ecosistema de IA generativa»)
_SIGLAS_GENERICAS = {"ia", "ai", "it", "ti", "tic", "tics"}
_SUFIJO_COMUN = re.compile(r"(?:ci[oó]n|ciones|si[oó]n|dad|dades|[ií]a|miento|ismo|aje|ura|encia|anza|ez)$", re.IGNORECASE)


def _fuerte(token: str) -> bool:
    """Sigla, nombre con mayúsculas internas, cifra o símbolo: «ITSM», «ServiceNow», «Office 365», «C#», «.NET»."""
    return bool(re.search(r"[A-Z].*[A-Z]|\d|[#+]|^\.[A-Za-z]|[a-z][A-Z]", token))


def nombres_propios(termino: str) -> List[str]:
    """Las herramientas, tecnologías y siglas de un término (lo que se exige al pie de la letra).
    El término entero si lo es («SQL Server Management Studio», «Grafana»); si no, sus tramos con nombre propio
    («ServiceNow y plataformas ITSM» → ServiceNow, ITSM). Valores y frases genéricas («Excelencia y
    profesionalismo», «Bases de datos no relacionales») no tienen ninguno: los incorpora la IA por el pedido."""
    tokens = termino.split()
    if len(tokens) == 1:
        t = tokens[0]
        propio = _fuerte(t) or (t[:1].isupper() and not _SUFIJO_COMUN.search(norm(t)))
        return [t] if propio and norm(t).strip(".") not in _SIGLAS_GENERICAS else []
    califica = [_fuerte(t) or (i > 0 and t[:1].isupper()) for i, t in enumerate(tokens)]
    if tokens[0][:1].isupper() and califica[1]:
        califica[0] = True  # «Inteligencia Artificial», «Microsoft Office»
    tramos, actual = [], []
    for t, ok in zip(tokens, califica):
        if ok:
            actual.append(t)
        elif actual:
            tramos.append(" ".join(actual))
            actual = []
    if actual:
        tramos.append(" ".join(actual))
    return [t for t in tramos if norm(t).strip(".") not in _SIGLAS_GENERICAS]


def exigidos(requisitos: Iterable[Any], idioma: str = ES) -> List[Tuple[Dict[str, Any], List[str]]]:
    """(requisito, alternativas) de cada nombre propio que tiene que aparecer en el CV, en cualquier idioma."""
    salida = []
    for r in requisitos or []:
        if not es_exigible(r):
            continue
        for alternativas in grupos(str(r["nombre"])):
            if len(alternativas) == 1:
                salida += [(r, [t]) for t in nombres_propios(alternativas[0])]
                continue
            propias = [t for a in alternativas for t in nombres_propios(a)]
            if propias:
                salida.append((r, list(dict.fromkeys(propias))))
    return salida


def faltantes(cv: str, requisitos: Iterable[Any], *, idioma: str = ES,
              equivalentes: Optional[Dict[str, str]] = None) -> List[Faltante]:
    """Los términos exigidos que no están en el CV. `equivalentes`: requisito → el término que la IA dijo haber usado
    en un CV en inglés."""
    equivalentes = equivalentes or {}
    salida = []
    for r, alternativas in exigidos(requisitos, idioma):
        nombre = str(r["nombre"])
        if equivalentes.get(nombre) and presente(equivalentes[nombre], cv):
            continue
        if not any(presente(a, cv) for a in alternativas):
            salida.append(Faltante(nombre, alternativas[0], str(r.get("estado") or "")))
    return list(dict.fromkeys(salida))
