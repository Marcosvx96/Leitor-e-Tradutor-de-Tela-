"""Abre a interface por 1,5 s e fecha, para detectar erros de construção."""

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
    root.after(1500, aplicativo._fechar)
    root.mainloop()
    print("UI construída e fechada sem erros.")


if __name__ == "__main__":
    main()
