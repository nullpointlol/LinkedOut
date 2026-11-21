"""Medición offline, sin LLM.

Uso: python -m core.quality_calibration RUTA_COPIA_SQLITE [--etiquetas etiquetas.json]
Escribe _ref/calibracion.md con las diez LISTO más recientes del Radar.
Etiquetas opcionales: objeto JSON {id_del_post: [señales FP humanas]}.
"""

from core.auditor import scan_anti_ai_writing


# Contrato estable con detected_words; no depender del detalle entre paréntesis.
_PREFIJOS_SENALES = {
    "impacto vago": "impacto vago",
    "repetición cercana": "repetición cercana",
    "simetría sintáctica": "simetría sintáctica",
    "raya": "raya",
}


def medir_senales(textos: list[str]) -> dict:
    """Cuenta una detección por patrón y texto, sin I/O ni concatenar documentos."""
    conteos = dict.fromkeys(_PREFIJOS_SENALES, 0)
    for texto in textos:
        detectadas = scan_anti_ai_writing(texto)["detected_words"]
        for prefijo in conteos:
            conteos[prefijo] += int(any(p == prefijo or p.startswith(prefijo + " (") for p in detectadas))
    return {"n": len(textos), "por_senal": conteos}


def calibrar_senales(casos: list[dict]) -> dict:
    """Contá detecciones y FP humanos una vez por señal y candidatura.

    No hay I/O ni llamadas LLM. Cada caso requiere etiquetas explícitas (una
    lista vacía significa que no se etiquetaron FP, no aprobación del corpus).
    Un FP es una detección actual etiquetada por una persona: una etiqueta sin
    detección no suma. ``cumple`` expresa solo el límite numérico de esta muestra,
    no la selección/validación del corpus ni la aceptación de CA4.
    El reporte contiene únicamente agregados, sin ids, CV ni contactos.
    """
    if not isinstance(casos, list) or len(casos) != 10:
        raise ValueError("Se requieren exactamente 10 casos con ids distintos")

    ids = set()
    for caso in casos:
        if not isinstance(caso, dict) or not all(
            isinstance(caso.get(campo), str) for campo in ("id", "cv", "subject", "body")
        ):
            raise ValueError("Cada caso requiere id, cv, subject y body de texto")
        ids.add(caso["id"])
        if not isinstance(caso.get("pitch", ""), str):
            raise ValueError("El pitch debe ser texto")
        etiquetas = caso.get("falsas_positivas")
        if not isinstance(etiquetas, list) or any(
            not isinstance(senal, str) or senal not in _PREFIJOS_SENALES
            for senal in etiquetas
        ):
            raise ValueError("Cada caso requiere falsas_positivas con señales conocidas")
    if len(ids) != 10:
        raise ValueError("Se requieren exactamente 10 casos con ids distintos")

    por_senal = {
        senal: {"detectadas": 0, "falsas_positivas": 0, "tasa_fp": 0.0}
        for senal in _PREFIJOS_SENALES
    }
    for caso in casos:
        detectadas = set()
        for campo in ("cv", "pitch", "subject", "body"):
            for palabra in scan_anti_ai_writing(caso.get(campo, ""))["detected_words"]:
                for prefijo, senal in _PREFIJOS_SENALES.items():
                    if palabra == prefijo or palabra.startswith(prefijo + " ("):
                        detectadas.add(senal)
        for senal in detectadas:
            por_senal[senal]["detectadas"] += 1
            if senal in caso["falsas_positivas"]:
                por_senal[senal]["falsas_positivas"] += 1

    for conteo in por_senal.values():
        conteo["tasa_fp"] = conteo["falsas_positivas"] / 10
    return {
        "n": 10,
        "por_senal": por_senal,
        "cumple": all(conteo["falsas_positivas"] <= 2 for conteo in por_senal.values()),
    }


def main(argv=None) -> int:
    import argparse
    import json
    from contextlib import closing
    from pathlib import Path
    import sqlite3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copia", type=Path, help="Base SQLite copiada, fuera de data/")
    parser.add_argument("--etiquetas", type=Path, help="JSON opcional {id_del_post: [señales FP]}")
    args = parser.parse_args(argv)
    copia = args.copia.resolve(strict=True)
    if "data" in (p.casefold() for p in copia.parts):
        parser.error("Usá una copia fuera de data/; no se permite leer la base real")
    consulta = """SELECT lp.id, p.cv_markdown, COALESCE(p.pitch, ''),
       COALESCE(lp.email_subject, ''), lp.email_body
FROM linkedin_posts AS lp
JOIN postulaciones AS p ON p.id = lp.postulacion_id
WHERE lp.estado = 'LISTO'
  AND TRIM(COALESCE(p.cv_markdown, '')) <> ''
  AND TRIM(COALESCE(lp.email_body, '')) <> ''
ORDER BY lp.created_at DESC, lp.id DESC
LIMIT 10"""
    with closing(sqlite3.connect(copia.as_uri() + "?mode=ro", uri=True)) as conn:
        filas = conn.execute(consulta).fetchall()
    etiquetas = {}
    if args.etiquetas:
        etiquetas = json.loads(args.etiquetas.read_text(encoding="utf-8"))
        if not isinstance(etiquetas, dict):
            parser.error("Las etiquetas deben ser un objeto JSON {id: [señales FP]}")
    casos = [dict(id=str(id_post), cv=cv, pitch=pitch, subject=subject, body=body,
                  falsas_positivas=etiquetas.get(str(id_post), []))
             for id_post, cv, pitch, subject, body in filas]
    if len(casos) != 10:
        parser.error("La copia debe contener al menos 10 posts LISTO con CV y cuerpo no vacíos")
    try:
        informe = calibrar_senales(casos)
    except ValueError as exc:
        parser.error(str(exc))
    salida = Path("_ref/calibracion.md")
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("# Calibración determinista\n\n10 candidaturas LISTO más recientes; CV, pitch, asunto y cuerpo.\n\n"
                      "Consulta usada (recencia por creación del post, desempate por id):\n\n```sql\n"
                      + consulta + "\n```\n\nIds desidentificados, en orden de selección: "
                      + ", ".join(f"caso-{i:02d}" for i in range(1, 11)) + ".\n\n"
                      + ("Etiquetas humanas provistas; ids omitidos del JSON: listas vacías.\n\n"
                         if args.etiquetas else "Sin etiquetas humanas: todas las listas FP están vacías.\n\n")
                      + "| Señal | Candidaturas detectadas | FP humanos | Tasa FP sobre 10 |\n| --- | --- | --- | --- |\n"
                      + "".join(f"| {senal} | {n['detectadas']} | {n['falsas_positivas']} | {n['tasa_fp']:.2f} |\n"
                                for senal, n in informe["por_senal"].items())
                      + "\nEstas tasas no acreditan aceptación del corpus; sin etiquetas no se evaluaron los FP.\n",
                      encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
