"""Verificação rápida do ambiente e do OCR (rode com a .venv do projeto)."""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from PIL import Image  # noqa: E402

from leitor_em_voz_alta import fala, ocr, traducao  # noqa: E402
from leitor_em_voz_alta.captura import Regiao, limitar_regiao  # noqa: E402
from leitor_em_voz_alta.fala import dividir_texto  # noqa: E402


def main() -> None:
    print("Idiomas OCR:", ocr.idiomas_disponiveis())
    print("Vozes SAPI:", fala.listar_vozes_sapi())
    print("Vozes neurais (amostra):", fala.listar_vozes_neural()[:5])
    print("Área virtual:", __import__("leitor_em_voz_alta.captura", fromlist=["area_virtual"]).area_virtual())
    print("Região limitada:", limitar_regiao(Regiao(-50, -50, 500, 300)))
    print("Pedacos de fala:", dividir_texto("Primeira frase. Segunda frase. Terceira!"))
    print("Idiomas de tradução:", len(traducao.IDIOMAS))
    print("Idioma detectado:", traducao.detectar("Hello world, this is a test."))
    try:
        print(
            "Tradução (en -> pt):",
            traducao.traduzir("Hello world, this is a test.", "pt", "en"),
        )
    except Exception as exc:
        print("Tradução indisponível no momento:", exc)

    caminho = RAIZ / "referencias" / "teste_ocr.png"
    if caminho.exists():
        texto = ocr.reconhecer(Image.open(caminho), "pt-BR")
        print("Texto reconhecido:", repr(texto))
        if "OCR" not in texto.upper():
            raise SystemExit("Falha: a palavra 'OCR' não foi reconhecida.")
    else:
        print("Imagem de teste não encontrada; pulando OCR.")

    print("OK: ambiente verificado.")


if __name__ == "__main__":
    main()
