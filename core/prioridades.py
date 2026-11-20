"""Reglas puras de prioridades, roles, captura y elegibilidad para el motor.

No importa `core.radar` ni `core.radar_store` para evitar acoplamientos y ciclos.
"""
import math
import re
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from core import candidato_datos

CARRIL_AGY: ContextVar[str] = ContextVar("carril_agy", default="analisis")


@contextmanager
def carril_agy(nombre: str):
    """Establece temporalmente el carril actual de AGY en el contexto de asyncio."""
    token = CARRIL_AGY.set(nombre)
    try:
        yield
    finally:
        CARRIL_AGY.reset(token)


def _normalizar_texto(texto: Optional[str]) -> str:
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFD", str(texto).casefold())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.split())


_PLACEHOLDERS_PUESTO = {
    "",
    "posicion no especificada",
    "puesto no especificado",
}

# Separadores conocidos que no deben sumar caracteres útiles a la captura
_RE_SEPARADORES_DESCRIPTIVOS = re.compile(
    r"(?:[\r\n]*Texto de la imagen \(dato de terceros\):[\r\n]*|[\r\n]*--- P[aá]gina de la oferta \([^)]*\) ---[\r\n]*)",
    re.IGNORECASE,
)

# Roles de Grupo B (genérico, no depende del candidato):
# Cualquier otro rol de informática: desarrollador, QA, datos, infraestructura,
# seguridad, cloud, devops, sistemas, IT, software, DBA, arquitecto, etc.
_RE_GRUPO_B = re.compile(
    r"\b(?:"
    r"desarrollador|"
    r"developer|"
    r"qa|"
    r"automation|"
    r"devops|"
    r"data engineer|"
    r"data scientist|"
    r"data analyst|"
    r"data|"
    r"datos|"
    r"cloud|"
    r"seguridad informatica|"
    r"seguridad de la informacion|"
    r"cybersecurity|ciberseguridad|soc|it security|information security|security operations|"
    r"product owner|scrum master|ingenier[oa] de software|"
    r"(?:founding|software|backend|frontend|platform|site reliability|sre|ml|ai|cloud|qa|test) engineer|"
    r"security (?:analyst|engineer|architect)|"
    r"sistemas|"
    r"administrador de sistemas|"
    r"fullstack|"
    r"frontend|"
    r"backend|"
    r"software|"
    r"dba|"
    r"it|"
    r"infraestructura|"
    r"infrastructure|"
    r"sysadmin|"
    r"redes|"
    r"programador|"
    r"tester|"
    r"testing|"
    r"computacion|"
    r"informatica|informatico|systems|networks?"
    r")\b",
    re.IGNORECASE,
)


def prioridad_rol(puesto: Optional[str]) -> str:
    """Clasifica el puesto en 'a', 'b' o 'c'.

    Unicode sin acentos, casefold, espacios normalizados.
    Coincidencias por palabras/frases completas: a precede a b, vacío/desconocido -> c.
    """
    normalizado = _normalizar_texto(puesto)
    if not normalizado or normalizado in _PLACEHOLDERS_PUESTO:
        return "c"
    # Grupo A: los roles objetivo del candidato (Mi perfil, roles_objetivo.json); sin ellos no hay A
    if candidato_datos.coincide_frase(normalizado, candidato_datos.roles()["roles_objetivo"]):
        return "a"
    if _RE_GRUPO_B.search(normalizado):
        return "b"
    return "c"


def _limpiar_separadores(texto: str) -> str:
    if not texto:
        return ""
    return _RE_SEPARADORES_DESCRIPTIVOS.sub("\n\n", texto)


def grupo_rol(post: dict) -> str:
    """Grupo a/b/c: el que dio el análisis de la oferta (lee el puesto y sus tareas); si no hay, por el título."""
    analisis = post.get("analisis") if isinstance(post.get("analisis"), dict) else {}
    grupo = str(analisis.get("grupo_rol") or "").strip().lower()
    if grupo in ("a", "b", "c"):
        return grupo
    return prioridad_rol(puesto_util(post, analisis_primero=True))


def puesto_util(post: dict, *, analisis_primero: bool = False) -> str:
    """Elige un puesto real, tolerando JSON inválido y placeholders antes del fallback."""
    fuentes = ("analisis", "triage") if analisis_primero else ("triage", "analisis")
    for fuente in fuentes:
        datos = post.get(fuente)
        puesto = datos.get("puesto") if isinstance(datos, dict) else None
        if isinstance(puesto, str) and _normalizar_texto(puesto) not in _PLACEHOLDERS_PUESTO:
            return puesto.strip()
    return ""


def clave_analisis(post: dict) -> tuple:
    """Día de publicación (lo más reciente primero), contacto, rol, fecha e id:
    compartida por motor y panel. El día es el de calendario (hora local), no una ventana de 24 h contada
    desde ahora: así dos posts del mismo día no cambian de orden según la hora en que se mira."""
    from core.colas import publicada_en  # import diferido: colas depende de este módulo
    publicada = publicada_en(post)
    dia = -publicada.astimezone().date().toordinal() if publicada is not None else 10 ** 6
    try:
        fecha = datetime.fromisoformat(post.get("created_at") or "")
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        timestamp = fecha.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        timestamp = 0.0
    return (dia, 0 if tiene_email(post) else 1, prioridad_rol(puesto_util(post)),
            -timestamp, post.get("id") or 0)


def tiene_email(post: dict) -> bool:
    triage = post.get("triage")
    triage = triage if isinstance(triage, dict) else {}
    candidatos = [post.get("email_to"), triage.get("email_destino")]
    emails = post.get("emails")
    if isinstance(emails, list):
        candidatos.extend(emails)
    return any(isinstance(valor, str) and valor.strip() for valor in candidatos)


def texto_capturado(post: dict) -> str:
    """Reúne las fuentes persistidas para análisis, CV y mail sin alterar el original."""
    texto = post.get("_texto_original", post.get("texto")) or ""
    imagen = post.get("texto_imagen") or ""
    pagina = post.get("texto_pagina") or ""
    if imagen:
        texto += "\n\nTexto de la imagen (dato de terceros):\n" + imagen
    if pagina:
        texto += "\n\n--- Página de la oferta (captura guardada) ---\n" + pagina
    return texto


# Memo de captura_suficiente y aviso_util mientras se arman las colas: con miles de posts, ordenar y repartir
# los evaluaba varias veces por post (1,1 s en GET /procesos con una base del tamaño real).
_MEMO_CAPTURA: ContextVar[Optional[dict]] = ContextVar("memo_captura", default=None)


@contextmanager
def memo_captura():
    """Dentro del bloque, cada post se evalúa una sola vez. Solo para lecturas: no cambia ningún resultado."""
    if _MEMO_CAPTURA.get() is not None:
        yield
        return
    token = _MEMO_CAPTURA.set({})
    try:
        yield
    finally:
        _MEMO_CAPTURA.reset(token)


def _memo(nombre: str, post, calcular):
    memo = _MEMO_CAPTURA.get()
    if memo is None or not isinstance(post, dict):
        return calcular(post)
    clave = (nombre, id(post))
    if clave not in memo:
        memo[clave] = (calcular(post), post)  # se guarda el post: su id no se reusa mientras viva el memo
    return memo[clave][0]


def captura_suficiente(post: dict) -> bool:
    """Verifica contenido útil combinado >= 400 y puesto no ausente ni placeholder."""
    return _memo("captura", post, _captura_suficiente)


def _captura_suficiente(post: dict) -> bool:
    if not isinstance(post, dict):
        return False
    # 1. Puesto útil de triage o análisis
    if not puesto_util(post):
        return False

    # 2. Contenido útil combinado
    texto_orig = post.get("_texto_original", post.get("texto")) or ""
    texto_img = post.get("texto_imagen") or ""
    texto_pag = post.get("texto_pagina") or ""

    partes = []
    for texto in (texto_orig, texto_img, texto_pag):
        if not isinstance(texto, str):
            continue
        for fragmento in re.split(r"\r?\n\s*\r?\n", _limpiar_separadores(texto)):
            limpio = fragmento.strip()
            normalizado = " ".join(limpio.split())
            # OCR/páginas pueden estar también dentro del texto preparado.
            # Deduplicar fragmentos evita contar una misma página dos veces,
            # incluso si fue persistida junto a otra o cambió su separador.
            if not normalizado or any(normalizado in " ".join(p.split()) for p in partes):
                continue
            partes = [p for p in partes if " ".join(p.split()) not in normalizado]
            partes.append(limpio)

    combinado = "\n".join(partes).strip()
    return len(combinado) >= 400


def aviso_util(post: dict) -> bool:
    """Captura suficiente, o un aviso corto pero completo: puesto real y un destino concreto para
    postularse (email en los datos o en el texto, o la página de la oferta leída). «Buscamos un profesional Jr. […] enviá tu CV a seleccion@empresa-ficticia.test» sirve igual."""
    return _memo("aviso", post, _aviso_util)


def _aviso_util(post: dict) -> bool:
    if captura_suficiente(post):
        return True
    if not isinstance(post, dict) or not puesto_util(post):
        return False
    if (post.get("texto_pagina") or "").strip():
        return True
    if tiene_email(post):
        return True
    # «Enviá tu CV por privado»: el destino es el autor (posts 9145 y 4676)
    if str(post.get("canal") or "").lower() == "dm" and str(post.get("destino_canal") or "").strip():
        return True
    if _aviso_completo_del_portal(post.get("fuente")):
        return True
    from core.linkedin_parser import extract_emails  # diferido: el parser no debe cargar al importar
    return bool(extract_emails(texto_capturado(post)))


_REGLAS_PORTALES_CACHE = None  # (momento, reglas)
REGLAS_PORTALES_VIGENCIA_S = 60  # se releen a lo sumo una vez por minuto


def leer_reglas_portales():
    """Las reglas de los portales, releídas a lo sumo una vez por minuto: `aviso_util` corre por cada oferta
    de las listas y releer el archivo cada vez dejó el dev al 100 % de CPU."""
    global _REGLAS_PORTALES_CACHE
    import time
    from core import reglas_portales  # diferido: lee el perfil
    ahora = time.monotonic()
    if _REGLAS_PORTALES_CACHE is None or ahora - _REGLAS_PORTALES_CACHE[0] > REGLAS_PORTALES_VIGENCIA_S:
        _REGLAS_PORTALES_CACHE = (ahora, reglas_portales.leer_reglas_portales())
    return _REGLAS_PORTALES_CACHE[1]


def _aviso_completo_del_portal(fuente) -> bool:
    """El portal ya trae el aviso completo, aunque sea corto (Indeed, ofertas 10943 y 11156): su regla del perfil
    no pide leer la página. Jooble trae un resumen y sí la pide."""
    if not isinstance(fuente, str) or not fuente:
        return False
    try:
        regla = leer_reglas_portales()[0].get(fuente)
    except Exception:
        return False
    return regla is not None and not regla.leer_pagina_aviso


def elegible_automatico(post: dict) -> bool:
    """Encolado automático: sin no_apto, > 70 estricto, abierta, captura válida, sin mensaje previo."""
    if not isinstance(post, dict):
        return False
    analisis = post.get("analisis")
    if not isinstance(analisis, dict):
        return False
    no_apto = analisis.get("no_apto")
    if isinstance(no_apto, dict) and no_apto.get("motivos"):
        return False
    score = analisis.get("match_score")
    try:
        score_val = float(score)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(score_val) or score_val <= 70.0:
        return False
    if bool(analisis.get("oferta_cerrada")):
        return False
    if post.get("email_body") and str(post["email_body"]).strip():
        return False
    if not aviso_util(post):
        return False
    return True
