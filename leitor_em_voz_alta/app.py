"""Interface principal: barra flutuante estilo lupa + painel do texto lido."""

from __future__ import annotations

import ctypes
import inspect
import queue
import re
import threading
import time
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from . import captura, fala, ocr, traducao
from .atalhos import COMBOS_PADRAO, formatar, normalizar, vk_para_token
from .captura import Regiao
from .config import (
    COR_DESTAQUE_PADRAO,
    COR_SELECAO_PADRAO,
    OPACIDADE_DESTAQUE_PADRAO,
    OPACIDADE_PADRAO,
    VOZ_NEURAL_PADRAO,
    ZOOM_ALTURA_PADRAO,
    ZOOM_LARGURA_PADRAO,
    carregar,
    salvar,
)
from .selecao import SeletorRegiao

# --------------------------------------------------------------- design tokens
PALETAS: dict[str, dict[str, str]] = {
    "claro": {
        "COR_FUNDO": "#F3F3F7",
        # #FFFFFF é reservado para a fonte branca do tema escuro. Usar um
        # branco quase idêntico na superfície evita colisão no mapa reverso.
        "COR_SUPERFICIE": "#FEFEFE",
        "COR_SUPERFICIE_2": "#F8F9FC",
        "COR_BORDA": "#E4E7EE",
        "COR_TEXTO": "#1B1B1F",
        "COR_BOTAO_CONTORNO_TEXTO": "#3A3F4B",
        "COR_SECUNDARIA": "#61667A",
        "COR_AZUL": "#0078D4",
        "COR_AZUL_CLARO": "#EAF2FE",
        "COR_VERDE": "#107C41",
        "COR_VERDE_CLARO": "#E8F5EC",
        "COR_VERMELHO": "#C42B1C",
        "COR_VERMELHO_CLARO": "#FDECEA",
        "COR_SOMBRA": "#0F172A",
    },
    "escuro": {
        "COR_FUNDO": "#1B1D22",
        "COR_SUPERFICIE": "#2A2D34",
        "COR_SUPERFICIE_2": "#23262C",
        "COR_BORDA": "#3D424D",
        "COR_TEXTO": "#FFFFFF",
        "COR_BOTAO_CONTORNO_TEXTO": "#F7F8FA",
        "COR_SECUNDARIA": "#AFB6C5",
        "COR_AZUL": "#4AB2FF",
        "COR_AZUL_CLARO": "#15324A",
        "COR_VERDE": "#5FD08A",
        "COR_VERDE_CLARO": "#16382A",
        "COR_VERMELHO": "#FF7A6B",
        "COR_VERMELHO_CLARO": "#3B201E",
        "COR_SOMBRA": "#000000",
    },
}

# Cores que estavam escritas diretamente nos widgets e também precisam mudar.
# No tema claro a tradução é identidade; no escuro cada cor ganha o equivalente.
_EXTRAS_CLARO = {
    "#ECEEF4": "#ECEEF4",  # faixa dos cartões / trough do volume
    "#C8CDD8": "#C8CDD8",  # scrollbar
    "#A6ADBC": "#A6ADBC",  # scrollbar ativa
    "#9AA0AE": "#9AA0AE",  # versão no rodapé
    "#CCE4FB": "#CCE4FB",  # seleção do texto
    "#E8EAF0": "#E8EAF0",  # hover dos botões da barra
}
_EXTRAS_ESCO = {
    "#ECEEF4": "#3C4049",
    "#C8CDD8": "#5A6070",
    "#A6ADBC": "#727A8A",
    "#9AA0AE": "#7F8797",
    "#CCE4FB": "#25496A",
    "#E8EAF0": "#343947",
}
PALETA_EXTRA: dict[str, str] = {}
TEMA_ATUAL = "claro"

for _chave, _valor in PALETAS["claro"].items():
    globals()[_chave] = _valor
PALETA_EXTRA.update(_EXTRAS_CLARO)
# Widgets podem ser criados com o app já no escuro (janela de configurações,
# por exemplo). O mapa precisa reconhecer as duas paletas, senão esses widgets
# ficam presos na cor com que nasceram.
COR_PARA_CHAVE: dict[str, str] = {}
for _paleta in PALETAS.values():
    for _chave, _valor in _paleta.items():
        COR_PARA_CHAVE[_valor] = _chave
for _claro, _escuro in _EXTRAS_ESCO.items():
    COR_PARA_CHAVE[_claro] = _claro
    COR_PARA_CHAVE[_escuro] = _claro
# Cores extras: chave canônica é sempre a do tema claro.
EXTRA_CANONICA = {
    **{cor: cor for cor in _EXTRAS_CLARO},
    **{escuro: claro for claro, escuro in _EXTRAS_ESCO.items()},
}


def _cor_tema(cor: str) -> str:
    """Traduz uma cor de qualquer tema para o tema ativo."""
    chave = COR_PARA_CHAVE.get(cor)
    if chave is None:
        return cor
    if chave in PALETAS["claro"]:
        return PALETAS[TEMA_ATUAL][chave]
    return PALETA_EXTRA.get(EXTRA_CANONICA.get(cor, chave), cor)


def _rgb(cor: str) -> tuple[int, int, int]:
    texto = cor.lstrip("#")
    return tuple(int(texto[pos:pos + 2], 16) for pos in (0, 2, 4))


def _misturar_cor(cor: str, fundo: str, opacidade: float) -> str:
    """Mistura a cor escolhida com o fundo para simular transparência no Tk."""
    primeiro, segundo = _rgb(cor), _rgb(fundo)
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, round(a * opacidade + b * (1 - opacidade))))
        for a, b in zip(primeiro, segundo)
    )


def _cor_legivel(cor: str) -> str:
    vermelho, verde, azul = _rgb(cor)
    brilho = (0.299 * vermelho + 0.587 * verde + 0.114 * azul) / 255
    return "#1B1B1F" if brilho > 0.55 else "#FFFFFF"


FONTE = ("Segoe UI", 9)
FONTE_BOLD = ("Segoe UI", 9, "bold")
FONTE_TITULO = ("Segoe UI", 11, "bold")
FONTE_LEGENDA = ("Segoe UI", 8)
FONTE_CODIGO = ("Consolas", 10)
ROTULO = dict(bg=COR_SUPERFICIE, fg=COR_SECUNDARIA, font=FONTE, anchor="w")
RAIO = 10
RAIO_JANELA = 18
RAIO_SOMBRA = 20
PAD = 12
ALFA_JANELA = 0.98

# Compatibilidade com o código antigo (configurações e botão de restaurar).
COR_TITULO = COR_AZUL

VELOCIDADES = {
    "0,5x": 0.5,
    "0,75x": 0.75,
    "1x": 1.0,
    "1,25x": 1.25,
    "1,5x": 1.5,
    "2x": 2.0,
}
ATALHOS_EVENTOS = {
    "selecionar": "Selecionar região",
    "ler": "Ler região/texto",
    "parar": "Parar leitura",
    "copiar": "Copiar texto",
    "traduzir": "Traduzir texto",
    "restaurar": "Mostrar janela",
}

# Win32: z-order da janela flutuante.
HWND_BOTTOM = 1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x0008
GA_ROOT = 2

# Win32: minimize/restaure e integração com a barra de tarefas.
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5
GWL_STYLE = -16
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


def _criar_regiao_arredondada(largura: int, altura: int, raio: int):
    """Cria uma região Win32 com cantos arredondados para a janela Tk."""
    gdi32 = ctypes.windll.gdi32
    funcao = gdi32.CreateRoundRectRgn
    funcao.argtypes = (ctypes.c_int,) * 6
    funcao.restype = ctypes.c_void_p
    return funcao(0, 0, int(largura) + 1, int(altura) + 1, int(raio), int(raio))


def _aplicar_regiao(hwnd: int, regiao) -> bool:
    """Aplica a região; a janela passa a clipar os cantos na hora."""
    user32 = ctypes.windll.user32
    funcao = user32.SetWindowRgn
    funcao.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    funcao.restype = ctypes.c_int
    return bool(funcao(ctypes.c_void_p(hwnd), ctypes.c_void_p(regiao), 1))


_WIN32 = None


def _win32_api():
    """Configura as assinaturas Win32 uma vez (evita truncar HWND em x64)."""
    global _WIN32
    if _WIN32 is not None:
        return _WIN32

    user32 = ctypes.windll.user32
    hwnd_t = ctypes.c_void_p
    long_t = ctypes.c_ssize_t

    for nome, argtypes, restype in (
        ("GetAncestor", (hwnd_t, ctypes.c_uint), hwnd_t),
        ("IsWindow", (hwnd_t,), ctypes.c_int),
        ("IsIconic", (hwnd_t,), ctypes.c_int),
        ("ShowWindow", (hwnd_t, ctypes.c_int), ctypes.c_int),
        ("GetForegroundWindow", (), hwnd_t),
        (
            "SetWindowPos",
            (hwnd_t, hwnd_t, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, ctypes.c_uint),
            ctypes.c_int,
        ),
    ):
        funcao = getattr(user32, nome, None)
        if funcao is not None:
            funcao.argtypes = argtypes
            funcao.restype = restype

    if hasattr(user32, "GetWindowLongPtrW"):
        get_long = user32.GetWindowLongPtrW
        set_long = user32.SetWindowLongPtrW
    else:
        get_long = user32.GetWindowLongW
        set_long = user32.SetWindowLongW
    get_long.argtypes = (hwnd_t, ctypes.c_int)
    get_long.restype = long_t
    set_long.argtypes = (hwnd_t, ctypes.c_int, long_t)
    set_long.restype = long_t

    _WIN32 = (user32, get_long, set_long)
    return _WIN32


class Aplicativo:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = carregar()
        self._widgets_tema: list[dict] = []
        self._cantos_tema: list[list] = []
        self._aplicar_paleta("escuro" if self.cfg.get("tema_escuro") else "claro")
        self.texto_atual = ""
        self.texto_original = ""
        self.idioma_detectado: str | None = None
        self.seletor: SeletorRegiao | None = None
        self.sequencia = 0
        self.status = tk.StringVar(value="Pronto. Use um atalho ou selecione uma região.")
        self._aviso_fala: str | None = None
        self.voz_var = tk.StringVar()
        self.velocidade_var = tk.StringVar(value=self._velocidade_para_texto())
        self.destino_var = tk.StringVar(value=self._destino_para_nome())
        self.hotkeys = None
        self.minimizado = False
        self.botao_restaurar = None
        self._id_monitor = None
        self.abaixo_app = False
        self.app_alvo_hwnd = None
        self.ocr_sequencia = 0
        self._painel_em_traducao = False
        # Destaque acompanhante da narração (só metadados; nunca mexe no áudio).
        self._destaque_limites: list[dict] = []
        self._destaque_posicoes: list[tuple[int, int, float, float]] = []
        self._destaque_indice = -1
        self._ultima_posicao_destaque = 0.0
        self._destaque_posicao_troca = 0.0
        self._fila_destaque: queue.Queue = queue.Queue()
        self._geometria_salva = None
        self._ids_taskbar: list[str] = []
        self._ids_cantos: list[str] = []
        self._regioes: dict[int, tuple[int, int, int]] = {}

        self._configurar_janela()
        self._construir_interface()
        self._id_destaque = self.root.after(30, self._drenar_destaque)
        self._instalar_hotkeys()

    # --------------------------------------------------------------- interface
    def _configurar_janela(self) -> None:
        self.root.title("Leitor em Voz Alta")
        self.root.overrideredirect(True)
        self.root.configure(bg=COR_FUNDO)
        self.root.geometry("1000x760+100+60")
        self.root.minsize(840, 620)
        self.root.attributes("-alpha", ALFA_JANELA)
        self._aplicar_topmost()
        # O -alpha cria uma janela layered; ajustar estilos depois é essencial
        # para o Tk não sobrescrever WS_EX_APPWINDOW com o cache dele.
        self._definir_icone()
        self._preparar_taskbar()
        self._agendar_reapply_taskbar()
        self._agendar_cantos()

    def _agendar_cantos(self) -> None:
        """O Tk só expõe o tamanho real depois de mapear a janela."""
        for identificador in getattr(self, "_ids_cantos", []):
            try:
                self.root.after_cancel(identificador)
            except (tk.TclError, ValueError):
                pass
        self._ids_cantos = []
        for atraso in (40, 200, 600):
            self._ids_cantos.append(
                self.root.after(
                    atraso,
                    lambda: self._arredondar_janela(self.root, RAIO_JANELA),
                )
            )

    def _agendar_reapply_taskbar(self) -> None:
        """O Tk reescreve os estilos da janela em vários momentos do map."""
        for identificador in getattr(self, "_ids_taskbar", []):
            try:
                self.root.after_cancel(identificador)
            except (tk.TclError, ValueError):
                pass
        self._ids_taskbar = []
        for atraso in (10, 60, 200, 600):
            self._ids_taskbar.append(
                self.root.after(atraso, self._preparar_taskbar)
            )

    def _definir_icone(self) -> None:
        """Ícone do app na janela e na barra de tarefas (arquivo .ico próprio)."""
        arquivo = Path(__file__).resolve().parent.parent / "icone.ico"
        try:
            if arquivo.exists():
                self.root.iconbitmap(default=str(arquivo))
        except Exception:
            pass
        try:
            imagem = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            desenho = ImageDraw.Draw(imagem)
            desenho.rounded_rectangle((2, 2, 62, 62), radius=14, fill=COR_AZUL)
            desenho.polygon(
                [(14, 27), (24, 27), (36, 17), (36, 47), (24, 37), (14, 37)],
                fill="white",
            )
            desenho.arc((39, 23, 55, 41), 300, 60, fill="white", width=4)
            self._icone_tk = ImageTk.PhotoImage(imagem, master=self.root)
            self.root.iconphoto(True, self._icone_tk)
        except Exception:
            pass

    @staticmethod
    def _rgb(cor: str) -> tuple[int, int, int]:
        return tuple(int(cor[i : i + 2], 16) for i in (1, 3, 5))

    @classmethod
    def _misturar(cls, a: str, b: str, fator: float) -> str:
        """Mistura duas cores hex; 0.16 clareia, 0.10 escurece."""
        return "#%02X%02X%02X" % tuple(
            round(x + (y - x) * fator) for x, y in zip(cls._rgb(a), cls._rgb(b))
        )

    def _aplicar_paleta(self, nome: str) -> None:
        """Troca as cores globais antes de criar/repintar os widgets."""
        global TEMA_ATUAL
        TEMA_ATUAL = nome
        for chave, valor in PALETAS[nome].items():
            globals()[chave] = valor
        PALETA_EXTRA.clear()
        PALETA_EXTRA.update(_EXTRAS_ESCO if nome == "escuro" else _EXTRAS_CLARO)
        ROTULO.update(bg=COR_SUPERFICIE, fg=COR_SECUNDARIA)
        self.tema_atual = nome

    def _registrar_tema(self) -> None:
        """Guarda as cores originais de cada widget para poder repintá-los."""
        self._widgets_tema: list[dict] = []
        self._cantos_tema: list[list] = []

        def visitar(widget) -> None:
            try:
                opcoes = widget.config()
            except (tk.TclError, AttributeError):
                opcoes = {}
            registro: dict = {"widget": widget, "opcoes": {}}
            # Tkinter devolve os aliases "bg"/"fg" como (alias, opcao), sem a
            # cor. Só os nomes canônicos trazem o valor no último elemento.
            for opcao in (
                "background", "foreground", "activebackground",
                "activeforeground", "highlightbackground", "troughcolor",
                "selectbackground", "selectcolor", "insertbackground",
            ):
                if opcao not in opcoes:
                    continue
                valor = opcoes[opcao][-1]
                if isinstance(valor, str) and valor.startswith("#"):
                    registro["opcoes"][opcao] = valor
            if hasattr(widget, "_cor_normal"):
                registro["botao"] = (widget._cor_normal, widget._cor_hover)
            if registro["opcoes"] or "botao" in registro:
                self._widgets_tema.append(registro)
            for etiqueta, _imagem, canto, raio in getattr(widget, "_cantos", []):
                self._cantos_tema.append([etiqueta, canto, raio, etiqueta.cget("bg")])
            for filho in widget.winfo_children():
                visitar(filho)

        visitar(self.root)

    def _regerar_cantos(self) -> None:
        """As tampas dos cantos são imagens; precisam ser recriadas."""
        for dados in self._cantos_tema:
            etiqueta, canto, raio, fundo = dados
            try:
                imagem = self._tampa_canto(etiqueta.master, canto, raio)
                etiqueta.config(image=imagem, bg=_cor_tema(fundo))
                etiqueta._imagem_tema = imagem
                dados[1] = canto
            except (tk.TclError, AttributeError):
                pass

    def _reaplicar_tema(self) -> None:
        for registro in getattr(self, "_widgets_tema", []):
            widget = registro["widget"]
            try:
                if registro["opcoes"]:
                    widget.config(
                        **{
                            opcao: _cor_tema(cor)
                            for opcao, cor in registro["opcoes"].items()
                        }
                    )
                if "botao" in registro:
                    normal, hover = registro["botao"]
                    widget._cor_normal = _cor_tema(normal)
                    widget._cor_hover = _cor_tema(hover)
                    widget.config(
                        bg=widget._cor_normal,
                        activebackground=widget._cor_hover,
                        highlightbackground=widget._cor_normal,
                    )
            except (tk.TclError, AttributeError):
                pass
        self._regerar_cantos()
        self._atualizar_tag_destaque()

    def alternar_tema(self) -> None:
        """Alterna entre tema claro e escuro e salva a preferência."""
        novo = "claro" if self.tema_atual == "escuro" else "escuro"
        self._aplicar_paleta(novo)
        self.cfg["tema_escuro"] = novo == "escuro"
        salvar(self.cfg)
        self._configurar_estilos(self.root)
        self._reaplicar_tema()
        self.root.config(bg=COR_FUNDO)
        sombra = getattr(self, "_sombra", None)
        if sombra is not None:
            sombra.config(bg=COR_SOMBRA)
        self.botao_tema.config(
            text="☀ Claro" if novo == "escuro" else "🌙 Escuro",
            bg=COR_SUPERFICIE_2, fg=COR_TEXTO, activebackground=_cor_tema("#E8EAF0"),
        )
        self._ao_mudar_status()
        self.status.set(
            "Modo escuro ativado." if novo == "escuro" else "Modo claro ativado."
        )

    @staticmethod
    def _janela_valida(hwnd: int) -> bool:
        try:
            return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(hwnd)))
        except Exception:
            return False

    def _colocar_abaixo_de(self, alvo: int) -> None:
        """Insere a janela abaixo do app de origem, sem roubar o foco."""
        try:
            user32 = ctypes.windll.user32
            hwnd = self._janela_root_hwnd()
            if not hwnd:
                return
            h = ctypes.c_void_p(hwnd)
            estilo = user32.GetWindowLongW(h, GWL_EXSTYLE)
            if estilo & WS_EX_TOPMOST:
                user32.SetWindowLongW(h, GWL_EXSTYLE, estilo & ~WS_EX_TOPMOST)
            flags = (
                SWP_NOMOVE
                | SWP_NOSIZE
                | SWP_NOACTIVATE
                | SWP_NOOWNERZORDER
                | SWP_NOSENDCHANGING
            )
            # Primeiro logo atrás do app de origem; depois no fim da ordem Z
            # para o shell/Tk não conseguirem trazê-la de volta para a frente.
            user32.SetWindowPos(h, ctypes.c_void_p(alvo), 0, 0, 0, 0, flags)
            user32.SetWindowPos(h, ctypes.c_void_p(HWND_BOTTOM), 0, 0, 0, 0, flags)
        except Exception:
            pass

    def _mostrar_janela_apos_leitura(self) -> None:
        """Reapresenta a janela, respeitando o app que estava ativo."""
        self._ocultar_sombra()
        self.root.attributes("-topmost", False)
        self.root.deiconify()
        self.root.update_idletasks()
        self._arredondar_janela(self.root, RAIO_JANELA)
        alvo = self.app_alvo_hwnd if self.abaixo_app else None
        if alvo and self._janela_valida(alvo):
            self._colocar_abaixo_de(alvo)
            self.root.after(80, lambda: self._colocar_abaixo_de(alvo))
        else:
            self._aplicar_topmost()
            self._posicionar_sombra()
        self.abaixo_app = False

    @staticmethod
    def _foreground_hwnd() -> int | None:
        try:
            user32, _, _ = _win32_api()
            return int(user32.GetForegroundWindow()) or None
        except Exception:
            return None

    def _hwnd_de(self, janela) -> int | None:
        """HWND do wrapper nativo (winfo_id() devolve o filho do Tk)."""
        try:
            janela.update_idletasks()
            user32, _, _ = _win32_api()
            bruto = int(janela.winfo_id())
            if not bruto:
                return None
            return int(user32.GetAncestor(ctypes.c_void_p(bruto), GA_ROOT) or bruto)
        except Exception:
            return None

    def _janela_root_hwnd(self) -> int | None:
        return self._hwnd_de(self.root)

    def _arredondar_janela(self, janela, raio: int) -> None:
        """Aplica cantos arredondados com SetWindowRgn (janela e sombra)."""
        try:
            hwnd = self._hwnd_de(janela)
            if not hwnd:
                return
            janela.update_idletasks()
            largura, altura = janela.winfo_width(), janela.winfo_height()
            if largura < 10 or altura < 10:
                return
            if self._regioes.get(hwnd) == (largura, altura, raio):
                return
            regiao = _criar_regiao_arredondada(largura, altura, raio)
            if not regiao:
                return
            if _aplicar_regiao(hwnd, regiao):
                self._regioes[hwnd] = (largura, altura, raio)
        except Exception:
            pass

    def _preparar_taskbar(self) -> bool:
        """(Re)aplica os estilos que colocam a janela na barra de tarefas."""
        try:
            user32, get_long, set_long = _win32_api()
            hwnd = self._janela_root_hwnd()
            if not hwnd:
                return False
            h = ctypes.c_void_p(hwnd)
            if not user32.IsWindow(h):
                return False

            mudou = False

            # O shell só cria o botão na barra com APPWINDOW e sem TOOLWINDOW.
            ex = int(get_long(h, GWL_EXSTYLE))
            novo_ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            if novo_ex != ex:
                set_long(h, GWL_EXSTYLE, novo_ex)
                mudou = True

            # Sem MINIMIZEBOX o Windows não trata a janela como minimizável.
            estilo = int(get_long(h, GWL_STYLE))
            novo_estilo = estilo | WS_SYSMENU | WS_MINIMIZEBOX
            if novo_estilo != estilo:
                set_long(h, GWL_STYLE, novo_estilo)
                mudou = True

            if mudou:
                # Sem hide/show: o ciclo hide/show faz o Windows reexibir a
                # janela depois do SW_MINIMIZE.
                user32.SetWindowPos(
                    h, None, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                    | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                )
            return True
        except Exception:
            return False

    def _esta_iconico(self) -> bool:
        try:
            user32, _, _ = _win32_api()
            hwnd = self._janela_root_hwnd()
            if not hwnd:
                return False
            h = ctypes.c_void_p(hwnd)
            return bool(user32.IsWindow(h) and user32.IsIconic(h))
        except Exception:
            return False

    def _minimizar_win32(self) -> bool:
        """Minimiza de verdade, inclusive se o OCR deixou a janela oculta."""
        try:
            user32, _, _ = _win32_api()
            hwnd = self._janela_root_hwnd()
            if not hwnd:
                return False
            h = ctypes.c_void_p(hwnd)
            if not user32.IsWindow(h) or not self._preparar_taskbar():
                return False

            # Não usar winfo_viewable(): withdraw() o deixa 0, mas
            # ShowWindow(SW_MINIMIZE) continua funcionando nesse estado.
            user32.ShowWindow(h, SW_MINIMIZE)

            for _ in range(5):  # IsIconic pode levar alguns ms
                if user32.IsIconic(h):
                    break
                self.root.update_idletasks()
                time.sleep(0.04)

            if not user32.IsIconic(h):
                return False

            self._preparar_taskbar()  # Tk/shell podem reescrever o cache
            return True
        except Exception:
            return False

    def _restaurar_win32(self) -> bool:
        try:
            user32, _, _ = _win32_api()
            hwnd = self._janela_root_hwnd()
            if not hwnd:
                return False
            h = ctypes.c_void_p(hwnd)
            if not user32.IsWindow(h):
                return False

            if user32.IsIconic(h):
                user32.ShowWindow(h, SW_RESTORE)
            else:
                # Pode estar withdraw pelo OCR ou pelo fallback.
                self.root.deiconify()
                user32.ShowWindow(h, SW_SHOW)

            for _ in range(5):
                if not user32.IsIconic(h):
                    break
                time.sleep(0.04)

            self._preparar_taskbar()
            return not bool(user32.IsIconic(h))
        except Exception:
            return False

    def _iniciar_monitor_estado(self) -> None:
        self._parar_monitor_estado()
        self._id_monitor = self.root.after(200, self._monitorar_estado)

    def _parar_monitor_estado(self) -> None:
        ident = getattr(self, "_id_monitor", None)
        if ident is not None:
            try:
                self.root.after_cancel(ident)
            except (tk.TclError, ValueError):
                pass
        self._id_monitor = None

    def _monitorar_estado(self) -> None:
        self._id_monitor = None
        if not self.minimizado:
            return
        if self._esta_iconico():
            self._iniciar_monitor_estado()
            return
        # Restauro externo: clique na barra, Alt+Tab, Win+D etc.
        self.minimizado = False
        self.botao_restaurar = None
        self.abaixo_app = False
        self._posicionar_sombra()
        self._aplicar_topmost()
        self._preparar_taskbar()
        self.status.set("Janela restaurada pela barra de tarefas.")

    def _aplicar_topmost(self) -> None:
        """Aplica o z-order desejado: topo, normal ou abaixo do app ativo."""
        topmost = bool(self.cfg.get("sempre_topo", False)) and not self.abaixo_app
        self.root.attributes("-topmost", topmost)
        if topmost:
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = int(self.root.winfo_id())
            nativa = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
            estilo = user32.GetWindowLongW(nativa, GWL_EXSTYLE)
            if estilo & WS_EX_TOPMOST:
                user32.SetWindowLongW(nativa, GWL_EXSTYLE, estilo & ~WS_EX_TOPMOST)
            if self.abaixo_app:
                user32.SetWindowPos(
                    nativa,
                    HWND_BOTTOM,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
                )
        except Exception:
            pass
        # attributes("-topmost") pode fazer o Tk recriar o cache de exstyle.
        self._preparar_taskbar()

    @staticmethod
    def _configurar_estilos(root: tk.Tk) -> None:
        estilo = ttk.Style(root)
        try:
            estilo.theme_use("clam")  # "vista" tem aparência de Windows 7
            estilo.configure(
                ".", font=FONTE, background=COR_FUNDO, foreground=COR_TEXTO
            )
            estilo.configure(
                "TCombobox",
                font=FONTE,
                foreground=COR_TEXTO,
                fieldbackground=COR_SUPERFICIE,
                background=COR_SUPERFICIE,
                arrowcolor=COR_SECUNDARIA,
                borderwidth=1,
                padding=4,
                selectbackground=COR_AZUL,
                selectforeground="#FFFFFF",
            )
            estilo.map(
                "TCombobox",
                bordercolor=[("focus", COR_AZUL), ("!focus", COR_BORDA)],
                fieldbackground=[("readonly", COR_SUPERFICIE)],
            )
            estilo.configure(
                "TNotebook",
                background=COR_FUNDO,
                borderwidth=0,
                tabmargins=(6, 4, 6, 0),
            )
            estilo.configure(
                "TNotebook.Tab",
                background=COR_SUPERFICIE_2,
                foreground=COR_TEXTO,
                padding=(12, 5),
                borderwidth=1,
            )
            estilo.map(
                "TNotebook.Tab",
                background=[
                    ("selected", COR_AZUL),
                    ("active", _cor_tema("#DDE3EE")),
                ],
                foreground=[("selected", "#FFFFFF")],
            )
            estilo.configure(
                "Vertical.TScrollbar",
                background=_cor_tema("#C8CDD8"),
                troughcolor=COR_SUPERFICIE_2,
                borderwidth=0,
                arrowsize=11,
                width=10,
            )
            estilo.map(
                "Vertical.TScrollbar",
                background=[("active", _cor_tema("#A6ADBC")), ("pressed", COR_AZUL)],
            )
        except tk.TclError:
            pass

    @staticmethod
    def _tampa_canto(master, quadrante: str, raio: int = RAIO):
        imagem = Image.new("RGBA", (raio, raio), (0, 0, 0, 0))
        ox, oy = {
            "no": (0, 0),
            "ne": (-raio, 0),
            "so": (0, -raio),
            "se": (-raio, -raio),
        }[quadrante]
        ImageDraw.Draw(imagem).ellipse(
            (ox, oy, ox + 2 * raio, oy + 2 * raio), fill=COR_SUPERFICIE
        )
        return ImageTk.PhotoImage(imagem, master=master)

    @classmethod
    def _arredondar(cls, moldura: tk.Frame, raio: int = RAIO) -> None:
        referencias = []
        posicoes = (
            ("no", dict(x=0, y=0, anchor="nw")),
            ("ne", dict(relx=1.0, y=0, anchor="ne")),
            ("so", dict(x=0, rely=1.0, anchor="sw")),
            ("se", dict(relx=1.0, rely=1.0, anchor="se")),
        )
        for canto, kw in posicoes:
            imagem = cls._tampa_canto(moldura, canto, raio)
            etiqueta = tk.Label(
                moldura, image=imagem, bg=COR_FUNDO, bd=0,
                highlightthickness=0, takefocus=0,
            )
            etiqueta.place(**kw)
            referencias.append((etiqueta, imagem, canto, raio))
        moldura._cantos = referencias  # evita coleta das PhotoImage

    @classmethod
    def _cartao(cls, parent, titulo: str | None = None,
                legenda: str | None = None):
        externo = tk.Frame(parent, bg=COR_FUNDO)
        faixa = tk.Frame(externo, bg=_cor_tema("#ECEEF4"), height=3)
        faixa.pack(fill="x", padx=6)
        moldura = tk.Frame(
            externo, bg=COR_SUPERFICIE,
            highlightbackground=COR_BORDA, highlightthickness=0,
        )
        moldura.pack(fill="both", expand=True)
        if titulo:
            cabecalho = tk.Frame(
                moldura, bg=COR_SUPERFICIE, padx=PAD, pady=10
            )
            cabecalho.pack(fill="x")
            tk.Frame(cabecalho, bg=COR_AZUL, width=3, height=14).pack(
                side="left", padx=(0, 7)
            )
            tk.Label(
                cabecalho, text=titulo, bg=COR_SUPERFICIE, fg=COR_TEXTO,
                font=FONTE_BOLD, anchor="w",
            ).pack(side="left")
            if legenda:
                tk.Label(
                    cabecalho, text=legenda, bg=COR_SUPERFICIE,
                    fg=COR_SECUNDARIA, font=FONTE_LEGENDA, anchor="w",
                ).pack(side="left", padx=8)
        corpo = tk.Frame(moldura, bg=COR_SUPERFICIE, padx=PAD, pady=10)
        corpo.pack(fill="both", expand=True)
        cls._arredondar(moldura)
        externo.corpo = corpo
        return externo

    @classmethod
    def _hover(cls, botao: tk.Button) -> None:
        botao.bind("<Enter>", lambda event: botao.config(bg=botao._cor_hover))
        botao.bind("<Leave>", lambda event: botao.config(bg=botao._cor_normal))
        botao.bind(
            "<ButtonPress-1>",
            lambda event: botao.config(bg=cls._misturar(botao._cor_normal, "#000000", 0.10)),
        )
        botao.bind(
            "<ButtonRelease-1>",
            lambda event: botao.config(bg=botao._cor_hover),
        )

    @classmethod
    def _botao(cls, parent, texto: str, comando, cor: str, largura: int, *,
               variante: str = "solido") -> tk.Button:
        if variante == "contorno":
            # No escuro o texto precisa ser branco; no claro mantém o cinza
            # escuro. A cor é canônica da paleta, então acompanha o tema.
            bg, fg = COR_SUPERFICIE, COR_BOTAO_CONTORNO_TEXTO
            hover = cls._misturar(cor, "#FFFFFF", 0.90)
            borda = dict(highlightbackground=COR_BORDA, highlightthickness=1)
        else:
            bg, fg, hover = cor, "#FFFFFF", cls._misturar(cor, "#FFFFFF", 0.16)
            borda = dict(highlightbackground=bg, highlightthickness=0)
        botao = tk.Button(
            parent, text=texto, command=comando, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            relief="flat", bd=0, width=largura, padx=10, pady=4,
            font=FONTE_BOLD, cursor="hand2", **borda,
        )
        botao._cor_normal, botao._cor_hover = bg, hover
        cls._hover(botao)
        return botao

    @classmethod
    def _configurar_principal(cls, botao, texto: str, comando, cor: str) -> None:
        """Repinta o botão principal sem perder o hover ao trocar de papel."""
        botao._cor_normal = cor
        botao._cor_hover = cls._misturar(cor, "#FFFFFF", 0.16)
        botao.config(
            text=texto, command=comando, bg=cor, fg="#FFFFFF",
            activebackground=botao._cor_hover,
            highlightbackground=cor,
        )

    def _criar_sombra(self) -> None:
        self._sombra = tk.Toplevel(self.root)
        self._sombra.overrideredirect(True)
        self._sombra.attributes("-alpha", 0.18)
        self._sombra.attributes("-topmost", False)
        self._sombra.configure(bg=COR_SOMBRA)
        self._sombra.withdraw()

    def _posicionar_sombra(self, x: int | None = None,
                           y: int | None = None) -> None:
        sombra = getattr(self, "_sombra", None)
        if sombra is None or not self.root.winfo_viewable():
            return
        x = self.root.winfo_x() if x is None else x
        y = self.root.winfo_y() if y is None else y
        sombra.geometry(
            f"{self.root.winfo_width()}x{self.root.winfo_height()}+{x + 7}+{y + 9}"
        )
        if not sombra.winfo_viewable():
            sombra.deiconify()
            sombra.lower()
        self._arredondar_janela(sombra, RAIO_SOMBRA)

    def _ocultar_sombra(self) -> None:
        sombra = getattr(self, "_sombra", None)
        if sombra is not None:
            try:
                sombra.withdraw()
            except tk.TclError:
                pass

    def _ao_configurar_root(self, event) -> None:
        if (
            getattr(event, "widget", self.root) is self.root
            and not getattr(self, "_arraste", None)
        ):
            self._arredondar_janela(self.root, RAIO_JANELA)
            self._posicionar_sombra()

    def _ao_mudar_status(self, *_evento) -> None:
        mensagem = self.status.get().lower()
        if any(palavra in mensagem for palavra in ("falha", "erro", "indispon")):
            cor, fundo, rotulo = COR_VERMELHO, COR_VERMELHO_CLARO, "Atenção"
        elif any(
            palavra in mensagem
            for palavra in ("conclu", "copiado", "salvo", "restaurada")
        ):
            cor, fundo, rotulo = COR_VERDE, COR_VERDE_CLARO, "Pronto"
        else:
            cor, fundo, rotulo = COR_AZUL, COR_AZUL_CLARO, "Pronto"
        try:
            self.status_ponto.config(fg=cor)
            self.chip_modo.config(text=f"● {rotulo}", bg=fundo, fg=cor)
        except tk.TclError:
            pass

    def _construir_interface(self) -> None:
        self._configurar_estilos(self.root)
        self._criar_sombra()

        # ---------------------------------------------------------- barra
        barra = tk.Frame(
            self.root, bg=COR_SUPERFICIE, height=48,
            highlightbackground=COR_BORDA, highlightthickness=1,
        )
        barra.grid(row=0, column=0, sticky="ew")
        barra.grid_propagate(False)
        barra.columnconfigure(2, weight=1)

        icone = tk.Frame(barra, bg=COR_AZUL, width=28, height=28)
        icone.grid(row=0, column=0, padx=(12, 8), pady=9)
        icone.grid_propagate(False)
        tk.Label(
            icone, text="◉", bg=COR_AZUL, fg="white",
            font=("Segoe UI", 13, "bold"),
        ).place(relx=0.5, rely=0.5, anchor="center")

        textos = tk.Frame(barra, bg=COR_SUPERFICIE)
        textos.grid(row=0, column=1, sticky="w")
        tk.Label(
            textos, text="Leitor em Voz Alta", bg=COR_SUPERFICIE,
            fg=COR_TEXTO, font=FONTE_TITULO,
        ).pack(anchor="w")
        tk.Label(
            textos, text="Captura de tela • OCR • leitura em voz alta",
            bg=COR_SUPERFICIE, fg=COR_SECUNDARIA, font=FONTE_LEGENDA,
        ).pack(anchor="w")

        self.chip_modo = tk.Label(
            barra, text="● Pronto", bg=COR_VERDE_CLARO, fg=COR_VERDE,
            font=FONTE_LEGENDA, padx=10, pady=3,
        )
        self.chip_modo.grid(row=0, column=2, sticky="e", padx=8)

        self.botao_configuracoes = tk.Button(
            barra,
            text="⚙ Configurações", command=self.abrir_configuracoes,
            bg=COR_SUPERFICIE_2, fg=COR_TEXTO,
            activebackground=_cor_tema("#E8EAF0"), relief="flat", bd=0,
            width=15, font=FONTE_LEGENDA, cursor="hand2",
        )
        self.botao_configuracoes.grid(row=0, column=3, padx=(0, 6))

        self.botao_tema = tk.Button(
            barra,
            text="☀ Claro" if self.tema_atual == "escuro" else "🌙 Escuro",
            command=self.alternar_tema, bg=COR_SUPERFICIE_2, fg=COR_TEXTO,
            activebackground=_cor_tema("#E8EAF0"), relief="flat", bd=0,
            width=8, font=FONTE_LEGENDA, cursor="hand2",
        )
        self.botao_tema.grid(row=0, column=4, padx=(0, 6))

        botao_min = tk.Button(
            barra, text="─", command=self.minimizar, bg=COR_SUPERFICIE,
            fg=COR_TEXTO, activebackground=_cor_tema("#E8EAF0"), relief="flat", bd=0,
            width=3, font=("Segoe UI", 12), cursor="hand2",
        )
        botao_min.grid(row=0, column=5, padx=(0, 2))
        botao_fechar = tk.Button(
            barra, text="✕", command=self._fechar, bg=COR_SUPERFICIE,
            fg=COR_TEXTO, activebackground=COR_VERMELHO,
            activeforeground="white", relief="flat", bd=0, width=3,
            font=("Segoe UI", 10), cursor="hand2",
        )
        botao_fechar.grid(row=0, column=6, padx=(0, 10))
        botao_fechar.bind(
            "<Enter>",
            lambda event: botao_fechar.config(bg=COR_VERMELHO, fg="white"),
        )
        botao_fechar.bind(
            "<Leave>",
            lambda event: botao_fechar.config(bg=COR_SUPERFICIE, fg=COR_TEXTO),
        )

        for widget in (barra, icone, textos, self.chip_modo, self.botao_tema):
            widget.bind("<ButtonPress-1>", self._iniciar_arraste)
            widget.bind("<B1-Motion>", self._arrastar)
            widget.bind("<ButtonRelease-1>", self._finalizar_arraste)

        # ---------------------------------------------------------- corpo
        corpo = tk.Frame(self.root, bg=COR_FUNDO, padx=12, pady=10)
        corpo.grid(row=1, column=0, sticky="nsew")
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        corpo.columnconfigure(0, weight=1)
        corpo.columnconfigure(1, weight=1)
        corpo.rowconfigure(2, weight=1)

        cartao_acoes = self._cartao(corpo, "Ações rápidas", "Atalhos configuráveis em ⚙")
        cartao_acoes.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        acoes = cartao_acoes.corpo
        acoes.columnconfigure(6, weight=1)

        self.botao_selecionar = self._botao(
            acoes, "⛶  Selecionar região", self.iniciar_selecao,
            COR_AZUL, 22, variante="contorno",
        )
        self.botao_selecionar.grid(row=0, column=0, padx=(0, 6))
        self.botao_principal = self._botao(
            acoes, "▶  Ler região", self.ler_regiao, COR_VERDE, 17
        )
        self.botao_principal.grid(row=0, column=1, padx=(0, 6))
        self.botao_texto = self._botao(
            acoes, "▶  Ler texto", self.ler_texto, COR_VERDE, 15,
            variante="contorno",
        )
        self.botao_texto.grid(row=0, column=2, padx=(0, 6))
        self.botao_parar = self._botao(
            acoes, "■  Parar", self.parar, COR_VERMELHO, 12
        )
        self.botao_parar.grid(row=0, column=3, padx=(0, 6))
        self.botao_colar = self._botao(
            acoes, "📋 Colar", self.colar, COR_SECUNDARIA, 9,
            variante="contorno",
        )
        self.botao_colar.grid(row=0, column=4, padx=(0, 6))
        self._botao(
            acoes, "Copiar", self.copiar, COR_SECUNDARIA, 10,
            variante="contorno",
        ).grid(row=0, column=5, padx=(0, 6))
        self._botao(
            acoes, "Limpar", self.limpar_texto, COR_SECUNDARIA, 8,
            variante="contorno",
        ).grid(row=0, column=6, padx=(0, 6), sticky="e")

        # ---------------------------------------------------------- voz
        cartao_voz = self._cartao(corpo, "Voz e leitura", "SAPI offline ou neural online")
        cartao_voz.grid(row=1, column=0, sticky="nsew", padx=(0, 5), pady=(0, 10))
        voz = cartao_voz.corpo
        voz.columnconfigure(1, weight=1)

        tk.Label(voz, text="Voz", **ROTULO).grid(row=0, column=0, sticky="w")
        ids_neural = fala.listar_vozes_neural()
        alvo = self.cfg.get("voz_neural", VOZ_NEURAL_PADRAO)
        if alvo not in ids_neural:
            ids_neural.insert(0, alvo)
        self._rotulos_neural = {
            f"🌟 {fala.nome_amigavel_neural(id_voz)}": id_voz
            for id_voz in ids_neural
        }
        rotulo_atual = f"🌟 {fala.nome_amigavel_neural(alvo)}"
        vozes = fala.listar_vozes_sapi() + list(self._rotulos_neural)
        if self.cfg.get("modo_neural"):
            self.voz_var.set(
                rotulo_atual if rotulo_atual in self._rotulos_neural
                else next(iter(self._rotulos_neural))
            )
        elif self.cfg.get("voz_sapi") not in vozes:
            self.voz_var.set(vozes[0] if vozes else next(iter(self._rotulos_neural)))
        else:
            self.voz_var.set(self.cfg["voz_sapi"])
        self.combo_vozes = ttk.Combobox(
            voz, textvariable=self.voz_var, values=vozes,
            state="readonly", width=32,
        )
        self.combo_vozes.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        self.combo_vozes.bind("<<ComboboxSelected>>", self._ao_mudar_voz)

        tk.Label(voz, text="Velocidade", **ROTULO).grid(row=1, column=0, sticky="w")
        self.combo_velocidade = ttk.Combobox(
            voz, textvariable=self.velocidade_var, values=list(VELOCIDADES),
            state="readonly", width=10,
        )
        self.combo_velocidade.grid(row=1, column=1, sticky="w", pady=(0, 4))
        self.combo_velocidade.bind(
            "<<ComboboxSelected>>", self._ao_mudar_velocidade
        )

        tk.Label(voz, text="Volume", **ROTULO).grid(row=2, column=0, sticky="w")
        self.volume = tk.Scale(
            voz, from_=0, to=100, orient="horizontal", length=190,
            showvalue=True, bg=COR_SUPERFICIE, troughcolor=_cor_tema("#ECEEF4"),
            activebackground=COR_AZUL, sliderrelief="flat",
            highlightthickness=0, font=FONTE_LEGENDA,
            command=self._ao_mudar_volume,
        )
        self.volume.set(int(self.cfg.get("volume", 90)))
        self.volume.grid(row=2, column=1, sticky="w")

        # ---------------------------------------------------------- tradução
        cartao_trad = self._cartao(corpo, "Tradução", "detecção automática")
        cartao_trad.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=(0, 10))
        trad = cartao_trad.corpo
        trad.columnconfigure(1, weight=1)

        self._botao(trad, "🌐  Traduzir", self.traduzir, COR_AZUL, 14).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self._botao(
            trad, "↩  Original", self.mostrar_original,
            COR_SECUNDARIA, 13, variante="contorno",
        ).grid(row=0, column=1, sticky="w")
        tk.Label(trad, text="Para", **ROTULO).grid(row=1, column=0, sticky="w")
        self.combo_destino = ttk.Combobox(
            trad, textvariable=self.destino_var,
            values=list(traducao.IDIOMAS), state="readonly", width=22,
        )
        self.combo_destino.grid(row=1, column=1, sticky="w", pady=(0, 4))
        self.combo_destino.bind("<<ComboboxSelected>>", self._ao_mudar_destino)

        chip_idioma = tk.Frame(trad, bg=COR_AZUL_CLARO)
        chip_idioma.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tk.Label(
            chip_idioma, text="🌐", bg=COR_AZUL_CLARO, fg=COR_AZUL,
            font=FONTE,
        ).pack(side="left", padx=(8, 4), pady=3)
        self.idioma_label = tk.Label(
            chip_idioma, text="Idioma detectado: —", bg=COR_AZUL_CLARO,
            fg=COR_AZUL, font=FONTE_LEGENDA,
        )
        self.idioma_label.pack(side="left", padx=(0, 8), pady=3)

        # ---------------------------------------------------------- texto
        cartao_texto = self._cartao(corpo, "Texto", "editável")
        cartao_texto.grid(row=2, column=0, columnspan=2, sticky="nsew")
        area = cartao_texto.corpo
        area.columnconfigure(0, weight=1)
        area.rowconfigure(1, weight=1)

        self.contador = tk.Label(
            area, text="0 caracteres", bg=COR_SUPERFICIE,
            fg=COR_SECUNDARIA, font=FONTE_LEGENDA,
        )
        self.contador.grid(row=0, column=0, sticky="e", pady=(0, 4))

        self.texto = tk.Text(
            area, height=14, wrap="word", bg=COR_SUPERFICIE, fg=COR_TEXTO,
            relief="flat", bd=0, font=FONTE_CODIGO, padx=10, pady=8,
            highlightbackground=COR_BORDA, highlightcolor=COR_AZUL,
            highlightthickness=1, insertbackground=COR_AZUL,
            selectbackground=_cor_tema("#CCE4FB"),
        )
        # Tag separada da seleção do usuário: destaca a palavra falada sem
        # interferir em texto selecionado ou na edição manual do painel.
        self._atualizar_tag_destaque()
        barra_rolagem = ttk.Scrollbar(area, orient="vertical", command=self.texto.yview)
        self.texto.configure(yscrollcommand=barra_rolagem.set)
        self.texto.grid(row=1, column=0, sticky="nsew")
        barra_rolagem.grid(row=1, column=1, sticky="ns")

        # ---------------------------------------------------------- rodapé
        rodape = tk.Frame(
            self.root, bg=COR_SUPERFICIE_2,
            highlightbackground=COR_BORDA, highlightthickness=1,
        )
        rodape.grid(row=2, column=0, sticky="ew")
        self.status_ponto = tk.Label(
            rodape, text="●", bg=COR_SUPERFICIE_2, fg=COR_AZUL,
            font=("Segoe UI", 10),
        )
        self.status_ponto.pack(side="left", padx=(14, 6), pady=7)
        tk.Label(
            rodape, textvariable=self.status, bg=COR_SUPERFICIE_2,
            fg=COR_SECUNDARIA, font=FONTE, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            rodape, text="v1.0", bg=COR_SUPERFICIE_2, fg=_cor_tema("#9AA0AE"),
            font=FONTE_LEGENDA,
        ).pack(side="right", padx=14)

        self.status.trace_add("write", self._ao_mudar_status)
        self.root.bind("<Configure>", self._ao_configurar_root)
        self._registrar_tema()

    def _iniciar_arraste(self, event) -> None:
        # Guarda a origem da JANELA no clique; reler winfo_x() a cada motion
        # soma o delta acumulado e faz a janela disparar para fora da tela.
        self._arraste = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
        )

    def _arrastar(self, event) -> None:
        arraste = getattr(self, "_arraste", None)
        if not arraste:
            return
        mx, my, wx, wy = arraste

        largura = self.root.winfo_width()
        altura = self.root.winfo_height()
        max_x = max(0, self.root.winfo_screenwidth() - largura)
        max_y = max(0, self.root.winfo_screenheight() - altura)

        x = min(max_x, max(0, wx + event.x_root - mx))
        y = min(max_y, max(0, wy + event.y_root - my))
        self.root.geometry(f"+{x}+{y}")
        self._posicionar_sombra(x, y)

    def _finalizar_arraste(self, event=None) -> None:
        self._arraste = None

    # ---------------------------------------------------------------- atalhos
    def _parar_hotkeys(self) -> None:
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception:
                pass
            self.hotkeys = None

    def _reinstalar_hotkeys(self) -> None:
        """Chamada sempre na thread Tk (stop() dentro do callback pode travar)."""
        self._parar_hotkeys()
        self._instalar_hotkeys()

    def _instalar_hotkeys(self) -> None:
        """Instala (ou reinstala) os atalhos configurados em config.json."""
        self._parar_hotkeys()
        try:
            from pynput import keyboard

            acoes = {
                "selecionar": self.iniciar_selecao,
                "ler": self.ler_regiao,
                "parar": self.parar,
                "copiar": self.copiar,
                "traduzir": self.traduzir,
                "restaurar": self.restaurar,
            }
            salvos = self.cfg.setdefault("atalhos", {})
            mapa = {}
            for chave, funcao in acoes.items():
                combinacao = normalizar(
                    salvos.get(chave), COMBOS_PADRAO.get(chave, COMBOS_PADRAO["ler"])
                )
                salvos[chave] = combinacao
                if combinacao:
                    mapa[combinacao] = lambda f=funcao: self._agendar(
                        self._proteger(f)
                    )
            self.hotkeys = keyboard.GlobalHotKeys(mapa)
            self.hotkeys.start()
        except Exception as exc:
            self.hotkeys = None
            self._agendar(self.status.set, f"Atalhos indisponíveis: {exc}")

    def _agendar(self, funcao, *argumentos) -> None:
        try:
            self.root.after(0, lambda: funcao(*argumentos))
        except (tk.TclError, RuntimeError):
            # A janela pode ter sido fechada enquanto OCR/TTS ainda rodavam.
            pass

    @staticmethod
    def _aceita_destaque(funcao) -> bool:
        """Detecta a assinatura real, inclusive quando falar está mockado."""
        alvo = getattr(funcao, "side_effect", None)
        if not callable(alvo):
            alvo = funcao
        try:
            parametros = inspect.signature(alvo).parameters
        except (TypeError, ValueError):
            return False
        if "destaque" in parametros:
            return True
        return any(
            item.kind == inspect.Parameter.VAR_KEYWORD
            for item in parametros.values()
        )

    # -------------------------------------------------- destaque da leitura
    def _atualizar_tag_destaque(self) -> None:
        """Aplica cor/opacidade configuradas ao destaque do painel."""
        texto = getattr(self, "texto", None)
        if texto is None:
            return
        cor = self.cfg.get("cor_destaque", COR_DESTAQUE_PADRAO)
        opacidade = int(
            self.cfg.get("opacidade_destaque", OPACIDADE_DESTAQUE_PADRAO)
        ) / 100
        fundo = _misturar_cor(cor, COR_SUPERFICIE, opacidade)
        try:
            texto.tag_config(
                "leitura_atual",
                background=fundo,
                foreground=_cor_legivel(fundo),
            )
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def _normalizar_palavra(valor: str) -> str:
        """Compara palavras ignorando acento, caixa e pontuação."""
        sem_acento = unicodedata.normalize("NFKD", valor)
        sem_acento = "".join(
            letra for letra in sem_acento if not unicodedata.combining(letra)
        )
        return re.sub(r"[^a-z0-9]", "", sem_acento.lower())

    @classmethod
    def _palavras_indexadas(cls, texto: str) -> list[tuple[str, int, int]]:
        return [
            (cls._normalizar_palavra(item.group()), item.start(), item.end())
            for item in re.finditer(r"\w+(?:['’]\w+)?", texto, re.UNICODE)
            if cls._normalizar_palavra(item.group())
        ]

    def _mapear_destaque(self, limites: list[dict]) -> None:
        """Converte os marcadores do edge-tts em posições do painel."""
        palavras = self._palavras_indexadas(self.texto.get("1.0", "end-1c"))
        mapeados: list[tuple[int, int, float, float]] = []
        cursor = 0
        for limite in limites:
            alvo = [
                self._normalizar_palavra(item.group())
                for item in re.finditer(
                    r"\w+(?:['’]\w+)?", str(limite.get("texto", "")), re.UNICODE
                )
            ]
            alvo = [item for item in alvo if item]
            if not alvo:
                continue
            encontrado: int | None = None
            # A marcação só vale se casar com a próxima palavra do painel.
            # Procurar mais à frente (ou no texto inteiro) fazia a marca aparecer
            # em outra ocorrência da mesma palavra e deslocar todas as seguintes.
            limite_busca = min(len(palavras), cursor + 3)
            for inicio in range(cursor, limite_busca + 1):
                if inicio + len(alvo) > len(palavras):
                    break
                if all(
                    palavras[inicio + desloc][0] == alvo[desloc]
                    for desloc in range(len(alvo))
                ):
                    encontrado = inicio
                    break
            if encontrado is None:
                # Ex.: "&" virou "e" no TTS; pula essa marca sem mexer no cursor
                # para não desalinhar as palavras que vêm depois.
                continue
            ultimo = min(encontrado + len(alvo), len(palavras)) - 1
            mapeados.append(
                (
                    palavras[encontrado][1],
                    palavras[ultimo][2],
                    float(limite.get("offset", 0)),
                    float(limite.get("duracao", 0)),
                )
            )
            cursor = ultimo + 1
        anterior = self._destaque_posicoes
        indice_anterior = self._destaque_indice
        # Os marcadores são publicados várias vezes: primeiro estimados, depois
        # substituídos pelas frases reais. Se as palavras continuam as mesmas,
        # mantemos a palavra já destacada em vez de voltar para a primeira.
        mesmo_prefixo = (
            bool(anterior)
            and len(anterior) <= len(mapeados)
            and all(
                novo[:2] == antigo[:2]
                for novo, antigo in zip(mapeados[:len(anterior)], anterior)
            )
        )
        self._destaque_posicoes = mapeados
        if mesmo_prefixo and 0 <= indice_anterior < len(mapeados):
            self._destaque_indice = indice_anterior
        else:
            self._destaque_indice = -1
        self._destaque_posicao_troca = getattr(
            self, "_ultima_posicao_destaque", 0.0
        )

    def _destacar_posicao(self, segundos: float, reajustar: bool = False) -> None:
        """Mantém a palavra ativa até ela terminar de ser falada."""
        if not self._destaque_posicoes:
            return
        indice = -1
        for posicao, (_, _, inicio, _duracao) in enumerate(self._destaque_posicoes):
            if segundos + 0.03 < inicio:
                break
            indice = posicao
        if indice < 0:
            return
        atual = self._destaque_indice
        if (
            not reajustar
            and 0 <= atual < len(self._destaque_posicoes)
            and indice > atual
        ):
            # A próxima palavra pode começar antes de a atual terminar (ou o
            # áudio ter uma pausa depois dela). Trocar no início da seguinte
            # fazia palavras longas, como "paralelepipedo", serem puladas cedo.
            # A espera é limitada: um marcador com duração anormalmente grande
            # (ou desatualizado) não pode prender a marcação para sempre.
            inicio_atual, duracao_atual = self._destaque_posicoes[atual][2:]
            limite = inicio_atual + min(duracao_atual, 1.5)
            if (
                segundos < limite
                and segundos - self._destaque_posicao_troca < 2.5
            ):
                return
        if indice == atual:
            return
        self._destaque_indice = indice
        self._destaque_posicao_troca = segundos
        inicio_char, fim_char, _, _ = self._destaque_posicoes[indice]
        try:
            self.texto.tag_remove("leitura_atual", "1.0", "end")
            self.texto.tag_add(
                "leitura_atual", f"1.0+{inicio_char}c", f"1.0+{fim_char}c"
            )
            self.texto.see(f"1.0+{inicio_char}c")
        except (tk.TclError, ValueError):
            pass

    def _limpar_destaque(self) -> None:
        try:
            self.texto.tag_remove("leitura_atual", "1.0", "end")
        except (tk.TclError, AttributeError):
            pass
        self._destaque_limites = []
        self._destaque_posicoes = []
        self._destaque_indice = -1
        self._ultima_posicao_destaque = 0.0
        self._destaque_posicao_troca = 0.0

    def _drenar_destaque(self) -> None:
        """Traz eventos da thread de áudio para a thread Tk com segurança."""
        while True:
            try:
                evento, valor = self._fila_destaque.get_nowait()
            except queue.Empty:
                break
            try:
                self._ao_destaque(evento, valor)
            except Exception:
                # Um marcador inesperado não pode matar a drenagem futura.
                continue
        try:
            self._id_destaque = self.root.after(30, self._drenar_destaque)
        except (tk.TclError, RuntimeError):
            pass

    def _ao_destaque(self, evento: str, valor) -> None:
        if evento == "limites":
            novos = list(valor or [])
            textos_novos = [str(item.get("texto", "")) for item in novos]
            textos_antigos = [
                str(item.get("texto", "")) for item in self._destaque_limites
            ]
            # As listas são republicadas várias vezes só para trocar os tempos.
            # Nesse caso basta atualizar os números: refazer a busca das palavras
            # era trabalho à toa na thread da interface.
            if (
                textos_novos == textos_antigos
                and len(novos) == len(self._destaque_posicoes)
            ):
                self._destaque_limites = novos
                self._destaque_posicoes = [
                    (
                        inicio, fim,
                        float(item.get("offset", 0)),
                        float(item.get("duracao", 0)),
                    )
                    for (inicio, fim, _, _), item in zip(
                        self._destaque_posicoes, novos
                    )
                ]
                # Os tempos mudaram: se a palavra atual foi empurrada para o
                # futuro, esperá-la congelaria o destaque. Nesse caso apenas,
                # reajusta sem aplicar a espera da palavra longa.
                atual = self._destaque_indice
                if (
                    self._ultima_posicao_destaque
                    and 0 <= atual < len(self._destaque_posicoes)
                    and self._ultima_posicao_destaque
                    < self._destaque_posicoes[atual][2] - 0.03
                ):
                    self._destacar_posicao(
                        self._ultima_posicao_destaque, reajustar=True
                    )
                return
            self._destaque_limites = novos
            self._mapear_destaque(novos)
        elif evento == "posicao":
            try:
                segundos = float(valor)
                self._ultima_posicao_destaque = segundos
                self._destacar_posicao(segundos)
            except (TypeError, ValueError):
                pass
        elif evento == "fim":
            self._limpar_destaque()

    def _proteger(self, funcao):
        """Ignora atalhos enquanto o overlay de seleção está aberto."""

        def executar(*argumentos):
            if self.seletor is None:
                funcao(*argumentos)

        return executar

    # ------------------------------------------------------------- seleção
    def iniciar_selecao(self) -> None:
        if self.seletor is not None:
            return
        self.parar()
        # Guarda o app ativo ANTES do overlay roubar o foco.
        self.app_alvo_hwnd = self._foreground_hwnd()
        self._abaixo_app = bool(
            self.app_alvo_hwnd and self.app_alvo_hwnd != self._janela_root_hwnd()
        )
        self._ocultar_sombra()
        self.root.withdraw()
        try:
            # Cada clique em "Selecionar região" é uma seleção nova: nunca
            # redesenhar a região anterior. Ela continua salva para Ctrl+Shift+L.
            self.seletor = SeletorRegiao(
                self.root,
                None,
                bool(self.cfg.get("mostrar_zoom", True)),
                self._ao_selecionar,
                bool(self.cfg.get("confirmar_ao_soltar", True)),
                opcoes=self.cfg,
            )
            if bool(self.cfg.get("confirmar_ao_soltar", True)):
                self.status.set(
                    "Arraste para selecionar e solte o mouse para ler. Esc cancela."
                )
            else:
                self.status.set(
                    "Selecione a área e pressione Enter (ou clique em Ler). Esc cancela."
                )
        except Exception as exc:
            self.root.deiconify()
            self.status.set(f"Falha ao abrir a seleção: {exc}")

    def _ao_selecionar(self, regiao: Regiao | None) -> None:
        self.seletor = None
        if regiao is None:
            self.root.deiconify()
            self._aplicar_topmost()
            self.status.set("Seleção cancelada.")
            return
        self.abaixo_app = bool(
            self.cfg.get("abaixo_apos_selecao", True) and self._abaixo_app
        )
        self.cfg["regiao"] = regiao.para_dicionario()
        salvar(self.cfg)
        self.status.set(
            f"Região: {regiao.largura} x {regiao.altura} em ({regiao.x}, {regiao.y}). Lendo..."
        )
        self.ler_regiao(regiao)

    # ---------------------------------------------------------- leitura / OCR
    def ler_regiao(self, regiao: Regiao | None = None) -> None:
        if regiao is None:
            regiao = Regiao.de_dicionario(self.cfg.get("regiao"))
            if regiao is None:
                self.status.set("Selecione uma região primeiro.")
                self.iniciar_selecao()
                return

        fala.parar()
        self.ocr_sequencia += 1
        sequencia = self.ocr_sequencia
        self._ocultar_sombra()
        self.root.withdraw()  # evita capturar a própria janela
        self.status.set("Capturando e reconhecendo o texto...")

        def tarefa() -> None:
            try:
                imagem = captura.capturar_area(
                    regiao, bool(self.cfg.get("melhorar_ocr", True))
                )
                texto = ocr.reconhecer(imagem, self.cfg.get("idioma_ocr", "pt-BR"))
                self.root.after(0, self._texto_reconhecido, texto, sequencia)
            except Exception as exc:
                self.root.after(0, self._erro, f"Falha no OCR: {exc}")

        threading.Thread(target=tarefa, daemon=True).start()

    def _texto_reconhecido(self, texto: str, sequencia: int | None = None) -> None:
        if sequencia is not None and sequencia != self.ocr_sequencia:
            return
        self._mostrar_janela_apos_leitura()
        # Por padrão o novo texto SUBSTITUI o anterior (sem precisar de Limpar).
        novo = texto.strip()
        anexar = bool(self.cfg.get("anexar_texto_ocr", True))
        if self._painel_em_traducao:
            base = self.texto_original or ""
            self._painel_em_traducao = False
        else:
            visivel = self.texto.get("1.0", "end-1c").strip()
            if visivel != self.texto_atual:
                self._sincronizar_painel()
            base = self.texto_original or ""
        if not anexar:
            base = ""
        combinado = f"{base}\n{novo}" if base and novo else (novo or base)
        self.texto_atual = combinado
        self.texto_original = combinado
        self._exibir_texto(combinado)
        self.idioma_detectado = traducao.detectar(combinado)
        self.idioma_label.config(
            text=f"Idioma detectado: {traducao.nome_idioma(self.idioma_detectado)}"
        )
        if not novo:
            self.status.set("Nenhum texto reconhecido nessa região.")
            return

        destino = self._destino_codigo()
        if (
            self.cfg.get("traduzir_automaticamente")
            and not (anexar and base)
            and self.idioma_detectado
            and traducao.codigo_base(self.idioma_detectado)
            != traducao.codigo_base(destino)
        ):
            self.status.set("Texto em outro idioma. Traduzindo automaticamente...")
            self.traduzir(automatico=True)
            return

        if anexar and base:
            resumo = f"{len(novo)} caracteres adicionados ao painel"
        else:
            resumo = f"{len(novo)} caracteres reconhecidos — texto anterior substituído"
        self.status.set(
            f"{resumo}. "
            f"Detectado: {traducao.nome_idioma(self.idioma_detectado)}. Lendo..."
        )
        self._falar(novo)

    def _erro(self, mensagem: str) -> None:
        self._mostrar_janela_apos_leitura()
        self.status.set(mensagem)

    # ------------------------------------------------------------- tradução
    def traduzir(self, automatico: bool = False) -> None:
        if not automatico:
            self._sincronizar_painel()
        if not self.texto_original:
            self.status.set("Não há texto para traduzir.")
            return
        destino = self._destino_codigo()
        origem = self.idioma_detectado or "auto"
        if (
            not automatico
            and origem != "auto"
            and traducao.codigo_base(origem) == traducao.codigo_base(destino)
        ):
            self.status.set(
                f"O texto já parece estar em {traducao.nome_idioma(origem)}. "
                "Escolha outro idioma em 'Para'."
            )
            return

        self.status.set(
            f"Traduzindo para {traducao.nome_idioma(destino)} "
            f"(de {traducao.nome_idioma(origem)})..."
        )
        texto = self.texto_original

        def tarefa() -> None:
            try:
                resultado = traducao.traduzir(texto, destino, origem)
                self.root.after(0, self._traducao_pronta, resultado, destino)
            except Exception as exc:
                self.root.after(0, self._erro, f"Falha na tradução: {exc}")

        threading.Thread(target=tarefa, daemon=True).start()

    def _traducao_pronta(self, resultado: str, destino: str) -> None:
        if not resultado.strip():
            self.status.set("A tradução voltou vazia.")
            return
        self.texto_atual = resultado.strip()
        self._painel_em_traducao = True
        self._exibir_texto(self.texto_atual)
        self.status.set(
            f"Traduzido para {traducao.nome_idioma(destino)}. Lendo a tradução..."
        )
        self._falar(self.texto_atual)

    def mostrar_original(self) -> None:
        if not self.texto_original or self.texto_atual == self.texto_original:
            self.status.set("O painel já mostra o texto original.")
            return
        self.texto_atual = self.texto_original
        self._painel_em_traducao = False
        self._exibir_texto(self.texto_atual)
        self.status.set("Texto original restaurado.")

    def _exibir_texto(self, texto: str) -> None:
        self._limpar_destaque()
        self.texto.delete("1.0", "end")
        self.texto.insert("1.0", texto)
        contador = getattr(self, "contador", None)
        if contador is not None:
            quantidade = len(texto)
            palavras = len(texto.split())
            contador.config(text=f"{quantidade} caracteres • {palavras} palavras")

    def _sincronizar_painel(self) -> str:
        """Considera o texto digitado/colado no painel, não só o resultado do OCR."""
        texto = self.texto.get("1.0", "end-1c").strip()
        if texto != self.texto_atual:
            self.texto_atual = texto
            if not self._painel_em_traducao:
                self.texto_original = texto
                self.idioma_detectado = traducao.detectar(texto) if texto else None
                nome = (
                    traducao.nome_idioma(self.idioma_detectado)
                    if self.idioma_detectado
                    else "—"
                )
                self.idioma_label.config(text=f"Idioma detectado: {nome}")
        return texto

    # ------------------------------------------------------------ fala / TTS
    def _falar(self, texto: str) -> None:
        if not texto.strip():
            return
        fala.parar()
        self._limpar_destaque()
        self.sequencia += 1
        atual = self.sequencia
        self._aviso_fala = None
        self._configurar_principal(
            self.botao_principal, "❚❚ Pausar", self.alternar_pausa, COR_AZUL
        )

        def tarefa() -> None:
            def aviso(mensagem: str) -> None:
                self._aviso_fala = mensagem
                self._agendar(self.status.set, mensagem)

            def progresso(mensagem: str) -> None:
                self._agendar(self.status.set, mensagem)

            def destaque(evento: str, valor) -> None:
                # Uma leitura cancelada não pode repintar a nova. A fila é
                # drenada na thread Tk; after() chamado da thread de áudio não
                # acordava o mainloop de forma confiável.
                if atual == self.sequencia:
                    self._fila_destaque.put((evento, valor))

            # Mocks e callers antigos ainda usam quatro argumentos; a função
            # real recebe o callback extra para informar palavra/posição/fim.
            if self._aceita_destaque(fala.falar):
                fala.falar(texto, self.cfg, aviso, progresso, destaque=destaque)
            else:
                fala.falar(texto, self.cfg, aviso, progresso)
            self._agendar(self._finalizado, atual)

        threading.Thread(target=tarefa, daemon=True).start()

    def ler_texto(self) -> None:
        texto = self._sincronizar_painel()
        if not texto:
            self.status.set("Não há texto para ler.")
            return
        self.status.set(f"Lendo {len(texto)} caracteres do painel...")
        self._falar(texto)

    def alternar_pausa(self) -> None:
        pausado = fala.pausar_continuar()
        self.status.set("Leitura pausada." if pausado else "Leitura em andamento...")
        self._configurar_principal(
            self.botao_principal,
            "▶ Continuar" if pausado else "❚❚ Pausar",
            self.alternar_pausa,
            COR_AZUL,
        )

    def parar(self) -> None:
        fala.parar()
        self._limpar_destaque()
        self.status.set("Leitura parada.")
        self._configurar_principal(
            self.botao_principal, "▶ Ler região", self.ler_regiao, COR_VERDE
        )

    def _finalizado(self, atual: int) -> None:
        if atual != self.sequencia:
            return
        aviso = self._aviso_fala
        self._aviso_fala = None
        self.parar()
        self._limpar_destaque()
        if aviso:
            self.status.set(f"Leitura concluída com voz local — {aviso}")
        else:
            self.status.set("Leitura concluída.")

    # ------------------------------------------------------------- utilitários
    def limpar_texto(self) -> None:
        self.parar()
        # Áudio temporário/cache em memória vai junto com o texto do painel.
        fala.limpar_cache()
        self.texto.delete("1.0", "end")
        self.texto_atual = ""
        self.texto_original = ""
        self._painel_em_traducao = False
        self.idioma_detectado = None
        self.idioma_label.config(text="Idioma detectado: —")
        self.status.set("Painel de texto limpo.")

    def colar(self) -> None:
        """Cola no painel o texto já copiado para a área de transferência."""
        try:
            texto = self.root.clipboard_get()
        except tk.TclError:
            self.status.set("Área de transferência vazia.")
            return
        if not texto or not texto.strip():
            self.status.set("Área de transferência vazia.")
            return
        self.parar()
        self._painel_em_traducao = False
        self._exibir_texto(texto)
        self._sincronizar_painel()
        self.status.set(f"Texto colado: {len(texto)} caracteres.")

    def copiar(self) -> None:
        texto = self._sincronizar_painel()
        if not texto:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(texto)
        self.status.set("Texto copiado para a área de transferência.")

    def _velocidade_para_texto(self) -> str:
        valor = float(self.cfg.get("velocidade", 1.0))
        return min(VELOCIDADES, key=lambda chave: abs(VELOCIDADES[chave] - valor))

    def _destino_para_nome(self) -> str:
        codigo = traducao.codigo_base(self.cfg.get("idioma_destino", "pt"))
        for nome, valor in traducao.IDIOMAS.items():
            if traducao.codigo_base(valor) == codigo:
                return nome
        return "Português"

    def _destino_codigo(self) -> str:
        return traducao.IDIOMAS[self.destino_var.get()]

    def _ao_mudar_destino(self, event=None) -> None:
        self.cfg["idioma_destino"] = self._destino_codigo()
        salvar(self.cfg)
        self.status.set(f"Idioma de destino: {self.destino_var.get()}.")

    def _ao_mudar_voz(self, event=None) -> None:
        escolhida = self.voz_var.get()
        if escolhida in getattr(self, "_rotulos_neural", {}):
            self.cfg["modo_neural"] = True
            self.cfg["voz_neural"] = self._rotulos_neural[escolhida]
            self.status.set(
                f"Voz neural: {fala.nome_amigavel_neural(self.cfg['voz_neural'])} "
                "(fallback automático para a voz local)."
            )
        else:
            self.cfg["modo_neural"] = False
            self.cfg["voz_sapi"] = escolhida
            self.status.set(f"Voz: {escolhida}")
        salvar(self.cfg)

    def _ao_mudar_velocidade(self, event=None) -> None:
        self.cfg["velocidade"] = VELOCIDADES[self.velocidade_var.get()]
        salvar(self.cfg)

    def _ao_mudar_volume(self, valor) -> None:
        self.cfg["volume"] = int(valor)
        salvar(self.cfg)

    # ------------------------------------------------------------ configurações
    def abrir_configuracoes(self) -> None:
        janela = tk.Toplevel(self.root)
        janela.title("Configurações")
        janela.configure(bg=COR_FUNDO)
        janela.resizable(False, False)
        janela.attributes("-topmost", True)
        janela.transient(self.root)
        janela.geometry("560x720+160+40")
        # O Notebook/Combobox usam estilos ttk: refazê-los aqui garante as
        # cores do tema ativo mesmo se a janela for aberta após alternar.
        self._configurar_estilos(self.root)

        tk.Label(
            janela,
            text="Configurações do Leitor em Voz Alta",
            bg=COR_FUNDO,
            fg=COR_TITULO,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        caderno = ttk.Notebook(janela)
        caderno.pack(fill="both", expand=True, padx=12, pady=6)
        aba_geral = tk.Frame(caderno, bg=COR_FUNDO)
        aba_selecao = tk.Frame(caderno, bg=COR_FUNDO)
        aba_leitura = tk.Frame(caderno, bg=COR_FUNDO)
        caderno.add(aba_geral, text="Geral")
        caderno.add(aba_selecao, text="Seleção")
        caderno.add(aba_leitura, text="Leitura")

        # ------------------------------------------------------------ aba Geral
        linha = tk.Frame(aba_geral, bg=COR_FUNDO)
        linha.pack(fill="x", padx=12, pady=4)
        tk.Label(
            linha, text="Idioma do OCR:", bg=COR_FUNDO, fg=COR_TEXTO,
            width=20, anchor="w",
        ).pack(side="left")
        combo_idioma = ttk.Combobox(
            linha,
            values=ocr.idiomas_disponiveis(),
            state="readonly",
            width=22,
        )
        combo_idioma.set(self.cfg.get("idioma_ocr", "pt-BR"))
        combo_idioma.pack(side="left")

        linha = tk.Frame(aba_geral, bg=COR_FUNDO)
        linha.pack(fill="x", padx=12, pady=4)
        tk.Label(
            linha, text="Voz neural:", bg=COR_FUNDO, fg=COR_TEXTO,
            width=20, anchor="w",
        ).pack(side="left")
        ids_neural = fala.listar_vozes_neural()
        atual_neural = self.cfg.get("voz_neural", VOZ_NEURAL_PADRAO)
        if atual_neural not in ids_neural:
            ids_neural.insert(0, atual_neural)
        rotulos_neural = [
            fala.nome_amigavel_neural(id_voz) for id_voz in ids_neural
        ]
        combo_neural = ttk.Combobox(
            linha, values=rotulos_neural, state="readonly", width=34
        )
        combo_neural.current(ids_neural.index(atual_neural))
        combo_neural.pack(side="left")

        opcoes = [
            ("sempre_topo", "Manter janela sempre no topo"),
            ("abaixo_apos_selecao", "Após selecionar, ficar atrás do app lido"),
            ("anexar_texto_ocr", "Manter texto anterior (anexar OCR em vez de substituir)"),
            ("confirmar_ao_soltar", "Confirmar região ao soltar o mouse"),
            ("melhorar_ocr", "Melhorar imagem antes do OCR"),
            ("traduzir_automaticamente", "Traduzir automaticamente se o idioma for outro"),
        ]
        variaveis = {}
        for chave, texto in opcoes:
            variavel = tk.BooleanVar(value=bool(self.cfg.get(chave, True)))
            variaveis[chave] = variavel
            tk.Checkbutton(
                aba_geral,
                text=texto,
                variable=variavel,
                bg=COR_FUNDO,
                fg=COR_TEXTO,
                activebackground=COR_SUPERFICIE_2,
                activeforeground=COR_TEXTO,
                # No tema escuro o quadrado vinha com SystemWindow (escuro),
                # igual ao fundo: não dava para ver se estava marcado.
                selectcolor=COR_AZUL,
                anchor="w",
                font=("Segoe UI", 9),
            ).pack(fill="x", padx=12, pady=2)

        tk.Label(
            aba_geral,
            text="Atalhos globais (clique em um atalho e pressione a combinação; Esc cancela):",
            bg=COR_FUNDO,
            fg=COR_TITULO,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 2))

        for acao, descricao in ATALHOS_EVENTOS.items():
            linha = tk.Frame(aba_geral, bg=COR_FUNDO)
            linha.pack(fill="x", padx=12, pady=1)
            tk.Label(
                linha, text=descricao, bg=COR_FUNDO, fg=COR_TEXTO,
                width=24, anchor="w",
            ).pack(side="left")
            # Normaliza aqui também: atalhos antigos salvos como <45> aparecem
            # como INSERT em vez de um número sem sentido.
            combo = normalizar(
                (self.cfg.get("atalhos") or {}).get(acao),
                COMBOS_PADRAO[acao],
            )
            botao = tk.Button(
                linha,
                text=formatar(combo),
                command=None,
                bg=COR_SUPERFICIE,
                fg=COR_TEXTO,
                relief="solid",
                bd=1,
                width=22,
                font=("Segoe UI", 8),
                cursor="hand2",
            )
            botao.pack(side="left")
            botao.configure(
                command=lambda a=acao, b=botao: self._gravar_atalho(janela, a, b)
            )

        tk.Label(
            aba_geral,
            text="Use letras, números ou F1-F12 com Ctrl/Shift/Alt/Win.",
            bg=COR_FUNDO,
            fg=COR_SECUNDARIA,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=12, pady=(4, 0))

        # -------------------------------------------------------- aba Seleção
        tk.Label(
            aba_selecao,
            text="Marquee / lupa de seleção",
            bg=COR_FUNDO,
            fg=COR_TITULO,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        sel_vars: dict = {}
        sel_vars["mostrar_zoom"] = tk.BooleanVar(
            value=bool(self.cfg.get("mostrar_zoom", True))
        )
        tk.Checkbutton(
            aba_selecao,
            text="Mostrar lupa de zoom (desmarque para remover)",
            variable=sel_vars["mostrar_zoom"],
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            activebackground=COR_SUPERFICIE_2,
            activeforeground=COR_TEXTO,
            selectcolor=COR_AZUL,
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(fill="x", padx=12, pady=2)

        def escala(chave: str, texto: str, de: int, ate: int, valor: int, sufixo: str) -> None:
            quadro = tk.Frame(aba_selecao, bg=COR_FUNDO)
            quadro.pack(fill="x", padx=12, pady=3)
            tk.Label(
                quadro, text=texto, bg=COR_FUNDO, fg=COR_TEXTO,
                width=27, anchor="w",
            ).pack(side="left")
            var = tk.IntVar(value=valor)
            rotulo = tk.Label(
                quadro, text=f"{valor}{sufixo}", bg=COR_FUNDO,
                fg=COR_TEXTO, width=6,
            )
            rotulo.pack(side="right")
            tk.Scale(
                quadro,
                from_=de,
                to=ate,
                orient="horizontal",
                variable=var,
                showvalue=False,
                length=170,
                bg=COR_FUNDO,
                fg=COR_TEXTO,
                troughcolor=_cor_tema("#ECEEF4"),
                activebackground=COR_AZUL,
                highlightthickness=0,
                command=lambda _=None, v=var, l=rotulo, s=sufixo: l.config(
                    text=f"{v.get()}{s}"
                ),
            ).pack(side="left", fill="x", expand=True)
            sel_vars[chave] = var

        escala(
            "zoom_largura", "Largura da lupa:", 200, 800,
            int(self.cfg.get("zoom_largura", ZOOM_LARGURA_PADRAO)), " px",
        )
        escala(
            "zoom_altura", "Altura da lupa:", 120, 500,
            int(self.cfg.get("zoom_altura", ZOOM_ALTURA_PADRAO)), " px",
        )
        escala(
            "opacidade_selecao", "Opacidade da área:", 5, 100,
            int(self.cfg.get("opacidade_selecao", OPACIDADE_PADRAO)), "%",
        )

        tk.Label(
            aba_selecao,
            text="A lupa fica no canto superior direito e não acompanha o mouse.",
            bg=COR_FUNDO,
            fg=COR_SECUNDARIA,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=12, pady=(0, 6))

        quadro_cor = tk.Frame(aba_selecao, bg=COR_FUNDO)
        quadro_cor.pack(fill="x", padx=12, pady=6)
        tk.Label(
            quadro_cor, text="Cor da área:", bg=COR_FUNDO, fg=COR_TEXTO,
            width=27, anchor="w",
        ).pack(side="left")
        cor_var = tk.StringVar(
            value=self.cfg.get("cor_selecao", COR_SELECAO_PADRAO)
        )
        sel_vars["cor_selecao"] = cor_var
        tk.Entry(
            quadro_cor,
            textvariable=cor_var,
            width=10,
            bg=COR_SUPERFICIE,
            fg=COR_TEXTO,
            relief="solid",
            bd=1,
            font=("Segoe UI", 9),
        ).pack(side="left")

        previa = tk.Canvas(
            quadro_cor,
            width=90,
            height=24,
            bg=COR_SUPERFICIE,
            highlightthickness=1,
            highlightbackground=COR_BORDA,
        )
        previa.pack(side="left", padx=8)
        retangulo_previa = previa.create_rectangle(2, 2, 88, 22, outline="")
        cor_var.trace_add(
            "write",
            lambda *_: previa.itemconfig(retangulo_previa, fill=cor_var.get()),
        )
        previa.itemconfig(retangulo_previa, fill=cor_var.get())

        def escolher_cor() -> None:
            from tkinter.colorchooser import askcolor

            _rgb, hexcor = askcolor(color=cor_var.get(), parent=janela)
            if hexcor:
                cor_var.set(str(hexcor).upper())

        tk.Button(
            quadro_cor,
            text="Escolher cor",
            command=escolher_cor,
            bg=COR_SUPERFICIE_2,
            fg=COR_TEXTO,
            relief="flat",
            bd=0,
            font=("Segoe UI", 8),
            cursor="hand2",
        ).pack(side="left")

        # -------------------------------------------------------- aba Leitura
        tk.Label(
            aba_leitura,
            text="Destaque da leitura no painel",
            bg=COR_FUNDO,
            fg=COR_TITULO,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        leitura_vars: dict = {}
        quadro_opacidade = tk.Frame(aba_leitura, bg=COR_FUNDO)
        quadro_opacidade.pack(fill="x", padx=12, pady=3)
        tk.Label(
            quadro_opacidade,
            text="Opacidade do destaque:",
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            width=27,
            anchor="w",
        ).pack(side="left")
        opacidade_destaque_var = tk.IntVar(
            value=int(
                self.cfg.get("opacidade_destaque", OPACIDADE_DESTAQUE_PADRAO)
            )
        )
        rotulo_opacidade = tk.Label(
            quadro_opacidade,
            text=f"{opacidade_destaque_var.get()}%",
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            width=6,
        )
        rotulo_opacidade.pack(side="right")
        tk.Scale(
            quadro_opacidade,
            from_=5,
            to=100,
            orient="horizontal",
            variable=opacidade_destaque_var,
            showvalue=False,
            length=170,
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            troughcolor=_cor_tema("#ECEEF4"),
            activebackground=COR_AZUL,
            highlightthickness=0,
            command=lambda _=None: rotulo_opacidade.config(
                text=f"{opacidade_destaque_var.get()}%"
            ),
        ).pack(side="left", fill="x", expand=True)
        leitura_vars["opacidade_destaque"] = opacidade_destaque_var

        quadro_cor_destaque = tk.Frame(aba_leitura, bg=COR_FUNDO)
        quadro_cor_destaque.pack(fill="x", padx=12, pady=6)
        tk.Label(
            quadro_cor_destaque,
            text="Cor do destaque:",
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            width=27,
            anchor="w",
        ).pack(side="left")
        cor_destaque_var = tk.StringVar(
            value=self.cfg.get("cor_destaque", COR_DESTAQUE_PADRAO)
        )
        leitura_vars["cor_destaque"] = cor_destaque_var
        tk.Entry(
            quadro_cor_destaque,
            textvariable=cor_destaque_var,
            width=10,
            bg=COR_SUPERFICIE,
            fg=COR_TEXTO,
            relief="solid",
            bd=1,
            font=("Segoe UI", 9),
        ).pack(side="left")

        previa_destaque = tk.Canvas(
            quadro_cor_destaque,
            width=90,
            height=24,
            bg=COR_SUPERFICIE,
            highlightthickness=1,
            highlightbackground=COR_BORDA,
        )
        previa_destaque.pack(side="left", padx=8)
        retangulo_destaque = previa_destaque.create_rectangle(
            2, 2, 88, 22, outline=""
        )
        cor_destaque_var.trace_add(
            "write",
            lambda *_: previa_destaque.itemconfig(
                retangulo_destaque, fill=cor_destaque_var.get()
            ),
        )
        previa_destaque.itemconfig(
            retangulo_destaque, fill=cor_destaque_var.get()
        )

        def escolher_cor_destaque() -> None:
            from tkinter.colorchooser import askcolor

            _rgb, hexcor = askcolor(color=cor_destaque_var.get(), parent=janela)
            if hexcor:
                cor_destaque_var.set(str(hexcor).upper())

        tk.Button(
            quadro_cor_destaque,
            text="Escolher cor",
            command=escolher_cor_destaque,
            bg=COR_SUPERFICIE_2,
            fg=COR_TEXTO,
            relief="flat",
            bd=0,
            font=("Segoe UI", 8),
            cursor="hand2",
        ).pack(side="left")

        tk.Label(
            aba_leitura,
            text="A cor é misturada com o fundo do painel conforme a opacidade.",
            bg=COR_FUNDO,
            fg=COR_SECUNDARIA,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=12, pady=(0, 6))

        # A janela de configurações é criada depois do registro inicial: sem
        # registrar de novo ela ficaria com as cores do tema em que nasceu.
        self._registrar_tema()

        def fechar_config() -> None:
            self._reinstalar_hotkeys()
            janela.destroy()
            self._registrar_tema()

        janela.protocol("WM_DELETE_WINDOW", fechar_config)

        def salvar_config() -> None:
            self.cfg["idioma_ocr"] = combo_idioma.get() or "pt-BR"
            escolhida = combo_neural.get()
            self.cfg["voz_neural"] = (
                ids_neural[rotulos_neural.index(escolhida)]
                if escolhida in rotulos_neural
                else VOZ_NEURAL_PADRAO
            )
            for chave, variavel in variaveis.items():
                self.cfg[chave] = variavel.get()
            self.cfg["mostrar_zoom"] = bool(sel_vars["mostrar_zoom"].get())
            self.cfg["zoom_largura"] = int(sel_vars["zoom_largura"].get())
            self.cfg["zoom_altura"] = int(sel_vars["zoom_altura"].get())
            self.cfg["opacidade_selecao"] = int(
                sel_vars["opacidade_selecao"].get()
            )
            cor_digitada = cor_var.get().strip().upper()
            self.cfg["cor_selecao"] = (
                cor_digitada
                if re.fullmatch(r"#[0-9A-F]{6}", cor_digitada)
                else COR_SELECAO_PADRAO
            )
            self.cfg["opacidade_destaque"] = int(
                leitura_vars["opacidade_destaque"].get()
            )
            cor_destaque_digitada = cor_destaque_var.get().strip().upper()
            self.cfg["cor_destaque"] = (
                cor_destaque_digitada
                if re.fullmatch(r"#[0-9A-F]{6}", cor_destaque_digitada)
                else COR_DESTAQUE_PADRAO
            )
            self._atualizar_tag_destaque()
            salvar(self.cfg)
            self._aplicar_topmost()
            self.status.set("Configurações salvas.")
            fechar_config()

        tk.Button(
            janela,
            text="Salvar",
            command=salvar_config,
            bg=COR_AZUL,
            fg="white",
            relief="flat",
            width=16,
            font=("Segoe UI", 9, "bold"),
        ).pack(pady=12)

    @staticmethod
    def _texto_da_tecla(key, listener) -> str | None:
        """Converte uma tecla do pynput para a sintaxe de atalhos do app.

        No Windows, ``listener.canonical()`` transforma teclas especiais (F1,
        PageUp, setas etc.) em ``KeyCode(vk=...)``. Sem o mapa abaixo elas
        apareciam como números (``<112>``) no botão do atalho.
        """
        from pynput import keyboard

        try:
            canonico = listener.canonical(key)
        except Exception:
            canonico = key
        if isinstance(canonico, keyboard.Key):
            return f"<{canonico.name}>"
        caractere = getattr(canonico, "char", None)
        if caractere and caractere.isascii() and caractere.isalnum():
            return caractere.lower()
        vk = getattr(canonico, "vk", None)
        return vk_para_token(vk) if vk is not None else None

    def _gravar_atalho(self, janela: tk.Toplevel, acao: str, botao: tk.Button) -> None:
        """Abre um diálogo e grava a combinação pressionada para ``acao``."""
        try:
            from pynput import keyboard
        except Exception as exc:
            self.status.set(f"Não foi possível gravar atalhos: {exc}")
            return

        descricao = ATALHOS_EVENTOS.get(acao, acao)
        self._parar_hotkeys()  # evita disparar ações enquanto se grava

        dialogo = tk.Toplevel(janela)
        dialogo.title("Gravar atalho")
        dialogo.configure(bg=COR_FUNDO)
        dialogo.resizable(False, False)
        dialogo.attributes("-topmost", True)
        dialogo.transient(janela)
        dialogo.geometry("330x105+260+260")
        aviso = tk.StringVar(value=f"{descricao}: pressione a combinação...")
        tk.Label(
            dialogo,
            textvariable=aviso,
            bg=COR_FUNDO,
            fg=COR_TEXTO,
            font=("Segoe UI", 10, "bold"),
            wraplength=300,
            justify="center",
        ).pack(fill="x", padx=12, pady=(16, 4))
        tk.Label(
            dialogo,
            text="Esc cancela • use letras, números ou F1-F12",
            bg=COR_FUNDO,
            fg=COR_SECUNDARIA,
            font=("Segoe UI", 8),
        ).pack()

        modificadores: set[str] = set()
        finalizado = False

        def agendar(funcao) -> None:
            try:
                janela.after(0, funcao)
            except tk.TclError:
                funcao()

        def atualizar(texto: str) -> None:
            def executar() -> None:
                try:
                    aviso.set(texto)
                except tk.TclError:
                    pass

            agendar(executar)

        def concluir(combo: str | None) -> None:
            nonlocal finalizado
            if finalizado:
                return
            finalizado = True

            def aplicar() -> None:
                try:
                    listener.stop()
                except Exception:
                    pass
                if combo:
                    self.cfg.setdefault("atalhos", {})[acao] = combo
                    salvar(self.cfg)
                    try:
                        botao.config(text=formatar(combo))
                    except tk.TclError:
                        pass
                    self.status.set(f"Atalho de {descricao}: {formatar(combo)}")
                try:
                    dialogo.destroy()
                except tk.TclError:
                    pass
                self._reinstalar_hotkeys()

            agendar(aplicar)

        def ao_press(key) -> None:
            if finalizado:
                return
            texto = self._texto_da_tecla(key, listener)
            if not texto:
                return
            if texto in {"<ctrl>", "<shift>", "<alt>", "<cmd>"}:
                modificadores.add(texto)
                atualizar(" + ".join(sorted(modificadores)) + " + ...")
                return
            if texto in {"<esc>", "<escape>"}:
                concluir(None)
                return
            ordem = ("<ctrl>", "<alt>", "<shift>", "<cmd>")
            bruto = "+".join([m for m in ordem if m in modificadores] + [texto])
            combinacao = normalizar(bruto, "")
            if combinacao:
                atualizar(f"Salvo: {formatar(combinacao)}")
                concluir(combinacao)
            else:
                atualizar("Combinação inválida. Tente outra.")

        def ao_release(key) -> None:
            texto = self._texto_da_tecla(key, listener)
            if texto:
                modificadores.discard(texto)

        listener = keyboard.Listener(on_press=ao_press, on_release=ao_release)
        dialogo.protocol("WM_DELETE_WINDOW", lambda: concluir(None))
        listener.start()
        dialogo.focus_force()
        dialogo.grab_set()

    # ------------------------------------------------------------ minimizar
    def minimizar(self) -> None:
        """Minimiza para a barra de tarefas; usa 🔊 apenas se o Win32 falhar."""
        if self.minimizado:
            return
        # Não chamar parar(): a leitura em andamento continua em segundo plano.
        self._ocultar_sombra()
        self._geometria_salva = (self.root.winfo_x(), self.root.winfo_y())
        if (
            self._minimizar_win32()
            or (self._preparar_taskbar() and self._minimizar_win32())
        ):
            self.minimizado = True
            self.botao_restaurar = None
            self.status.set(
                "Minimizado; a leitura continua. Clique no ícone da barra "
                "ou use o atalho para restaurar."
            )
            self._iniciar_monitor_estado()
            return
        self._minimizar_fallback()

    def _minimizar_fallback(self) -> None:
        """Caminho de segurança se a janela não entrar na barra de tarefas."""
        if self.botao_restaurar is not None:
            return
        self._parar_monitor_estado()
        self.root.withdraw()
        self.minimizado = True

        self.botao_restaurar = tk.Toplevel(self.root)
        self.botao_restaurar.overrideredirect(True)
        self.botao_restaurar.attributes("-topmost", True)
        botao = tk.Button(
            self.botao_restaurar,
            text="🔊",
            command=self.restaurar,
            bg=COR_TITULO,
            fg="white",
            activebackground=COR_AZUL,
            activeforeground="white",
            relief="flat",
            bd=0,
            font=("Segoe UI Emoji", 12),
            cursor="hand2",
        )
        botao.pack(fill="both", expand=True, padx=2, pady=2)
        x, y = self._geometria_salva or (self.root.winfo_x(), self.root.winfo_y())
        self.botao_restaurar.geometry(f"44x34+{x}+{y}")
        self.status.set("Janela minimizada. Clique em 🔊 ou use o atalho para restaurar.")

    def restaurar(self) -> None:
        self._parar_monitor_estado()
        if self.botao_restaurar is not None:
            self._restaurar_fallback()
            return
        if self._restaurar_win32():
            self.minimizado = False
            self.abaixo_app = False
            if self._geometria_salva:
                x, y = self._geometria_salva
                self.root.geometry(f"+{x}+{y}")
            self._preparar_taskbar()
            self._aplicar_topmost()
            self._arredondar_janela(self.root, RAIO_JANELA)
            self._posicionar_sombra()
            self.status.set("Janela restaurada.")
            return
        self._restaurar_fallback()

    def _restaurar_fallback(self) -> None:
        if self.botao_restaurar is not None:
            try:
                self.botao_restaurar.destroy()
            except tk.TclError:
                pass
            self.botao_restaurar = None
        self.minimizado = False
        self.abaixo_app = False
        self.root.deiconify()
        if self._geometria_salva:
            x, y = self._geometria_salva
            self.root.geometry(f"+{x}+{y}")
        self.root.lift()
        self._preparar_taskbar()
        self._aplicar_topmost()
        self._arredondar_janela(self.root, RAIO_JANELA)
        self._posicionar_sombra()
        self.status.set("Janela restaurada.")

    # ------------------------------------------------------------------ saída
    def _fechar(self) -> None:
        self._parar_monitor_estado()
        for identificador in getattr(self, "_ids_taskbar", []):
            try:
                self.root.after_cancel(identificador)
            except (tk.TclError, ValueError):
                pass
        self._ids_taskbar = []
        for identificador in getattr(self, "_ids_cantos", []):
            try:
                self.root.after_cancel(identificador)
            except (tk.TclError, ValueError):
                pass
        self._ids_cantos = []
        try:
            self.root.after_cancel(self._id_destaque)
        except (tk.TclError, ValueError, AttributeError):
            pass
        self._id_destaque = None
        sombra = getattr(self, "_sombra", None)
        if sombra is not None:
            try:
                sombra.destroy()
            except tk.TclError:
                pass
            self._sombra = None
        if self.botao_restaurar is not None:
            try:
                self.botao_restaurar.destroy()
            except tk.TclError:
                pass
            self.botao_restaurar = None
        fala.parar()
        fala.limpar_cache()
        if self.hotkeys:
            try:
                self.hotkeys.stop()
            except Exception:
                pass
        salvar(self.cfg)
        self.root.destroy()


def main() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    Aplicativo(root)
    root.mainloop()
