@echo off
rem Gera o instalador do Leitor em Voz Alta (sem janela de console no app final).
cd /d "%~dp0.."
"%~dp0..\.venv\Scripts\python.exe" "%~dp0gerar_instalador.py"
echo.
pause
