"""Reglas de los portales de empleo (Portales, 14/11): `<DATA_DIR>/candidato/reglas_portales.json`.

Cada portal tiene sus reglas, editables en Mi perfil («Reglas de Portales»). El código no trae ningún valor (creador,
15/11: todo «hardcodeo» de reglas va en Mi perfil): si a un portal le falta un valor o tiene uno inválido, ese portal
no corre y se abre un ticket (`portales/reglas_invalidas`); los demás siguen. Un portal apagado no corre ni avisa.

Formato:
    {"jooble": {...}, "indeed": {...}, "computrabajo": {...}, "bumeran": {...},
     "duplicados": {"umbral_titulo": 90, "dias": 14}}

  Comunes a todos los portales:
    activo                    true/false
    consultas_por_dia         entero > 0
    minutos_entre_consultas   entero > 0: el ritmo; nunca dos consultas al mismo portal más seguidas
    ubicacion                 texto: dónde se busca
    resultados_por_consulta   entero > 0: ofertas por consulta
    dias                      entero > 0: antigüedad máxima de una oferta
    modo                      "todas" (sin búsquedas: las ofertas recientes) o "busquedas" (las de busquedas.json
                              marcadas para ese portal)
    listados                  lista de textos (puede ir vacía): con «todas», las páginas de listado que se recorren
                              (Computrabajo: «empleos-en-capital-federal»…; Bumeran: «empleos»)
    leer_pagina_aviso         true/false: el portal trae un resumen y el aviso completo se lee de su página
    plazo_consulta_s          entero > 0: segundos que se espera la respuesta del portal
    min_descripcion_completa  entero > 0: caracteres desde los que un aviso se toma como completo

  Propios de cada portal (lo que su fuente necesita para conectarse): ver PROPIOS.
    url                       dirección del sitio (Jooble: la de su API, con «{clave}»)
    url_busqueda              RemoteOK, Remotive, Himalayas: la de su API con «{palabras}», para el modo «busquedas»
    pais                      Indeed: el país de JobSpy («argentina»)
    sitemap                   Bumeran: su sitemap de avisos (dice qué avisos hay y cuándo cambiaron)
    plazo_pagina_s            Bumeran: segundos que se espera una página en el navegador

  duplicados: una oferta con la misma empresa y un título parecido (RapidFuzz, token_set_ratio ≥ umbral_titulo, de 1 a
  100) dentro de los últimos `dias` días es la misma oferta.
"""
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import core.paths

ARCHIVO = "reglas_portales.json"
PORTALES = ("jooble", "indeed", "computrabajo", "bumeran", "remoteok", "remotive", "himalayas")
NOMBRES = {"jooble": "Jooble", "indeed": "Indeed", "computrabajo": "Computrabajo", "bumeran": "Bumeran",
           "remoteok": "RemoteOK", "remotive": "Remotive", "himalayas": "Himalayas"}
MODOS = ("todas", "busquedas")
DUPLICADOS = "duplicados"
_ENTEROS = ("consultas_por_dia", "minutos_entre_consultas", "resultados_por_consulta", "dias", "plazo_consulta_s",
            "min_descripcion_completa")
_COMUNES = ("activo", "ubicacion", "modo", "listados", "leer_pagina_aviso", *_ENTEROS)
# Lo que la fuente de cada portal necesita para conectarse (no son reglas: sin esto no hay cómo leerlo)
PROPIOS = {"jooble": ("url",), "indeed": ("pais",), "computrabajo": ("url",), "bumeran": ("url", "sitemap", "plazo_pagina_s"),
           "remoteok": ("url", "url_busqueda"), "remotive": ("url", "url_busqueda"), "himalayas": ("url", "url_busqueda")}
# Propios que pueden ir vacíos (url_busqueda: solo con el modo «busquedas»)
_OPCIONALES = ("url_busqueda",)
_ENTEROS_PROPIOS = ("plazo_pagina_s",)


@dataclass(frozen=True)
class ReglaPortal:
    portal: str
    activo: bool
    consultas_por_dia: int
    minutos_entre_consultas: int
    ubicacion: str
    resultados_por_consulta: int
    dias: int
    modo: str
    listados: Tuple[str, ...]
    leer_pagina_aviso: bool
    plazo_consulta_s: int
    min_descripcion_completa: int
    # Propios: vacíos en los portales que no los usan (ver PROPIOS)
    url: str = ""
    url_busqueda: str = ""
    pais: str = ""
    sitemap: str = ""
    plazo_pagina_s: int = 0


@dataclass(frozen=True)
class ReglaDuplicados:
    umbral_titulo: int
    dias: int


def _entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def errores_de_portal(portal: str, regla: Any) -> List[str]:
    """Los errores de las reglas de un portal (vacía = válidas), con el campo."""
    nombre = NOMBRES.get(portal, portal)
    campos = (*_COMUNES, *PROPIOS.get(portal, ()))
    if not isinstance(regla, dict):
        return [f"{nombre}: tiene que ser un objeto con {', '.join(campos)}."]
    errores: List[str] = [f"{nombre}: campo desconocido «{c}»." for c in regla if c not in campos]
    for clave in ("activo", "leer_pagina_aviso"):
        if clave not in regla:
            errores.append(f"{nombre}: falta «{clave}».")
        elif not isinstance(regla[clave], bool):
            errores.append(f"{nombre}: «{clave}» tiene que ser true o false.")
    for clave in ("ubicacion", *[p for p in PROPIOS.get(portal, ()) if p not in (*_ENTEROS_PROPIOS, *_OPCIONALES)]):
        if clave not in regla:
            errores.append(f"{nombre}: falta «{clave}».")
        elif not isinstance(regla[clave], str) or not regla[clave].strip():
            errores.append(f"{nombre}: «{clave}» tiene que ser un texto.")
    for clave in (*_ENTEROS, *[p for p in PROPIOS.get(portal, ()) if p in _ENTEROS_PROPIOS]):
        if clave not in regla:
            errores.append(f"{nombre}: falta «{clave}».")
        elif not _entero(regla[clave]):
            errores.append(f"{nombre}: «{clave}» tiene que ser un entero mayor que 0.")
    if "modo" not in regla:
        errores.append(f"{nombre}: falta «modo» (todas o busquedas).")
    elif regla["modo"] not in MODOS:
        errores.append(f"{nombre}: «modo» tiene que ser todas o busquedas.")
    listados = regla.get("listados", [])
    if not (isinstance(listados, list) and all(isinstance(x, str) and x.strip() for x in listados)):
        errores.append(f"{nombre}: «listados» tiene que ser una lista de páginas de listado.")
    busqueda = regla.get("url_busqueda")
    if busqueda is not None and not (isinstance(busqueda, str) and (not busqueda.strip() or "{palabras}" in busqueda)):
        errores.append(f"{nombre}: «url_busqueda» tiene que llevar «{{palabras}}» donde van las palabras de la búsqueda.")
    if regla.get("modo") == "busquedas" and "url_busqueda" in PROPIOS.get(portal, ()) and not str(busqueda or "").strip():
        errores.append(f"{nombre}: con «busquedas», falta «url_busqueda».")
    url = regla.get("url")
    if portal == "jooble" and isinstance(url, str) and url.strip() and "{clave}" not in url:
        errores.append(f"{nombre}: «url» tiene que llevar «{{clave}}» donde va la key de la API.")
    return errores


def errores_de_duplicados(dato: Any) -> List[str]:
    if not isinstance(dato, dict):
        return ["«duplicados» tiene que ser un objeto con umbral_titulo y dias."]
    errores = [f"duplicados: campo desconocido «{c}»." for c in dato if c not in ("umbral_titulo", "dias")]
    umbral = dato.get("umbral_titulo")
    if not (_entero(umbral) and umbral <= 100):
        errores.append("duplicados: «umbral_titulo» tiene que ser un entero de 1 a 100.")
    if not _entero(dato.get("dias")):
        errores.append("duplicados: «dias» tiene que ser un entero mayor que 0.")
    return errores


def validar_reglas_portales(datos: Any) -> List[str]:
    """Los errores de reglas_portales.json (vacía = se puede guardar). Exige todos los portales y «duplicados»."""
    if not isinstance(datos, dict):
        return ["Las reglas de Portales tienen que ser un objeto JSON."]
    errores = [f"Portal desconocido: «{clave}»." for clave in datos if clave not in (*PORTALES, DUPLICADOS)]
    for portal in PORTALES:
        if portal not in datos:
            errores.append(f"Falta «{portal}».")
        else:
            errores.extend(errores_de_portal(portal, datos[portal]))
    if DUPLICADOS not in datos:
        errores.append(f"Falta «{DUPLICADOS}».")
    else:
        errores.extend(errores_de_duplicados(datos[DUPLICADOS]))
    return errores


def _leer() -> Tuple[Optional[Dict[str, Any]], str]:
    ruta = core.paths.DATA_DIR / "candidato" / ARCHIVO
    if not ruta.is_file():
        return None, f"No existe {ARCHIVO} en el perfil: ningún portal corre hasta que se cargue."
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{ARCHIVO} no es un JSON válido: {exc}"
    if not isinstance(datos, dict):
        return None, f"{ARCHIVO}: tiene que ser un objeto JSON."
    return datos, ""


def leer_reglas_portales() -> Tuple[Dict[str, ReglaPortal], Dict[str, str]]:
    """(reglas válidas por portal, motivo por portal que no puede correr). Cada portal se valida solo: uno mal
    escrito no frena al otro."""
    datos, motivo = _leer()
    if datos is None:
        return {}, {p: motivo for p in PORTALES}
    validas: Dict[str, ReglaPortal] = {}
    invalidas: Dict[str, str] = {}
    for portal in PORTALES:
        if portal not in datos:
            invalidas[portal] = f"{ARCHIVO}: falta «{portal}»."
            continue
        errores = errores_de_portal(portal, datos[portal])
        if errores:
            invalidas[portal] = f"{ARCHIVO}: " + " ".join(errores)
            continue
        r = datos[portal]
        validas[portal] = ReglaPortal(
            portal=portal, activo=r["activo"], consultas_por_dia=r["consultas_por_dia"],
            minutos_entre_consultas=r["minutos_entre_consultas"], ubicacion=r["ubicacion"].strip(),
            resultados_por_consulta=r["resultados_por_consulta"], dias=r["dias"], modo=r["modo"],
            listados=tuple(x.strip() for x in r.get("listados") or []), leer_pagina_aviso=r["leer_pagina_aviso"],
            plazo_consulta_s=r["plazo_consulta_s"], min_descripcion_completa=r["min_descripcion_completa"],
            url=str(r.get("url") or "").strip(), url_busqueda=str(r.get("url_busqueda") or "").strip(), pais=str(r.get("pais") or "").strip(),
            sitemap=str(r.get("sitemap") or "").strip(), plazo_pagina_s=int(r.get("plazo_pagina_s") or 0),
        )
    return validas, invalidas


def leer_duplicados() -> Optional[ReglaDuplicados]:
    """La regla de duplicados aproximados, o None si falta o es inválida (entonces solo valen el link y el título y
    la empresa exactos)."""
    datos, _ = _leer()
    dato = (datos or {}).get(DUPLICADOS)
    if dato is None or errores_de_duplicados(dato):
        return None
    return ReglaDuplicados(umbral_titulo=dato["umbral_titulo"], dias=dato["dias"])
