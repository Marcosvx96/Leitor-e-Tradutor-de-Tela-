"""Tradução online e detecção de idioma (sem chave de API)."""

from __future__ import annotations

from deep_translator import GoogleTranslator, MyMemoryTranslator
from langdetect import detect

# Nome amigável -> código base usado pela interface.
IDIOMAS: dict[str, str] = {
    "Português": "pt",
    "Inglês": "en",
    "Espanhol": "es",
    "Francês": "fr",
    "Alemão": "de",
    "Italiano": "it",
    "Japonês": "ja",
    "Coreano": "ko",
    "Chinês (simplificado)": "zh-CN",
    "Russo": "ru",
    "Árabe": "ar",
    "Hindi": "hi",
    "Holandês": "nl",
    "Sueco": "sv",
    "Dinamarquês": "da",
    "Finlandês": "fi",
    "Polonês": "pl",
    "Turco": "tr",
    "Grego": "el",
    "Tcheco": "cs",
    "Húngaro": "hu",
    "Romeno": "ro",
    "Ucraniano": "uk",
    "Tailândes": "th",
    "Vietnamita": "vi",
    "Indonésio": "id",
    "Hebraico": "he",
}

# O MyMemory espera códigos com região (pt-BR, en-US etc.).
REGIOES_MYMEMORY: dict[str, str] = {
    "pt": "pt-BR",
    "en": "en-US",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "ru": "ru-RU",
    "ar": "ar-SA",
    "hi": "hi-IN",
    "nl": "nl-NL",
    "sv": "sv-SE",
    "da": "da-DK",
    "fi": "fi-FI",
    "pl": "pl-PL",
    "tr": "tr-TR",
    "el": "el-GR",
    "cs": "cs-CZ",
    "hu": "hu-HU",
    "ro": "ro-RO",
    "uk": "uk-UA",
    "th": "th-TH",
    "vi": "vi-VN",
    "id": "id-ID",
    "he": "he-IL",
}

NOMES_POR_CODIGO = {codigo.lower(): nome for nome, codigo in IDIOMAS.items()}
NOMES_POR_CODIGO.update(
    {
        "zh": "Chinês (simplificado)",
        "iw": "Hebraico",
    }
)


def codigo_base(codigo: str) -> str:
    """Normaliza 'zh-cn', 'pt-BR', 'iw' etc. para o código base."""
    normalizado = codigo.strip().lower().replace("_", "-")
    if normalizado in ("zh-cn", "zh-tw"):
        return "zh"
    if normalizado == "iw":
        return "he"
    if normalizado == "ji":
        return "yi"
    return normalizado.split("-")[0]


def nome_idioma(codigo: str | None) -> str:
    if not codigo:
        return "idioma desconhecido"
    return NOMES_POR_CODIGO.get(codigo_base(codigo), codigo)


def codigo_mymemory(codigo: str) -> str:
    base = codigo_base(codigo)
    return REGIOES_MYMEMORY.get(base, codigo)


def detectar(texto: str) -> str | None:
    """Detecta o idioma do texto localmente. Retorna None se falhar."""
    try:
        return detect(texto)
    except Exception:
        return None


def _dividir(texto: str, tamanho: int = 1200) -> list[str]:
    """Quebra textos longos em blocos, sem cortar palavras."""
    partes: list[str] = []
    restante = texto.strip()
    while len(restante) > tamanho:
        corte = restante.rfind(" ", 0, tamanho)
        if corte < tamanho // 2:
            corte = tamanho
        partes.append(restante[:corte])
        restante = restante[corte:].lstrip()
    if restante:
        partes.append(restante)
    return partes


def traduzir(texto: str, destino: str, origem: str = "auto") -> str:
    """Traduz o texto. Chame dentro de uma thread de trabalho."""
    partes = _dividir(texto)
    if not partes:
        return ""

    origem_mymemory = (
        codigo_mymemory(origem) if origem and origem != "auto" else "en-US"
    )
    try:
        tradutor = MyMemoryTranslator(
            source=origem_mymemory, target=codigo_mymemory(destino)
        )
        return "\n".join(tradutor.translate(parte) for parte in partes)
    except Exception:
        # Fallback: Google Translate (pode estar sujeito a limite de requisições).
        tradutor = GoogleTranslator(source=origem or "auto", target=destino)
        return "\n".join(tradutor.translate(parte) for parte in partes)
