"""Scraper y extractor inteligente de ofertas laborales vía URL (cero dependencias pesadas)."""
import re
import base64
import html
import json
from core import lector_web
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from html.parser import HTMLParser

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

NOISE_CONTAINER_REGEX = re.compile(
    r"(^|[-_\s])("
    r"nav|navbar|navigation|nav-menu|menu|"
    r"sidebar|"
    r"similar|suggested|related-job|similar-job|"
    r"site-header|main-header|header-menu|"
    r"footer|footer-links|site-footer|"
    r"cookie|banner|modal|popup|dialog|"
    r"social|share-button|share-btn|"
    r"breadcrumb|pagination"
    r")([-_\s]|$)",
    re.IGNORECASE,
)


class HTMLContentExtractor(HTMLParser):
    """Parser liviano que remueve elementos irrelevantes y extrae texto estructurado."""

    IGNORE_TAGS = {"script", "style", "nav", "header", "footer", "aside", "svg", "noscript", "iframe", "button"}
    VOID_TAGS = {"img", "input", "br", "hr", "meta", "link", "source", "track", "wbr", "area", "col", "base"}
    BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "article", "section", "tr"}

    def __init__(self):
        super().__init__()
        self.ignore_stack = []
        self.text_parts = []
        self.meta_og = {}
        self.title_tag = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        attr_dict = {k.lower(): v or "" for k, v in attrs}
        combined_attrs = " ".join([attr_dict.get("id", ""), attr_dict.get("class", ""), attr_dict.get("role", "")])

        is_noise = (tag_lower in self.IGNORE_TAGS) or bool(NOISE_CONTAINER_REGEX.search(combined_attrs))

        if len(self.ignore_stack) == 0:
            if is_noise:
                if tag_lower not in self.VOID_TAGS:
                    self.ignore_stack.append(tag_lower)
                return
        else:
            if tag_lower not in self.VOID_TAGS:
                self.ignore_stack.append(tag_lower)
            return

        if tag_lower == "title":
            self.in_title = True

        if tag_lower == "meta":
            prop = attr_dict.get("property") or attr_dict.get("name")
            content = attr_dict.get("content")
            if prop and content:
                self.meta_og[prop.lower()] = content.strip()

        if tag_lower in self.BLOCK_TAGS:
            self.text_parts.append("\n")
        elif tag_lower == "br":
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if len(self.ignore_stack) > 0:
            if tag_lower in self.ignore_stack:
                while self.ignore_stack and self.ignore_stack[-1] != tag_lower:
                    self.ignore_stack.pop()
                if self.ignore_stack:
                    self.ignore_stack.pop()
            return

        if tag_lower == "title":
            self.in_title = False

        if tag_lower in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if len(self.ignore_stack) > 0:
            return

        if self.in_title:
            self.title_tag += data
        else:
            self.text_parts.append(data)


BOILERPLATE_PATTERNS = [
    r"(?i)aceptar(?:\s+todas)?(?:\s+las)?\s+cookies",
    r"(?i)utilizamos\s+cookies",
    r"(?i)pol[íi]tica\s+de\s+privacidad",
    r"(?i)configuraci[óo]n\s+de\s+cookies",
    r"(?i)this\s+website\s+uses\s+cookies",
    r"(?i)compartir\s+(?:este\s+)?(?:empleo|aviso|trabajo)",
    r"(?i)share\s+(?:this\s+)?job",
    r"(?i)todos\s+los\s+derechos\s+reservados",
    r"(?i)all\s+rights\s+reserved",
    r"(?i)copyright\s+(?:©|\(c\))?\s*\d{4}",
    r"(?i)postularme\s+ahora",
    # Ruido típico de navegación de portales de empleo (LinkedIn, Bumeran, etc.)
    r"(?i)pasar\s+al\s+contenido\s+principal",
    r"(?i)descubre\s+a\s+qui[eé]n\s+ha\s+contratado",
    r"(?i)s[eé]\s+de\s+los\s+primeros\s+\d+\s+solicitantes",
    r"(?i)^\s*guardar\s*$",
    r"(?i)^\s*\d+\s+solicitudes\s*$",
    r"(?i)^hace\s+\d+\s+(?:minutos?|horas?|d[ií]as?|semanas?|meses?)\s*$",
    r"(?i)aceptar\s+y\s+unirse\s+a\s+linkedin",
    r"(?i)al\s+hacer\s+clic\s+en\s+[«\"]?continuar[»\"]?",
    r"(?i)explora\s+contenido\s+destacado",
    r"(?i)encuentra\s+publicaciones\s+e\s+informaci[óo]n",
    r"(?i)inicia\s+sesi[óo]n\s+para\s+crear",
    r"(?i)¿te\s+interesa\s+este\s+empleo\?",
    r"(?i)ver\s+contenido\s+destacado",
    r"(?i)empleos\s+similares",
    r"(?i)m[aá]s\s+empleos",
    r"(?i)aviso\s+sobre\s+empleos\s+fraudulentos",
    r"(?i)pautas\s+para\s+la\s+comunidad",
    r"(?i)control\s+de\s+cookies",
]

CUT_PATTERNS = [
    r"(?i)^\s*otros\s+perfiles\s+vistos",
    r"(?i)^\s*mira\s+a\s+qui[eé]n\s+conoces",
    r"(?i)^\s*las\s+recomendaciones\s+duplican",
    r"(?i)^\s*empleos\s+similares",
    r"(?i)^\s*personas\s+tambi[eé]n\s+buscaron",
    r"(?i)^\s*recibe\s+una\s+notificaci[óo]n\s+cuando",
    r"(?i)^\s*crear\s+alerta",
    r"(?i)^\s*m[aá]s\s+empleos\s+(?:en|similares|de|como)",
    r"(?i)^\s*avisos\s+similares",
    r"(?i)^\s*explora\s+m[aá]s\s+empleos",
    r"(?i)^\s*similares\s+a\s+este\s+empleo",
    r"(?i)^\s*puestos\s+similares",
]


def filter_job_boilerplate(text: str) -> str:
    """Filtra líneas de ruido típicas de portales de empleo y corta tajantemente ante bloques de recomendación."""
    if not text:
        return ""
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
        # Detener la iteración y descartar todo el texto posterior al hallar cabeceras de recomendación/alertas
        if any(re.search(pat, line_strip) for pat in CUT_PATTERNS):
            break
        is_boilerplate = any(re.search(pat, line_strip) for pat in BOILERPLATE_PATTERNS)
        if not is_boilerplate:
            cleaned_lines.append(line_strip)
    return "\n".join(cleaned_lines)


def clean_html_text(html_content: str) -> str:
    """Extrae texto plano limpio y estructurado desde código HTML."""
    if not html_content:
        return ""
    parser = HTMLContentExtractor()
    parser.feed(html_content)
    raw_text = "".join(parser.text_parts)

    # Decodificar entidades HTML (&nbsp;, &aacute;, etc.)
    decoded = html.unescape(raw_text)

    # Reemplazar espacios de no separación y espacios invisibles
    decoded = decoded.replace("\xa0", " ").replace("\u200b", " ")

    # Limpiar espacios horizontales redundantes por línea
    lines = []
    for line in decoded.splitlines():
        cleaned_line = re.sub(r"[\s]+", " ", line).strip()
        if cleaned_line:
            lines.append(cleaned_line)

    unfiltered = "\n".join(lines)
    return filter_job_boilerplate(unfiltered)


def _clean_title(title: str) -> str:
    """Normaliza el título de la oferta eliminando sufijos de sitios web y ubicaciones redundantes."""
    if not title:
        return ""
    t = html.unescape(title).strip()
    # Eliminar prefijos de sitios web (ej. "Empleos IT: Oferta de Trabajo ...")
    t = re.sub(
        r"^(?:[A-Za-z0-9\s]+:\s*(?:Oferta\s*de\s*(?:Trabajo|Empleo)|B[uú]squeda)\s*)",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    # Si contiene separadores comunes como " - ", " | ", " • ", " — ", " · ", tomar el título
    parts = re.split(r"\s+[-|•—~·]\s+", t)
    if parts:
        t = parts[0].strip()
    # Si tiene patrón "Puesto en/at Empresa", aislar el puesto
    m_en = re.match(r"^(.*?)\s+(?:en|at)\s+(.*?)$", t, re.IGNORECASE)
    if m_en:
        t = m_en.group(1).strip()
    # Eliminar sufijo de ubicación en paréntesis al final: ej. " (Ciudad Autónoma...)"
    # Cuantificadores posesivos: con «[^()]+» dentro de «(...)*» un paréntesis largo seguido de texto hacía
    # backtracking catastrófico y colgaba el portal entero con el GIL tomado
    t = re.sub(r"\s*\((?:[^()]++|\([^()]*+\))*+\)\s*$", "", t).strip()
    return t


JOB_PORTAL_DOMAINS_AND_NAMES = {
    "linkedin", "bumeran", "zonajobs", "computrabajo", "indeed", "glassdoor",
    "empleosit", "empleos it", "getonbrd", "workana", "upwork", "joinrs",
}


def _clean_company(company: str) -> str:
    """Normaliza el nombre de la empresa descartando URLs y nombres genéricos de portales de empleo."""
    if not company:
        return ""
    c = html.unescape(company).strip()
    # Descartar si coincide con el nombre de un portal de empleo conocido
    if c.lower() in JOB_PORTAL_DOMAINS_AND_NAMES:
        return ""
    # Descartar si es una URL directa o un dominio web (ej. https://www.empleosit.com.ar)
    if c.startswith(("http://", "https://")) or re.search(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", c, re.IGNORECASE):
        return ""
    return c


def _extract_json_ld(html_content: str) -> Optional[Dict[str, Any]]:
    """Extrae estructura JobPosting schema.org desde script application/ld+json si existe."""
    pattern = re.compile(
        r"""<script\s+type=['"]application/ld\+json['"][^>]*>(.*?)</script>""",
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html_content):
        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json, strict=False)
        except Exception:
            continue

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                items = data["@graph"]
            else:
                items = [data]

        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            is_job = (item_type == "JobPosting") or (isinstance(item_type, list) and "JobPosting" in item_type)
            if is_job:
                return item
    return None


def _normalizar_identificadores_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Extrae (urn_completa, id_numerico) de una URL o URN de LinkedIn."""
    if not url:
        return None, None
    from urllib.parse import unquote
    s = unquote(str(url)).strip()

    # 1. URN explícita: urn:li:<tipo>:<digits>
    m_urn = re.search(r"\burn:li:([a-zA-Z0-9_-]+):(\d+)\b", s, re.IGNORECASE)
    if not m_urn:
        m_urn = re.search(r"urn:li:([a-zA-Z0-9_-]+):(\d+)", s, re.IGNORECASE)
    if m_urn:
        tipo = m_urn.group(1).lower()
        numero = m_urn.group(2)
        return f"urn:li:{tipo}:{numero}", numero

    # 2. Slug de actividad: activity-<digits> o share-<digits> o ugcPost-<digits>
    m_slug = re.search(r"(?:activity|share|ugcPost|card)[-_:]+(\d+)", s, re.IGNORECASE)
    if m_slug:
        tipo_m = re.search(r"(activity|share|ugcPost|card)", m_slug.group(0), re.IGNORECASE)
        tipo = tipo_m.group(1).lower() if tipo_m else "activity"
        numero = m_slug.group(1)
        return f"urn:li:{tipo}:{numero}", numero

    # 3. Parámetro shareId o ugcPostId
    m_param = re.search(r"\b(shareId|ugcPostId)=(\d+)", s, re.IGNORECASE)
    if m_param:
        tipo = "share" if m_param.group(1).lower() == "shareid" else "ugcpost"
        numero = m_param.group(2)
        return f"urn:li:{tipo}:{numero}", numero

    # 4. Path /feed/update/<digits>
    m_feed = re.search(r"/feed/update/(\d+)", s, re.IGNORECASE)
    if m_feed:
        return f"urn:li:activity:{m_feed.group(1)}", m_feed.group(1)

    # 5. Si es puramente numérico
    if s.isdigit():
        return None, s

    return None, None


def _coincide_post_con_url(post: Dict[str, Any], url: str, urn_target: Optional[str], id_target: Optional[str]) -> bool:
    if not url:
        return False
    from urllib.parse import unquote
    url_desc = unquote(url).strip().rstrip("/")
    post_url = unquote(post.get("post_url") or "").strip().rstrip("/")
    if post_url and (post_url == url_desc or post_url == url.strip().rstrip("/")):
        return True

    p_urn = (post.get("urn") or "").strip()
    p_urn_norm, p_id = _normalizar_identificadores_url(p_urn) if p_urn else (None, None)

    p_post_url = (post.get("post_url") or "").strip()
    p_purl_urn, p_purl_id = _normalizar_identificadores_url(p_post_url) if p_post_url else (None, None)

    # 1. Comparación exacta de URN normalizada (cuando ambos identificadores tienen tipo)
    if urn_target:
        target_lower = urn_target.lower()
        if p_urn.lower() == target_lower:
            return True
        if p_urn_norm and p_urn_norm.lower() == target_lower:
            return True
        if p_purl_urn and p_purl_urn.lower() == target_lower:
            return True

    # 2. Comparación de ID numérico: reservada para identificadores sin tipo.
    # Si ambos identificadores tienen tipo y son contradictorios (ej. card vs activity o share vs activity),
    # no deben equipararse únicamente por el número.
    if id_target:
        if p_id and p_id == id_target:
            if not p_urn_norm or not urn_target:
                return True
        if p_purl_id and p_purl_id == id_target:
            if not p_purl_urn or not urn_target:
                return True

    return False


def _seleccionar_post(posts: List[Dict[str, Any]], url: str) -> Optional[Dict[str, Any]]:
    """Selecciona una sola publicación sin equiparar identificadores contradictorios."""
    urn_target, id_target = _normalizar_identificadores_url(url)
    coincidentes = [p for p in posts if _coincide_post_con_url(p, url, urn_target, id_target)]
    if len(coincidentes) == 1:
        return coincidentes[0]
    if not coincidentes and len(posts) == 1 and not urn_target and not id_target:
        return posts[0]
    return None


def _extraer_imagenes_de_html(html_content: str, url: str = "") -> List[str]:
    """Extrae URLs de imágenes de contenido de LinkedIn si existen."""
    if not html_content:
        return []
    try:
        from core.linkedin_parser import parse_posts_html
        posts = parse_posts_html(html_content)
        if posts:
            post = _seleccionar_post(posts, url)
            return (post.get("imagenes") or []) if post is not None else []
    except Exception:
        pass

    try:
        from bs4 import BeautifulSoup
        from core.linkedin_parser import _extraer_imagenes_de_contenedor, _SEL_TARJETA
        soup = BeautifulSoup(html_content, "html.parser")
        urn_target, id_target = _normalizar_identificadores_url(url)
        if (urn_target or id_target) and (soup.select(_SEL_TARJETA) or soup.select("[data-urn]")):
            return []
        return _extraer_imagenes_de_contenedor(soup)
    except Exception:
        return []


def extract_job_from_html(html_content: str, url: str = "") -> Dict[str, Any]:
    """Extrae título, empresa y contenido textual a partir del HTML."""
    from core.linkedin_parser import parse_posts_html

    post = _seleccionar_post(parse_posts_html(html_content), url)
    imagenes = (post.get("imagenes") or []) if post is not None else _extraer_imagenes_de_html(html_content, url)
    # 1. Intentar extracción de alta fidelidad vía JSON-LD (JobPosting standard)
    job_ld = _extract_json_ld(html_content)
    if job_ld and post is None:
        ld_title = _clean_title(str(job_ld.get("title") or ""))
        org = job_ld.get("hiringOrganization")
        ld_company = ""
        if isinstance(org, dict):
            ld_company = _clean_company(str(org.get("name") or ""))
        elif isinstance(org, str):
            ld_company = _clean_company(org)

        raw_desc = job_ld.get("description") or ""
        clean_desc = clean_html_text(raw_desc) if raw_desc else ""

        if len(clean_desc) > 80:
            return {
                "titulo": ld_title or "Posición Técnica",
                "empresa": ld_company or "Empresa Confidencial",
                "contenido": clean_desc,
                "url": url,
                "imagenes": imagenes,
            }

    # 2. Extracción semántica a partir del DOM HTML
    parser = HTMLContentExtractor()
    parser.feed(html_content)

    meta = parser.meta_og
    page_title = html.unescape(parser.title_tag).strip()

    # Inferencia de título del puesto y empresa
    raw_title = meta.get("og:title") or page_title
    inferred_company = ""
    if raw_title:
        title_segments = re.split(r"\s+[-|•—~·]\s+", html.unescape(raw_title).strip())
        if title_segments:
            first_seg = title_segments[0].strip()
            m_en = re.match(r"^(.*?)\s+(?:en|at)\s+(.*?)$", first_seg, re.IGNORECASE)
            if m_en:
                inferred_company = _clean_company(m_en.group(2).strip())

    titulo = _clean_title(raw_title)
    if not titulo:
        titulo = "Posición Técnica"

    # Inferencia de empresa
    empresa = _clean_company(meta.get("og:site_name") or "")
    if not empresa and inferred_company:
        empresa = inferred_company
    if not empresa and page_title:
        parts = re.split(r"\s+[-|•—~·]\s+", page_title)
        if len(parts) > 1 and len(parts[1].strip()) < 50:
            empresa = _clean_company(parts[1].strip())

    if not empresa:
        empresa = "Empresa Confidencial"

    # Texto e imágenes deben pertenecer a la misma publicación. Un post gráfico
    # conserva texto vacío para habilitar su lectura, sin sumar tarjetas ni metadatos ajenos.
    cleaned_body = (post.get("texto") or "") if post is not None else clean_html_text(html_content)

    # Si el cuerpo quedó vacío o casi vacío, usar og:description
    if post is None and len(cleaned_body) < 80 and meta.get("og:description"):
        cleaned_body = f"{titulo}\n\n{clean_html_text(meta['og:description'])}"

    return {
        "titulo": titulo,
        "empresa": empresa,
        "contenido": cleaned_body,
        "url": url,
        "imagenes": imagenes,
    }


def _fetch_url_sync(url: str, timeout: int = 15) -> str:
    """Descarga síncrona (corre en un hilo aparte). El link es de terceros: core/lector_web.py impide que apunte
    a la propia PC o a la red local, corta respuestas enormes y verifica TLS (auditoría 17/11, M3)."""
    return lector_web.leer(url, HEADERS, timeout=timeout).texto()


class OfertaInexistente(RuntimeError):
    """El portal responde que el aviso ya no existe (404/410): pedir el texto no sirve."""


# Un aviso de WorkMundi lleva en la URL el de LinkedIn en base64 («linkedin:<id>») y muestra solo un resumen
_RE_WORKMUNDI = re.compile(r"^https?://(?:www\.)?workmundi\.com/vaga/([A-Za-z0-9_-]+)")


def url_de_lectura(url: str) -> str:
    """La URL de donde se lee el aviso completo: el de LinkedIn detrás de un resumen de WorkMundi (oferta 7759)."""
    m = _RE_WORKMUNDI.match(url or "")
    if m:
        try:
            origen = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            return url
        id_linkedin = re.fullmatch(r"linkedin:(\d+)", origen)
        if id_linkedin:
            return f"https://www.linkedin.com/jobs/view/{id_linkedin.group(1)}/"
    return url


# Si la lectura simple falla o viene vacía se lee con el navegador de Portales, salvo en estos casos
_RE_SIN_NAVEGADOR = re.compile(r"HTTP Error (?:404|410)\b|destino no permitido|getaddrinfo|network is unreachable|"
                               r"no route to host", re.IGNORECASE)
_RE_LETRA = re.compile(r"[^\W\d_]")
_RE_CADUCADA = re.compile(r"este empleo caduc[óo]|this job has expired", re.IGNORECASE)
_RE_INGRESO = re.compile(r"\s*(?:sign in|log in|iniciar sesi[óo]n|ingresar)\b", re.IGNORECASE)


async def _leer_con_navegador(url: str, timeout: int) -> Dict[str, Any]:
    from core import navegador_portales
    html_code = await navegador_portales.lector().html(url, timeout * 2)
    if _RE_CADUCADA.search(clean_html_text(html_code)):
        raise RuntimeError("HTTP Error 410: el aviso caducó")
    oferta = extract_job_from_html(html_code, url=url)
    if _RE_INGRESO.match(oferta.get("titulo") or ""):
        raise RuntimeError("el sitio pide iniciar sesión")
    return oferta


async def scrape_job_url(url: str, timeout: int = 15) -> Dict[str, Any]:
    """Ingesta asíncrona de una URL web y extracción automática de la vacante."""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        try:
            oferta = extract_job_from_html(await asyncio.to_thread(_fetch_url_sync, url_de_lectura(url), timeout),
                                           url=url)
        except Exception as e:
            if _RE_SIN_NAVEGADOR.search(str(e)):
                raise
            return await _leer_con_navegador(url_de_lectura(url), timeout)
        if _RE_LETRA.search(oferta.get("contenido") or ""):
            return oferta
        try:
            return await _leer_con_navegador(url_de_lectura(url), timeout)
        except Exception:
            if oferta.get("imagenes"):
                return oferta
            raise
    except Exception as e:
        # Fallback de contingencia si la URL no es accesible o bloquea bots
        return {
            "titulo": "Oferta Importada por URL",
            "empresa": "Portal Web Externo",
            "contenido": f"Contenido importado desde {url}.\n(Aviso: No se pudo scrapear automáticamente la página: {e}. Podés ingresar o editar la descripción manualmente).",
            "url": url,
            "error": str(e),
            "imagenes": [],
        }
