"""Subagente 3: Auditor Ácido (Doble Lente: ATS + Wikipedia Signs of AI Writing)."""
import asyncio
import json
import logging
import re
from copy import deepcopy
from typing import Dict, Any, List, Tuple, Optional
from core.bridge import AntigravityBridge
from core.prompts_evaluacion import (
    REVISION_SENALES_IA_PROMPT,
    SIGNS_OF_AI_WRITING_WORDS,
    GENERIC_CATEGORY_WORDS,
    RECRUITER_CRITIC_DIRECTIVES,
    AUDITOR_PROMPT,
)
from core.cv_guardas import corregir_perfil, quitar_requisitos_de_habilidades
from core.idioma import EN, SECCIONES, TITULOS, idioma_de_cv, idioma_de_texto, seccion_canonica
from core.text_normalization import normalizar_guiones, transformar_fragmentos

logger = logging.getLogger(__name__)


# El CV en inglés se corrige en inglés (prevalece sobre las directivas en español del prompt)
_DIRECTIVA_INGLES = (
    "- IDIOMA: el CV está en inglés y así debe quedar (secciones SUMMARY, EXPERIENCE, TECHNICAL SKILLS, "
    "EDUCATION, LANGUAGES; viñetas que empiezan con el verbo en pasado, sin pronombres; las reglas de -icé y de "
    "1ª persona en español no aplican). El pitch queda en el idioma en que está.\n"
)

# Lente LLM de señales de IA: máximo de señales que se aceptan y CV que se le pasa
MAX_SENALES_IA = 6
MAX_CV_REVISION_CHARS = 8000
# Citas más largas que esto se descartan (no se recortan)
MAX_FRASE_SENAL = 240

ANTI_AI_REPLACEMENTS = {
    r"\bdelving into\b": "analizando",
    r"\bdelving\b": "analizando",
    r"\bdelve into\b": "analizar",
    r"\bdelve\b": "profundizar",
    r"\bpivotal\b": "clave",
    r"\btapestry of\b": "conjunto de",
    r"\btapestry\b": "estructura",
    r"\bfostered\b": "promovió",
    r"\bfostering\b": "impulsando",
    r"\bfoster\b": "impulsar",
    r"\bbeacon of\b": "referente en",
    r"\bbeacon\b": "referencia",
    r"\btestament to\b": "evidencia de",
    r"\brealm of\b": "área de",
    r"\brealm\b": "área",
    r"\brealms\b": "áreas",
    r"\butilize\b": "usar",
    r"\butilizing\b": "utilizando",
    r"\bleveraging\b": "aprovechando",
    r"\bleverage\b": "usar",
    r"\bmoreover,?\b": "además,",
    r"\bfurthermore,?\b": "asimismo,",
    r"\bin summary,?\b": "en resumen,",
    r"\bto summarize,?\b": "en resumen,",
    r"\bin conclusion,?\b": "en resumen,",
    r"\bit is worth noting,?\b": "cabe destacar,",
    r"\bnot only\b": "no solo",
    r"\bplays a vital role\b": "cumple un rol clave",
    r"\bstands as a\b": "se posiciona como",
    r"\bholistic\b": "integral",
    r"\bholistically\b": "de forma integral",
    r"\bsynergies\b": "coordinación",
    r"\bsynergy\b": "coordinación",
    r"\bcrucial\b": "central",
    r"\bparamount\b": "principal",
    r"\barticulando sinergias\b": "coordinando esfuerzos",
    r"\barticulando\b": "coordinando",
    r"\barticular\b": "coordinar",
    r"\barticulación\b": "coordinación",
    r"\bsin fricción\b": "de forma directa",
    r"\becosistemas dinámicos\b": "entornos dinámicos",
    r"\becosistemas transaccionales\b": "sistemas transaccionales",
    r"\becosistemas\b": "entornos",
    r"\becosistema\b": "entorno",
    r"\bcon foco en\b": "enfocado en",
    r"\borientado a la excelencia\b": "con foco técnico",
    r"\bfoco e interés\b": "interés",
    r"\bentornos dinámicos y desafiantes\b": "entornos dinámicos",
    r"\bcomunicación fluida\b": "comunicación",
    r"\bde manera fluida\b": "con fluidez",
    r"\bgarantizando la consistencia\b": "asegurando la consistencia",
    r"\bgarantizando la continuidad\b": "asegurando la continuidad",
    r"\bsin afectación operativa\b": "sin impacto operativo",
    r"\balineación operativa\b": "coordinación operativa",
    r"\bmitigación rápida\b": "mitigación",
    r"\bapalancar\b": "potenciar",
    r"\bapalancando\b": "potenciando",
    r"\bholístico\b": "integral",
    r"\bholística\b": "integral",
    # «Robusto» no permite inferir escalabilidad: queda para revisión localizada.
    r"\bpieza clave\b": "parte",
    r"\brol clave\b": "rol",
    r"\bpapel fundamental\b": "rol activo",
    r"\bsinérgico\b": "colaborativo",
    r"\bsinergia\b": "coordinación",
    r"\bsinergias\b": "coordinación",
    r"\bde punta a punta\b": "end-to-end",
    r"\bproactivo\b": "con iniciativa",
    r"\bproactiva\b": "con iniciativa",
    r"\bsólida trayectoria\b": "experiencia comprobada",
    r"\bsolida trayectoria\b": "experiencia comprobada",
    r"\bsólida experiencia\b": "experiencia comprobada",
    r"\bsolida experiencia\b": "experiencia comprobada",
    r"\btrayectoria destacada\b": "trayectoria",
    r"\bamplia experiencia\b": "experiencia",
    r"\bamplio conocimiento\b": "conocimiento",
    r"\bmarcado interés\b": "interés profesional",
    r"\bmarcado interes\b": "interés profesional",
    r"\bvasto conocimiento\b": "conocimiento técnico",
    r"\bvasta experiencia\b": "amplia práctica",
    r"\bdestacada labor\b": "labor técnica",
    r"\bdestacado desempe[ñn]o\b": "desempeño",
    r"\ba lo largo de mi carrera\b": "en mi trayectoria",
    r"\ben el transcurso de mi trayectoria\b": "en mi trayectoria",
    r"\bde alta exigencia\b": "con exigencias altas",
    r"\balta exigencia\b": "exigencia",
    r"\bde alto impacto\b": "clave",
    r"\bespecializo mi labor\b": "trabajo",
    r"\bme caracterizo por\b": "tengo práctica en",
    r"\bvalor agregado\b": "resultados",
    r"\ben el d[íi]a a d[íi]a\b": "en la operación",
    r"\bde manera eficiente\b": "con eficiencia",
    r"\bde forma eficiente\b": "con eficiencia",
    r"\bapasionad[oa]s?\s+por\b": "interesado en",
    r"\borientad[oa]s?\s+a\s+resultados\b": "con foco técnico",
    r"\bjugador(?:a)?\s+de\s+equipo\b": "colaborativo",
    r"\bpensamiento\s+fuera\s+de\s+la\s+caja\b": "resolución de problemas",
    r"\bdeep\s+dive\b": "análisis detallado",
    r"\bnavigating\b": "gestionando",
    r"\bnavigate\b": "gestionar",
    r"\bvibrancy\b": "dinamismo",
    r"\bvibrant\b": "activo",
    r"\bunderscores\b": "destaca",
    r"\bunderscoring\b": "destacando",
    r"\bshowcasing\b": "mostrando",
    r"\bspearheading\b": "liderando",
    r"\bspearhead\b": "liderar",
    r"\bseamlessly\b": "de forma integrada",
    r"\bseamless\b": "integrado",
    r"\bunwavering\b": "constante",
}

# El catálogo público conserva sus sustituciones históricas. Al sanear contenido
# del candidato, quitar el énfasis sin atribuir cualidades no verificadas.
_REEMPLAZOS_NEUTROS = {
    r"\bsólida trayectoria\b": "trayectoria",
    r"\bsolida trayectoria\b": "trayectoria",
    r"\bsólida experiencia\b": "experiencia",
    r"\bsolida experiencia\b": "experiencia",
    r"\bmarcado interés\b": "interés",
    r"\bmarcado interes\b": "interés",
    r"\bvasto conocimiento\b": "conocimiento",
    r"\bdestacada labor\b": "labor",
}

# Para un CV en inglés: el reemplazo queda en inglés
ANTI_AI_REPLACEMENTS_EN = {
    r"\bdelving into\b": "analyzing",
    r"\bdelving\b": "analyzing",
    r"\bdelve into\b": "analyze",
    r"\bdelve\b": "dig",
    r"\bpivotal\b": "key",
    r"\btapestry of\b": "set of",
    r"\btapestry\b": "mix",
    r"\bfostered\b": "built",
    r"\bfostering\b": "building",
    r"\bfoster\b": "build",
    r"\bbeacon of\b": "example of",
    r"\bbeacon\b": "reference",
    r"\btestament to\b": "evidence of",
    r"\brealm of\b": "field of",
    r"\brealms\b": "fields",
    r"\brealm\b": "field",
    r"\butilized\b": "used",
    r"\butilize\b": "use",
    r"\butilizing\b": "using",
    r"\bleveraged\b": "used",
    r"\bleveraging\b": "using",
    r"\bleverage\b": "use",
    r"\bspearheaded\b": "led",
    r"\bspearheading\b": "leading",
    r"\bspearhead\b": "lead",
    r"\bshowcasing\b": "showing",
    r"\bseamlessly\b": "smoothly",
    r"\bseamless\b": "smooth",
    r"\bcrucial\b": "key",
    r"\bparamount\b": "essential",
    r"\bholistically\b": "end to end",
    r"\bholistic\b": "end-to-end",
    r"\bsynergies\b": "collaboration",
    r"\bsynergy\b": "collaboration",
    r"\bunwavering\b": "steady",
    r"\bnavigating\b": "managing",
    r"\bnavigate\b": "manage",
    r"\bvibrancy\b": "activity",
    r"\bvibrant\b": "active",
    r"\bdeep\s+dive\b": "detailed analysis",
    r"\bunderscores\b": "highlights",
    r"\bunderscoring\b": "highlighting",
    r"\bplays\s+a\s+vital\s+role\b": "is key",
    r"\bstands\s+as\s+a\b": "is a",
    r"\bit\s+is\s+worth\s+noting,?\s*": "notably, ",
    r"\bin\s+conclusion,?\s*": "",
    r"\bmoreover,?\s*": "",
    r"\bfurthermore,?\s*": "",
    r"\bin summary,?\s*": "",
    r"\bto summarize,?\s*": "",
    r"\bresults-driven\b": "",
    r"\bproven track record of\b": "experience in",
    r"\bproven track record\b": "experience",
    r"\bnot only\b": "not just",
}


# Patrones de "Signs of AI writing" (Wikipedia) que una lista de palabras no ve
# Cláusula de consecuencia en gerundio al cierre de la viñeta ("…, reduciendo tiempos")
_GERUNDIOS_CONSECUENCIA = (
    "asegurando|garantizando|optimizando|reduciendo|estableciendo|validando|mitigando|evitando|mejorando"
    "|agilizando|permitiendo|logrando|aumentando|disminuyendo|facilitando|acelerando|minimizando|maximizando"
    "|impulsando|contribuyendo|posibilitando|potenciando"
)
_GERUNDIOS_CONSECUENCIA_EN = (
    "ensuring|reducing|improving|enabling|streamlining|optimizing|increasing|decreasing|minimizing|maximizing"
    "|driving|boosting|enhancing|accelerating|resulting in|leading to|allowing|facilitating|contributing to"
)
_RE_CONSECUENCIA = re.compile(
    rf"(?:,\s*|\s+)(?:(?:{_GERUNDIOS_CONSECUENCIA}|{_GERUNDIOS_CONSECUENCIA_EN})\b|con lo cual\b"
    rf"|lo que (?:permitió|redujo|mejoró)\b|which (?:resulted|reduced|improved|enabled)\b)[^.!?\n]*[.!?]?$",
    re.IGNORECASE,
)
# Proporción de viñetas con ese cierre a partir de la cual la estructura es repetitiva
UMBRAL_SIMETRIA = 0.40
# Negritas fuera de las etiquetas de HABILIDADES: más de esto es "exceso de negritas"
MAX_NEGRITAS = 4
# Apertura formulaica del PERFIL
_RE_APERTURA_PERFIL = re.compile(
    # «Profesional con experiencia en…» y «<Rol> con N años…» son la forma pedida del perfil (creador, 15/11)
    r"^(?:cuento con|soy (?:un|una) profesional|poseo"
    r"|results[- ]driven|highly (?:motivated|skilled)|seasoned|dynamic|passionate|dedicated|detail[- ]oriented"
    r"|i am an? )\b",
    re.IGNORECASE,
)
# Impacto vago: "reduje/mejoré… tiempos/la dependencia/la eficiencia" sin ningún número en la viñeta
_RE_IMPACTO_VAGO = re.compile(
    r"\b(?:reduciendo|reduje|mejorando|mejoré|optimizando|optimicé|agilizando|agilicé|disminuyendo|disminuí"
    r"|aumentando|aumenté|acelerando|aceleré)\s+(?:los\s+|la\s+|el\s+)?(?:tiempos?|dependencia|eficiencia"
    r"|calidad|productividad|errores|incidencias|costos|riesgos?)\b"
    r"|\bpara\s+(?:acelerar|agilizar|mejorar|optimizar|reducir)\s+(?:los\s+|la\s+|el\s+)?"
    r"(?:despacho|tiempos?|eficiencia|calidad|productividad|costos|riesgos?)\b"
    r"|\b(?:reducing|reduced|improving|improved|optimizing|optimized|streamlining|streamlined|increasing|increased"
    r"|accelerating|accelerated|enhancing|enhanced|boosting|boosted)\s+(?:the\s+)?(?:overall\s+)?(?:time|efficiency"
    r"|quality|productivity|errors|costs|risks?|performance|turnaround|dependency)\b",
    re.IGNORECASE,
)
MIN_VIÑETAS_VAGAS = 1

# Evidencias empíricas cualitativas de alcance, procesos, arquitectura y gobernanza
_RE_EVIDENCIA_CUALITATIVA = re.compile(
    r'\b(?:'
    r'criterios? de aceptaci[oó]n|'
    r'historias? de usuario|'
    r'matri(?:z|ces) de (?:testing|prueba|pruebas)|'
    r'casos? de prueba|'
    r'pases? (?:a producci[oó]n|productivos?)|'
    r'ambientes? de (?:certificaci[oó]n|desarrollo|testing|preproducci[oó]n|producci[oó]n|qa)|'
    r'consistencia de datos|'
    r'modelos? relacionales?|'
    r'sistemas? transaccionales?|'
    r'procesos? (?:cr[ií]ticos?|transaccionales?|operativos?)|'
    r'relevamiento (?:funcional|de requerimientos|de requisitos)|'
    r'reglas? de negocio|'
    r'interlocuci[oó]n t[eé]cnica|'
    r'guardias? on[- ]?call|'
    r'auditor[ií]as? de (?:procesos|historias cl[ií]nicas|c[oó]digo)|'
    r'acceptance criteria|user stor(?:y|ies)|test (?:cases?|matri(?:x|ces)|plans?)|'
    r'production (?:releases?|deployments?)|(?:qa|staging|production|testing) environments?|'
    r'data consistency|relational models?|transactional systems?|critical processes|'
    r'requirements gathering|business rules|on[- ]call|code reviews?|(?:process|code) audits?'
    r')\b',
    re.IGNORECASE,
)

# Solo estos cierres completos se pueden borrar sin interpretar su alcance.
_RE_CIERRE_VAGO_SEGURO = re.compile(
    r",\s*(?:para acelerar el despacho según SLA|reduciendo (?:los )?tiempos de auditoría)"
    r"(?P<punto>[.!?]?)(?=[ \t]*(?:\n|$))", re.IGNORECASE,
)
_RE_GUARDIA = re.compile(r"\b(?:cambio|pase)\s+de\s+guardia\b", re.IGNORECASE)


def _vinetas_continuadas(texto: str) -> Tuple[List[str], List[str]]:
    """Une renglones PDF de una viñeta hasta otra viñeta, título o párrafo."""
    todas, experiencia = [], []
    en_exp = False
    actual = None
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            actual = None
            if re.match(r"^##(?!#)\s", limpia):
                en_exp = seccion_canonica(limpia) == "EXPERIENCIA"
            continue
        if re.match(r"^[*\-•]\s+", limpia):
            actual = [re.sub(r"^[*\-•]\s+", "", limpia)]
            todas.append(actual)
            if en_exp:
                experiencia.append(actual)
        elif actual is not None:
            actual.append(limpia)
    return ([" ".join(b) for b in todas], [" ".join(b) for b in experiencia])


def _es_impacto_vago(texto: str) -> bool:
    """Distingue ausencia de cifra de ausencia de evidencia.
    Un resultado cualitativo verificable no bloquea; un resultado no respaldado sí."""
    if not _RE_IMPACTO_VAGO.search(texto):
        return False
    if re.search(r"\d", texto):
        return False
    # Una práctica (testing, reglas, procesos) no acredita su efecto. Sin cifra,
    # sólo aceptar una comparación registrada o un resultado observable ligado
    # explícitamente al atributo: trazabilidad completa de los requisitos.
    if re.search(r"\b(?:comparaci[oó]n|medici[oó]n)\s+(?:registrada|documentada)\s+"
                 r"(?:del?\s+)?antes y despu[eé]s\b", texto, re.I):
        return False
    if (re.search(r"\b(?:mejoré|optimicé)\s+(?:la\s+)?calidad\b", texto, re.I)
            and re.search(r"\b(?:cada|todos? los) requisitos?\s+(?:qued[oó]|quedaron|est[aá]n?)\s+"
                          r"(?:vinculad[oa]s?|asociad[oa]s?|trazad[oa]s?)\s+a\s+(?:su|un)\s+caso de prueba\b",
                          texto, re.I)):
        return False
    return True


def _normalizar_senal(texto: str) -> str:
    """Misma comparación para aceptar una cita y comprobar si sigue en el CV."""
    return re.sub(r"[\s*]+", " ", str(texto or "")).strip().lower()


def _normalizar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip().lower()


def _es_objecion_negrita(senal: Dict[str, str]) -> bool:
    patron = (senal.get("patron") or "").lower()
    return "negrit" in patron or "bold" in patron


def _senales_presentes(texto: str, senales: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Evalúa la vigencia de una señal según su patrón.
    Una objeción de negrita desaparece cuando ese formato ya no está en el texto;
    las señales semánticas que sigan en el texto se mantienen."""
    normal_semantica = _normalizar_senal(texto)
    normal_formato = _normalizar_espacios(texto)
    presentes = []
    for s in senales:
        frase = s.get("frase")
        if not frase:
            continue
        if _es_objecion_negrita(s):
            # Una objeción de negrita desaparece cuando ese formato ya no está en el texto
            if "**" in frase:
                frase_formato = _normalizar_espacios(frase)
                if frase_formato in normal_formato:
                    presentes.append(s)
            else:
                # La lente puede citar una oración sin los marcadores. Ubicar
                # esa cita con su formato real, no exigir que toda sea negrita.
                patron_cita = r"\**" + "".join(
                    r"[\s*]+" if c == " " else re.escape(c) + r"\**"
                    for c in _normalizar_senal(frase))
                if any("**" in m.group() for m in re.finditer(patron_cita, texto, re.I)):
                    presentes.append(s)
        else:
            # Señal semántica: se evalúa sobre el texto normalizado
            if _normalizar_senal(frase) in normal_semantica:
                presentes.append(s)
    return presentes


def _sin_frase(texto: str, frase: str) -> str:
    """Borra `frase` si es una oración entera o una cláusula final tras una coma (reescritura vacía
    = la lente sugiere quitarla). En otro caso devuelve `texto` sin cambios."""
    if frase.endswith(".") and ", " + frase in texto:
        return texto.replace(", " + frase, ".", 1)
    m = re.search(r"(^|(?<=[.!?]) )" + re.escape(frase) + r"(?= |$)", texto, flags=re.MULTILINE)
    if frase.endswith(".") and m:
        # Tras otra oración, el espacio previo cae con el match; al inicio de línea se quita el siguiente
        inicio, fin = m.start(), m.end()
        if not m.group(1) and fin < len(texto) and texto[fin] == " ":
            fin += 1
        return texto[:inicio] + texto[fin:]
    return texto


def aplicar_reescrituras(texto: str, senales: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, str]]]:
    """Reemplaza cada `frase` (cita literal) por su `reescritura`; con reescritura vacía la borra si es
    una oración o una cláusula final. Devuelve el texto y las señales que no se pudieron aplicar."""
    restantes = []
    for x in senales:
        frase, nueva = x.get("frase") or "", (x.get("reescritura") or "").strip()
        if not frase or frase not in texto:
            candidato = texto
        elif nueva:
            candidato = texto.replace(frase, nueva, 1)
        else:
            candidato = _sin_frase(texto, frase)
        # Una reescritura que deja negritas sin cerrar rompe el Markdown: no se aplica
        if (candidato != texto and len(nueva) <= max(2 * len(frase), len(frase) + 80)
                and candidato.count("**") % 2 == texto.count("**") % 2):
            texto = candidato
        else:
            restantes.append(x)
    return texto, restantes


# La remediación edita líneas puntuales de un CV que ya escribió el Redactor (High): con Low se
# degradaba el texto entero
EFFORT_REMEDIACION = "medium"
TIMEOUT_POR_EFFORT = {"low": 180, "medium": 420, "high": 600}
TIMEOUT_REMEDIACION_S = TIMEOUT_POR_EFFORT[EFFORT_REMEDIACION]
# Orden de admisión estable; cada destino puede consumir a lo sumo max_attempts.
_ORDEN_DESTINOS = ("HABILIDADES", "PERFIL", "EXPERIENCIA", "EDUCACION", "IDIOMAS", "pitch")


def _lineas_editables(cv: str) -> List[int]:
    """Índices de las líneas que la remediación puede tocar: el contenido de las secciones `##`
    (no el nombre, el puesto ni el contacto, ni los títulos)."""
    lineas, editables, en_seccion = cv.splitlines(), [], False
    for i, linea in enumerate(lineas):
        if linea.startswith("## "):
            en_seccion = True
        elif en_seccion and linea.strip() and not linea.startswith("#"):
            editables.append(i)
    return editables


def _lineas_numeradas(cv: str) -> str:
    lineas = cv.splitlines()
    editables = set(_lineas_editables(cv))
    return "\n".join(f"{i}: {l}" if i in editables else f"   {l}" for i, l in enumerate(lineas))


def _aplicar_lineas(cv: str, cambios: List[Any]) -> str:
    """Aplica parches sin conflictos, con índices enteros y Markdown de una sola línea."""
    lineas = cv.splitlines()
    editables = set(_lineas_editables(cv))
    propuestas: Dict[int, set] = {}
    for c in cambios:
        if not isinstance(c, dict) or type(c.get("n")) is not int:
            continue
        n, texto = c["n"], c.get("texto")
        if n not in editables or not isinstance(texto, str):
            continue
        propuestas.setdefault(n, set()).add(texto)
    for n, alternativas in propuestas.items():
        # Un conflicto no tiene ganador, ni siquiera si llega primero un parche válido.
        if len(alternativas) != 1:
            continue
        texto = next(iter(alternativas))
        if (texto.strip() and not texto.lstrip().startswith("#")
                and len(texto.splitlines()) == 1 and "\r" not in texto and "\n" not in texto
                and texto.count("**") % 2 == 0
                and texto.count("`") % 2 == 0
                and texto.count("[") == texto.count("]")):
            lineas[n] = texto
    # Un no-op conserva incluso el salto final y los separadores originales.
    originales = cv.splitlines(keepends=True)
    return "".join(original if linea == original.rstrip("\r\n") else
                   linea + original[len(original.rstrip("\r\n")):]
                   for original, linea in zip(originales, lineas))


def _destinos_remediacion(cv: str, pitch: str, report: Dict[str, Any]) -> Dict[str, List[int]]:
    """Ubica hallazgos observables; una falla estructural no habilita todo el CV."""
    lineas = cv.splitlines()
    por_seccion: Dict[str, List[int]] = {}
    seccion = None
    editables = set(_lineas_editables(cv))
    for n, linea in enumerate(lineas):
        if linea.startswith("## "):
            seccion = seccion_canonica(linea)
        elif n in editables and seccion in SECCIONES:
            por_seccion.setdefault(seccion, []).append(n)

    marcadas = set()
    if report["ats"].get("missing_keywords"):
        marcadas.add("HABILIDADES")
    senales = report["anti_ai"].get("senales_llm") or []
    detectadas = set(report["anti_ai"].get("detected_words") or [])
    # Compartir las continuaciones y conservar el alcance global previo cuando
    # EXPERIENCIA tiene menos de cuatro viñetas.
    _, vinetas_exp = _vinetas_continuadas(f"{cv}\n\n{pitch}")
    for seccion, numeros in por_seccion.items():
        cuerpo = "\n".join(lineas[n] for n in numeros)
        if _senales_presentes(cuerpo, senales):
            marcadas.add(seccion)
        locales = scan_anti_ai_writing(f"## {seccion}\n{cuerpo}")["detected_words"]
        if detectadas.intersection(locales):
            marcadas.add(seccion)
        # Hallazgos agregados: localizar sus líneas sin habilitar secciones ajenas.
        if (any(d.startswith("exceso de negritas") for d in detectadas)
                and seccion != "HABILIDADES" and re.search(r"\*\*[^*]+\*\*", cuerpo)):
            marcadas.add(seccion)
        if (any(d.startswith("simetría sintáctica") for d in detectadas)
                and (seccion == "EXPERIENCIA" or len(vinetas_exp) < 4)
                and any(_RE_CONSECUENCIA.search(b) for b in _vinetas_continuadas(cuerpo)[0])):
            marcadas.add(seccion)

    destinos = {s: por_seccion[s] for s in _ORDEN_DESTINOS
                if s in marcadas and s in por_seccion}
    if not scan_anti_ai_writing(pitch)["clean"]:
        destinos["pitch"] = []
    return destinos


def _cambios_del_destino(base: str, resultado: Any, numeros: List[int]) -> List[Any]:
    """Convierte respuestas nuevas y antiguas en parches del único destino enviado."""
    if not isinstance(resultado, dict):
        return []
    cambios = resultado.get("lineas")
    if not isinstance(cambios, list):
        legado = resultado.get("cv_markdown")
        if not isinstance(legado, str):
            return []
        originales, nuevas = base.splitlines(), legado.splitlines()
        # Insertar/borrar líneas destruye la correspondencia con los n absolutos.
        if len(originales) != len(nuevas):
            return []
        editables = set(_lineas_editables(base))
        if any(original != nueva for n, (original, nueva) in enumerate(zip(originales, nuevas))
               if n not in editables):
            return []
        cambios = [{"n": n, "texto": nuevas[n]} for n in numeros
                   if originales[n] != nuevas[n]]
    permitidas = set(numeros)
    return [c for c in cambios if isinstance(c, dict) and type(c.get("n")) is int
            and c["n"] in permitidas]


def _seccion(texto: str, nombre: str) -> str:
    """Cuerpo de la primera sección `##` de esa categoría (PERFIL, EXPERIENCIA, HABILIDADES…), con
    su título en español o en inglés. Vacío si no está."""
    for m in re.finditer(r"^##(?!#)([^\n]*)\n(.*?)(?=^##(?!#)|\Z)", texto, flags=re.MULTILINE | re.DOTALL):
        if seccion_canonica(m.group(1)) == nombre:
            return m.group(2).strip()
    return ""


def _negritas_fuera_de_habilidades(texto: str) -> int:
    sin_habilidades = texto.replace(_seccion(texto, "HABILIDADES"), "") if _seccion(texto, "HABILIDADES") else texto
    return len(re.findall(r"\*\*[^*\n]+\*\*", sin_habilidades))


def scan_anti_ai_writing(text: str) -> Dict[str, Any]:
    """Escanea el texto en busca de palabras fetiche, clichés de IA (Wikipedia guidelines), verbos en 3ª persona y errores ortográficos con -izé."""
    if not text:
        return {"clean": True, "detected_words": [], "anti_ai_score": 100}

    # Las referencias literales no participan de las señales de escritura.
    text = transformar_fragmentos(text, lambda parte: parte, ocultar_protegidos=True)
    detected = []
    text_lower = text.lower()

    for word in SIGNS_OF_AI_WRITING_WORDS:
        # Búsqueda con límite de palabra para evitar falsos positivos
        pattern = r"\b" + re.escape(word.lower()) + r"\b"
        if re.search(pattern, text_lower):
            detected.append(word)

    # Detección de errores ortográficos en verbos en pretérito terminados en -izé (deben ser -icé en español)
    ize_matches = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]*iz[éÉ]\b", text)
    for bad_verb in ize_matches:
        bad_verb_clean = bad_verb.strip()
        if bad_verb_clean and bad_verb_clean not in detected:
            detected.append(f"{bad_verb_clean} (ortografía: usar terminación -icé)")

    # Detección de inconsistencia de voz: verbos en pretérito en 3ª persona singular (debe redactarse en 1ª persona activa)
    third_person_pattern = r"\b(desarrolló|optimizó|administró|coordinó|implementó|lideró|diagnosticó|estandarizó|ejecutó|diseñó|relevó|participó|colaboró|automatizó|articuló|depuró)\b"
    # En una viñeta, solo el verbo con el que abre: en el medio puede tener otro sujeto («una herramienta que …
    # estandarizó el circuito», V27 del 15/11). El perfil va en 3ª persona (creador, 15/11): no se mira. La prosa
    # del mail o del pitch, entera
    tp_matches, seccion = [], ""
    for linea in text.splitlines():
        l = linea.strip()
        if l.startswith("## "):
            seccion = seccion_canonica(l)
            continue
        if seccion == "PERFIL":
            continue
        if re.match(r"^[•*\-]\s", l):
            apertura = re.sub(r"^[•*\-]\s*(?:\*\*[^*]+:?\*\*:?\s*)?(?:\*\*)?", "", l)
            m = re.match(third_person_pattern, apertura, flags=re.IGNORECASE)
            tp_matches += [m] if m else []
        elif not l.startswith("#"):
            tp_matches += list(re.finditer(third_person_pattern, l, flags=re.IGNORECASE))
    seen_tp = set()
    for match in tp_matches:
        bad_verb = match.group(0).strip()
        bad_verb_lower = bad_verb.lower()
        if bad_verb and bad_verb_lower not in seen_tp:
            seen_tp.add(bad_verb_lower)
            detected.append(f"{bad_verb} (inconsistencia de voz: redactar en 1ª persona 'Desarrollé')")

    # Las continuaciones del PDF pertenecen a la misma viñeta.
    all_bullets, exp_bullets = _vinetas_continuadas(text)
    objetivos = [(exp_bullets, len(exp_bullets) >= 4)] if len(exp_bullets) >= 2 else []
    if len(exp_bullets) < 4:
        objetivos.append((all_bullets, True))
    for objetivo, alcance_previo in objetivos:
        cierres = [b for b in objetivo if _RE_CONSECUENCIA.search(b)]
        sin_cifras = sum(not re.search(r"\d", b) for b in cierres)
        # Ampliar al 40% solo los cierres sin resultados medidos. Conservar
        # además el bloqueo anterior (>=4 viñetas, >=50%), aun con cifras.
        previa = alcance_previo and len(objetivo) >= 4 and len(cierres) / len(objetivo) >= 0.50
        ampliada = len(objetivo) >= 2 and sin_cifras >= 2 and sin_cifras / len(objetivo) >= UMBRAL_SIMETRIA
        if previa or ampliada:
            detected.append("simetría sintáctica (al menos dos cierres de consecuencia en el 40% o más de las viñetas)")
            break

    # Misma política de tokens que el normalizador. El prefijo permite observar
    # también rayas en títulos sin tratar esos títulos como identidad protegida.
    if any(normalizar_guiones("Texto " + l) != "Texto " + l for l in text.splitlines()):
        detected.append("raya (guion largo en prosa o rango)")
    guardias = list(_RE_GUARDIA.finditer(text))
    if any(b.start() - a.end() <= 240 for a, b in zip(guardias, guardias[1:])):
        detected.append("repetición cercana (cambio/pase de guardia)")

    if _negritas_fuera_de_habilidades(text) > MAX_NEGRITAS:
        detected.append(f"exceso de negritas (más de {MAX_NEGRITAS} fuera de {TITULOS[idioma_de_cv(text)]['HABILIDADES']})")

    perfil = _seccion(text, "PERFIL")
    if perfil and _RE_APERTURA_PERFIL.search(perfil):
        detected.append("apertura formulaica del PERFIL («Cuento con…», «Profesional con…», «Results-driven…»)")

    # Mail y prosa sin marcadores también cuentan; unir los saltos del PDF.
    unidades = all_bullets + [re.sub(r"\s+", " ", p) for p in
                              re.split(r"(?<=[.!?])\s+|\n\s*\n", text)]
    if sum(_es_impacto_vago(b) for b in unidades) >= MIN_VIÑETAS_VAGAS:
        detected.append("impacto vago (consecuencia sin cifra ni alcance verificable)")

    penalty = len(detected) * 15
    anti_ai_score = max(0, 100 - penalty)
    is_clean = len(detected) == 0

    return {
        "clean": is_clean,
        "detected_words": detected,
        "anti_ai_score": anti_ai_score,
        "recommendation": "Aprobado (lenguaje natural y empírico)" if is_clean else f"Remover palabras de IA, corregir ortografía o voz en 1ª persona: {', '.join(detected)}"
    }


# Viñeta de CV en inglés que arranca con un verbo de acción (pasado regular o irregular, o presente)
_RE_VERBO_ACCION_EN = (
    r"^[*\-•]\s+(?:\*\*[^*]+:\*\*\s*)?(?:[A-Z][a-z]+ed|Led|Built|Ran|Wrote|Drove|Made|Took|Set|Kept|Brought"
    r"|Won|Taught|Oversaw|Grew|Cut|Shipped|Rebuilt|Undertook|Sold|Held|Met|Found|Gave|Began|Spent|Lead|Build|Run"
    r"|Manage|Develop|Design|Own|Maintain|Support|Coordinate|Analyze|Write|Deliver|Create|Automate)\b"
)


_STOPWORDS_KEYWORD = {"de", "en", "con", "y", "para", "la", "el", "los", "las", "un", "una", "o", "a", "al", "del", "por",
                      "que", "se", "of", "and", "the", "for", "in", "with", "to", "an", "on", "or", "at", "by"}


def palabras_de_keyword(keyword: str) -> Tuple[List[str], List[str]]:
    """(palabras, palabras núcleo) de una keyword: la keyword cuenta en el CV con la frase entera o con una sola de
    sus palabras núcleo (o, si no tiene, con una de sus palabras). La usan la auditoría y las pautas del pedido."""
    # Con tildes y eñe: «técnica» se partía en «t» + «cnica» (10937, 16/11)
    words = [w for w in re.findall(r'[a-zA-Z0-9áéíóúüñÁÉÍÓÚÜÑ#+.]+', (keyword or "").lower())
             if w not in _STOPWORDS_KEYWORD and len(w) >= 2]
    return words, [w for w in words if w not in GENERIC_CATEGORY_WORDS]


def _keyword_presente(keyword: str, cv_lower: str) -> bool:
    """La frase entera o una sola de sus palabras núcleo (o, si no tiene, una de sus palabras)."""
    if keyword.lower() in cv_lower:
        return True
    words, substantive_words = palabras_de_keyword(keyword)
    # Exigir que al menos un sustantivo nuclear técnico (ej. QR, tarjetas, SQL, Docker) esté presente en el CV; si
    # el término solo contenía palabras de categoría (ej. "Integraciones" o "Herramientas"), una de ellas
    for w in substantive_words or words:
        if re.search(r'(?<![a-zA-Z0-9])' + re.escape(w) + r'(?![a-zA-Z0-9])', cv_lower):
            return True
    return False


def evaluate_ats_compliance(
    cv_text: str,
    keywords: List[str],
    expected_companies: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Evalúa la conformidad estructural, densidad de keywords, métricas empíricas y escaneabilidad para un reclutador."""
    if not cv_text:
        return {
            "ats_score": 0.0,
            "has_all_sections": False,
            "keyword_coverage": 0.0,
            "missing_keywords": keywords,
            "deducciones": ["-100.0 pts: CV vacío."],
            "dimensiones": {
                "estructura": 0.0,
                "keywords": 0.0,
                "metricas_empiricas": 0.0,
                "escaneabilidad": 0.0,
            }
        }

    cv_upper = cv_text.upper()
    titulos = TITULOS[idioma_de_cv(cv_text)]
    presentes = {seccion_canonica(l) for l in cv_text.splitlines() if re.match(r"^##(?!#)", l.strip())}
    found_sections = [titulos[s] for s in SECCIONES if s in presentes]
    missing_sections = [titulos[s] for s in SECCIONES if s not in presentes]

    has_all_sections = len(missing_sections) == 0
    # Dimensión 1: Estructura & Secciones ATS (Máx: 20.0 pts)
    sec_ratio = len(found_sections) / len(SECCIONES)
    estructura_score = round(sec_ratio * 20.0, 1)

    # Verificación estricta de Cobertura de Experiencias Laborales (Cero Gaps)
    if expected_companies is None:
        try:
            from core.profile_parser import get_candidate_companies
            inferred = get_candidate_companies()
            target_companies = inferred if inferred else []
        except Exception:
            target_companies = []
    else:
        target_companies = expected_companies

    found_experiences = []
    missing_experiences = []
    if target_companies:
        for comp in target_companies:
            comp_clean = comp.strip()
            if not comp_clean:
                continue
            if comp_clean.upper() in cv_upper:
                found_experiences.append(comp_clean)
            else:
                missing_experiences.append(comp_clean)
        has_all_experiences = len(missing_experiences) == 0
    else:
        has_all_experiences = True

    # Dimensión 2: Cobertura de Keywords Sustantivas (Máx: 35.0 pts)
    found_kw = []
    missing_kw = []
    cv_lower = cv_text.lower()

    for kw in keywords:
        kw_clean = kw.strip()
        if not kw_clean:
            continue
        # Coincidencia exacta de la frase completa
        if kw_clean.lower() in cv_lower:
            found_kw.append(kw_clean)
            continue
        # Un requisito compuesto exige cada término; dentro de un término, alcanza una alternativa (16/11, oferta
        # 11257: «Grafana, Dynatrace, Elastic» pasaba con «Elastic»)
        from core.cobertura_requisitos import grupos
        terminos = grupos(kw_clean)
        if len(terminos) > 1:
            matched = all(any(_keyword_presente(a, cv_lower) for a in alternativas) for alternativas in terminos)
        else:
            matched = _keyword_presente(kw_clean, cv_lower)
        if matched:
            found_kw.append(kw_clean)
        else:
            missing_kw.append(kw_clean)

    # Sin keywords exigidas no falta ninguna: cobertura completa (antes daba 0 y restaba 35 puntos; 15/11, una oferta
    # cuyos requisitos no tienen evidencia en el maestro no exige keywords)
    keyword_coverage = round(len(found_kw) / len(keywords), 2) if keywords else 1.0
    keywords_score = round(keyword_coverage * 35.0, 1)

    # Viñetas de logros
    bullets = [line.strip() for line in cv_text.splitlines() if re.match(r'^[*\-•]\s+', line.strip())]
    bullet_text = " ".join(bullets)

    # Dimensión 3: Métricas Empíricas & Verificabilidad de Impacto (Máx: 25.0 pts)
    # Reclutador busca números, %, volúmenes o plazos en los logros (excluyendo años calendario)
    metric_pattern = r'\b(?:\d+(?:\.\d+)?%(?:/\w+)?|\d+\+?\s*(?:proyectos|usuarios|servicios|incidentes|flujos|meses|años|horas|minutos|semanas|días|transacciones|operaciones|projects|users|services|incidents|workflows|months|years|hours|minutes|weeks|days|transactions|operations|tickets|clients|customers)|(?!(?:19|20)\d{2})\b\d{2,}\b)'
    metrics_found = re.findall(metric_pattern, bullet_text, flags=re.IGNORECASE)
    metric_count = len(metrics_found)

    # Evidencias empíricas cualitativas de alcance, procesos, arquitectura y gobernanza
    qualitative_found = _RE_EVIDENCIA_CUALITATIVA.findall(bullet_text)
    qualitative_count = len(qualitative_found)

    total_evidencias = metric_count + qualitative_count

    if total_evidencias >= 3:
        metricas_score = 25.0
    elif total_evidencias == 2:
        metricas_score = 18.0
    elif total_evidencias == 1:
        metricas_score = 10.0
    else:
        metricas_score = 0.0

    # Dimensión 4: Escaneabilidad para Reclutador en 10-15s (Máx: 20.0 pts)
    # Evalúa viñetas concisas (<= 250 caracteres) y verbos de acción en 1ª persona, con uso sobrio de negritas
    exp_text = _seccion(cv_text, "EXPERIENCIA") or cv_text
    exp_bullets = [line.strip() for line in exp_text.splitlines() if re.match(r'^[*\-•]\s+', line.strip())]
    all_bullets = [line.strip() for line in cv_text.splitlines() if re.match(r'^[*\-•]\s+', line.strip())]

    target_bullets = exp_bullets if exp_bullets else all_bullets

    ACTION_VERBS_1ST_PERSON_PATTERN = (
        r"\b("
        r"desarrollé|diseñé|implementé|lideré|coordiné|gestioné|optimizé|optimicé|"
        r"creé|automaticé|automatizé|estandaricé|estandarizé|mantuve|programé|"
        r"configuré|dirigí|administré|relevé|ejecuté|definí|construí|desplegué|"
        r"migré|arquitecté|conduje|analicé|evalué|integré|resolví|rediseñé|"
        r"impulsé|monitoreé|generé|capacité|facilité|transformé|aseguré|"
        r"establecí|validé|reduje|aumenté|aceleré|presenté|consolidé|unifiqué|"
        r"modelé|estructuré|operé|remedié|testeé|probé|refactoricé|prioricé|"
        r"orquesté|supervisé|planifiqué|colaboré|participé|diagnostiqué|mapeé|"
        r"confeccioné|trabajé|realicé|hice|entregué|obtuve|alcancé|promoví|"
        r"especifiqué|documenté|audité|articulé|mitigué|sinteticé|actualicé|"
        r"diseño|desarrollo|lidero|coordino|gestiono|optimizo|automatizo|"
        r"administro|ejecuto|defino|construyo|despliego|integro|resuelvo"
        r")\b"
    )

    if target_bullets:
        action_bullets = [
            b for b in target_bullets
            if re.search(ACTION_VERBS_1ST_PERSON_PATTERN, b, re.IGNORECASE) or
               re.match(r'^[*\-•]\s+(?:\*\*[^*]+:\*\*\s*)?[A-ZÁÉÍÓÚ][a-záéíóúñ]+[éí]\b', b) or
               re.match(_RE_VERBO_ACCION_EN, b)
        ]
        action_ratio = len(action_bullets) / len(target_bullets)
    else:
        action_ratio = 0.0

    if all_bullets:
        brief_bullets = [b for b in all_bullets if len(b) <= 250]
        brief_ratio = len(brief_bullets) / len(all_bullets)
        escaneabilidad_score = round((action_ratio * 10.0) + (brief_ratio * 10.0), 1)
    else:
        brief_ratio = 0.0
        escaneabilidad_score = 0.0

    # Escaneabilidad y concisión de viñetas
    char_count = len(cv_text)
    lectura_estimada_min = round(max(0.5, char_count / 1500), 1)

    final_ats_score = round(estructura_score + keywords_score + metricas_score + escaneabilidad_score, 1)
    final_ats_score = min(100.0, max(0.0, final_ats_score))

    # Blindaje contra falsos 100: ats_score solo otorga 100% si la cobertura real sustantiva es del 100%
    # y todas las dimensiones canónicas están 100% cubiertas
    if (keyword_coverage < 1.0 or
        not has_all_sections or
        not has_all_experiences or
        metricas_score < 25.0 or
        escaneabilidad_score < 20.0):
        final_ats_score = min(99.0, final_ats_score)

    # Construcción concisa de deducciones (< 100 pts)
    deducciones = []
    if estructura_score < 20.0:
        ded_pts = round(20.0 - estructura_score, 1)
        deducciones.append(f"-{ded_pts} pts en Estructura: faltan secciones obligatorias ({', '.join(missing_sections)}).")

    if keywords_score < 35.0:
        ded_pts = round(35.0 - keywords_score, 1)
        preview_kw = ", ".join(missing_kw[:3])
        more = f" (+{len(missing_kw)-3} más)" if len(missing_kw) > 3 else ""
        deducciones.append(f"-{ded_pts} pts en Keywords: faltan {len(missing_kw)} keywords clave ({preview_kw}{more}).")

    if metricas_score < 25.0:
        ded_pts = round(25.0 - metricas_score, 1)
        if total_evidencias == 0:
            deducciones.append(
                f"-{ded_pts} pts en Impacto & Métricas: sin evidencias empíricas (cuantitativas o de alcance operativo) verificables (requeridas >=3)."
            )
        else:
            deducciones.append(
                f"-{ded_pts} pts en Impacto & Métricas: detectada{'s' if total_evidencias > 1 else ''} solo {total_evidencias} evidencia{'s' if total_evidencias > 1 else ''} empírica{'s' if total_evidencias > 1 else ''} (cuantitativas o de alcance operativo) (requeridas >=3)."
            )

    if escaneabilidad_score < 20.0:
        ded_pts = round(20.0 - escaneabilidad_score, 1)
        razones = []
        if target_bullets and action_ratio < 1.0:
            razones.append("viñetas sin verbo de acción en 1ª persona")
        if all_bullets and brief_ratio < 1.0:
            razones.append("viñetas que exceden 250 caracteres")
        motivo = " o ".join(razones) if razones else "formato de viñetas no óptimo"
        deducciones.append(f"-{ded_pts} pts en Escaneabilidad: {motivo}.")

    return {
        "ats_score": final_ats_score,
        "has_all_sections": has_all_sections,
        "found_sections": found_sections,
        "missing_sections": missing_sections,
        "has_all_experiences": has_all_experiences,
        "found_experiences": found_experiences,
        "missing_experiences": missing_experiences,
        "keyword_coverage": keyword_coverage,
        "found_keywords": found_kw,
        "missing_keywords": missing_kw,
        "deducciones": deducciones,
        "longitud": {
            "caracteres": char_count,
            "lectura_estimada_min": lectura_estimada_min,
            "dentro_de_limite": True,
        },
        "dimensiones": {
            "estructura": estructura_score,
            "keywords": keywords_score,
            "metricas_empiricas": metricas_score,
            "escaneabilidad": escaneabilidad_score,
        }
    }


VEREDICTO_CON_OBSERVACIONES = "PASE CON OBSERVACIONES"


def solo_limites_del_perfil(reporte: Dict[str, Any]) -> bool:
    """¿Lo único que falla son límites del perfil (keywords o métricas que el candidato no tiene y no
    se pueden inventar)? Los defectos corregibles siguen bloqueando: señales de IA, secciones o
    empresas faltantes y cualquier bloqueo del control final (`bloqueos_finales`)."""
    if not isinstance(reporte, dict) or reporte.get("bloqueos_finales"):
        return False
    ats = reporte.get("ats") if isinstance(reporte.get("ats"), dict) else {}
    anti = reporte.get("anti_ai") if isinstance(reporte.get("anti_ai"), dict) else {}
    return (anti.get("clean") is True and ats.get("has_all_sections") is True
            and ats.get("has_all_experiences", True) is not False)


def aceptar_limites_del_perfil(reporte: Dict[str, Any]) -> Dict[str, Any]:
    """Creador, 09/11: lo que no se puede corregir sin mentir no bloquea el envío. Un dictamen que solo
    falla por límites del perfil queda aprobado «con observaciones» y conserva sus deducciones."""
    if not isinstance(reporte, dict) or reporte.get("approved") is not False or not solo_limites_del_perfil(reporte):
        return reporte
    reporte["approved"] = True
    reporte["verdict"] = VEREDICTO_CON_OBSERVACIONES
    reporte["limites_perfil"] = True
    if isinstance(reporte.get("hallazgos_unificados"), dict):
        reporte["hallazgos_unificados"]["requiere_accion"] = False
    if isinstance(reporte.get("evaluacion_profesional"), dict):
        reporte["evaluacion_profesional"]["approved"] = True
        reporte["evaluacion_profesional"]["verdict"] = VEREDICTO_CON_OBSERVACIONES
    return reporte


class AuditorAgent:
    """Subagente Auditor Ácido con micro-ciclo de remediación correctiva 100% LLM."""

    def __init__(self, bridge: Optional[AntigravityBridge] = None, effort: Optional[str] = None):
        self.bridge = bridge or AntigravityBridge(effort=effort)
        if effort and hasattr(self.bridge, "effort") and self.bridge.effort != effort:
            self.bridge.effort = effort
        self.effort = effort or getattr(self.bridge, "effort", "low")

    def audit(
        self,
        cv_markdown: str,
        pitch: str,
        keywords: List[str],
        expected_companies: Optional[List[str]] = None,
        senales_llm: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Ejecuta la auditoría doble lente sin modificar el texto. `senales_llm` (de
        `revisar_senales_ia`) suma las señales de escritura IA que vio el LLM."""
        combined_text = f"{cv_markdown}\n\n{pitch}"
        ai_scan = scan_anti_ai_writing(combined_text)
        if senales_llm:
            extra = [f"«{x['frase']}» ({x['patron']})" for x in senales_llm]
            detectadas = ai_scan["detected_words"] + extra
            ai_scan = {
                **ai_scan,
                "clean": False,
                "detected_words": detectadas,
                "anti_ai_score": max(0, 100 - len(detectadas) * 15),
                "recommendation": f"Reescribir las señales de escritura IA: {', '.join(detectadas)}",
                "senales_llm": senales_llm,
            }
        ats_eval = evaluate_ats_compliance(cv_markdown, keywords, expected_companies=expected_companies)

        # Score global ponderado: 70% ATS, 30% Anti-AI
        raw_global = (ats_eval["ats_score"] * 0.7) + (ai_scan["anti_ai_score"] * 0.3)
        if not ats_eval.get("has_all_experiences", True):
            raw_global = max(0.0, raw_global - 20.0)
        global_score = round(min(100.0, max(0.0, raw_global)), 1)

        missing_kw = ats_eval.get("missing_keywords", [])
        ai_words = ai_scan.get("detected_words", [])
        missing_exp = ats_eval.get("missing_experiences", [])

        # Deducciones unificadas de auditoría (< 100 pts)
        deducciones_totales = list(ats_eval.get("deducciones", []))
        if not ats_eval.get("has_all_experiences", True):
            deducciones_totales.append(f"-20.0 pts en Historial: omisión de empresas esperadas de la trayectoria ({', '.join(missing_exp)}).")
        if ai_scan.get("anti_ai_score", 100) < 100:
            ded_pts = 100 - ai_scan["anti_ai_score"]
            deducciones_totales.append(f"-{ded_pts} pts en Redacción: términos no naturales o faltas ortográficas ({', '.join(ai_scan['detected_words'][:3])}).")

        # Criterio estricto e independiente (ATS >= 80% y Cobertura >= 80%)
        approved = (
            ats_eval["ats_score"] >= 80.0
            and ats_eval["keyword_coverage"] >= 0.80
            and ai_scan["clean"]
            and ats_eval["has_all_sections"]
            and ats_eval.get("has_all_experiences", True)
        )

        verdict = "PASE A ENTREVISTA" if approved else "REQUIERE REMEDIACIÓN"

        findings_list = []
        if missing_kw:
            kw_preview = ", ".join(missing_kw[:6])
            more = f" (+{len(missing_kw) - 6} más)" if len(missing_kw) > 6 else ""
            findings_list.append(f"Integrar {len(missing_kw)} keywords aprobadas faltantes: {kw_preview}{more}.")
        if ai_words:
            findings_list.append(f"Remover clichés y muletillas de IA detectadas: {', '.join(ai_words)}.")
        if missing_exp:
            findings_list.append(f"Restaurar empresas obligatorias del historial laboral: {', '.join(missing_exp)}.")
        if ats_eval["ats_score"] < 80.0:
            findings_list.append(f"Score ATS ({ats_eval['ats_score']}%) inferior al piso del 80%. Reforzar viñetas con métricas empíricas.")

        hallazgos_unificados = {
            "keywords_faltantes": missing_kw,
            "palabras_ai": ai_words,
            "empresas_faltantes": missing_exp,
            "lista_observaciones": findings_list,
            "requiere_accion": not approved,
        }

        diagnostico_documento = {
            "ats_score": ats_eval["ats_score"],
            "keyword_coverage": ats_eval["keyword_coverage"],
            "found_keywords": ats_eval["found_keywords"],
            "missing_keywords": ats_eval["missing_keywords"],
            "dimensiones": ats_eval["dimensiones"],
            "has_all_sections": ats_eval["has_all_sections"],
            "has_all_experiences": ats_eval.get("has_all_experiences", True),
            "longitud": ats_eval["longitud"],
            "deducciones": ats_eval.get("deducciones", []),
            "hallazgos_unificados": hallazgos_unificados,
        }

        evaluacion_profesional = {
            "verdict": verdict,
            "approved": approved,
            "global_score": global_score,
            "anti_ai_score": ai_scan["anti_ai_score"],
            "anti_ai_clean": ai_scan["clean"],
            "anti_ai_words": ai_scan["detected_words"],
            "deducciones": deducciones_totales,
            "hallazgos_unificados": hallazgos_unificados,
        }

        return {
            "global_score": global_score,
            "approved": approved,
            "verdict": verdict,
            "ats_score": ats_eval["ats_score"],
            "anti_ai_score": ai_scan["anti_ai_score"],
            "deducciones": deducciones_totales,
            "diagnostico_documento": diagnostico_documento,
            "evaluacion_profesional": evaluacion_profesional,
            "hallazgos_unificados": hallazgos_unificados,
            "ats": ats_eval,
            "anti_ai": ai_scan,
        }

    def remediate_text_deterministic(self, text: str) -> str:
        """Aplica reemplazos deterministas de ANTI_AI_REPLACEMENTS y corrección ortográfica de verbos en -izé."""
        if not text:
            return "" if text is None else text

        def sanear_prosa(cleaned):
            replacements = (ANTI_AI_REPLACEMENTS_EN if
                idioma_de_texto(text) == EN or idioma_de_cv(text) == EN else
                {**ANTI_AI_REPLACEMENTS, **_REEMPLAZOS_NEUTROS})
            for pattern, sub in replacements.items():
                cleaned = re.sub(pattern, lambda m, sub=sub:
                    sub[:1].upper() + sub[1:] if m.group(0)[:1].isupper() else sub,
                    cleaned, flags=re.IGNORECASE)
            return cleaned

        cleaned = transformar_fragmentos(text, sanear_prosa)
        # El resto de las correcciones también respeta referencias y código.
        return transformar_fragmentos(cleaned, self._corregir_prosa_determinista)

    @staticmethod
    def _corregir_prosa_determinista(cleaned: str) -> str:
        """Ortografía y cierres separables; nunca se aplica a tokens protegidos."""
        def _fix_ize(match):
            prefix = match.group(1)
            e_accent = match.group(2)
            if match.group(0).isupper():
                return f"{prefix}IC{e_accent}"
            return f"{prefix}ic{e_accent}"

        cleaned = re.sub(r"\b([a-zA-ZáéíóúÁÉÍÓÚñÑ]*)[iI][zZ]([éÉ])\b", _fix_ize, cleaned)
        cleaned = _RE_CIERRE_VAGO_SEGURO.sub(lambda m: m.group("punto"), cleaned)
        return cleaned

    def remediate_deterministic(
        self,
        cv_markdown: str,
        keywords: List[str],
        expected_companies: Optional[List[str]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Purga heurística y quirúrgica de términos prohibidos de IA (método de contingencia)."""
        cleaned = self.remediate_text_deterministic(cv_markdown)
        audit = self.audit(cleaned, "", keywords, expected_companies=expected_companies)
        return cleaned, audit

    async def revisar_senales_ia(self, cv_markdown: str) -> List[Dict[str, str]]:
        """Lente LLM de "Signs of AI writing" (Wikipedia): patrones que una lista de palabras no ve.

        Devuelve solo señales cuya `frase` es una cita literal del CV (las demás se descartan).
        Ante cualquier fallo devuelve [] (la auditoría determinística sigue valiendo)."""
        if not (cv_markdown or "").strip():
            return []
        prompt = REVISION_SENALES_IA_PROMPT.format(cv=cv_markdown[:MAX_CV_REVISION_CHARS])
        try:
            respuesta = await self.bridge.run_prompt(prompt, json_mode=True, effort="low", timeout_seconds=120)
        except Exception:
            return []
        crudas = respuesta.get("senales") if isinstance(respuesta, dict) else None
        if not isinstance(crudas, list):
            return []
        cv_normal = _normalizar_senal(cv_markdown)
        senales: List[Dict[str, str]] = []
        for x in crudas[:MAX_SENALES_IA]:
            if not isinstance(x, dict):
                continue
            # Nunca se recorta la cita: una cita parcial reemplazada por su reescritura deja la cola
            # de la frase original pegada ("…prueba. REST** con Postman…")
            frase = re.sub(r"\s+", " ", str(x.get("frase") or "")).strip()
            if not 4 <= len(frase) <= MAX_FRASE_SENAL or _normalizar_senal(frase) not in cv_normal:
                continue
            senales.append({
                "frase": frase,
                "patron": re.sub(r"\s+", " ", str(x.get("patron") or "señal de IA")).strip()[:80],
                "reescritura": re.sub(r"\s+", " ", str(x.get("reescritura") or "")).strip()[:240],
            })
        return senales

    async def _auditar_completo(self, cv: str, pitch: str, keywords: List[str],
                                expected_companies: Optional[List[str]]) -> Dict[str, Any]:
        senales = await self.revisar_senales_ia(cv)
        return self.audit(cv, pitch, keywords, expected_companies=expected_companies, senales_llm=senales)

    async def audit_and_remediate(
        self,
        cv_markdown: str,
        pitch: str,
        keywords: List[str],
        max_attempts: int = 1,
        expected_companies: Optional[List[str]] = None,
        *, _canales: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Audita el CV en hasta dos llamadas a la IA: una revisión y una remediación consolidada.

        Contrato:
        1. Reglas deterministas primero sobre el CV y Pitch antes de cualquier llamada a la IA.
        2. Una sola revisión con IA (revisar_senales_ia) por CV; no se vuelve a pedir tras parchar.
        3. Si quedan hallazgos, una sola llamada de remediación con todas las secciones marcadas juntas.
           Sin hallazgos, cero llamadas de remediación.
        4. Verificación final sin IA con scan_anti_ai_writing + evaluate_ats_compliance + deterministas.
        5. Preservar timeout por effort, registro de fallos y formato del reporte.
        """
        self.hallazgos_por_etapa = []
        # Invariante: max_attempts <= 0 no modifica contenido ni pide remediación.
        if max_attempts <= 0:
            senales = await self.revisar_senales_ia(cv_markdown)
            report = self.audit(
                cv_markdown,
                pitch,
                keywords,
                expected_companies=expected_companies,
                senales_llm=senales,
            )
            return cv_markdown, pitch, report

        def sanear_cv(cv: str) -> str:
            # El idioma lo fija el documento; identidad, contacto y títulos no son editables.
            titulo = TITULOS[idioma_de_cv(cv)]["PERFIL"]
            lineas = cv.splitlines()
            cambios = [{"n": n, "texto": transformar_fragmentos(lineas[n], lambda parte:
                self.remediate_text_deterministic(f"## {titulo}\n{parte}").split("\n", 1)[1])}
                for n in _lineas_editables(cv)]
            return _aplicar_lineas(cv, cambios)

        # 1. Reglas deterministas primero sobre el CV y Pitch, antes de cualquier llamada a la IA
        current_cv = corregir_perfil(sanear_cv(normalizar_guiones(cv_markdown)))[0]
        current_pitch = transformar_fragmentos(normalizar_guiones(pitch), self.remediate_text_deterministic)

        # 2. Una sola revisión con IA por CV
        senales = await self.revisar_senales_ia(current_cv)

        senales_activas = _senales_presentes(current_cv, senales)
        report = self.audit(
            current_cv,
            current_pitch,
            keywords,
            expected_companies=expected_companies,
            senales_llm=senales_activas,
        )

        canales = _canales if _canales is not None else {}
        canales_marcados = {c: t for c, t in canales.items()
                            if not scan_anti_ai_writing(t)["clean"]}
        inicial = {"etapa": "auditoria_inicial",
                   "hallazgos": deepcopy(report["hallazgos_unificados"]),
                   "canales": {c: scan_anti_ai_writing(t)["detected_words"]
                               for c, t in canales_marcados.items()}}

        def consolidar(reporte):
            pendientes = [{"canal": c, "hallazgo": h}
                          for c, t in canales.items()
                          for h in scan_anti_ai_writing(t)["detected_words"]]
            if canales_marcados or pendientes:
                reporte["hallazgos_canales"] = pendientes
                if pendientes:
                    reporte["hallazgos_unificados"]["canales"] = pendientes
                bloqueantes = [f"mensaje ({h['canal']}): {h['hallazgo']}" for h in pendientes
                               if h["canal"] not in {"web_ats", "inmail"}]
                if bloqueantes:
                    reporte["approved"] = False
                    reporte["verdict"] = "REQUIERE REMEDIACIÓN"
                    unificados = reporte["hallazgos_unificados"]
                    unificados["requiere_accion"] = True
                    unificados["lista_observaciones"] = list(dict.fromkeys(
                        [*unificados["lista_observaciones"], *bloqueantes]))
                    reporte["evaluacion_profesional"]["approved"] = False
                    reporte["evaluacion_profesional"]["verdict"] = reporte["verdict"]
            self.hallazgos_por_etapa = [inicial, {
                "etapa": "auditoria_final", "hallazgos": deepcopy(reporte["hallazgos_unificados"]),
                "canales": pendientes}]
            return reporte

        # Los canales que no se envían (web_ats, inmail) nunca disparan una remediación propia:
        # viajan solo si ya hacía falta remediar; si no, quedan registrados para revisar.
        canales_que_bloquean = {c for c in canales_marcados if c not in {"web_ats", "inmail"}}
        if report["approved"] and not canales_que_bloquean:
            return current_cv, current_pitch, consolidar(report)

        # 3. Si quedan hallazgos, UNA sola llamada de remediación consolidada
        destinos = _destinos_remediacion(current_cv, current_pitch, report)
        if not destinos and not canales_marcados:
            logger.warning("[Auditor] No se remedia: no hay destino seguro para los hallazgos")
            return current_cv, current_pitch, consolidar(report)

        todos_numeros = []
        for sec in _ORDEN_DESTINOS:
            if sec in destinos and sec != "pitch":
                todos_numeros.extend(destinos[sec])
        todos_numeros = sorted(list(dict.fromkeys(todos_numeros)))
        pitch_marcado = "pitch" in destinos

        lineas_base = current_cv.splitlines()
        contexto = "\n".join("   " + l for l in lineas_base if l.startswith("#"))
        contenido = "\n".join(f"{n}: {lineas_base[n]}" for n in todos_numeros)

        missing_kw = report["ats"].get("missing_keywords", [])
        ai_words = report["anti_ai"].get("detected_words", [])
        missing_exp = report["ats"].get("missing_experiences", [])
        reescrituras = "\n".join(
            f"  * «{x['frase']}» ({x['patron']}) → {x['reescritura'] or 'reescribir con un hecho concreto'}"
            for x in report["anti_ai"].get("senales_llm") or []
        ) or "  (ninguna)"
        secciones_str = ", ".join(s for s in _ORDEN_DESTINOS if s in destinos)

        prompt = f"""
{AUDITOR_PROMPT}

DESTINOS A REMEDIAR: {secciones_str}. Sólo estas secciones pueden cambiar.
CONTEXTO INMUTABLE (no editable; no agregues hechos, métricas ni empresas):
{contexto}

CV A REMEDIAR (únicamente las líneas de las secciones marcadas, con números absolutos de base 0):
\"\"\"
{contenido}
\"\"\"

PITCH {'A REMEDIAR' if pitch_marcado else 'INMUTABLE (devolvé pitch vacío)'}:
\"\"\"
{current_pitch if pitch_marcado else ''}
\"\"\"

CANALES A REMEDIAR (JSON, sólo estas claves pueden cambiar):
{json.dumps(canales_marcados, ensure_ascii=False)}
Devolvé sus correcciones en "pitches_multicanal": {{"canal": "texto"}}.
Hallazgos por canal: {inicial['canales']}

HALLAZGOS A CORREGIR Y DIRECTIVAS EXACTAS DE AUDITORÍA:
- Keywords aprobadas FALTANTES: {missing_kw}. Van por transferencia de conocimientos (creador, 09/11): integrá en
  'HABILIDADES TÉCNICAS' las herramientas o conocimientos del mismo mundo que los de la base (Docker o cloud junto a
  DevOps y Linux; Scrum junto a Jira e historias de usuario), en la categoría que corresponda. Una keyword de otro
  mundo, un rubro («mercado de capitales»), una formación o una cantidad de años NO va a HABILIDADES: dejala afuera.
  También en la viñeta de experiencia donde esa práctica encaje, con los términos de la oferta, sin inventar
  años, roles, empresas ni fechas.
- Señales de escritura IA a eliminar (Wikipedia "Signs of AI writing"): {ai_words}
- Reescrituras sugeridas para esas señales (adaptalas; no inventes datos que el CV no tenga):
{reescrituras}
- Variá la estructura de las viñetas: no cierres más de una con una consecuencia en gerundio («…, reduciendo X»); si no hay cifra, contá el hecho sin adjetivar el impacto.
- Empresas esperadas de la trayectoria: {missing_exp}
- DIRECTIVA ESTRICTA: El CV debe tener estructura limpia ATS, tono en primera persona singular activa, y las empresas de la trayectoria sin gaps temporales.
{_DIRECTIVA_INGLES if idioma_de_cv(current_cv) == EN else ""}
- EDITÁ SOLO las líneas que tienen un hallazgo o donde una keyword faltante entra con verdad (HABILIDADES o la
  viñeta que ya cuenta esa práctica). El resto del CV ya está aprobado: no lo reescribas. No inventes datos.

Devolvé un objeto JSON con:
- "lineas": [{{"n": int (número de línea), "texto": str (la línea nueva completa, con su viñeta y Markdown)}}]
- "pitch": str (Pitch corregido sin muletillas de IA; "" si no necesita cambios)
"""
        try:
            resultado = await self.bridge.run_prompt(
                prompt,
                json_mode=True,
                effort=EFFORT_REMEDIACION,
                timeout_seconds=TIMEOUT_REMEDIACION_S,
            )
        except Exception as exc:
            logger.warning("[Auditor] Falló la remediación de %s (intento 1): %s", secciones_str, exc)
            resultado = None

        patched_cv = current_cv
        patched_pitch = current_pitch
        if isinstance(resultado, dict):
            parches_canales = resultado.get("pitches_multicanal")
            if isinstance(parches_canales, dict):
                for canal in canales_marcados:
                    nuevo = parches_canales.get(canal)
                    if isinstance(nuevo, str) and nuevo.strip():
                        canales[canal] = transformar_fragmentos(
                            normalizar_guiones(nuevo.strip()), self.remediate_text_deterministic)
            if pitch_marcado:
                nuevo_pitch = resultado.get("pitch")
                if isinstance(nuevo_pitch, str) and nuevo_pitch.strip():
                    patched_pitch = nuevo_pitch.strip()
            cambios = _cambios_del_destino(current_cv, resultado, todos_numeros)
            patched_cv = _aplicar_lineas(current_cv, cambios)

        # 4. Verificación final sin IA: scan_anti_ai_writing + evaluate_ats_compliance + reglas deterministas
        senales_restantes = _senales_presentes(patched_cv, senales)
        if senales_restantes:
            reporte_parches = self.audit(
                patched_cv, patched_pitch, keywords, expected_companies=expected_companies,
                senales_llm=senales_restantes)
            destinos = _destinos_remediacion(patched_cv, patched_pitch, reporte_parches)
            lineas = patched_cv.splitlines()
            cambios = []
            for numeros in destinos.values():
                for n in numeros:
                    linea = lineas[n]
                    nueva, _ = aplicar_reescrituras(
                        linea, [s for s in senales_restantes if s["frase"] in linea])
                    if nueva != linea:
                        cambios.append({"n": n, "texto": nueva})
            patched_cv = _aplicar_lineas(patched_cv, cambios)

        # La remediación puede reescribir el perfil: se vuelven a aplicar los controles de años y requisitos
        patched_cv = corregir_perfil(sanear_cv(normalizar_guiones(patched_cv)))[0]
        # La remediación no puede devolver a HABILIDADES rubro, formación ni años (creador, 09/11)
        patched_cv = quitar_requisitos_de_habilidades(patched_cv)[0]
        patched_pitch = transformar_fragmentos(normalizar_guiones(patched_pitch), self.remediate_text_deterministic)

        senales_finales = _senales_presentes(patched_cv, senales)
        final_report = self.audit(
            patched_cv,
            patched_pitch,
            keywords,
            expected_companies=expected_companies,
            senales_llm=senales_finales,
        )

        return patched_cv, patched_pitch, consolidar(final_report)

    async def audit_and_remediate_all(
        self,
        cv_markdown: str,
        pitch: str,
        pitches_multicanal: Optional[Dict[str, str]] = None,
        keywords: Optional[List[str]] = None,
        max_attempts: int = 1,
        expected_companies: Optional[List[str]] = None,
    ) -> Tuple[str, str, Dict[str, str], Dict[str, Any]]:
        """Audita y sanea integralmente el CV, el Pitch base y todos los canales de pitches_multicanal,
        consolidando los hallazgos localizados antes de la única remediación (sin agregar llamadas)."""
        kw_list = keywords if keywords is not None else []

        # 1. Normalización y saneamiento determinista inicial de todos los canales
        clean_pitches: Dict[str, str] = {}
        if pitches_multicanal:
            for channel, text in pitches_multicanal.items():
                if not text:
                    clean_pitches[channel] = text or ""
                    continue
                text = normalizar_guiones(text)
                cleaned_text = transformar_fragmentos(text, self.remediate_text_deterministic)
                if channel == "inmail" and len(cleaned_text) > 300:
                    trimmed = cleaned_text[:297].rsplit(" ", 1)[0] + "..."
                    if len(trimmed) > 300:
                        trimmed = cleaned_text[:300]
                    cleaned_text = trimmed
                clean_pitches[channel] = cleaned_text

        # 2. Auditoría y única remediación de CV y Pitch (1 sola llamada de remediación si es necesaria)
        cv_clean, pitch_clean, report = await self.audit_and_remediate(
            cv_markdown, pitch, kw_list, max_attempts=max_attempts, expected_companies=expected_companies,
            **({"_canales": clean_pitches} if any(
                not scan_anti_ai_writing(t)["clean"] for t in clean_pitches.values()) else {}),
        )

        self.hallazgos_por_etapa = [{
            "etapa": "saneamiento_determinista",
            "hallazgos": {c: scan_anti_ai_writing(t)["detected_words"] for c, t in
                          [("CV", cv_markdown), ("pitch", pitch),
                           *((f"mensaje ({c})", t) for c, t in (pitches_multicanal or {}).items())]},
        }, *getattr(self, "hallazgos_por_etapa", [])]

        return cv_clean, pitch_clean, clean_pitches, report
