"""Captura de tela e preparação da imagem para o OCR."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from io import BytesIO

import mss
from PIL import Image, ImageFilter

# Constantes GetSystemMetrics para a área virtual (todos os monitores juntos).
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


@dataclass
class Regiao:
    x: int
    y: int
    largura: int
    altura: int

    @property
    def direita(self) -> int:
        return self.x + self.largura

    @property
    def inferior(self) -> int:
        return self.y + self.altura

    def para_dicionario(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "largura": self.largura,
            "altura": self.altura,
        }

    @classmethod
    def de_dicionario(cls, dados: dict | None) -> "Regiao | None":
        if not dados:
            return None
        try:
            return cls(
                int(dados["x"]),
                int(dados["y"]),
                int(dados["largura"]),
                int(dados["altura"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def area_virtual() -> tuple[int, int, int, int]:
    """Retorna (x, y, largura, altura) cobrindo todos os monitores."""
    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def limitar_regiao(regiao: Regiao) -> Regiao:
    """Mantém a região dentro da área virtual e com tamanho mínimo."""
    vx, vy, vw, vh = area_virtual()
    x = max(vx, min(regiao.x, vx + vw - 40))
    y = max(vy, min(regiao.y, vy + vh - 30))
    direita = min(vx + vw, max(x + 40, regiao.direita))
    inferior = min(vy + vh, max(y + 30, regiao.inferior))
    return Regiao(x, y, direita - x, inferior - y)


def _para_pil(captura) -> Image.Image:
    return Image.frombytes("RGB", captura.size, captura.rgb)


def capturar_area(regiao: Regiao, melhorar: bool = False) -> Image.Image:
    """Captura somente o retângulo escolhido."""
    regiao = limitar_regiao(regiao)
    with mss.mss() as capturador:
        imagem = capturador.grab(
            {
                "left": regiao.x,
                "top": regiao.y,
                "width": regiao.largura,
                "height": regiao.altura,
            }
        )
    resultado = _para_pil(imagem)
    return preparar_para_ocr(resultado) if melhorar else resultado


def capturar_tela_completa() -> Image.Image:
    """Captura toda a área virtual. Usada para o preview de zoom."""
    with mss.mss() as capturador:
        imagem = capturador.grab(capturador.monitors[0])
    return _para_pil(imagem)


def preparar_para_ocr(imagem: Image.Image) -> Image.Image:
    """Amplia e nitidifica sem binarizar, preservando acentos e letras finas.

    Aumentar contraste e/ou binarizar fazia o OCR trocar ``m`` por ``v``/``n`` e
    ``i`` por ``1`` em fontes pequenas (``também -> tamvem``, ``ideal ->
    idecal``). Manter os tons originais e aplicar uma ampliação moderada com
    nitidez recuperou esses detalhes nos testes com capturas pequenas.
    """
    resultado = imagem
    if resultado.width < 1400:
        # Sempre pelo menos 2x: em larguras como 520/720 px o fator antigo
        # (baseado apenas em 1400) ficava em 1 e o erro aparecia.
        fator = min(3, max(2, 1400 // max(1, resultado.width)))
        resultado = resultado.resize(
            (resultado.width * fator, resultado.height * fator),
            Image.Resampling.LANCZOS,
        )
    return resultado.filter(
        ImageFilter.UnsharpMask(radius=2, percent=160, threshold=2)
    )


def para_png(imagem: Image.Image) -> bytes:
    buffer = BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()
