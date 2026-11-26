"""Overlay de seleção de região, no estilo da lupa do Windows."""

from __future__ import annotations

import ctypes
from typing import Callable

import tkinter as tk
from PIL import Image, ImageTk

from . import captura
from .captura import Regiao

COR_SOMBRA = "#101820"
# Azul de seleção padrão do Windows 11 (accent color).
COR_SELECAO = "#0078D4"
# Janela separada para o realce: translúcida como o marquee do Windows, para
# deixar o texto de baixo legível enquanto a área é escolhida.
ALFA_REALCE = 0.45
ALFA_SOMBRA = 0.20
COR_BORDA = COR_SELECAO
COR_ALCA = "#FFFFFF"
COR_TEXTO = "#FFFFFF"
# Linhas-guia amarelas: contrastam com o azul e com o fundo escurecido.
COR_GUIA = "#FFE082"
COR_BARRA = "#242838"
COR_LER = COR_SELECAO
COR_CANCELAR = "#B04A5A"

TOLERANCIA = 9
LARGURA_ZOOM = 360
ALTURA_ZOOM = 200
FATOR_ZOOM = 2

CURSORES = {
    "no": "size_nw_se",
    "se": "size_nw_se",
    "ne": "size_ne_sw",
    "so": "size_ne_sw",
    "n": "size_ns",
    "s": "size_ns",
    "o": "size_we",
    "e": "size_we",
}

# WS_EX_TRANSPARENT em janela layered faz o mouse passar através dela.
# O Tk JÁ aplica WS_EX_LAYERED por causa de -alpha/-transparentcolor. Reaplicar
# esse bit com SetWindowLongW apaga o conteúdo UpdateLayeredWindow da janela e
# o overlay fica invisível (foi o bug do "nada aparece na seleção").
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
GA_ROOT = 2


def _ignorar_mouse(janela: tk.Misc) -> None:
    """Faz uma janela visual do overlay não interceptar cliques."""
    try:
        user32 = ctypes.windll.user32
        hwnd = int(janela.winfo_id())
        nativa = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        estilo = user32.GetWindowLongW(nativa, GWL_EXSTYLE)
        # Só acrescenta WS_EX_TRANSPARENT. Nunca mexer em WS_EX_LAYERED aqui.
        user32.SetWindowLongW(nativa, GWL_EXSTYLE, estilo | WS_EX_TRANSPARENT)
    except Exception:
        pass


class SeletorRegiao:
    """
    Mostra um overlay transparente por cima da tela.

    O usuário desenha, move e redimensiona o retângulo. Ao confirmar
    (Enter, duplo clique ou botão "Ler"), a região é entregue ao callback.
    """

    def __init__(
        self,
        root: tk.Tk,
        regiao_inicial: Regiao | None,
        mostrar_zoom: bool,
        callback: Callable[[Regiao | None], None],
        confirmar_ao_soltar: bool = True,
        opcoes: dict | None = None,
    ) -> None:
        self.root = root
        self.callback = callback
        opcoes = opcoes or {}
        self.mostrar_zoom = mostrar_zoom
        self.confirmar_ao_soltar = confirmar_ao_soltar
        self.cor_selecao = opcoes.get("cor_selecao", COR_SELECAO)
        self.alfa_realce = max(
            0.05, min(1.0, float(opcoes.get("opacidade_selecao", 45)) / 100.0)
        )
        self.zoom_largura = max(160, int(opcoes.get("zoom_largura", LARGURA_ZOOM)))
        self.zoom_altura = max(100, int(opcoes.get("zoom_altura", ALTURA_ZOOM)))
        self._finalizado = False
        self.vx, self.vy, self.vw, self.vh = captura.area_virtual()
        if self.vw <= 0 or self.vh <= 0:  # fallback se o Windows não informar a área
            self.vx = self.vy = 0
            self.vw, self.vh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.regiao = captura.limitar_regiao(regiao_inicial) if regiao_inicial else None
        self.estado: tuple | None = None
        self.anchor: tuple[int, int] | None = None
        self.botoes: list[dict] = []
        self._imagem_zoom: ImageTk.PhotoImage | None = None
        self._id_ants: str | None = None
        self._borda_item: int | None = None
        self._passo_ants = 0
        self._cursor: tuple[int, int] | None = None

        self.imagem_fundo = (
            captura.capturar_tela_completa() if mostrar_zoom else None
        )

        self.sombra, self.canvas_sombra = self._criar_janela(ALFA_SOMBRA)
        self.realce, self.canvas_realce = self._criar_janela(self.alfa_realce)
        self.borda, self.canvas = self._criar_janela(1.0)
        self._criar_zoom()
        self._criar_entrada()
        # Aplicar depois de mapear: o Tk troca o HWND nativo na criação.
        for janela in (self.sombra, self.realce, self.borda, self.zoom):
            if janela is not None:
                janela.after(0, lambda j=janela: _ignorar_mouse(j))
        # A entrada SEMPRE precisa ser a última no topo: é ela que recebe
        # mouse/teclado. Elevá-la depois do desenho evita que o marquee roube
        # os cliques.
        self._instalar_eventos()
        self._redesenhar()
        self.entrada.lift()
        self.canvas_entrada.focus_force()

    # ---------------------------------------------------------------- janelas
    def _criar_janela(self, alfa: float):
        janela = tk.Toplevel(self.root)
        janela.overrideredirect(True)
        janela.attributes("-topmost", True)
        janela.attributes("-transparentcolor", "black")
        janela.attributes("-alpha", alfa)
        janela.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        canvas = tk.Canvas(janela, bg="black", highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        return janela, canvas

    def _criar_zoom(self) -> None:
        if not self.mostrar_zoom:
            self.zoom = None
            self.zoom_label = None
            return
        self.zoom = tk.Toplevel(self.root)
        self.zoom.overrideredirect(True)
        self.zoom.attributes("-topmost", True)
        self.zoom.attributes("-alpha", 0.99)  # layered, para ignorar o mouse
        titulo = tk.Label(
            self.zoom,
            text="Zoom 2x",
            bg=self.cor_selecao,
            fg=COR_TEXTO,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        titulo.pack(fill="x")
        self.zoom_label = tk.Label(self.zoom, bg="black")
        self.zoom_label.pack()
        # Escondida até o primeiro movimento: sem isso a janela aparece vazia
        # no canto superior esquerdo (o "quadrado preto" que o usuário viu).
        self.zoom.withdraw()

    def _criar_entrada(self) -> None:
        """Janela invisível que captura mouse/teclado em toda a área virtual.

        As janelas visuais usam ``-transparentcolor``; no Windows as áreas
        transparentes deixam os cliques passar, então os eventos precisam de
        uma janela própria, sem cor transparente, por cima de todas.
        """
        self.entrada = tk.Toplevel(self.root)
        self.entrada.overrideredirect(True)
        self.entrada.attributes("-topmost", True)
        self.entrada.attributes("-alpha", 0.01)  # praticamente invisível
        self.entrada.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.canvas_entrada = tk.Canvas(
            self.entrada, bg="white", highlightthickness=0, bd=0
        )
        self.canvas_entrada.pack(fill="both", expand=True)
        self.entrada.lift()

    def _instalar_eventos(self) -> None:
        self.canvas_entrada.bind("<ButtonPress-1>", self._ao_pressionar)
        self.canvas_entrada.bind("<B1-Motion>", self._ao_arrastar)
        self.canvas_entrada.bind("<ButtonRelease-1>", self._ao_soltar)
        self.canvas_entrada.bind("<Double-Button-1>", self._ao_duplo_clique)
        self.canvas_entrada.bind("<ButtonPress-3>", lambda event: self.cancelar())
        self.canvas_entrada.bind("<Motion>", self._ao_mover)
        for widget in (self.canvas_entrada, self.entrada):
            widget.bind("<Key>", self._ao_tecla)

    # --------------------------------------------------------------- conversões
    def _global(self, event) -> tuple[int, int]:
        return event.x + self.vx, event.y + self.vy

    def _relativas(self) -> tuple[int, int, int, int]:
        if not self.regiao:
            return 0, 0, 0, 0
        r = self.regiao
        return r.x - self.vx, r.y - self.vy, r.direita - self.vx, r.inferior - self.vy

    @staticmethod
    def _dentro(gx: int, gy: int, r: Regiao) -> bool:
        return r.x - TOLERANCIA <= gx <= r.direita + TOLERANCIA and (
            r.y - TOLERANCIA <= gy <= r.inferior + TOLERANCIA
        )

    @staticmethod
    def _handle(gx: int, gy: int, r: Regiao) -> str | None:
        cantos = {
            "no": (r.x, r.y),
            "ne": (r.direita, r.y),
            "so": (r.x, r.inferior),
            "se": (r.direita, r.inferior),
            "n": ((r.x + r.direita) // 2, r.y),
            "s": ((r.x + r.direita) // 2, r.inferior),
            "o": (r.x, (r.y + r.inferior) // 2),
            "e": (r.direita, (r.y + r.inferior) // 2),
        }
        for nome, (hx, hy) in cantos.items():
            if abs(gx - hx) <= TOLERANCIA and abs(gy - hy) <= TOLERANCIA:
                return nome
        return None

    # ----------------------------------------------------------------- eventos
    def _ao_pressionar(self, event) -> None:
        gx, gy = self._global(event)
        self._cursor = (gx, gy)
        for botao in self.botoes:
            if (
                botao["x1"] <= event.x <= botao["x2"]
                and botao["y1"] <= event.y <= botao["y2"]
            ):
                self.estado = ("botao", botao["acao"])
                return

        r = self.regiao
        if r and self._dentro(gx, gy, r):
            alca = self._handle(gx, gy, r)
            if alca:
                self.estado = ("redim", (alca, Regiao(r.x, r.y, r.largura, r.altura)))
            else:
                self.estado = ("mover", (gx, gy, r.x, r.y))
        else:
            self.anchor = (gx, gy)
            self.regiao = Regiao(gx, gy, 1, 1)
            self.estado = ("criar", None)

        self._redesenhar()
        self._atualizar_zoom(gx, gy)

    def _ao_arrastar(self, event) -> None:
        if not self.estado:
            return
        gx, gy = self._global(event)
        self._cursor = (gx, gy)
        acao = self.estado[0]

        if acao == "criar" and self.anchor:
            ax, ay = self.anchor
            self.regiao = captura.limitar_regiao(
                Regiao(min(ax, gx), min(ay, gy), abs(gx - ax), abs(gy - ay))
            )
        elif acao == "mover":
            sx, sy, rx, ry = self.estado[1]
            atual = self.regiao
            self.regiao = captura.limitar_regiao(
                Regiao(rx + gx - sx, ry + gy - sy, atual.largura, atual.altura)
            )
        elif acao == "redim":
            alca, original = self.estado[1]
            esq, top, dir_, inf = (
                original.x,
                original.y,
                original.direita,
                original.inferior,
            )
            if "o" in alca:
                esq = min(gx, dir_ - 40)
            if "e" in alca:
                dir_ = max(gx, esq + 40)
            if "n" in alca:
                top = min(gy, inf - 30)
            if "s" in alca:
                inf = max(gy, top + 30)
            self.regiao = captura.limitar_regiao(Regiao(esq, top, dir_ - esq, inf - top))
        else:
            return

        self._redesenhar()
        self._atualizar_zoom(gx, gy)

    def _ao_soltar(self, event) -> None:
        if self.estado and self.estado[0] == "botao":
            acao = self.estado[1]
            for botao in self.botoes:
                if (
                    botao["x1"] <= event.x <= botao["x2"]
                    and botao["y1"] <= event.y <= botao["y2"]
                    and botao["acao"] == acao
                ):
                    if acao == "ler":
                        self.confirmar()
                    else:
                        self.cancelar()
                    return
        acao = self.estado[0] if self.estado else None
        self.estado = None
        self._redesenhar()
        # No MORT, soltar o mouse já cria a área (sem precisar de Enter).
        if self.confirmar_ao_soltar and acao in {"criar", "mover", "redim"}:
            self.confirmar()

    def _ao_duplo_clique(self, event) -> None:
        self.confirmar()

    def _ao_mover(self, event) -> None:
        """Mostra o cursor e as guias na posição atual do mouse."""
        gx, gy = self._global(event)
        anterior = self._cursor
        self._cursor = (gx, gy)
        alca = self._handle(gx, gy, self.regiao) if self.regiao else None
        if alca:
            cursor = CURSORES.get(alca, "crosshair")
        elif self.regiao and self._dentro(gx, gy, self.regiao):
            cursor = "fleur"
        else:
            cursor = "crosshair"
        self.canvas_entrada.config(cursor=cursor)
        # Durante o arraste o _ao_arrastar já redesenha. Fora dele, atualiza as
        # guias amarelas e a etiqueta para o usuário ver onde está apontando.
        if self.estado is None and anterior != self._cursor:
            self._redesenhar()

    def _ao_tecla(self, event) -> None:
        if event.keysym in ("Return", "KP_Enter"):
            self.confirmar()
        elif event.keysym == "Escape":
            self.cancelar()
        elif event.keysym == "Delete":
            self.regiao = None
            self.estado = None
            self._redesenhar()

    # ---------------------------------------------------------------- desenho
    def _redesenhar(self) -> None:
        self.canvas_sombra.delete("all")
        self.canvas_realce.delete("all")
        self.canvas.delete("all")
        self.botoes = []
        self._borda_item = None

        if not self.regiao or self.regiao.largura < 2 or self.regiao.altura < 2:
            self.canvas_sombra.create_rectangle(
                0, 0, self.vw, self.vh, fill=COR_SOMBRA, outline=""
            )
            self.canvas.create_text(
                self.vw // 2, self.vh // 2,
                text="Clique e arraste para selecionar a região",
                fill=COR_TEXTO, font=("Segoe UI", 14, "bold"),
            )
            return

        x1, y1, x2, y2 = self._relativas()

        # Escurece apenas o que está fora da seleção.
        self.canvas_sombra.create_rectangle(0, 0, self.vw, y1, fill=COR_SOMBRA, outline="")
        self.canvas_sombra.create_rectangle(0, y2, self.vw, self.vh, fill=COR_SOMBRA, outline="")
        self.canvas_sombra.create_rectangle(0, y1, x1, y2, fill=COR_SOMBRA, outline="")
        self.canvas_sombra.create_rectangle(x2, y1, self.vw, y2, fill=COR_SOMBRA, outline="")

        # Realce azul da área: janela própria com alpha maior que a escurecedora,
        # para o azul continuar visível mesmo sobre uma tela escura (visual da
        # seleção do Windows, com o conteúdo ainda legível através dele).
        self.canvas_realce.create_rectangle(
            x1, y1, x2, y2, fill=self.cor_selecao, outline=""
        )

        # Marquee: contorno azul sólido + tracejado branco animado por cima.
        # Mesmo entre um ponto e outro do tracejado a área continua demarcada.
        self.canvas.create_rectangle(
            x1, y1, x2, y2, outline=self.cor_selecao, width=4
        )
        self._borda_item = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            outline=COR_ALCA, width=2,
            dash=(6, 4), dashoffset=self._passo_ants,
        )
        if self._id_ants is None:
            self._animar_selecao()

        # Guias amarelas no cursor mostram exatamente onde o mouse está.
        if self._cursor:
            cgx, cgy = self._cursor[0] - self.vx, self._cursor[1] - self.vy
            self.canvas.create_line(
                cgx, 0, cgx, self.vh, fill=COR_GUIA, width=1, dash=(4, 6)
            )
            self.canvas.create_line(
                0, cgy, self.vw, cgy, fill=COR_GUIA, width=1, dash=(4, 6)
            )
            self.canvas.create_oval(
                cgx - 6, cgy - 6, cgx + 6, cgy + 6,
                outline=COR_GUIA, width=2,
            )

        for hx, hy in (
            (x1, y1),
            (x2, y1),
            (x1, y2),
            (x2, y2),
            ((x1 + x2) // 2, y1),
            ((x1 + x2) // 2, y2),
            (x1, (y1 + y2) // 2),
            (x2, (y1 + y2) // 2),
        ):
            self.canvas.create_rectangle(
                hx - 6, hy - 6, hx + 6, hy + 6,
                fill=COR_ALCA, outline=COR_BORDA, width=2,
            )

        # Etiqueta acompanha o cursor: mostra a área e onde o mouse está.
        largura_etiqueta = 360
        if self._cursor:
            cx = self._cursor[0] - self.vx + 18
            cy = self._cursor[1] - self.vy + 18
        else:
            cx, cy = x1, y1 - 30
        cx = max(0, min(cx, self.vw - largura_etiqueta - 4))
        cy = max(0, min(cy, self.vh - 30))
        self.canvas.create_rectangle(
            cx, cy, cx + largura_etiqueta, cy + 26,
            fill=self.cor_selecao, outline=COR_ALCA, width=1,
        )
        texto = (
            f"X {self.regiao.x}  Y {self.regiao.y}   "
            f"{self.regiao.largura} x {self.regiao.altura}"
        )
        if self._cursor:
            texto += (
                f"   |   Cursor X {self._cursor[0]} Y {self._cursor[1]}"
            )
        self.canvas.create_text(
            cx + 8, cy + 13,
            anchor="w",
            fill=COR_TEXTO,
            font=("Segoe UI", 10, "bold"),
            text=texto,
        )

        self._desenhar_botoes(x1, y1, x2, y2)

    def _desenhar_botoes(self, x1: int, y1: int, x2: int, y2: int) -> None:
        largura, altura = 110, 26
        # Recua 12 px da borda para não cobrir a alça de redimensionamento.
        bx2 = min(x2 - 12, self.vw)
        by2 = min(y2, self.vh)
        bx1 = max(bx2 - (largura * 2 + 8), 0)
        by1 = max(by2 - altura - 8, 0)
        if by1 < y1 and y1 - altura - 8 >= 0:
            by1 = y1 - altura - 8
            by2 = by1 + altura

        especificacoes = (
            (bx1, "ler", "Ler (Enter)", self.cor_selecao),
            (bx1 + largura + 8, "cancelar", "Cancelar (Esc)", COR_CANCELAR),
        )
        for esquerda, acao, texto, cor in especificacoes:
            direita = esquerda + largura
            if direita > self.vw:
                continue
            self.canvas.create_rectangle(
                esquerda, by1, direita, by2, fill=cor, outline="#FFFFFF"
            )
            self.canvas.create_text(
                (esquerda + direita) // 2,
                (by1 + by2) // 2,
                text=texto,
                fill="white",
                font=("Segoe UI", 8, "bold"),
            )
            self.botoes.append(
                {"x1": esquerda, "y1": by1, "x2": direita, "y2": by2, "acao": acao}
            )

    # ------------------------------------------------------- marching ants
    def _animar_selecao(self) -> None:
        """Anima o tracejado da moldura, como a seleção de arquivos do Windows."""
        self._id_ants = None
        if self._finalizado:
            return
        self._passo_ants = (self._passo_ants + 1) % 12
        if self._borda_item is not None:
            try:
                self.canvas.itemconfig(
                    self._borda_item, dashoffset=self._passo_ants
                )
            except tk.TclError:
                pass
        self._id_ants = self.root.after(70, self._animar_selecao)

    def _parar_animacao(self) -> None:
        identificador = getattr(self, "_id_ants", None)
        if identificador is not None:
            try:
                self.root.after_cancel(identificador)
            except (tk.TclError, ValueError):
                pass
        self._id_ants = None

    def _atualizar_zoom(self, gx: int, gy: int) -> None:
        if not self.zoom_label or not self.imagem_fundo:
            return
        rec_larg = self.zoom_largura // FATOR_ZOOM
        rec_alt = self.zoom_altura // FATOR_ZOOM
        esq = max(0, min(gx - rec_larg // 2, self.imagem_fundo.width - rec_larg))
        top = max(0, min(gy - rec_alt // 2, self.imagem_fundo.height - rec_alt))
        recorte = self.imagem_fundo.crop((esq, top, esq + rec_larg, top + rec_alt))
        recorte = recorte.resize(
            (self.zoom_largura, self.zoom_altura), Image.Resampling.LANCZOS
        )
        self._imagem_zoom = ImageTk.PhotoImage(recorte, master=self.root)
        self.zoom_label.configure(image=self._imagem_zoom)

        # Lupa fixa no canto superior direito: ela não acompanha o mouse e
        # portanto nunca cobre a área que o usuário está selecionando.
        px = max(self.vx, self.vx + self.vw - self.zoom_largura - 12)
        py = self.vy + 12
        if self.zoom.state() == "withdrawn":
            self.zoom.deiconify()
            # A janela foi mapeada agora: o HWND nativo só existe a partir daqui.
            self.zoom.after(0, lambda: _ignorar_mouse(self.zoom))
        # geometry("+x+y") sem tamanho é recalculada pelo Tk quando a imagem da
        # lupa muda de tamanho e a janela volta para +0+0. Fixar largura/altura
        # depois de update_idletasks mantém a lupa no canto superior direito.
        self.zoom.update_idletasks()
        larg, alt = self.zoom.winfo_reqwidth(), self.zoom.winfo_reqheight()
        self.zoom.geometry(f"{larg}x{alt}+{px}+{py}")
        self.zoom.lift()
        self.entrada.lift()  # a captura de mouse continua acima da lupa

    # -------------------------------------------------------------- finalize
    def _esconder(self) -> None:
        self._parar_animacao()
        for janela in (self.sombra, self.realce, self.borda, self.zoom, self.entrada):
            if janela is not None:
                janela.withdraw()

    def confirmar(self) -> None:
        if self._finalizado:
            return
        if not self.regiao or self.regiao.largura < 40 or self.regiao.altura < 30:
            return
        self._finalizado = True
        self._esconder()
        # after em vez de sleep: não congela a interface nem o callback de soltar.
        self.root.after(120, self._entregar)

    def _entregar(self) -> None:
        if self.callback:
            self.callback(self.regiao)
        self._destruir()

    def cancelar(self) -> None:
        if self._finalizado:
            return
        self._finalizado = True
        self._esconder()
        if self.callback:
            self.callback(None)
        self._destruir()

    def _destruir(self) -> None:
        self._parar_animacao()
        for janela in (self.sombra, self.realce, self.borda, self.zoom, self.entrada):
            if janela is not None:
                try:
                    janela.destroy()
                except tk.TclError:
                    pass
        self._imagem_zoom = None
        self.imagem_fundo = None
