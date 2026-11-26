"""Reconhecimento de texto usando o OCR nativo do Windows (funciona offline)."""

from __future__ import annotations

import asyncio
import os
import tempfile

from PIL import Image

from . import captura
from .config import DADOS

_motores: dict[str, object] = {}


def idiomas_disponiveis() -> list[str]:
    """Idiomas de OCR instalados no Windows (ex.: pt-BR)."""
    try:
        import winrt.windows.media.ocr as ocr_winrt

        return [idioma.language_tag for idioma in ocr_winrt.OcrEngine.available_recognizer_languages]
    except Exception:
        return ["pt-BR"]


async def _obter_motor(idioma: str):
    import winrt.windows.globalization as globalization
    import winrt.windows.media.ocr as ocr_winrt

    if idioma not in _motores:
        motor = ocr_winrt.OcrEngine.try_create_from_language(globalization.Language(idioma))
        if motor is None:
            motor = ocr_winrt.OcrEngine.try_create_from_user_profile_languages()
        _motores[idioma] = motor
    return _motores[idioma]


async def _reconhecer(png: bytes, idioma: str) -> str:
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.storage as storage

    DADOS.mkdir(parents=True, exist_ok=True)
    descritor, caminho = tempfile.mkstemp(prefix="ocr_", suffix=".png", dir=DADOS)
    try:
        with os.fdopen(descritor, "wb") as arquivo:
            arquivo.write(png)

        storage_file = await storage.StorageFile.get_file_from_path_async(caminho)
        fluxo = await storage_file.open_async(storage.FileAccessMode.READ)
        decodificador = await imaging.BitmapDecoder.create_async(fluxo)
        bitmap = await decodificador.get_software_bitmap_async()

        motor = await _obter_motor(idioma)
        if motor is None:
            raise RuntimeError(
                "Nenhum motor OCR disponível. Instale o pacote de idioma no Windows."
            )

        resultado = await motor.recognize_async(bitmap)
        linhas = []
        for linha in resultado.lines:
            palavras = [palavra.text for palavra in linha.words if palavra.text]
            if palavras:
                linhas.append(" ".join(palavras))
        return "\n".join(linhas).strip()
    finally:
        try:
            os.remove(caminho)
        except OSError:
            pass


def reconhecer(imagem: Image.Image, idioma: str = "pt-BR") -> str:
    """Versão síncrona: chame dentro de uma thread de trabalho."""
    return asyncio.run(_reconhecer(captura.para_png(imagem), idioma))
