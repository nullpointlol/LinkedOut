"""Panel «Revisar» (creador, 15/11): cada ítem dice qué revisar y por qué en una frase breve, sin el texto completo."""
import pytest

from core import revision_store as rs


@pytest.mark.parametrize("item,frase", [
    ({"tipo": "dato_inventado", "campo": "pretensión salarial", "detalle": "Completado en el mensaje"},
     "Revisá el mensaje porque contiene una remuneración estimada."),
    ({"tipo": "senal_ia", "campo": "CV", "detalle": "raya (guion largo en prosa o rango)"},
     "Revisá la redacción del CV porque tiene un guion largo."),
    ({"tipo": "senal_ia", "campo": "mensaje (email)", "detalle": "impacto vago (consecuencia sin cifra ni alcance verificable)"},
     "Revisá la redacción del mensaje porque promete un resultado sin un dato que lo respalde."),
    ({"tipo": "contradiccion", "campo": "nivel de inglés", "detalle": "No se pudo corregir: El nivel declarado es: C1"},
     "Revisá el nivel de inglés del CV porque no coincide con el de tu perfil y no se pudo corregir."),
    ({"tipo": "senal_ia", "campo": "CV", "detalle": "El nivel declarado es: English: C1"},
     "Revisá el nivel de inglés del CV porque no coincide con el de tu perfil."),
    ({"tipo": "placeholder", "campo": "todo", "detalle": "Quedó sin completar en el CV"},
     "Revisá el CV porque quedó un campo sin completar."),
    ({"tipo": "contradiccion", "campo": "puesto objetivo", "detalle": "Completado en el CV"},
     "Revisá el título del CV porque se corrigió para que coincida con tu perfil."),
    ({"tipo": "senal_ia", "campo": "body", "detalle": "body: tiene 180 palabras (máximo 150)"},
     "Revisá la redacción del mensaje porque es más largo de lo permitido."),
])
def test_cada_item_dice_que_revisar_y_por_que(item, frase):
    assert rs.frase_revision(item) == frase


def test_toda_frase_es_una_sola_oracion_con_porque():
    for tipo in rs.TIPOS:
        f = rs.frase_revision({"tipo": tipo, "campo": "CV", "detalle": "algo raro"})
        assert f.startswith("Revisá ") and " porque " in f and f.count(".") == 1 and f.endswith(".")


def test_listar_trae_la_frase(tmp_path):
    db = tmp_path / "r.db"
    rs.registrar("senal_ia", "CV", "texto largo del CV", "raya (guion largo en prosa o rango)", db_path=db)
    assert rs.listar(db_path=db)[0]["frase"] == "Revisá la redacción del CV porque tiene un guion largo."
