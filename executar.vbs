' Inicializador silencioso: abre o app sem janela de console.
Option Explicit
Dim shell, pasta, python, aplicativo
Set shell = CreateObject("WScript.Shell")
pasta = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
python = pasta & "\.venv\Scripts\pythonw.exe"
aplicativo = pasta & "\main.py"
shell.CurrentDirectory = pasta
shell.Run """" & python & """ """ & aplicativo & """", 0, False
