"""Configurações simples, salvas em JSON dentro da pasta do projeto."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .atalhos import COMBOS_PADRAO as ATALHOS_PADRAO
from .atalhos import normalizar_todos

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
CAMINHO_CONFIG = DADOS / "config.json"

VOZ_NEURAL_PADRAO = "pt-BR-ThalitaMultilingualNeural"

# Overlay de seleção / marquee.
COR_SELECAO_PADRAO = "#0078D4"
OPACIDADE_PADRAO = 45
ZOOM_LARGURA_PADRAO = 360
ZOOM_ALTURA_PADRAO = 200

# Destaque da leitura no painel de texto.
COR_DESTAQUE_PADRAO = "#CCE4FB"
OPACIDADE_DESTAQUE_PADRAO = 70


def _inteiro_limito(valor, padrao: int, minimo: int, maximo: int) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return max(minimo, min(numero, maximo))


def _cor_hex(valor, padrao: str) -> str:
    if isinstance(valor, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", valor.strip()):
        return valor.strip().upper()
    return padrao


PADRAO: dict = {
    "regiao": None,
    "idioma_ocr": "pt-BR",
    "idioma_destino": "pt",
    "traduzir_automaticamente": False,
    "voz_sapi": "Microsoft Maria Desktop",
    "modo_neural": False,
    "voz_neural": VOZ_NEURAL_PADRAO,
    "velocidade": 1.0,
    "volume": 90,
    "sempre_topo": False,
    "tema_escuro": False,
    "abaixo_apos_selecao": True,
    "anexar_texto_ocr": False,
    "confirmar_ao_soltar": True,
    "mostrar_zoom": True,
    "zoom_largura": ZOOM_LARGURA_PADRAO,
    "zoom_altura": ZOOM_ALTURA_PADRAO,
    "cor_selecao": COR_SELECAO_PADRAO,
    "opacidade_selecao": OPACIDADE_PADRAO,
    "cor_destaque": COR_DESTAQUE_PADRAO,
    "opacidade_destaque": OPACIDADE_DESTAQUE_PADRAO,
    "melhorar_ocr": True,
    "atalhos": dict(ATALHOS_PADRAO),
}


def carregar() -> dict:
    """Lê o config.json; se não existir, retorna os valores padrão."""
    DADOS.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(PADRAO)
    if CAMINHO_CONFIG.exists():
        try:
            with CAMINHO_CONFIG.open("r", encoding="utf-8") as arquivo:
                salvo = json.load(arquivo)
            if isinstance(salvo, dict):
                cfg.update(
                    {chave: valor for chave, valor in salvo.items() if chave in PADRAO}
                )
        except (OSError, ValueError):
            pass
    cfg["atalhos"] = normalizar_todos(cfg.get("atalhos"))
    cfg["zoom_largura"] = _inteiro_limito(
        cfg.get("zoom_largura"), ZOOM_LARGURA_PADRAO, 200, 800
    )
    cfg["zoom_altura"] = _inteiro_limito(
        cfg.get("zoom_altura"), ZOOM_ALTURA_PADRAO, 120, 500
    )
    cfg["opacidade_selecao"] = _inteiro_limito(
        cfg.get("opacidade_selecao"), OPACIDADE_PADRAO, 5, 100
    )
    cfg["cor_selecao"] = _cor_hex(cfg.get("cor_selecao"), COR_SELECAO_PADRAO)
    cfg["opacidade_destaque"] = _inteiro_limito(
        cfg.get("opacidade_destaque"), OPACIDADE_DESTAQUE_PADRAO, 5, 100
    )
    cfg["cor_destaque"] = _cor_hex(
        cfg.get("cor_destaque"), COR_DESTAQUE_PADRAO
    )
    return cfg


def salvar(cfg: dict) -> None:
    """Grava as configurações dentro de dados/config.json."""
    DADOS.mkdir(parents=True, exist_ok=True)
    temporario = CAMINHO_CONFIG.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(cfg, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(CAMINHO_CONFIG)
