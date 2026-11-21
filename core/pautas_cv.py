"""Pautas de calidad del CV: una sola fuente para el pedido de ajuste y para la auditoría.

El pedido las recibe como requisitos escritos; la auditoría las controla con su propio código y su propia lente LLM:
`core/auditor.py` (palabras, impacto vago, simetría, raya, negritas, apertura, keywords con `palabras_de_keyword`),
`PATRONES_SENALES_IA` (la lente «Signs of AI writing», la misma lista que lee la auditoría) y
`core/cv_maestro.inventos` y `largos`. Toda regla nueva va acá y en su control, nunca solo en el texto del pedido."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.auditor import MAX_NEGRITAS, UMBRAL_SIMETRIA, palabras_de_keyword
from core.cv_guardas import bloque_prompt
from core.idioma import EN
from core.prompts_evaluacion import (PATRONES_SENALES_IA, PERFIL_FORMULAS_PROHIBIDAS, PERFIL_REGLA_ANIOS,
                                     PERFIL_REGLA_PERSONA, SIGNS_OF_AI_WRITING_WORDS)

# Largos máximos, en caracteres (el auditor exige viñetas de 250 o menos; la página la confirma el PDF)
LARGO_MAX_VINETA = 230
LARGO_MAX_AUDITOR = 250  # el auditor no acepta viñetas más largas (escaneabilidad)
LARGO_MAX_PERFIL = 420
LARGO_MAX_HABILIDAD = 170
# Guía para el pedido (la página la controla el PDF, no este número): cuánto puede crecer una viñeta
CRECIMIENTO_MAX_VINETA = 1.25
# Perfil: resumen en 3ª persona impersonal, alineado al título, con los años del área y estos
# fuertes que matchean con la oferta como mínimo (lo controla `core/cv_maestro.control_perfil`)
PERFIL_MIN_FUERTES = 2
PERFIL_MAX_FUERTES = 3


def nucleo(keyword: str) -> List[str]:
    """Las palabras de la keyword con las que la auditoría ya la da por presente (una sola alcanza)."""
    palabras, nucleos = palabras_de_keyword(keyword)
    tokens = nucleos or palabras
    originales = re.findall(r"[\wÁÉÍÓÚáéíóúÑñ#+.]+", keyword or "")
    salida = []
    for t in tokens:
        orig = next((o for o in originales if o.lower().startswith(t)), t)
        if orig not in salida:
            salida.append(orig)
    return salida


def bloque_keywords(faltan: List[str], cubiertas: Optional[List[str]] = None, con_evidencia: bool = False) -> str:
    if not faltan:
        return "Todas las keywords ya están cubiertas por el texto: no agregues ninguna a la fuerza."
    lineas = [f"   - {k}: alcanza con que aparezca una de estas palabras: {', '.join(nucleo(k)) or k}" for k in faltan]
    extra = f"\n    Ya cubiertas (no las fuerces): {'; '.join(cubiertas)}." if cubiertas else ""
    # Con la evidencia de la selección, los términos de las viñetas ya están puestos donde se demuestran: lo
    # que falta va en el perfil, que resume fortalezas sin atribuirlas a un cargo
    donde = ("nombralas en el perfil (P1), nunca en una viñeta" if con_evidencia
             else "incorporalas donde la práctica encaja, con naturalidad")
    return (f"Faltan en el texto ({donde}; NO copies el nombre entero del requisito como frase):\n"
            + "\n".join(lineas) + extra)


def bloque_perfil(titulo: str, areas: Optional[List[Dict]], fuertes: Optional[List[str]], ingles: bool) -> str:
    con_anios = bool(areas)
    anios = bloque_prompt(areas or []) if areas else "- No pongas cantidad de años: el perfil no tiene esas cifras."
    if ingles:
        anios += ("\n- En inglés: «más de 6» se escribe «over 6 years»; «6» se escribe «6 years»; «1», «1 year»."
                  "\n- Todo en inglés: los fuertes y los términos de la oferta van traducidos aunque la lista esté en "
                  "castellano («documentación técnica» → «technical documentation»).")
    lista = "; ".join(fuertes or []) or "(las keywords de la oferta)"
    # Ejemplos con un candidato inventado: nunca datos reales en el código. Son una forma posible, no una plantilla
    if ingles:
        forma = ("Billing analyst with 5 years of experience in telecom systems. Experience in functional testing, "
                 "requirements gathering and SQL queries with Postman." if con_anios else
                 "Professional with experience in customer service, order picking and stock control. Experience in "
                 "cash handling and spreadsheets.")
    else:
        forma = ("Analista de facturación con 5 años de experiencia en sistemas de telecomunicaciones. Experiencia en "
                 "pruebas funcionales, relevamiento de requerimientos y consultas SQL con Postman." if con_anios else
                 "Profesional con experiencia en atención al público, armado de pedidos y control de stock. "
                 "Experiencia en manejo de caja y planillas.")
    rol = (f"Se alinea con el título «{titulo}»: abrí con ese rol o su área" if titulo
           else "Abrí con «Profesional con experiencia en…»")
    cuando_anios = f"; {PERFIL_REGLA_ANIOS}" if con_anios else ""
    return f"""9. PERFIL: es un resumen de 2 o 3 oraciones, no una viñeta más; {PERFIL_REGLA_PERSONA}. NUNCA «Gestiono…»,
   «Atiendo…», «Analizo…» ni «Me desempeño…»; tampoco un verbo en pasado («Implementé…», «Handled…»). {rol}{cuando_anios}.
   Nombra entre {PERFIL_MIN_FUERTES} y {PERFIL_MAX_FUERTES} fuertes del candidato que matchean con la oferta, elegidos de:
   {lista}. Los fuertes son habilidades o herramientas concretas, nunca condiciones ni actitudes (compromiso,
   disponibilidad, idioma, ubicación). Ejemplo con otro candidato (es la forma, no copies sus datos): «{forma}».
   No repitas el título con otras palabras («…en análisis funcional como analista funcional») ni copies el nombre
   de un requisito como frase («traducción de necesidades en soluciones TI»): usá sus términos centrales.
   PROHIBIDO en el perfil: {"; ".join(f"«{f}»" for f in PERFIL_FORMULAS_PROHIBIDAS)}; cierres sobre compromiso o actitud, y enumeraciones de tres.
{anios}"""


def pautas(keywords: List[str], idioma: str = "es", cubiertas: Optional[List[str]] = None, *, titulo: str = "",
           areas: Optional[List[Dict]] = None, fuertes: Optional[List[str]] = None, con_evidencia: bool = False) -> str:
    """Las pautas como requisitos. `keywords`: las que faltan en el texto; `cubiertas`: las que ya están;
    `areas`: los años por área del candidato; `fuertes`: las keywords en match para el perfil."""
    ingles = idioma == EN
    prohibidas = ", ".join(dict.fromkeys(SIGNS_OF_AI_WRITING_WORDS))
    estilo = "\n".join(f"   - {p}" for p in PATRONES_SENALES_IA)
    voz = ("Viñetas en inglés, cada una empieza con un verbo en pasado (Led, Built, Resolved), sin pronombres."
           if ingles else
           "Viñetas en 1ª persona del pretérito («Desarrollé», «Coordiné»), nunca en 3ª persona («desarrolló»); "
           "los verbos en -izar terminan en -icé («optimicé», nunca «optimizé»).")
    return f"""PAUTAS DE CALIDAD (son requisitos; una auditoría aparte las controla con código y con otra IA):
1. NO INVENTAR: ningún año, empresa, cargo, fecha ni cifra que no esté en la línea original o en el CV maestro.
   No agregues porcentajes, cantidades ni años de experiencia. Los encabezados de cada empresa no se tocan.
   El nivel de un idioma (B2, C1…) solo si figura en el CV maestro: nunca el que pide la oferta. Va una sola vez,
   en IDIOMAS: en el perfil, las viñetas y las habilidades, el idioma sin su nivel («en inglés»).
2. FRASES PROHIBIDAS (ni en el perfil, ni en las viñetas, ni en las habilidades): {prohibidas}.
3. LO QUE LA REVISIÓN DE ESTILO RECHAZA (no lo escribas en ninguna línea):
{estilo}
4. NADA DE IMPACTO VAGO: no cierres con «reduciendo tiempos», «mejorando la eficiencia», «optimizando la calidad»,
   «para agilizar…», «logrando…» ni equivalentes en inglés («improving efficiency», «ensuring…») si la línea no
   tiene una cifra del maestro que lo respalde. Contá qué hiciste y con qué, no un efecto sin medir.
5. SIN CIERRES EN GERUNDIO: ninguna línea cierra con «…, gestionando…», «…, validando…», «…, logrando…»,
   «…, ensuring…», «…, resulting in…». Si la línea original cierra así, partí la idea con «y» y un verbo conjugado
   («…y validé cada…») o sacá el cierre. (La auditoría marca la simetría desde el {round(UMBRAL_SIMETRIA * 100)} % de
   las viñetas y la revisión de estilo marca cualquier cierre en gerundio.)
6. {voz}
7. SIN RAYA: nunca el guion largo (—) ni el medio (–); usá coma, punto o dos puntos.
8. NEGRITAS: como mucho {MAX_NEGRITAS} en todo el CV fuera de las etiquetas de HABILIDADES, solo sobre keywords.
{bloque_perfil(titulo, areas, fuertes, ingles)}
10. CAMBIOS MÍNIMOS: en las viñetas cambiá solo las palabras necesarias; si una viñeta ya está bien, no la toques.
11. LARGOS MÁXIMOS: perfil {LARGO_MAX_PERFIL} caracteres; cada viñeta {LARGO_MAX_VINETA} caracteres y, en lo
    posible, no más de un {round((CRECIMIENTO_MAX_VINETA - 1) * 100)} % más larga que su texto original; cada línea
    de habilidades {LARGO_MAX_HABILIDAD} caracteres. El CV tiene que seguir entrando en 1 página.
12. KEYWORDS: {bloque_keywords(keywords, cubiertas, con_evidencia)}
13. HABILIDADES: cada línea ya trae solo lo que matchea la oferta y algunas afines. No agregues líneas ni ítems{
        "." if con_evidencia else ", salvo un término habilitado por el matching que no quede en ninguna otra línea del CV."}"""
