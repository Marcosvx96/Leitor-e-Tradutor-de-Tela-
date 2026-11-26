"""Normalização e validação dos atalhos globais (sintaxe do pynput)."""

from __future__ import annotations

import re
from typing import Any

COMBOS_PADRAO: dict[str, str] = {
    "selecionar": "<ctrl>+<shift>+s",
    "ler": "<ctrl>+<shift>+l",
    "parar": "<ctrl>+<shift>+p",
    "copiar": "<ctrl>+<shift>+c",
    "traduzir": "<ctrl>+<shift>+t",
    "restaurar": "<ctrl>+<shift>+m",
}

MODIFICADORES = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "shift": "<shift>",
    "alt": "<alt>",
    "win": "<cmd>",
    "windows": "<cmd>",
    "super": "<cmd>",
    "cmd": "<cmd>",
}

ORDEM = ("<ctrl>", "<alt>", "<shift>", "<cmd>")

ALIASES = {
    "enter": "<enter>",
    "return": "<enter>",
    "esc": "<esc>",
    "escape": "<esc>",
    "space": "<space>",
    "tab": "<tab>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    **{f"f{i}": f"<f{i}>" for i in range(1, 21)},
}

_TOKEN_CANONICO = re.compile(r"<[a-z_0-9]+>")
_TOKEN_SIMPLES = re.compile(r"[a-z0-9]")
_TOKEN_VK = re.compile(r"<(\d+)>")

# VKs de modificadores esquerdo/direito precisam virar o modificador genérico.
_MODIFICADORES_VK = {
    17: "<ctrl>", 162: "<ctrl>", 163: "<ctrl>",
    16: "<shift>", 160: "<shift>", 161: "<shift>",
    18: "<alt>", 164: "<alt>", 165: "<alt>",
    91: "<cmd>", 92: "<cmd>",
}
_VK_NOMES: dict[int, str] | None = None


def vk_para_token(vk: int) -> str:
    """Converte um virtual-key do Windows para `<nome>` legível pelo pynput.

    Também recupera atalhos antigos salvos como `<112>` ou `<45>`.
    """
    global _VK_NOMES
    if _VK_NOMES is None:
        nomes: dict[int, str] = {}
        try:
            from pynput import keyboard

            for tecla in keyboard.Key:
                codigo = getattr(getattr(tecla, "value", None), "vk", None)
                if codigo is None:
                    continue
                nomes.setdefault(
                    codigo, _MODIFICADORES_VK.get(codigo, f"<{tecla.name}>")
                )
        except Exception:
            pass
        for codigo, token in _MODIFICADORES_VK.items():
            nomes.setdefault(codigo, token)
        _VK_NOMES = nomes
    return _VK_NOMES.get(vk, f"<{vk}>")


def _token(parte: str) -> str | None:
    texto = parte.strip().lower()
    if not texto:
        return None
    if texto in MODIFICADORES:
        return MODIFICADORES[texto]
    if texto in ALIASES:
        return ALIASES[texto]
    numerico = _TOKEN_VK.fullmatch(texto)
    if numerico:
        return vk_para_token(int(numerico.group(1)))
    if _TOKEN_CANONICO.fullmatch(texto) or _TOKEN_SIMPLES.fullmatch(texto):
        return texto
    return None


def normalizar(valor: Any, padrao: str) -> str:
    """Aceita 'Ctrl+Shift+S' e devolve '<ctrl>+<shift>+s'.

    Combinações inválidas (sem tecla principal, acentos, pontuação) caem
    no padrão. Também valida com o parser real do pynput, quando disponível.
    """
    if not isinstance(valor, str):
        return padrao
    partes = valor.replace(" ", "").split("+")
    if not partes or any(not parte for parte in partes):
        return padrao

    modificadores: set[str] = set()
    teclas: list[str] = []
    for parte in partes:
        convertido = _token(parte)
        if convertido is None:
            return padrao
        if convertido in ORDEM:
            modificadores.add(convertido)
        else:
            teclas.append(convertido)

    if len(teclas) != 1 or len(set(teclas)) != 1:
        return padrao

    final = "+".join([m for m in ORDEM if m in modificadores] + teclas)
    try:
        from pynput import keyboard

        keyboard.HotKey.parse(final)
    except Exception:
        # Sem pynput (ou token desconhecido) mantemos a sintaxe normalizada.
        pass
    return final


def formatar(combo: str) -> str:
    """Mostra a combinação de forma amigável: Ctrl + Shift + F12."""
    partes: list[str] = []
    for parte in combo.split("+"):
        if parte.startswith("<") and parte.endswith(">"):
            interno = parte[1:-1]
            if interno.isdigit():
                parte = vk_para_token(int(interno))
                interno = parte[1:-1]
            nome = interno.upper().replace("_", " ")
            # "Win" é mais reconhecível que "Cmd" para quem usa Windows.
            if nome == "CMD":
                nome = "WIN"
        else:
            nome = parte.upper()
        partes.append(nome)
    return " + ".join(partes)


def normalizar_todos(valores: Any) -> dict[str, str]:
    """Completa, normaliza e remove colisões entre as ações."""
    dados = valores if isinstance(valores, dict) else {}
    resultado: dict[str, str] = {}
    usados: set[str] = set()
    for acao, padrao in COMBOS_PADRAO.items():
        combo = normalizar(dados.get(acao), padrao)
        if combo in usados:
            combo = padrao
        resultado[acao] = combo
        usados.add(combo)
    return resultado
