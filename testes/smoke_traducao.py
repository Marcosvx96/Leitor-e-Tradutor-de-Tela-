"""Teste rápido do fluxo Traduzir -> exibir -> falar."""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import tkinter as tk  # noqa: E402

from leitor_em_voz_alta.app import Aplicativo  # noqa: E402


def main() -> None:
    root = tk.Tk()
    aplicativo = Aplicativo(root)
    aplicativo.texto_original = "Hello world, this is a test."
    aplicativo.texto_atual = aplicativo.texto_original
    aplicativo._exibir_texto(aplicativo.texto_original)
    aplicativo.idioma_detectado = "en"
    aplicativo.destino_var.set("Português")
    aplicativo.traduzir()

    def conferir() -> None:
        print("Texto traduzido:", aplicativo.texto_atual)
        print("Status:", aplicativo.status.get())
        if aplicativo.texto_atual == aplicativo.texto_original:
            raise SystemExit("Falha: a tradução não atualizou o painel.")
        aplicativo._fechar()

    root.after(7000, conferir)
    root.mainloop()
    print("Fluxo de tradução funcionou.")


if __name__ == "__main__":
    main()
