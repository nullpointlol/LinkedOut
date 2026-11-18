"""Tests de conocimientos ausentes del candidato (creador, 10/11/2026).

Contrato de _ref/feature/spec.md: Terraform y Cloud no van al CV ni como «nociones de».
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import server
from core import cv_guardas
from core.cv_guardas import COMPLETO
from core.writer import WriterAgent
from tests.test_cv_completo_base import BASE, PERSONALES, CV_COMPLETO_OK, _analisis


def test_cargar_lista_y_casos_vacios(tmp_path, monkeypatch):
    # con {"conocimientos": ["Terraform", "AWS", ""]} devuelve ["Terraform", "AWS"]
    f_valido = tmp_path / "valido.json"
    f_valido.write_text(json.dumps({"conocimientos": ["Terraform", "AWS", ""]}), encoding="utf-8")
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_valido, raising=False)
    assert cv_guardas.cargar_conocimientos_ausentes() == ["Terraform", "AWS"]

    # archivo inexistente → []
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", tmp_path / "inexistente.json", raising=False)
    assert cv_guardas.cargar_conocimientos_ausentes() == []

    # texto "{no es json" → []
    f_invalido = tmp_path / "invalido.json"
    f_invalido.write_text("{no es json", encoding="utf-8")
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_invalido, raising=False)
    assert cv_guardas.cargar_conocimientos_ausentes() == []

    # {"otra": 1} → []
    f_otra = tmp_path / "otra.json"
    f_otra.write_text(json.dumps({"otra": 1}), encoding="utf-8")
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_otra, raising=False)
    assert cv_guardas.cargar_conocimientos_ausentes() == []


def test_palabra_completa_sin_mayusculas_ni_tildes():
    ausentes = ["Terraform", "AWS", "GCP", "Cloud"]
    assert cv_guardas.es_conocimiento_ausente("nociones de Terraform y Cloud (AWS/GCP)", ausentes=ausentes) is True
    assert cv_guardas.es_conocimiento_ausente("aws", ausentes=ausentes) is True
    assert cv_guardas.es_conocimiento_ausente("CLOUD", ausentes=ausentes) is True

    assert cv_guardas.es_conocimiento_ausente("terraformar", ausentes=ausentes) is False
    assert cv_guardas.es_conocimiento_ausente("Cloudflare", ausentes=ausentes) is False
    assert cv_guardas.es_conocimiento_ausente("Docker", ausentes=ausentes) is False
    assert cv_guardas.es_conocimiento_ausente("Jenkins", ausentes=ausentes) is False


def test_habilidades_sin_ausentes_y_linea_vacia_borrada(tmp_path, monkeypatch):
    f_ausentes = tmp_path / "conocimientos_ausentes.json"
    f_ausentes.write_text(
        json.dumps({"conocimientos": ["Terraform", "AWS", "GCP", "Azure", "Cloud"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_ausentes, raising=False)

    cv = (
        "## HABILIDADES TÉCNICAS\n"
        "• **Infraestructura y seguridad:** Docker, Jenkins, nociones de Terraform y Cloud (AWS/GCP), Codacy\n"
        "• **Cloud:** AWS, GCP\n"
    )
    resultado, cambios = cv_guardas.quitar_requisitos_de_habilidades(cv)

    assert "• **Infraestructura y seguridad:** Docker, Jenkins, Codacy" in resultado
    assert "• **Cloud:**" not in resultado
    assert "Terraform" not in resultado
    assert "AWS" not in resultado
    assert "GCP" not in resultado
    assert "se quitó de habilidades «nociones de Terraform y Cloud (AWS/GCP)»" in cambios


def test_sin_archivo_nada_cambia(tmp_path, monkeypatch):
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", tmp_path / "inexistente.json", raising=False)

    cv = (
        "## HABILIDADES TÉCNICAS\n"
        "• **Infraestructura y seguridad:** Docker, Jenkins, nociones de Terraform y Cloud (AWS/GCP), Codacy\n"
    )
    resultado, cambios = cv_guardas.quitar_requisitos_de_habilidades(cv)

    assert resultado == cv
    assert cambios == []
    assert cv_guardas.es_conocimiento_ausente("Terraform") is False


def test_requisito_ausente_no_es_keyword(tmp_path, monkeypatch):
    f_ausentes = tmp_path / "conocimientos_ausentes.json"
    f_ausentes.write_text(
        json.dumps({"conocimientos": ["Terraform", "AWS", "GCP", "Azure", "Cloud"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_ausentes, raising=False)

    assert server._keywords_del_cv(["Terraform", "Docker", "Cloud (AWS / GCP)"], {}) == ["Docker"]


@pytest.mark.asyncio
async def test_prompt_del_cv_lista_lo_que_no_sabe(tmp_path, monkeypatch):
    linea_esperada = (
        "- Conocimientos que el candidato NO tiene (nunca van al CV, ni como «nociones», ni en viñetas ni en habilidades): Terraform, AWS."
    )

    f_ausentes = tmp_path / "conocimientos_ausentes.json"
    f_ausentes.write_text(json.dumps({"conocimientos": ["Terraform", "AWS"]}), encoding="utf-8")
    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", f_ausentes, raising=False)

    bridge = AsyncMock()
    bridge.run_prompt.return_value = {
        "cv_markdown": CV_COMPLETO_OK,
        "pitch": "p",
        "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"},
    }
    writer = WriterAgent(bridge=bridge)
    await writer.generate_cv_and_pitch(_analisis(COMPLETO), profile={"experiencia": BASE, "personales": PERSONALES})
    prompt = bridge.run_prompt.call_args.args[0]
    assert linea_esperada in prompt

    monkeypatch.setattr(cv_guardas, "RUTA_CONOCIMIENTOS_AUSENTES", tmp_path / "inexistente.json", raising=False)
    bridge_sin = AsyncMock()
    bridge_sin.run_prompt.return_value = {
        "cv_markdown": CV_COMPLETO_OK,
        "pitch": "p",
        "pitches_multicanal": {"inmail": "i", "email": "e", "web_ats": "w"},
    }
    writer_sin = WriterAgent(bridge=bridge_sin)
    await writer_sin.generate_cv_and_pitch(_analisis(COMPLETO), profile={"experiencia": BASE, "personales": PERSONALES})
    prompt_sin = bridge_sin.run_prompt.call_args.args[0]
    assert "Conocimientos que el candidato NO tiene" not in prompt_sin


def test_archivo_de_ejemplo_versionado():
    """El archivo real es de cada candidato (<DATA_DIR>/candidato, sin versionar); se versiona un ejemplo."""
    ruta = Path(__file__).resolve().parent.parent / "config" / "ejemplo" / "candidato" / "conocimientos_ausentes.json"
    assert cv_guardas.cargar_conocimientos_ausentes(ruta) == ["Terraform"]
