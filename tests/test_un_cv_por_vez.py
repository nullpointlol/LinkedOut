"""Tests de la regla: generar un CV por vez en el portal."""
import server


def test_radar_loop_un_solo_worker_de_generacion():
    loop = server._build_radar_loop()
    assert loop._generation_workers == 1


def test_constante_generaciones_en_paralelo():
    assert getattr(server, "GENERACIONES_EN_PARALELO", None) == 1
