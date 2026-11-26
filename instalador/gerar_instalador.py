"""Gera o instalador Windows do Leitor em Voz Alta.

Etapas:
1. PyInstaller cria a pasta dist/LeitorVozAlta (executável + dependências).
2. O ffmpeg.exe encontrado na máquina é copiado para dentro do pacote.
3. O Inno Setup compila o instalador final em instalador/Output/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent
DIST = RAIZ / "dist" / "LeitorVozAlta"
BUILD = RAIZ / "build"
ISS = AQUI / "setup.iss"


def executar(comando: list[Path | str], cwd: Path = RAIZ) -> None:
    print("+", " ".join(str(parte) for parte in comando))
    subprocess.run([str(parte) for parte in comando], cwd=str(cwd), check=True)


def localizar_python() -> Path:
    candidato = RAIZ / ".venv" / "Scripts" / "python.exe"
    return candidato if candidato.is_file() else Path(sys.executable)


def localizar_ffmpeg() -> Path:
    encontrado = shutil.which("ffmpeg")
    if encontrado:
        return Path(encontrado).resolve()
    candidatos = [
        Path.home() / "AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return candidato.resolve()
    raise SystemExit(
        "ffmpeg não encontrado. Instale o FFmpeg e tente novamente.\n"
        "No Windows: winget install Gyan.FFmpeg"
    )


def localizar_iscc() -> Path:
    """Encontra o compilador do Inno Setup 6 em locais comuns."""
    bases = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages",
    ]
    for base in bases:
        if not base.is_dir():
            continue
        encontrados = list(base.glob("**/ISCC.exe"))
        if encontrados:
            return encontrados[0]
    raise SystemExit(
        "Inno Setup 6 não encontrado. Instale com:\n"
        "  winget install --id JRSoftware.InnoSetup --source winget"
    )


def main() -> None:
    python = localizar_python()
    pyinstaller = python.parent / "pyinstaller.exe"
    if not pyinstaller.is_file():
        raise SystemExit(
            "PyInstaller não está instalado no ambiente. Execute:\n"
            f'  "{python}" -m pip install pyinstaller'
        )

    if DIST.exists():
        shutil.rmtree(DIST)
    BUILD.mkdir(parents=True, exist_ok=True)

    executar([
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", "LeitorVozAlta",
        "--icon", RAIZ / "icone.ico",
        "--distpath", RAIZ / "dist",
        "--workpath", BUILD / "pyinstaller",
        "--specpath", BUILD,
        "--collect-submodules", "winrt",
        RAIZ / "main.py",
    ])

    ffmpeg = localizar_ffmpeg()
    print(f"ffmpeg: {ffmpeg}")
    # O app resolve __file__ dentro de _internal e procura o ffmpeg ao lado do
    # pacote, então uma única cópia nessa pasta é suficiente.
    interno = DIST / "_internal"
    destino = interno / "ffmpeg.exe" if interno.is_dir() else DIST / "ffmpeg.exe"
    shutil.copy2(ffmpeg, destino)
    shutil.copy2(RAIZ / "icone.ico", DIST / "icone.ico")

    executar([localizar_iscc(), ISS])
    instalador = AQUI / "Output" / "LeitorVozAlta-Setup.exe"
    if not instalador.is_file():
        raise SystemExit("O Inno Setup não gerou o arquivo esperado.")
    print(f"\nInstalador pronto: {instalador}")


if __name__ == "__main__":
    main()
