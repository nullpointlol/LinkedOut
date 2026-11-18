"""Parser puro de resultados de búsqueda de LinkedIn y extracción de emails.

Módulo sin red ni playwright: recibe HTML (o texto) y devuelve estructuras simples.
Lo consume el harvester (Task 6) y el loop del Radar filtra los posts por `emails`.
"""
import re
import unicodedata
from typing import Iterable, List, Optional, Tuple, TypedDict
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit

from bs4 import BeautifulSoup, Tag

RawPost = TypedDict("RawPost", {
    "urn": str,
    "url": str,               # perfil del autor (destino del canal "dm")
    "post_url": str,          # link a la publicación; vacío si la página no lo trae
    "autor": str,
    "cargo_autor": str,
    "texto": str,
    "fecha_rel": str,
    "emails": List[str],
    "links": List[str],       # URLs https externas a LinkedIn (o lnkd.in / jobs/view), sin duplicados
    "whatsapp": List[str],    # números en solo dígitos y con código de país (ej. "5491155555555")
    "imagenes": List[str],    # URLs https de imágenes de contenido (máx. 4, solo feedshare de media.licdn.com)
})

_URN_PREFIX = "urn:li:activity:"
_POST_URL_TEMPLATE = "https://www.linkedin.com/feed/update/{urn}/"
_SEARCH_URL_BASE = "https://www.linkedin.com/search/results/content/"
# Valores de filtro: cadenas JSON entrecomilladas, codificadas como %22...%22
_SORT_BY = quote('"date_posted"', safe="")
_DATE_POSTED = quote('"past-week"', safe="")

# Selectores del DOM actual de LinkedIn (el texto es el único imprescindible)
_SEL_AUTOR = ".update-components-actor__title"
_SEL_CARGO = ".update-components-actor__description"
_SEL_FECHA = ".update-components-actor__sub-description"
_SEL_ACTOR = ".update-components-actor"
_SEL_TEXTO = ".update-components-text, .feed-shared-inline-show-more-text"
_SEL_TOGGLE_VER_MAS = "button, [class*='see-more-less-toggle']"

# DOM de noviembre 2026: clases ofuscadas; solo role/componentkey/data-testid/aria-label son estables
_SEL_TARJETA = '[role="listitem"][componentkey^="update-card"]'
_ID_TEXTO_TARJETA = "expandable-text-box"
_SEL_TEXTO_TARJETA = f'[data-testid="{_ID_TEXTO_TARJETA}"]'
_PREFIJO_TARJETA = "update-card-focus"
_URN_TARJETA = "urn:li:card:"
_PREFIJO_ARIA_PERFIL = "Ver perfil de "
_RE_FECHA_REL = re.compile(
    r"^\d+\s?(?:s|seg|min|minutos?|h|horas?|d|días?|sem|semanas?|m|mes|meses|a|año|años)\.?$",
    re.IGNORECASE,
)
# Líneas del encabezado de una tarjeta del feed que NO son el autor: contexto social
# ("X recomienda esto", "Pedro y Ana recomiendan esto", "X y 3 personas más…", "X comentó"),
# notificaciones de grupo, sugerencias, anuncios y contador de seguidores. Se aplica al texto sin tildes.
_RE_CONTEXTO = re.compile(
    r"(?i)\b(?:"
    r"recomiendan?|recomendo|recomendaron|ha(?:n)? recomendado"
    r"|comentan?|comento|comentaron|ha(?:n)? comentado"
    r"|reaccionan?|reacciono|reaccionaron|ha(?:n)? reaccionado"
    r"|comparten?|compartio|compartieron|ha(?:n)? compartido"
    r"|volvio a publicar|volvieron a publicar|ha(?:n)? publicado"
    r"|les? (?:gustan?|encantan?|parecen?|divierten?|importan?|interesan?)"
    r"|siguen? esta pagina|celebran?|apoyan?|reposte\w*|creen? que esto"
    r"|y \d[\d.,]* personas? mas|y otr[oa]s(?: \d[\d.,]*)?(?: personas?)?"
    r"|likes? this|loves? this|celebrates? this|reposted|supports? this|finds? this|commented|follows"
    r"|and \d[\d.,]* (?:others?|people)|and others"
    r")\b"
    r"|^quien ha visto tu perfil|^promocionado$|^\d[\d.,]*\s+seguidores$"
    r"|^seguid[oa]s? por\b|^followed by\b"
    r"|^(?:nuevo comentario|nueva publicacion|nuevas publicaciones|actividad|publico) en tu grupo\b"
    r"|^new (?:comment|post) in your group\b"
)
# Contexto estructural: <p> con un enlace a un perfil seguido de texto (fuera del enlace) que
# termina en "esto"/"this" ("<a>Pedro</a> y <a>Ana</a> recomiendan esto")
_RE_FIN_CONTEXTO = re.compile(r"(?i)\b(?:esto|this)\W*$")
# Líneas que nunca son autor ni cargo (valen también después de haber encontrado el autor)
_RE_RUIDO_ENCABEZADO = re.compile(
    r"(?i)^quien ha visto tu perfil|^promocionado$|^\d[\d.,]*\s+seguidores$"
    # Texto de botones del encabezado de la tarjeta
    r"|^(?:contactar|seguir|siguiendo|conectar|enviar mensaje|follow|following|connect|message)$"
)


# ---------------------------------------------------------------------------
# Extracción de emails
# ---------------------------------------------------------------------------

# Ofuscaciones entre corchetes: "[at]", "(arroba)", "{dot}", "(punto)"...
# Solo espacios y tabs (nunca saltos de línea) para no unir líneas distintas.
_RE_OBF_ARROBA = re.compile(r"[ \t]*[\[({<][ \t]*(?:at|arroba)[ \t]*[\])}>][ \t]*", re.IGNORECASE)
_RE_OBF_PUNTO = re.compile(r"[ \t]*[\[({<][ \t]*(?:dot|punto)[ \t]*[\])}>][ \t]*", re.IGNORECASE)

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
# Una etiqueta Capitalizada ("Gracias") después del primer punto no es parte del dominio:
# evita pegar la palabra siguiente cuando falta el espacio tras el punto final de la oración
_LABEL_NO_CAP = rf"(?![A-Z][a-z]){_LABEL}"
_LABEL_MINUS = r"[a-z0-9]+(?:-[a-z0-9]+)*"


def _separador(etiqueta: str) -> str:
    """Separadores de dominio: ".", " . " (etiqueta en minúscula, para no absorber una oración
    que sigue a un punto suelto) o " dot "."""
    return (
        rf"(?:\.{etiqueta}"
        rf"|[ \t]+\.[ \t]+{_LABEL_MINUS}"
        rf"|[ \t]+(?i:dot)[ \t]+{etiqueta})"
    )


# El primer separador acepta cualquier etiqueta; los siguientes no aceptan Capitalizadas
_DOMINIO = rf"{_LABEL}{_separador(_LABEL)}(?:{_separador(_LABEL_NO_CAP)})*"
_RE_CANDIDATO = re.compile(
    r"(?<![\w.%+-])"                               # inicio de token
    r"(?P<local>[A-Za-z0-9._%+-]+)"
    r"(?P<conector>"
    r"@|[ \t]+@|@[ \t]+"                           # '@', ' @x', '@ x': un solo lado con espacio
    r"|[ \t]+@[ \t]+"                              # ' @ ': exige dominio con puntos espaciados
    r"|[ \t]+(?i:at)[ \t]+"                        # ' at ': exige dominio con ' dot '
    r")"
    rf"(?P<dominio>{_DOMINIO})"                    # al menos un separador: "at the office" no matchea
)
_RE_SEP_ESPACIADO = re.compile(r"[ \t]+(?i:dot)[ \t]+|[ \t]+\.[ \t]+")
_RE_CON_ESPACIOS = re.compile(r"[ \t]")
_RE_CON_DOT_PALABRA = re.compile(r"[ \t](?i:dot)[ \t]")
_RE_EMAIL_VALIDO = re.compile(r"[a-z0-9][a-z0-9._%+-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}")

_DOMINIOS_EXCLUIDOS = ("linkedin.com", "example.com", "ejemplo.com")
_LOCALES_EXCLUIDOS = ("ejemplo",)


def _dominio_excluido(dominio: str) -> bool:
    return any(dominio == d or dominio.endswith("." + d) for d in _DOMINIOS_EXCLUIDOS)


def extract_emails(text: str) -> List[str]:
    """Extrae emails de un texto: minúsculas, sin duplicados, en orden de aparición.

    Desofusca variantes habituales ("[at]", "(arroba)", "jobs @ acme . com",
    "jobs at acme dot com"). Un "at" suelto solo cuenta con dominio en palabras ("dot") y
    " @ " con ambos lados espaciados solo con dominio espaciado, así que la prosa
    ("we are at the office", "contact us at acme.com", "Engineer @ stripe.com") no genera emails.
    Descarta emails de linkedin.com, dominios de ejemplo y el local-part "ejemplo".
    """
    if not text:
        return []
    normalizado = _RE_OBF_ARROBA.sub("@", text)
    normalizado = _RE_OBF_PUNTO.sub(".", normalizado)

    resultado: List[str] = []
    for m in _RE_CANDIDATO.finditer(normalizado):
        conector = m.group("conector")
        crudo = m.group("dominio")
        # Un 'at' suelto solo vale con dominio en palabras ("x at y dot z") y ' @ ' con ambos
        # lados espaciados solo con dominio espaciado ("x @ y . z"): evita "Engineer @ stripe.com"
        if conector.strip().lower() == "at" and not _RE_CON_DOT_PALABRA.search(crudo):
            continue
        if (conector.strip() == "@" and conector[0].isspace() and conector[-1].isspace()
                and not _RE_CON_ESPACIOS.search(crudo)):
            continue
        local = m.group("local").lstrip(".")
        dominio = _RE_SEP_ESPACIADO.sub(".", crudo)
        email = f"{local}@{dominio}".lower()
        if ".." in email or not _RE_EMAIL_VALIDO.fullmatch(email):
            continue
        local, dominio = email.split("@", 1)
        if local in _LOCALES_EXCLUIDOS or _dominio_excluido(dominio):
            continue
        if email not in resultado:
            resultado.append(email)
    return resultado


# ---------------------------------------------------------------------------
# WhatsApp, links y filtro de posible búsqueda laboral
# ---------------------------------------------------------------------------

# Enlaces de WhatsApp con el número en la URL (wa.me/<n>, api|web.whatsapp.com/send?phone=<n>)
_RE_WA_ME = re.compile(r"(?i)\bwa\.me/\+?(\d{8,15})\b")
_RE_WA_SEND = re.compile(r"(?i)\bwhatsapp\.com/send/?\?[^\s\"'<>]*?\bphone=(?:\+|%2B)?(\d{8,15})\b")
# Número precedido (a ≤ 40 caracteres) por "whatsapp" / "wsp" / "wpp"
_RE_WA_CLAVE = re.compile(
    r"(?i)\b(?:whats\s?app|whatsap|wsp|wpp|wapp)\b[^\d+\n]{0,40}(\+?\d[\d \t().-]{6,22}\d)"
)
# Prefijos de país sin "+" que se aceptan (ninguno choca con un código de área argentino:
# estos empiezan con 11, 2 o 3)
_PREFIJOS_PAIS_SIN_MAS = ("51", "52", "54", "55", "56", "57", "58", "591", "593", "595", "598")
_RE_URL_WA = re.compile(r"(?i)(?:^|[/.])(?:wa\.me|api\.whatsapp\.com|web\.whatsapp\.com|whatsapp\.com)(?:/|$)")


def _numero_con_pais(crudo: str) -> str:
    """'+54 9 11 4444-3333' -> '5491144443333'; '' si no trae código de país explícito.

    Sin "+" (ni "00") solo se acepta un prefijo de país conocido con largo coherente: un
    número local ("011 4444 3333", "11 4444-3333") no se completa ni se inventa.
    """
    con_mas = crudo.strip().startswith("+")
    digitos = re.sub(r"\D", "", crudo)
    if digitos.startswith("00"):
        digitos, con_mas = digitos[2:], True
    if not 8 <= len(digitos) <= 15 or digitos.startswith("0"):
        return ""
    if con_mas:
        return digitos
    if digitos.startswith(_PREFIJOS_PAIS_SIN_MAS) and 11 <= len(digitos) <= 13:
        return digitos
    return ""


def extract_whatsapp(texto: str, hrefs: List[str]) -> List[str]:
    """Números de WhatsApp (solo dígitos, con código de país) de un texto y de sus hrefs.

    Reconoce `wa.me/<n>`, `api.whatsapp.com/send?phone=<n>` (en el texto o en los hrefs) y
    números escritos a ≤ 40 caracteres de "whatsapp"/"wsp"/"wpp". Un móvil argentino
    "+54 9 11 …" queda `549…`. Sin código de país no se devuelve nada. Sin duplicados.
    """
    resultado: List[str] = []

    def agregar(numero: str) -> None:
        if numero and numero not in resultado:
            resultado.append(numero)

    fuentes = [unquote(h) for h in hrefs if _RE_URL_WA.search(h or "")]
    fuentes.append(texto or "")
    for fuente in fuentes:
        for patron in (_RE_WA_ME, _RE_WA_SEND):
            for m in patron.finditer(fuente):
                agregar(m.group(1))
    for m in _RE_WA_CLAVE.finditer(texto or ""):
        agregar(_numero_con_pais(m.group(1)))
    return resultado


_RE_URL_EN_TEXTO = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_PUNTUACION_FINAL = ".,;:!?)]}»\"'"


def _es_dominio(host: str, dominio: str) -> bool:
    return host == dominio or host.endswith("." + dominio)


def _normalizar_link(href: str) -> str:
    """URL `https://` externa útil o ''.

    Desenvuelve las redirecciones de LinkedIn (`/safety/go/?url=<destino>`) y descarta
    perfiles, empresas, hashtags, búsquedas y el feed de LinkedIn, `mailto:`, `http:` y
    WhatsApp (esos van a `whatsapp`). Se conservan `lnkd.in` y `linkedin.com/jobs/view/…`.
    """
    href = (href or "").strip().rstrip(_PUNTUACION_FINAL)
    try:
        partes = urlparse(href)
    except ValueError:
        return ""
    if partes.scheme.lower() != "https" or not partes.hostname:
        return ""
    host = partes.hostname.lower()
    if _es_dominio(host, "linkedin.com"):
        if partes.path.lower().startswith("/safety/go"):
            destino = parse_qs(partes.query).get("url", [""])[0]
            return _normalizar_link(destino) if destino else ""
        if partes.path.lower().startswith("/jobs/view/"):
            return href
        return ""
    if host == "lnkd.in":
        return href
    if _es_dominio(host, "licdn.com") or any(_es_dominio(host, d) for d in ("wa.me", "whatsapp.com")):
        return ""
    return href


def extract_links(texto: str, hrefs: Iterable[str]) -> List[str]:
    """Links externos de los hrefs (en orden) y de las URLs escritas en el texto; sin duplicados."""
    resultado: List[str] = []
    for crudo in list(hrefs) + _RE_URL_EN_TEXTO.findall(texto or ""):
        link = _normalizar_link(crudo)
        if link and link not in resultado:
            resultado.append(link)
    return resultado


# Filtro amplio ES/EN/PT de "posible búsqueda laboral" (texto sin tildes, en minúscula)
_RE_POSIBLE_BUSQUEDA = re.compile(
    r"\b(?:buscamos|estamos buscando|busqueda|vacantes?|contratando|sumate|postulate|postula"
    r"|envia tu cv|envie seu cv|cv a|oportunidad(?:es)? laboral(?:es)?|hiring|job opening|open position"
    r"|apply|vagas?"
    r"|incorporamos|incorporando|seleccionamos|se busca|se necesita|reclutando|contratamos"
    r"|ofertas? laboral(?:es)?|envi[ae]nos tu cv|manda tu cv|deja tu cv|aplica aca|join our team"
    r"|we are looking for|posicion disponible|nueva posicion)\b"
    r"|#(?:empleo|trabajo|hiring|job)\b"
)


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def es_posible_busqueda(texto: str) -> bool:
    """True si el texto parece una búsqueda laboral (filtro amplio ES/EN/PT, sin distinguir mayúsculas)."""
    return bool(_RE_POSIBLE_BUSQUEDA.search(_sin_tildes((texto or "").lower())))


# ---------------------------------------------------------------------------
# Parser de posts
# ---------------------------------------------------------------------------

def _limpiar_texto(texto: str) -> str:
    """Colapsa espacios por línea y descarta líneas vacías."""
    lineas = (re.sub(r"[ \t ]+", " ", ln).strip() for ln in texto.splitlines())
    return "\n".join(ln for ln in lineas if ln)


def _texto_visible(el: Tag) -> str:
    """Texto de un elemento del actor: prefiere el span aria-hidden (el visually-hidden lo duplica)."""
    oculto = el.select_one('[aria-hidden="true"]')
    crudo = (oculto or el).get_text(" ", strip=True)
    return re.sub(r"\s+", " ", crudo).strip()


def _fecha_relativa(el: Tag) -> str:
    """'3 d • ' -> '3 d' (descarta el separador y marcas como 'Editado')."""
    for parte in _texto_visible(el).split("•"):
        if parte.strip():
            return parte.strip()
    return ""


def _es_contenedor_post(tag: Tag) -> bool:
    return str(tag.get("data-urn", "")).startswith(_URN_PREFIX)


def _extraer_texto(contenedor: Tag, selector: str = _SEL_TEXTO) -> str:
    return _texto_de_bloques(contenedor.select(selector))


def _texto_de_bloques(bloques: List[Tag]) -> str:
    ids = {id(b) for b in bloques}
    partes: List[str] = []
    for bloque in bloques:
        # Solo el bloque más externo: el interior ya está incluido en su texto
        if any(id(p) in ids for p in bloque.parents):
            continue
        for ruido in bloque.select(_SEL_TOGGLE_VER_MAS):
            ruido.decompose()
        for br in bloque.find_all("br"):
            br.replace_with("\n")
        texto = _limpiar_texto(bloque.get_text(""))
        if texto and texto not in partes:
            partes.append(texto)
    return "\n".join(partes)


def _emails_mailto(contenedor: Tag) -> str:
    return _destinos_mailto(contenedor.select('a[href^="mailto:"]'))


def _destinos_mailto(anclas: Iterable[Tag]) -> str:
    destinos = []
    for a in anclas:
        href = str(a.get("href", ""))
        if href.lower().startswith("mailto:"):
            destinos.append(unquote(href[len("mailto:"):]).split("?")[0])
    return " ".join(destinos)


def _clave_tarjeta(tarjeta: Tag) -> str:
    """'update-card-focus<ID>FeedType_...' -> '<ID>' (id estable del post en el DOM de 2026)."""
    clave = str(tarjeta.get("componentkey", ""))[len(_PREFIJO_TARJETA):]
    return clave.split("FeedType", 1)[0] or clave


_RE_RUTA_AUTOR = re.compile(r"/(?:in|company)/")
# Ids de la publicación en el DOM de 2026: el bloque traducible del texto trae su share/ugcPost y
# los posts de grupos un link con `highlightedUpdateUrn`. Muchos posts no traen ninguno: el
# harvester lo pide con "Copiar enlace a la publicación".
_RE_ID_TRADUCIBLE = re.compile(r"\b(shareId|ugcPostId)=(\d{15,20})\b")
_TIPO_URN_TRADUCIBLE = {"shareId": "share", "ugcPostId": "ugcPost"}
_RE_URN_ACTIVIDAD = re.compile(r"urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)(\d{15,20})", re.IGNORECASE)


def url_de_urn(tipo: str, numero: str) -> str:
    return _POST_URL_TEMPLATE.format(urn=f"urn:li:{tipo}:{numero}")


def _url_publicacion(tarjeta: Tag, caja: Optional[Tag], anclas: List[Tag]) -> str:
    """Resuelve el id propio antes de la barra social, incluso en posts sin texto.

    Prioriza el bloque traducible del texto cuando existe y excluye reposts anidados.
    """
    candidatos = []
    if caja is not None:
        ancestros = []
        for padre in caja.parents:
            if padre is tarjeta:
                break
            ancestros.append(padre)
        candidatos.extend([caja, *ancestros, *caja.find_all(True)])
    candidatos.extend([tarjeta, *[n for n in _nodos_antes(tarjeta, _fin_del_post(tarjeta))
                                  if isinstance(n, Tag)]])
    for nodo in candidatos:
        if not _es_nodo_del_post(nodo, tarjeta):
            continue
        for atributo in ("componentkey", "id"):
            m = _RE_ID_TRADUCIBLE.search(str(nodo.get(atributo) or ""))
            if m:
                return url_de_urn(_TIPO_URN_TRADUCIBLE[m.group(1)], m.group(2))
    for a in anclas:
        if not _es_nodo_del_post(a, tarjeta):
            continue
        href = unquote(str(a.get("href", "")))
        if "highlightedUpdateUrn=" in href or "/feed/update/" in href:
            m = _RE_URN_ACTIVIDAD.search(href)
            if m:
                tipo = {"activity": "activity", "share": "share", "ugcpost": "ugcPost"}[m.group(1).lower()]
                return url_de_urn(tipo, m.group(2))
    return ""


def _es_nodo_del_post(nodo: Tag, contenedor: Tag) -> bool:
    """Excluye el contenedor y los descendientes de publicaciones anidadas."""
    if nodo is contenedor:
        return True
    for padre in [nodo, *nodo.parents]:
        if padre is contenedor:
            return True
        if (str(padre.get("data-urn", "")).startswith(("urn:li:activity:", "urn:li:share:", "urn:li:ugcPost:"))
                or (padre.get("role") == "listitem"
                    and str(padre.get("componentkey", "")).startswith(_PREFIJO_TARJETA))):
            return False
    return False


def _hrefs(contenedor: Tag) -> List[str]:
    return [str(a["href"]) for a in contenedor.select("a[href]")]


def _nodos_antes(tarjeta: Tag, limite: Optional[Tag]):
    """Nodos de la tarjeta, en orden de documento, hasta llegar a `limite` (sin él ni sus hijos).
    Con `limite=None` recorre la tarjeta entera."""
    for nodo in tarjeta.descendants:
        if nodo is limite:
            return
        yield nodo


# Fin del post dentro de una tarjeta: la barra de acciones sociales (Recomendar/Comentar/...) o, si no
# se la reconoce, el bloque de comentarios. Lo que sigue (comentarios de terceros, con sus propios
# emails, links o WhatsApp) nunca es el post ni un destino de postulación.
_RE_BARRA_SOCIAL = re.compile(
    r"(?i)^(?:comentar|comment|recomendar|like|reaccionar|react|me gusta"
    r"|estado del bot[oó]n de reacci[oó]n|reaction button state)\b"
)


def _es_bloque_comentarios(tag: Tag) -> bool:
    return ("commentlist" in str(tag.get("data-testid", "")).lower()
            or "urn:li:comment:" in str(tag.get("componentkey", "")))


def _fin_del_post(tarjeta: Tag) -> Optional[Tag]:
    """Primer nodo que ya no es el post: la barra social o el bloque de comentarios (None si no hay)."""
    for nodo in tarjeta.descendants:
        if not isinstance(nodo, Tag):
            continue
        if nodo.name == "button" and _RE_BARRA_SOCIAL.match(str(nodo.get("aria-label", "")).strip()):
            return nodo
        if _es_bloque_comentarios(nodo):
            return nodo
    return None


def _etiquetas(a: Tag) -> str:
    """Texto y aria-label del enlace y de sus hijos (el nombre puede estar solo en un aria-label)."""
    partes = [a.get_text(" ", strip=True), str(a.get("aria-label", ""))]
    partes += [str(el["aria-label"]) for el in a.select("[aria-label]")]
    return " ".join(partes).casefold()


_EXCLUSIONES_PATH_IMG = ("profile-displayphoto", "profile-framedphoto", "company-logo")


def _es_imagen_feedshare_valida(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if url.startswith("data:"):
        return False
    try:
        partes = urlsplit(url)
    except Exception:
        return False
    if (partes.scheme or "").lower() != "https":
        return False
    if (partes.hostname or "").lower() != "media.licdn.com":
        return False
    if partes.username or partes.password:
        return False
    path = partes.path or ""
    if "feedshare" not in path:
        return False
    if any(excl in path for excl in _EXCLUSIONES_PATH_IMG):
        return False
    return True


def _extraer_url_img(img: Tag) -> Optional[str]:
    for attr in ("src", "data-delayed-url", "data-src"):
        val = img.get(attr)
        if val and isinstance(val, str) and _es_imagen_feedshare_valida(val):
            return val.strip()
    return None


def _es_nodo_comentario(tag: Tag) -> bool:
    for p in [tag, *tag.parents]:
        if not isinstance(p, Tag):
            continue
        if _es_bloque_comentarios(p):
            return True
        cls = " ".join(p.get("class", []) if isinstance(p.get("class"), list) else [str(p.get("class", ""))]).lower()
        if "comment" in cls or "comments" in cls:
            return True
        testid = str(p.get("data-testid", "")).lower()
        if "comment" in testid:
            return True
    return False


def _extraer_imagenes_de_contenedor(contenedor: Tag, nodos: Optional[List[Tag]] = None) -> List[str]:
    if nodos is not None:
        candidatos = [n for n in nodos if n.name == "img"]
    else:
        candidatos = contenedor.find_all("img")
    imagenes: List[str] = []
    for img in candidatos:
        if _es_nodo_comentario(img):
            continue
        # Limitar la búsqueda de contenedores de otras publicaciones a los ancestros
        # de la imagen anteriores a `contenedor`.
        es_de_otro_post = False
        for p in img.parents:
            if p is contenedor:
                break
            if (
                str(p.get("data-urn", "")).startswith(("urn:li:activity:", "urn:li:share:", "urn:li:ugcPost:"))
                or (p.get("role") == "listitem" and str(p.get("componentkey", "")).startswith(_PREFIJO_TARJETA))
            ):
                es_de_otro_post = True
                break
        if es_de_otro_post:
            continue
        url_img = _extraer_url_img(img)
        if url_img and url_img not in imagenes:
            imagenes.append(url_img)
            if len(imagenes) == 4:
                break
    return imagenes


def _url_autor(tarjeta: Tag, caja: Optional[Tag], autor: str, fin: Optional[Tag] = None) -> str:
    """Perfil o página del autor: el primer `/in/` o `/company/` (antes del texto) cuyo texto o
    aria-label contiene el nombre; si no hay, el último antes del texto. Así la persona de la
    línea de contexto ("X recomienda esto") no se confunde con el autor del post."""
    limite = caja if caja is not None else fin
    candidatos = [
        n for n in _nodos_antes(tarjeta, limite)
        if isinstance(n, Tag) and n.name == "a" and n.get("href")
        and _RE_RUTA_AUTOR.search(urlparse(str(n["href"])).path)
    ]
    if autor:
        for a in candidatos:
            if autor.casefold() in _etiquetas(a):
                return str(a["href"])
    return str(candidatos[-1]["href"]) if candidatos else ""


def _es_perfil(a: Tag) -> bool:
    return bool(a.get("href")) and bool(_RE_RUTA_AUTOR.search(urlparse(str(a["href"])).path))


def _es_contexto_estructural(p: Tag) -> bool:
    """`<p>` con un enlace a un perfil y, fuera del enlace, texto que termina en "esto"/"this":
    "<a>Pedro</a> y <a>Ana</a> recomiendan esto" es contexto social, no el autor."""
    if not any(_es_perfil(a) for a in p.find_all("a")):
        return False
    fuera = []
    for cadena in p.find_all(string=True):
        ancla = cadena.find_parent("a")
        # Fuera de todo enlace interior al <p> (un enlace que envuelve al <p> entero no cuenta)
        if ancla is None or ancla in p.parents:
            fuera.append(str(cadena))
    resto = re.sub(r"\s+", " ", _sin_tildes("".join(fuera))).strip()
    return bool(_RE_FIN_CONTEXTO.search(resto))


def _lineas_encabezado(tarjeta: Tag, caja: Optional[Tag], fin: Optional[Tag] = None) -> List[Tuple[str, bool]]:
    """(texto, es_contexto) de los `<p>` del encabezado (antes del cuerpo del post), sin vacíos ni
    servicios. `es_contexto` marca la línea de contexto social reconocida por su estructura."""
    limite = caja if caja is not None else fin
    lineas: List[Tuple[str, bool]] = []
    for p in tarjeta.find_all("p"):
        if limite is not None and (p is limite or limite in p.parents or p in limite.parents):
            break
        linea = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if linea and p.find_parent("a", href=re.compile(r"/services/")) is None:
            lineas.append((linea, _es_contexto_estructural(p)))
    return lineas


def _parse_tarjeta(tarjeta: Tag) -> "RawPost | None":
    """Post del DOM de noviembre 2026 (búsqueda y feed; sin data-urn ni permalink en la página).

    Texto, emails, links y WhatsApp salen solo del post: lo anterior a la barra de acciones
    sociales (encabezado, cuerpo y vista previa de enlaces). Los comentarios quedan afuera."""
    clave = _clave_tarjeta(tarjeta)
    if not clave:
        return None
    fin = _fin_del_post(tarjeta)
    nodos_post = [n for n in _nodos_antes(tarjeta, fin)
                  if isinstance(n, Tag) and _es_nodo_del_post(n, tarjeta)]
    cajas = [n for n in nodos_post if n.get("data-testid") == _ID_TEXTO_TARJETA]
    caja = cajas[0] if cajas else None
    texto = _texto_de_bloques(cajas) if cajas else ""
    imagenes = _extraer_imagenes_de_contenedor(tarjeta, nodos_post)
    if not texto and not imagenes:
        return None

    # Encabezado: [contexto social] autor, grado ("• 2º"), cargo, fecha relativa. Las líneas de
    # contexto ("X recomienda esto", "Promocionado", "N seguidores") no son autor ni cargo.
    autor, cargo, fecha = "", "", ""
    for linea, es_contexto in _lineas_encabezado(tarjeta, caja, fin):
        if linea.startswith("•"):
            continue
        if not autor:
            if not es_contexto and not _RE_CONTEXTO.search(_sin_tildes(linea)):
                autor = linea
            continue
        if linea == autor or _RE_RUIDO_ENCABEZADO.search(_sin_tildes(linea)):
            continue
        # La fecha puede venir sola ("2 h") o dentro de "junto con X • 2 h • Editado"
        partes_fecha = [x.strip() for x in linea.split("•") if _RE_FECHA_REL.match(x.strip())]
        if partes_fecha:
            fecha = fecha or partes_fecha[0]
        elif not cargo:
            cargo = linea
    if not autor:
        perfil = tarjeta.select_one(f'[aria-label^="{_PREFIJO_ARIA_PERFIL}"]')
        autor = str(perfil["aria-label"])[len(_PREFIJO_ARIA_PERFIL):].strip() if perfil else ""

    anclas = [n for n in nodos_post if n.name == "a" and n.get("href")]
    hrefs = [str(a["href"]) for a in anclas]
    return {
        "urn": _URN_TARJETA + clave,
        "url": _url_autor(tarjeta, caja, autor, fin),
        "post_url": _url_publicacion(tarjeta, caja, anclas),
        "autor": autor,
        "cargo_autor": cargo,
        "texto": texto,
        "fecha_rel": fecha,
        "emails": extract_emails(texto + "\n" + _destinos_mailto(anclas)),
        "links": extract_links(texto, hrefs),
        "whatsapp": extract_whatsapp(texto, hrefs),
        "imagenes": imagenes,
    }


def parse_posts_html(html: str) -> List[RawPost]:
    """Devuelve todos los posts de la página (con o sin email; el filtro lo hace el loop).

    Soporta el DOM de noviembre 2026 (tarjetas role="listitem") y el anterior (data-urn).
    Tolera que falte cualquier sub-selector salvo el texto: los posts sin texto se omiten.
    Los contenedores anidados (reposts) y los urn repetidos no generan duplicados.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    posts: List[RawPost] = []
    for tarjeta in soup.select(_SEL_TARJETA):
        post = _parse_tarjeta(tarjeta)
        if post and all(p["urn"] != post["urn"] for p in posts):
            posts.append(post)
    if posts:
        return posts
    vistos = set()
    for contenedor in soup.find_all(_es_contenedor_post):
        if contenedor.find_parent(_es_contenedor_post) is not None:
            continue
        urn = str(contenedor["data-urn"])
        if urn in vistos:
            continue
        texto = _extraer_texto(contenedor)
        imagenes = _extraer_imagenes_de_contenedor(contenedor)
        if not texto and not imagenes:
            continue
        vistos.add(urn)

        # Datos del actor: solo del primer bloque de actor (el de un repost interior es otro autor)
        zona = contenedor.select_one(_SEL_ACTOR) or contenedor
        autor = zona.select_one(_SEL_AUTOR)
        cargo = zona.select_one(_SEL_CARGO)
        fecha = zona.select_one(_SEL_FECHA)
        hrefs = _hrefs(contenedor)

        posts.append({
            "urn": urn,
            "url": _POST_URL_TEMPLATE.format(urn=urn),
            "post_url": _POST_URL_TEMPLATE.format(urn=urn),
            "autor": _texto_visible(autor) if autor else "",
            "cargo_autor": _texto_visible(cargo) if cargo else "",
            "texto": texto,
            "fecha_rel": _fecha_relativa(fecha) if fecha else "",
            "emails": extract_emails(texto + "\n" + _emails_mailto(contenedor)),
            "links": extract_links(texto, hrefs),
            "whatsapp": extract_whatsapp(texto, hrefs),
            "imagenes": imagenes,
        })
    return posts


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def build_search_url(query: str, orden: str = "date_posted") -> str:
    """URL de búsqueda de contenido, ordenada por fecha o relevancia y limitada a la última semana."""
    if orden not in ("date_posted", "relevance"):
        raise ValueError(f"orden inválido: {orden!r} (debe ser 'date_posted' o 'relevance')")
    keywords = quote(query, safe="")
    sort_by = quote(f'"{orden}"', safe="")
    return (
        f"{_SEARCH_URL_BASE}?keywords={keywords}"
        f"&sortBy={sort_by}&datePosted={_DATE_POSTED}"
    )


_LOGIN_PATH_PREFIXES = ("/login", "/checkpoint", "/authwall", "/uas/", "/signup")


def is_login_or_checkpoint_url(url: str) -> bool:
    """True si la URL es de login, checkpoint, authwall o registro de LinkedIn."""
    try:
        partes = urlparse(url)
    except ValueError:
        return False
    host = (partes.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return False
    return (partes.path or "").lower().startswith(_LOGIN_PATH_PREFIXES)
