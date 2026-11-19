"""Al tocar un requisito, la reconfirmación lee el mismo texto que el análisis (808: página; 3993: imagen)."""
from core import descartes, radar_api
from core.prioridades import texto_capturado
from tests.test_brechas_confirmacion import RESTRICCIONES, requisito


def test_reconfirmar_usa_imagen_y_pagina(monkeypatch):
    monkeypatch.setattr(descartes, "cargar_restricciones", lambda: RESTRICCIONES)
    post = {"texto": "Buscamos instructores", "texto_imagen": "Personas de 21 a 35 años.",
            "texto_pagina": "Modalidad: Presencial. Santiago."}
    analisis = {"requisitos": [requisito("Personas de 21 a 35 años.", "EDAD"),
                               requisito("Modalidad: Presencial. Santiago.", "UBICACION")]}
    radar_api._recalcular_descarte(analisis, texto_capturado(post))
    assert sorted(m["categoria"] for m in analisis["no_apto"]["motivos"]) == ["EDAD", "UBICACION"]
    # Solo con el texto de la publicación, las citas no aparecen y el sello se perdería
    radar_api._recalcular_descarte(analisis, post["texto"])
    assert "no_apto" not in analisis
    assert "_recalcular_descarte(analisis, texto_capturado(post)" in open(radar_api.__file__, encoding="utf-8").read()
