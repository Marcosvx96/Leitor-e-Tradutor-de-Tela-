"""Ponto de entrada do Leitor em Voz Alta.

Uso:
    .venv\\Scripts\\pythonw.exe main.py   (sem console)
    .venv\\Scripts\\python.exe main.py    (com console, para diagnóstico)
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes

    try:
        # Permite usar python.exe sem deixar a janela do cmd aberta.
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass

from leitor_em_voz_alta.app import main  # noqa: E402

if __name__ == "__main__":
    main()
