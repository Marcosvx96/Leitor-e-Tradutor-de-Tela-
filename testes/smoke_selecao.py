"""Abre o overlay, testa mover/redimensionar e confirma uma região."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import tkinter as tk  # noqa: E402

from leitor_em_voz_alta import selecao as modulo_selecao  # noqa: E402
from leitor_em_voz_alta.captura import Regiao  # noqa: E402
from leitor_em_voz_alta.selecao import SeletorRegiao  # noqa: E402


def teste_edicao() -> None:
    """Modo clássico: arrasta, ajusta e confirma com Enter."""
    root = tk.Tk()
    root.withdraw()
    recebido: list[Regiao | None] = []

    def concluido(regiao: Regiao | None) -> None:
        recebido.append(regiao)
        print("Callback recebeu:", regiao)
        root.after(50, root.destroy)

    seletor = SeletorRegiao(
        root, Regiao(200, 100, 420, 240), True, concluido, confirmar_ao_soltar=False
    )
    root.update()  # garante que a janela de entrada foi mapeada
    canvas = seletor.canvas_entrada

    # Marquee: azul translúcido (texto continua legível) e sombra leve.
    assert seletor.canvas_realce.find_all()
    assert (
        seletor.canvas_realce.itemcget(seletor.canvas_realce.find_all()[0], "fill")
        == "#0078D4"
    )
    assert abs(float(seletor.realce.attributes("-alpha")) - 0.45) < 0.01
    assert abs(float(seletor.sombra.attributes("-alpha")) - 0.20) < 0.01
    # A lupa não pode aparecer vazia no canto da tela antes do primeiro movimento.
    assert seletor.zoom is not None and seletor.zoom.state() == "withdrawn"

    # Etiqueta em tempo real com posição e tamanho da seleção.
    def textos_no_canvas() -> list[str]:
        return [
            seletor.canvas.itemcget(item, "text")
            for item in seletor.canvas.find_all()
            if seletor.canvas.type(item) == "text"
        ]

    textos = textos_no_canvas()
    assert any("X 200" in texto and "420 x 240" in texto for texto in textos), textos

    # A etiqueta e as guias acompanham o mouse antes de qualquer clique.
    canvas.event_generate("<Motion>", x=305, y=165)
    root.update()
    textos = textos_no_canvas()
    assert any("X 305" in texto and "Y 165" in texto for texto in textos), textos
    linhas = [
        item for item in seletor.canvas.find_all()
        if seletor.canvas.type(item) == "line"
    ]
    assert len(linhas) >= 2

    # "Formigas andando": borda tracejada cujo deslocamento muda com o tempo.
    assert seletor._borda_item is not None
    assert seletor.canvas.itemcget(seletor._borda_item, "dash")
    offset = seletor.canvas.itemcget(seletor._borda_item, "dashoffset")
    seletor._parar_animacao()
    seletor._animar_selecao()
    root.update()
    assert seletor.canvas.itemcget(seletor._borda_item, "dashoffset") != offset
    seletor._parar_animacao()

    # Mover a região existente (bug clássico: desempacotar 4 valores em 5).
    canvas.event_generate("<ButtonPress-1>", x=400, y=220)
    canvas.event_generate("<B1-Motion>", x=450, y=240)
    canvas.event_generate("<ButtonRelease-1>", x=450, y=240)
    assert seletor.regiao == Regiao(250, 120, 420, 240), seletor.regiao

    # Redimensionar pela alça direita (a inferior direita fica sob os botões).
    canvas.event_generate("<ButtonPress-1>", x=670, y=240)
    canvas.event_generate("<B1-Motion>", x=710, y=240)
    canvas.event_generate("<ButtonRelease-1>", x=710, y=240)
    assert seletor.regiao == Regiao(250, 120, 460, 240), seletor.regiao

    root.after(200, seletor.confirmar)
    root.mainloop()
    assert recebido and recebido[0] == Regiao(250, 120, 460, 240)


def teste_confirmar_ao_soltar() -> None:
    """Modo MORT: soltar o mouse já entrega a região."""
    root = tk.Tk()
    root.withdraw()
    recebido: list[Regiao | None] = []

    def concluido(regiao: Regiao | None) -> None:
        recebido.append(regiao)
        print("Callback ao soltar recebeu:", regiao)
        root.after(50, root.destroy)

    seletor = SeletorRegiao(root, None, False, concluido, confirmar_ao_soltar=True)
    root.update()
    canvas = seletor.canvas_entrada
    canvas.event_generate("<ButtonPress-1>", x=150, y=120)
    canvas.event_generate("<B1-Motion>", x=550, y=320)
    canvas.event_generate("<ButtonRelease-1>", x=550, y=320)
    root.mainloop()
    assert recebido == [Regiao(150, 120, 400, 200)], recebido


def teste_opcoes_personalizadas() -> None:
    """Cor/opacidade e tamanho da lupa vêm das configurações salvas."""
    root = tk.Tk()
    root.withdraw()
    seletor = SeletorRegiao(
        root,
        Regiao(100, 80, 300, 180),
        True,
        lambda _: None,
        confirmar_ao_soltar=False,
        opcoes={
            "cor_selecao": "#FF0000",
            "opacidade_selecao": 70,
            "zoom_largura": 240,
            "zoom_altura": 140,
        },
    )
    root.update()
    try:
        assert (
            seletor.canvas_realce.itemcget(seletor.canvas_realce.find_all()[0], "fill")
            == "#FF0000"
        )
        assert abs(float(seletor.realce.attributes("-alpha")) - 0.70) < 0.01
        assert seletor.zoom_largura == 240 and seletor.zoom_altura == 140

        # Primeiro arraste: a imagem da lupa usa o tamanho configurado e a
        # janela fica fixa no canto superior direito da área virtual.
        seletor.canvas_entrada.event_generate("<ButtonPress-1>", x=120, y=90)
        seletor.canvas_entrada.event_generate("<B1-Motion>", x=200, y=120)
        root.update()
        assert seletor.zoom is not None and seletor.zoom.state() == "normal"
        assert seletor._imagem_zoom is not None
        assert seletor._imagem_zoom.width() == 240
        assert seletor._imagem_zoom.height() == 140
        esperado_x = seletor.vx + seletor.vw - 240 - 12
        esperado_y = seletor.vy + 12
        assert abs(seletor.zoom.winfo_x() - esperado_x) <= 2, (
            seletor.zoom.winfo_x(), esperado_x
        )
        assert abs(seletor.zoom.winfo_y() - esperado_y) <= 2, (
            seletor.zoom.winfo_y(), esperado_y
        )
    finally:
        seletor._parar_animacao()
        root.destroy()


def teste_sem_lupa() -> None:
    """Remover a lupa (mostrar_zoom=False) não cria a janela do zoom."""
    root = tk.Tk()
    root.withdraw()
    seletor = SeletorRegiao(root, None, False, lambda _: None)
    root.update()
    try:
        assert seletor.zoom is None and seletor.zoom_label is None
        assert seletor.imagem_fundo is None
    finally:
        seletor._parar_animacao()
        root.destroy()


def teste_mouse_nao_apaga_overlay() -> None:
    """_ignorar_mouse nunca deve reaplicar WS_EX_LAYERED (apagava o desenho)."""

    class Janela:
        def winfo_id(self) -> int:
            return 123

    class User32:
        def __init__(self) -> None:
            self.chamadas: list[int] = []

        def GetAncestor(self, hwnd, _indice):
            return hwnd

        def GetWindowLongW(self, _hwnd, _indice):
            return 0x04  # sem WS_EX_LAYERED: quem decide isso é o Tk

        def SetWindowLongW(self, _hwnd, _indice, estilo):
            self.chamadas.append(estilo)
            return 0

    usuario = User32()
    with mock.patch.object(modulo_selecao.ctypes.windll, "user32", usuario):
        modulo_selecao._ignorar_mouse(Janela())

    assert usuario.chamadas == [0x24], [
        hex(estilo) for estilo in usuario.chamadas
    ]
    print("Mouse atravessa o overlay sem apagar o marquee layered do Tk.")


def main() -> None:
    teste_mouse_nao_apaga_overlay()
    teste_opcoes_personalizadas()
    teste_sem_lupa()
    teste_edicao()
    teste_confirmar_ao_soltar()
    print("Overlay de seleção funcionou (mover, redimensionar e soltar para ler).")


if __name__ == "__main__":
    main()
