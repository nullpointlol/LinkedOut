"""Portales de empleo: Jooble por su API oficial e Indeed Argentina con JobSpy.

Cada búsqueda trae ofertas normalizadas:
    {portal, id, urn, titulo, empresa, ubicacion, descripcion, completa, link, link_directo, emails, fecha}

- `completa`: la descripción es el aviso entero (Indeed). Jooble trae solo un resumen: el texto completo se lee
  después de la página del aviso, con el lector de ofertas por URL (core/radar.py, antes de clasificar).
- `link`: la página del aviso en el portal; `link_directo`: dónde se postula, si el portal lo da.

Nada de proxies ni de evadir bloqueos: si un portal rechaza la consulta, es un `ErrorPortal` y se avisa.
La red se inyecta (`post_json`, `scrape`) para que los tests usen respuestas grabadas, sin red.
"""
import asyncio
import html
import itertools
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.linkedin_parser import extract_emails
from core.reglas_portales import NOMBRES, PORTALES, ReglaPortal

# Nombre de la variable del .env con la key de Jooble (la key nunca va en el perfil ni en el código). Los demás valores
# (direcciones, país, plazos, largo de un aviso completo, qué portal lee la página del aviso) viven en el perfil
# (core/reglas_portales.py)
VARIABLE_CLAVE_JOOBLE = "JOOBLE_API_KEY"

_RE_ETIQUETA = re.compile(r"<[^>]+>")
_RE_ESPACIOS = re.compile(r"[ \t ]+")


class ErrorPortal(Exception):
    """El portal no respondió bien (sin clave, clave inválida, límite del día, bloqueo, captcha o red). Abre un ticket
    `portales/consulta_fallida` (con un captcha, el ticket; se investiga después)."""


def clave_jooble() -> str:
    """La key de Jooble: `JOOBLE_API_KEY` del `.env` del portal (core/mailer.ruta_env) o del entorno. «» si falta."""
    from dotenv import dotenv_values

    from core.mailer import ruta_env
    try:
        valor = dotenv_values(ruta_env()).get(VARIABLE_CLAVE_JOOBLE)
    except OSError:
        valor = None
    return (valor or os.environ.get(VARIABLE_CLAVE_JOOBLE) or "").strip()


def _texto_plano(valor: Any) -> str:
    texto = html.unescape(_RE_ETIQUETA.sub(" ", str(valor or "")))
    lineas = [_RE_ESPACIOS.sub(" ", l).strip() for l in texto.splitlines()]
    return "\n".join(l for l in lineas if l).strip()


def _fecha(valor: Any) -> Optional[date]:
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        try:
            return datetime.fromtimestamp(valor / 1000).date()  # pandas: milisegundos
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")[:26]).date()
    except ValueError:
        try:
            return date.fromisoformat(str(valor)[:10])
        except ValueError:
            return None


def _oferta(portal: str, id_portal: Any, titulo: Any, empresa: Any, ubicacion: Any, descripcion: str,
            completa: bool, link: Any, link_directo: Any, fecha: Optional[date]) -> Optional[Dict[str, Any]]:
    id_texto = str(id_portal or "").strip()
    link = str(link or "").strip()
    titulo = _texto_plano(titulo)
    if not id_texto or not link or not titulo:
        return None
    return {
        "portal": portal, "id": id_texto, "urn": f"{portal}:{id_texto}", "titulo": titulo,
        "empresa": _texto_plano(empresa), "ubicacion": _texto_plano(ubicacion),
        "descripcion": descripcion, "completa": completa, "link": link,
        "link_directo": str(link_directo or "").strip(), "emails": extract_emails(descripcion),
        "fecha": fecha.isoformat() if fecha else "",
    }


def _reciente(fecha: Optional[date], dias: int, hoy: date) -> bool:
    return fecha is None or fecha >= hoy - timedelta(days=dias)


# ------------------------------------------------------------------ Jooble

def _post_json(url: str, cuerpo: Dict[str, Any], plazo: float) -> Tuple[int, Any]:
    pedido = urllib.request.Request(url, data=json.dumps(cuerpo).encode("utf-8"), method="POST",
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=plazo) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None


def buscar_jooble(palabras: str, regla: ReglaPortal, clave: str, *,
                  post_json: Callable[[str, Dict[str, Any], float], Tuple[int, Any]] = _post_json,
                  hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    """Una consulta (1 página) a la API de Jooble. La key nunca sale en un error."""
    if not clave:
        raise ErrorPortal(f"Falta {VARIABLE_CLAVE_JOOBLE} en el .env del portal")
    cuerpo = {"keywords": palabras, "location": regla.ubicacion, "page": 1,
              "ResultOnPage": regla.resultados_por_consulta}
    try:
        estado, datos = post_json(regla.url.replace("{clave}", clave), cuerpo, regla.plazo_consulta_s)
    except Exception as e:
        raise ErrorPortal(f"Jooble no respondió: {type(e).__name__}: {str(e).replace(clave, '<clave>')}") from None
    if estado == 403:
        raise ErrorPortal("Jooble rechazó la key (403): revisá JOOBLE_API_KEY")
    if estado == 429:
        raise ErrorPortal("Jooble: se llegó al límite de consultas del día (429)")
    if estado != 200 or not isinstance(datos, dict) or not isinstance(datos.get("jobs"), list):
        raise ErrorPortal(f"Jooble respondió {estado} sin la lista de ofertas")
    hoy = hoy or date.today()
    ofertas = []
    for job in datos["jobs"]:
        if not isinstance(job, dict):
            continue
        fecha = _fecha(job.get("updated"))
        if not _reciente(fecha, regla.dias, hoy):
            continue
        oferta = _oferta("jooble", job.get("id"), job.get("title"), job.get("company"), job.get("location"),
                         _texto_plano(job.get("snippet")), False, job.get("link"), "", fecha)
        if oferta:
            oferta["origen"] = _texto_plano(job.get("source"))
            ofertas.append(oferta)
    return ofertas


# ------------------------------------------------------------------ Indeed (JobSpy)

def _scrape_jobspy(**kwargs: Any) -> List[Dict[str, Any]]:
    from jobspy import scrape_jobs  # pesada (pandas): se importa recién al consultar
    df = scrape_jobs(**kwargs)
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def buscar_indeed(palabras: str, regla: ReglaPortal, *,
                  scrape: Callable[..., List[Dict[str, Any]]] = _scrape_jobspy,
                  hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    """Una consulta a Indeed con JobSpy (el país, del perfil), sin proxies."""
    try:
        filas = scrape(site_name=["indeed"], search_term=palabras, location=regla.ubicacion,
                       country_indeed=regla.pais, hours_old=regla.dias * 24,
                       results_wanted=regla.resultados_por_consulta, description_format="markdown", verbose=0)
    except Exception as e:
        raise ErrorPortal(f"Indeed no respondió: {type(e).__name__}: {e}") from None
    hoy = hoy or date.today()
    ofertas = []
    for fila in filas or []:
        if not isinstance(fila, dict):
            continue
        fecha = _fecha(fila.get("date_posted"))
        if not _reciente(fecha, regla.dias, hoy):
            continue
        descripcion = str(fila.get("description") or "").strip()
        oferta = _oferta("indeed", fila.get("id"), fila.get("title"), fila.get("company"), fila.get("location"),
                         descripcion, len(descripcion) >= regla.min_descripcion_completa, fila.get("job_url"),
                         fila.get("job_url_direct"), fecha)
        if oferta:
            mails = fila.get("emails")
            if isinstance(mails, list):
                oferta["emails"] = list(dict.fromkeys([*oferta["emails"], *[m for m in mails if isinstance(m, str)]]))
            ofertas.append(oferta)
    return ofertas


# ------------------------------------------------------------------ fechas relativas («Hace 2 días», «Publicado ayer»)

_RE_HACE = re.compile(r"hace\s+(\d+)\s+(minuto|hora|d[ií]a|semana|mes)", re.IGNORECASE)


def fecha_relativa(texto: str, hoy: date) -> Optional[date]:
    """La fecha de «Hace 3 horas», «Ayer», «Publicado hace 2 días», «Hace 1 mes»… None si no se entiende."""
    t = html.unescape(texto or "").strip().lower()
    if not t:
        return None
    if "ayer" in t:
        return hoy - timedelta(days=1)
    if "hoy" in t:
        return hoy
    m = _RE_HACE.search(t)
    if not m:
        return None
    n, unidad = int(m.group(1)), m.group(2)
    if unidad in ("minuto", "hora"):
        return hoy
    return hoy - timedelta(days={"semana": 7, "mes": 30}.get(unidad, 1) * n)


# ------------------------------------------------------------------ Computrabajo (listados públicos)

# Valores del filtro de fecha de sus listados (robots.txt lo permite en /empleos-*, no en /ofertas-de-trabajo/)
PUBDATE_COMPUTRABAJO = (1, 3, 7, 15, 30)
_RE_CT_AVISO = re.compile(r"<article\b[^>]*\bdata-id=['\"]([A-Fa-f0-9]+)['\"][^>]*>(.*?)</article>", re.S)
_RE_CT_LINK = re.compile(r"<a\b[^>]*class=\"[^\"]*js-o-link[^\"]*\"[^>]*href=\"([^\"#]+)[^\"]*\"[^>]*>(.*?)</a>", re.S)
_RE_CT_EMPRESA = re.compile(r"<a\b[^>]*offer-grid-article-company-url[^>]*>(.*?)</a>", re.S)
_RE_CT_LUGAR = re.compile(r"<p class=\"fs16 fc_base mt5\">\s*<span class=\"mr10\">(.*?)</span>", re.S)
_RE_CT_FECHA = re.compile(r"<p class=\"fs13 fc_aux mt15\">\s*(.*?)\s*</p>", re.S)
_FIN_DETALLE_CT = ('<div class="pos', "Denunciar")


def avisos_computrabajo(pagina: str, base: str) -> List[Dict[str, str]]:
    """Los avisos de una página de listado: {id, titulo, link, empresa, ubicacion, cuando}."""
    salida = []
    for id_aviso, cuerpo in _RE_CT_AVISO.findall(pagina or ""):
        link = _RE_CT_LINK.search(cuerpo)
        if not link:
            continue
        empresa, lugar, cuando = (_RE_CT_EMPRESA.search(cuerpo), _RE_CT_LUGAR.search(cuerpo), _RE_CT_FECHA.search(cuerpo))
        salida.append({"id": id_aviso, "titulo": _texto_plano(link.group(2)), "link": base.rstrip("/") + link.group(1),
                       "empresa": _texto_plano(empresa.group(1)) if empresa else "",
                       "ubicacion": _texto_plano(lugar.group(1)) if lugar else "",
                       "cuando": _texto_plano(cuando.group(1)) if cuando else ""})
    return salida


def detalle_computrabajo(pagina: str) -> str:
    """La descripción del aviso (sección «Descripción de la oferta»)."""
    inicio = (pagina or "").find('div-link="oferta"')
    if inicio < 0:
        return ""
    trozo = pagina[inicio:inicio + 20000]
    trozo = trozo[trozo.find(">") + 1:]
    fin = min((i for i in (trozo.find(m) for m in _FIN_DETALLE_CT) if i > 0), default=len(trozo))
    return _texto_plano(re.sub(r"</(p|li|div|h\d)>", "\n", trozo[:fin]))


def _get_html(url: str, plazo: float) -> str:
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LinkedOut",
                                                  "Accept-Language": "es-AR,es;q=0.9"})
    try:
        with urllib.request.urlopen(pedido, timeout=plazo) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise ErrorPortal(f"El portal rechazó la consulta ({e.code})") from None
        raise ErrorPortal(f"El portal respondió {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorPortal(f"El portal no respondió: {type(e).__name__}") from None


def buscar_computrabajo(listado: str, regla: ReglaPortal, *, get: Optional[Callable[[str], str]] = None,
                        ya_vistos: Callable[[List[str]], set] = lambda urns: set(),
                        hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    """Las ofertas recientes de un listado de Computrabajo («empleos-en-capital-federal»…), de la más nueva a la más
    vieja, hasta `resultados_por_consulta` o `dias`. Lee el aviso completo solo de las que no se vieron."""
    hoy = hoy or date.today()
    get = get or (lambda url: _get_html(url, regla.plazo_consulta_s))
    pubdate = next((d for d in PUBDATE_COMPUTRABAJO if d >= regla.dias), PUBDATE_COMPUTRABAJO[-1])
    ofertas: List[Dict[str, Any]] = []
    for pagina in itertools.count(1):
        avisos = avisos_computrabajo(get(f"{regla.url.rstrip('/')}/{listado.strip('/')}?pubdate={pubdate}&p={pagina}"),
                                     regla.url)
        if not avisos:
            break
        vistos = ya_vistos([f"computrabajo:{a['id']}" for a in avisos])
        viejo = False
        for a in avisos:
            fecha = fecha_relativa(a["cuando"], hoy)
            if not _reciente(fecha, regla.dias, hoy):
                viejo = True
                continue
            if f"computrabajo:{a['id']}" in vistos:
                continue  # ya entró: no se vuelve a pedir su aviso
            descripcion = detalle_computrabajo(get(a["link"]))
            oferta = _oferta("computrabajo", a["id"], a["titulo"], a["empresa"], a["ubicacion"], descripcion,
                             len(descripcion) >= regla.min_descripcion_completa, a["link"], "", fecha)
            if oferta:
                ofertas.append(oferta)
            if len(ofertas) >= regla.resultados_por_consulta:
                return ofertas
        if viejo:
            break
    return ofertas


# ------------------------------------------------------------------ Bumeran (con el navegador, como LinkedIn)

# Filtro de fecha de sus listados: «-publicacion-menor-a-N-dias» (1 día = «-publicacion-hoy»)
DIAS_BUMERAN = (1, 3, 7, 15, 30)


def url_listado_bumeran(base: str, listado: str, dias: int, pagina: int) -> str:
    tope = next((d for d in DIAS_BUMERAN if d >= dias), DIAS_BUMERAN[-1])
    filtro = "publicacion-hoy" if tope == 1 else f"publicacion-menor-a-{tope}-dias"
    return f"{base.rstrip('/')}/{listado.strip('/')}-{filtro}.html" + (f"?page={pagina}" if pagina > 1 else "")


def ofertas_bumeran(tarjetas: List[Dict[str, str]], regla: ReglaPortal, hoy: date) -> Tuple[List[Dict[str, Any]], bool]:
    """(ofertas, hay_viejas) de las tarjetas que leyó el navegador: {id, href, encabezado, datos}. El encabezado trae
    «Publicado hace 2 días», el título, la empresa y el aviso entero; `datos`, la ubicación."""
    ofertas, viejas = [], False
    for t in tarjetas:
        lineas = [l.strip() for l in str(t.get("encabezado") or "").splitlines() if l.strip()]
        if len(lineas) < 2:
            continue
        fecha = fecha_relativa(lineas[0], hoy)
        if not _reciente(fecha, regla.dias, hoy):
            viejas = True
            continue
        titulo, empresa = lineas[1], (lineas[2] if len(lineas) > 2 else "")
        descripcion = "\n".join(lineas[3:])
        datos = [l.strip() for l in str(t.get("datos") or "").splitlines() if l.strip()]
        oferta = _oferta("bumeran", t.get("id"), titulo, empresa, datos[0] if datos else "", descripcion,
                         len(descripcion) >= regla.min_descripcion_completa,
                         regla.url.rstrip("/") + str(t.get("href") or ""), "", fecha)
        if oferta:
            ofertas.append(oferta)
    return ofertas, viejas


_RE_SITEMAP_AVISO = re.compile(r"<loc>\s*([^<]*?-(\d+)\.html)\s*</loc>\s*(?:<lastmod>\s*([^<]+?)\s*</lastmod>)?", re.S)


def avisos_del_sitemap(xml: str, dias: int, hoy: date) -> List[str]:
    """Los ids de los avisos del sitemap de Bumeran que cambiaron en los últimos `dias` (sin fecha: también)."""
    return [i for _, i, cambio in _RE_SITEMAP_AVISO.findall(xml or "") if _reciente(_fecha(cambio), dias, hoy)]


async def buscar_bumeran(listado: str, regla: ReglaPortal, lector: Any = None, hoy: Optional[date] = None, *,
                         get: Optional[Callable[[str], str]] = None,
                         ya_vistos: Callable[[List[str]], set] = lambda urns: set()) -> List[Dict[str, Any]]:
    """Las ofertas recientes de un listado de Bumeran. Su sitemap oficial (`sitemap` del perfil, por HTTP) dice qué
    avisos hay: si no hay ninguno nuevo, no se abre el navegador. Si hay, se leen las tarjetas de los listados con el
    navegador de Portales (core/navegador_portales.py) hasta encontrarlos. Si el sitio pide una verificación (captcha),
    `ErrorPortal`: queda en un ticket."""
    hoy = hoy or date.today()
    get = get or (lambda url: _get_html(url, regla.plazo_consulta_s))
    recientes = avisos_del_sitemap(await asyncio.to_thread(get, regla.sitemap), regla.dias, hoy)
    vistos = ya_vistos([f"bumeran:{i}" for i in recientes])
    pendientes = {i for i in recientes if f"bumeran:{i}" not in vistos}
    if recientes and not pendientes:
        return []  # el sitemap no trae nada nuevo
    if lector is None:
        from core import navegador_portales
        lector = navegador_portales.lector()
    ofertas: List[Dict[str, Any]] = []
    for pagina in itertools.count(1):
        tarjetas = await lector.tarjetas_bumeran(url_listado_bumeran(regla.url, listado, regla.dias, pagina),
                                                 regla.plazo_pagina_s)
        if not tarjetas:
            break
        nuevas, viejas = ofertas_bumeran(tarjetas, regla, hoy)
        ofertas.extend(o for o in nuevas if f"bumeran:{o['id']}" not in vistos)
        pendientes -= {str(t.get("id")) for t in tarjetas}
        if len(ofertas) >= regla.resultados_por_consulta or viejas or (recientes and not pendientes):
            break
    return ofertas[:regla.resultados_por_consulta]


# ------------------------------------------------------------------ remoto en el exterior: APIs públicas gratis
# RemoteOK, Remotive y Himalayas: JSON sin clave. Con «todas», `url`; con «busquedas», `url_busqueda` con «{palabras}».
# Sus condiciones piden nombrarlos y enlazar al aviso: el aviso guarda el link al sitio de origen.

def _get_json(url: str, plazo: float) -> Any:
    pedido = urllib.request.Request(url, headers={"User-Agent": "LinkedOut (busqueda de empleo personal)",
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=plazo) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ErrorPortal(f"El portal respondió {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ErrorPortal(f"El portal no respondió: {type(e).__name__}") from None


def _url_api(palabras: str, regla: ReglaPortal) -> str:
    if palabras and regla.url_busqueda:
        return regla.url_busqueda.replace("{palabras}", urllib.parse.quote(palabras))
    return regla.url


def _ofertas_api(portal: str, filas: List[Dict[str, Any]], campos: Dict[str, str], regla: ReglaPortal,
                 hoy: date) -> List[Dict[str, Any]]:
    """Normaliza las filas de una API con el mapa `campos` (id, titulo, empresa, ubicacion, descripcion, link, fecha)."""
    ofertas = []
    for fila in filas:
        if not isinstance(fila, dict):
            continue
        valor = lambda clave: fila.get(campos[clave]) if campos.get(clave) else None  # noqa: E731
        ubicacion = valor("ubicacion")
        if isinstance(ubicacion, list):
            ubicacion = ", ".join(str(u) for u in ubicacion) or "Remoto"
        cuando = valor("fecha")
        if isinstance(cuando, (int, float)) and not isinstance(cuando, bool) and cuando < 1e11:
            fecha = datetime.fromtimestamp(cuando).date()  # epoch en segundos (Himalayas)
        else:
            fecha = _fecha(cuando)
        if not _reciente(fecha, regla.dias, hoy):
            continue
        descripcion = _texto_plano(re.sub(r"</(p|li|div|h\d)>|<br\s*/?>", "\n", str(valor("descripcion") or "")))
        oferta = _oferta(portal, valor("id"), valor("titulo"), valor("empresa"), ubicacion, descripcion,
                         len(descripcion) >= regla.min_descripcion_completa, valor("link"), "", fecha)
        if oferta:
            ofertas.append(oferta)
        if len(ofertas) >= regla.resultados_por_consulta:
            break
    return ofertas


def buscar_remoteok(palabras: str, regla: ReglaPortal, *, get_json: Callable[[str, float], Any] = _get_json,
                    hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    datos = get_json(_url_api(palabras, regla), regla.plazo_consulta_s)
    filas = [f for f in (datos if isinstance(datos, list) else []) if isinstance(f, dict) and f.get("id")]
    return _ofertas_api("remoteok", filas, {"id": "id", "titulo": "position", "empresa": "company", "ubicacion": "location",
                                            "descripcion": "description", "link": "url", "fecha": "date"},
                        regla, hoy or date.today())


def buscar_remotive(palabras: str, regla: ReglaPortal, *, get_json: Callable[[str, float], Any] = _get_json,
                    hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    datos = get_json(_url_api(palabras, regla), regla.plazo_consulta_s)
    filas = datos.get("jobs") if isinstance(datos, dict) else None
    if not isinstance(filas, list):
        raise ErrorPortal("Remotive respondió sin la lista de ofertas")
    return _ofertas_api("remotive", filas, {"id": "id", "titulo": "title", "empresa": "company_name",
                                            "ubicacion": "candidate_required_location", "descripcion": "description",
                                            "link": "url", "fecha": "publication_date"}, regla, hoy or date.today())


def buscar_himalayas(palabras: str, regla: ReglaPortal, *, get_json: Callable[[str, float], Any] = _get_json,
                     hoy: Optional[date] = None) -> List[Dict[str, Any]]:
    datos = get_json(_url_api(palabras, regla), regla.plazo_consulta_s)
    filas = datos.get("jobs") if isinstance(datos, dict) else None
    if not isinstance(filas, list):
        raise ErrorPortal("Himalayas respondió sin la lista de ofertas")
    return _ofertas_api("himalayas", filas, {"id": "guid", "titulo": "title", "empresa": "companyName",
                                             "ubicacion": "locationRestrictions", "descripcion": "description",
                                             "link": "applicationLink", "fecha": "pubDate"}, regla, hoy or date.today())


async def buscar(portal: str, palabras: str, regla: ReglaPortal) -> List[Dict[str, Any]]:
    """La consulta de `portal`, fuera del event loop (la red es síncrona). Con el modo «todas», `palabras` es el
    listado que toca (Computrabajo, Bumeran) o nada (Indeed: las de los últimos días, sin palabras)."""
    if portal == "jooble":
        return await asyncio.to_thread(buscar_jooble, palabras, regla, clave_jooble())
    if portal == "indeed":
        return await asyncio.to_thread(buscar_indeed, palabras, regla)
    if portal == "computrabajo":
        from core import portales_store
        return await asyncio.to_thread(buscar_computrabajo, palabras, regla, ya_vistos=portales_store.ya_vistos)
    if portal == "bumeran":
        from core import portales_store
        return await buscar_bumeran(palabras, regla, ya_vistos=portales_store.ya_vistos)
    remotos = {"remoteok": buscar_remoteok, "remotive": buscar_remotive, "himalayas": buscar_himalayas}
    if portal in remotos:
        return await asyncio.to_thread(remotos[portal], palabras, regla)
    raise ErrorPortal(f"Portal desconocido: {portal}")


def texto_de_oferta(oferta: Dict[str, Any]) -> str:
    """El texto que se guarda y analiza: título, empresa, ubicación y la descripción."""
    cabecera = " · ".join(x for x in (oferta.get("empresa"), oferta.get("ubicacion")) if x)
    origen = f"Publicada en {oferta['origen']}" if oferta.get("origen") else ""
    partes = [oferta.get("titulo") or "", cabecera, origen, oferta.get("descripcion") or ""]
    return "\n\n".join(p for p in partes if p).strip()


__all__ = ["PORTALES", "NOMBRES", "ErrorPortal", "buscar", "buscar_jooble", "buscar_indeed", "buscar_computrabajo",
           "buscar_bumeran", "clave_jooble", "texto_de_oferta"]
