"""Medida «tabla sin huecos»: cada acción del inventario y cada campo de Mi perfil tiene lugar en el rediseño.

Uso: python docs/rediseno/mapa_completo.py  -> sale con 1 si falta alguno.
- Acciones: claves de tests/inventario_botones.json (fijos, dinámicos y cubiertos en otro test) contra mapa-acciones.json.
- Campos de Mi perfil: rótulos y claves que static/perfil.js dibuja contra mapa-campos.json.
"""
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
AQUI = pathlib.Path(__file__).parent


def acciones():
    inv = json.loads((RAIZ / "tests" / "inventario_botones.json").read_text(encoding="utf-8"))
    claves = [x["clave"] for x in inv["fijos"]] + [x["clave"] for x in inv["dinamicos"]]
    claves += [x if isinstance(x, str) else x.get("clave") for x in inv["dinamicos_cubiertos_en_otro_test"]]
    mapa = json.loads((AQUI / "mapa-acciones.json").read_text(encoding="utf-8"))
    return claves, [c for c in claves if not mapa.get(c)]


def campos():
    js = (RAIZ / "static" / "perfil.js").read_text(encoding="utf-8")
    # Tarjetas: ['clave', 'Título'] en «definiciones»; campos: claves de datos que cada formulario lee o escribe
    tarjetas = re.findall(r"\['([a-z_]+)', '[^']+'\]", js.split("const JSON_FORMULARIO")[0])
    datos = set(re.findall(r"\['([a-z_]+)', '[^']*', '(?:text|number|checkbox|lista|date|textarea)'\]", js))
    # Reglas del Radar: rutas ['grupo', 'campo'] → «grupo.campo»
    for grupo, sub in re.findall(r"\[\['([a-z_]+)'(?:, '([a-z_]+)')?\]", js):
        datos.add(f"{grupo}.{sub}" if sub else grupo)
    datos |= {"edad", "localidades_permitidas", "residencia", "residencia_cerca_de", "roles_objetivo", "areas",
              "coincidencias", "transferibles", "habilidades_base", "conocimientos", "cargos_relevantes_entre",
              "empresas", "cv_simple", "activo", "un_cargo_por_empresa", "llenar_pagina", "titular", "titular_en",
              "vinetas_por_cargo", "secciones_habilidades", "lineas_habilidades", "titulo_habilidades",
              "titulo_habilidades_en", "umbral", "min_ofertas", "duplicados", "umbral_titulo", "anios_por_area",
              "cuenta_feed", "cuenta_busquedas", "portales", "palabras", "anios", "reemplaza", "consulta",
              "activa", "resultados", "errores", "vista_previa_anios"}
    esperados = sorted(set(tarjetas) | datos)
    mapa = json.loads((AQUI / "mapa-campos.json").read_text(encoding="utf-8"))
    return esperados, [c for c in esperados if not mapa.get(c)]


def main():
    falta = 0
    for nombre, (todos, sin) in (("acciones", acciones()), ("campos de Mi perfil", campos())):
        print(f"{nombre}: {len(todos)} en el inventario, {len(todos) - len(sin)} mapeados, {len(sin)} sin lugar")
        for c in sin:
            print(f"  FALTA {c}")
        falta += len(sin)
    return 1 if falta else 0


if __name__ == "__main__":
    sys.exit(main())
