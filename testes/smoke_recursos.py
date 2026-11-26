"""Testes dos recursos novos: atalhos configuráveis, anexo de OCR, minimizar e fluxo."""

from __future__ import annotations

import asyncio
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from leitor_em_voz_alta import app as modulo_app  # noqa: E402
from leitor_em_voz_alta import atalhos, captura  # noqa: E402
from leitor_em_voz_alta.atalhos import COMBOS_PADRAO  # noqa: E402
from leitor_em_voz_alta.captura import Regiao  # noqa: E402


def teste_atalhos() -> None:
    assert atalhos.normalizar("Ctrl+Shift+S", "") == "<ctrl>+<shift>+s"
    assert atalhos.normalizar("SHIFT+ctrl+F12", "") == "<ctrl>+<shift>+<f12>"
    assert atalhos.normalizar("ç", "padrao") == "padrao"
    assert atalhos.normalizar("ctrl+alt", "padrao") == "padrao"
    todos = atalhos.normalizar_todos(
        {"ler": "Ctrl+Shift+L", "copiar": "Ctrl+Shift+L"}
    )
    assert todos["ler"] == "<ctrl>+<shift>+l"
    assert todos["copiar"] == COMBOS_PADRAO["copiar"]
    assert atalhos.formatar("<ctrl>+<shift>+s") == "CTRL + SHIFT + S"
    assert atalhos.formatar("<ctrl>+<shift>+<f12>") == "CTRL + SHIFT + F12"
    assert atalhos.formatar("<ctrl>+<page_up>") == "CTRL + PAGE UP"
    # Atalhos salvos pela versão com bug (VK puro) são migrados ao carregar.
    assert atalhos.normalizar("<45>", "") == "<insert>"
    assert atalhos.formatar("<45>") == "INSERT"
    print("Atalhos normalizados e sem colisão.")


def teste_gravacao_atalho() -> None:
    """Simula as teclas direto no callback do listener (sem tocar no teclado real)."""
    from pynput import keyboard

    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    janela = tk.Toplevel(root)
    janela.withdraw()
    botao = tk.Button(janela, text="CTRL + SHIFT + L")

    with mock.patch("pynput.keyboard.Listener") as lista:
        lista.return_value.canonical.side_effect = lambda tecla: tecla
        aplicativo._gravar_atalho(janela, "ler", botao)
        ao_press = lista.call_args.kwargs["on_press"]
        ao_press(keyboard.Key.ctrl)
        ao_press(keyboard.Key.shift)
        ao_press(keyboard.KeyCode.from_char("j"))
        root.update()

    assert aplicativo.cfg["atalhos"]["ler"] == "<ctrl>+<shift>+j"
    assert botao.cget("text") == "CTRL + SHIFT + J"

    # Teclas especiais chegam como VK; o botão precisa mostrar o nome real.
    with mock.patch("pynput.keyboard.Listener") as lista:
        lista.return_value.canonical.side_effect = lambda tecla: tecla
        aplicativo._gravar_atalho(janela, "ler", botao)
        ao_press = lista.call_args.kwargs["on_press"]
        ao_press(keyboard.Key.ctrl)
        ao_press(keyboard.Key.shift)
        ao_press(keyboard.KeyCode(vk=123))  # F12
        root.update()

    assert aplicativo.cfg["atalhos"]["ler"] == "<ctrl>+<shift>+<f12>"
    assert botao.cget("text") == "CTRL + SHIFT + F12"

    chamadas: list[int] = []
    acao = aplicativo._proteger(lambda: chamadas.append(1))
    acao()
    aplicativo.seletor = mock.Mock()
    acao()
    assert chamadas == [1], chamadas
    aplicativo.seletor = None
    aplicativo._fechar()
    print("Gravação de atalho e bloqueio durante seleção funcionaram.")


def teste_anexo_ocr() -> None:
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.cfg["traduzir_automaticamente"] = False
    aplicativo.cfg["anexar_texto_ocr"] = True

    with (
        mock.patch.object(aplicativo, "_mostrar_janela_apos_leitura"),
        mock.patch.object(modulo_app.traducao, "detectar", return_value="pt"),
        mock.patch.object(modulo_app.fala, "falar") as falar,
    ):
        aplicativo._texto_reconhecido("Primeira linha")
        aplicativo._texto_reconhecido("Segunda linha")
        aplicativo._texto_reconhecido("Ignorada", sequencia=aplicativo.ocr_sequencia - 1)

    painel = aplicativo.texto.get("1.0", "end-1c")
    assert painel == "Primeira linha\nSegunda linha", painel
    assert aplicativo.texto_original == painel
    assert [c.args[0] for c in falar.call_args_list] == [
        "Primeira linha",
        "Segunda linha",
    ]
    aplicativo._fechar()
    print("OCR anexado ao painel; leitura obsoleta descartada.")


def teste_preparacao_ocr_letras_pequenas() -> None:
    """A preparação não pode trocar `m/v/n` nem perder acentos em fonte pequena."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    from leitor_em_voz_alta import ocr

    alvo = "também ideal também ideal"
    fonte = None
    for nome in ("calibri.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            fonte = ImageFont.truetype("C:/Windows/Fonts/" + nome, 10)
            break
        except OSError:
            continue
    if fonte is None:
        print("OCR small text test skipped: no Windows font.")
        return

    imagem = Image.new("RGB", (520, 48), "white")
    ImageDraw.Draw(imagem).text((5, 12), alvo, font=fonte, fill="black")
    imagem = imagem.filter(ImageFilter.GaussianBlur(0.35))
    preparada = captura.preparar_para_ocr(imagem)
    assert preparada.width >= imagem.width * 2, preparada.size
    resultado = asyncio.run(ocr._reconhecer(captura.para_png(preparada), "pt-BR"))
    assert "também" in resultado and "ideal" in resultado, ascii(resultado)
    print("OCR preserved small letters and accents.")


def teste_substituicao() -> None:
    """Por padrão, uma nova leitura substitui o texto anterior do painel."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.cfg["anexar_texto_ocr"] = False
    aplicativo.cfg["traduzir_automaticamente"] = False

    with (
        mock.patch.object(aplicativo, "_mostrar_janela_apos_leitura"),
        mock.patch.object(modulo_app.traducao, "detectar", return_value="pt"),
        mock.patch.object(modulo_app.fala, "falar"),
    ):
        aplicativo._texto_reconhecido("Texto antigo")
        aplicativo._texto_reconhecido("Texto novo")

    painel = aplicativo.texto.get("1.0", "end-1c")
    assert painel == "Texto novo", painel
    assert aplicativo.texto_original == "Texto novo"
    aplicativo._fechar()
    print("Nova leitura substituiu o texto anterior (sem precisar de Limpar).")


def teste_vozes_neutrais() -> None:
    """Vozes pt-BR aparecem com nomes amigáveis e IDs válidos no cfg."""
    from leitor_em_voz_alta import fala
    from leitor_em_voz_alta.config import VOZ_NEURAL_PADRAO

    assert VOZ_NEURAL_PADRAO == "pt-BR-ThalitaMultilingualNeural"
    assert VOZ_NEURAL_PADRAO in fala.listar_vozes_neural()
    assert "natural" in fala.nome_amigavel_neural(VOZ_NEURAL_PADRAO)

    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    rotulo = f"🌟 {fala.nome_amigavel_neural(VOZ_NEURAL_PADRAO)}"
    aplicativo.voz_var.set(rotulo)
    aplicativo._ao_mudar_voz()
    assert aplicativo.cfg["modo_neural"] is True
    assert aplicativo.cfg["voz_neural"] == VOZ_NEURAL_PADRAO
    aplicativo._fechar()
    print("Voz neural pt-BR configurável por nome amigável.")


def teste_taskbar_estilos() -> None:
    """A janela overrideredirect ganha estilos de app normal na barra."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    user32 = mock.Mock()
    user32.IsWindow.return_value = 1
    get_long = mock.Mock(side_effect=[0x00000080, 0])  # TOOLWINDOW, sem SYSMENU
    set_long = mock.Mock()

    with (
        mock.patch.object(
            modulo_app, "_win32_api", return_value=(user32, get_long, set_long)
        ),
        mock.patch.object(aplicativo, "_janela_root_hwnd", return_value=123),
    ):
        assert aplicativo._preparar_taskbar() is True

    estilos = [chamada.args for chamada in set_long.call_args_list]
    assert (mock.ANY, modulo_app.GWL_EXSTYLE, modulo_app.WS_EX_APPWINDOW) in estilos
    assert (
        mock.ANY,
        modulo_app.GWL_STYLE,
        modulo_app.WS_SYSMENU | modulo_app.WS_MINIMIZEBOX,
    ) in estilos
    aplicativo._fechar()
    print("Janela preparada para aparecer na barra de tarefas.")


def teste_minimizar() -> None:
    """Caminho normal vai para a barra; fallback 🔊 continua disponível."""
    root = tk.Tk()
    aplicativo = modulo_app.Aplicativo(root)

    with (
        mock.patch.object(aplicativo, "_minimizar_win32", return_value=True),
        mock.patch.object(aplicativo, "_restaurar_win32", return_value=True),
    ):
        aplicativo.minimizar()
        root.update()
        assert aplicativo.minimizado
        assert aplicativo.botao_restaurar is None
        assert "barra" in aplicativo.status.get()

        aplicativo.restaurar()
        root.update()
        assert not aplicativo.minimizado
        assert aplicativo.botao_restaurar is None

    with mock.patch.object(aplicativo, "_minimizar_win32", return_value=False):
        aplicativo.minimizar()
    root.update()
    assert aplicativo.botao_restaurar is not None
    aplicativo.restaurar()
    root.update()
    assert not aplicativo.minimizado
    assert aplicativo.botao_restaurar is None

    aplicativo._fechar()
    print("Minimizar para a barra e o botao de restaurar funcionaram.")


def teste_z_order() -> None:
    """Após ler de outro app, a janela é inserida atrás dele (sem mexer no real)."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.abaixo_app = True
    aplicativo.app_alvo_hwnd = 12345

    with (
        mock.patch.object(aplicativo, "_janela_valida", return_value=True),
        mock.patch.object(aplicativo, "_colocar_abaixo_de") as colocar,
    ):
        aplicativo._mostrar_janela_apos_leitura()
        root.after(120, root.quit)
        root.mainloop()  # executa o reforço agendado 80 ms depois

    assert colocar.call_args_list == [mock.call(12345), mock.call(12345)]
    assert aplicativo.abaixo_app is False
    aplicativo._fechar()
    print("Janela posicionada atrás do app ativo.")


def teste_fluxo() -> None:
    """Seleção com soltar-do-mouse -> OCR -> painel -> fala."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.cfg["confirmar_ao_soltar"] = True
    aplicativo.cfg["anexar_texto_ocr"] = False
    aplicativo.cfg["traduzir_automaticamente"] = False
    aplicativo.cfg["regiao"] = None  # simula uma seleção nova, sem região salva
    chamadas: list[tuple] = []

    with (
        mock.patch.object(
            modulo_app.captura,
            "capturar_area",
            side_effect=lambda regiao, melhorar: chamadas.append(("captura", regiao))
            or "imagem",
        ),
        mock.patch.object(
            modulo_app.ocr,
            "reconhecer",
            side_effect=lambda imagem, idioma: chamadas.append(("ocr", idioma))
            or "Texto da região",
        ),
        mock.patch.object(
            modulo_app.fala,
            "falar",
            side_effect=lambda texto, cfg, aviso=None, progresso=None: chamadas.append(
                ("fala", texto)
            ),
        ),
        mock.patch.object(modulo_app.fala, "parar"),
        mock.patch.object(aplicativo, "_mostrar_janela_apos_leitura"),
    ):
        root.update()
        aplicativo.iniciar_selecao()
        root.update()
        seletor = aplicativo.seletor
        assert seletor is not None, aplicativo.status.get()
        canvas = seletor.canvas_entrada
        canvas.event_generate("<ButtonPress-1>", x=150, y=120)
        canvas.event_generate("<B1-Motion>", x=550, y=320)
        canvas.event_generate("<ButtonRelease-1>", x=550, y=320)

        def conferir() -> None:
            try:
                print("Chamadas:", chamadas)
                assert ("captura", Regiao(150, 120, 400, 200)) in chamadas, chamadas
                assert ("ocr", "pt-BR") in chamadas, chamadas
                assert ("fala", "Texto da região") in chamadas, chamadas
                assert aplicativo.texto.get("1.0", "end-1c") == "Texto da região"
            finally:
                aplicativo._fechar()

        root.after(1500, conferir)
        root.mainloop()
    print("Fluxo completo (soltar -> OCR -> fala) funcionou.")


def teste_cantos_arredondados() -> None:
    """A janela ganha região Win32 com cantos arredondados."""
    root = tk.Tk()
    root.geometry("480x320+0+0")
    root.update()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._regioes = {}
    largura, altura = root.winfo_width(), root.winfo_height()

    with (
        mock.patch.object(
            modulo_app, "_criar_regiao_arredondada", return_value=777
        ) as criar,
        mock.patch.object(modulo_app, "_aplicar_regiao", return_value=True) as aplicar,
    ):
        aplicativo._arredondar_janela(root, modulo_app.RAIO_JANELA)

    assert criar.call_args.args == (largura, altura, modulo_app.RAIO_JANELA)
    assert aplicar.call_args.args == (aplicativo._janela_root_hwnd(), 777)
    aplicativo._fechar()
    print("Cantos arredondados aplicados na janela.")


def teste_cache_memoria() -> None:
    """Releitura sai do cache em memória; nada fica em disco."""
    import edge_tts

    from leitor_em_voz_alta import fala

    chave = ("texto de teste", "voz", 0)
    fala._CACHE_NEURAL[chave] = b"x" * 2000
    with (
        mock.patch.object(edge_tts, "Communicate", side_effect=RuntimeError("rede")) as comun,
        mock.patch.object(fala, "_tocar_memoria") as tocar,
    ):
        fala.falar("texto de teste", {"modo_neural": True, "voz_neural": "voz"})
    comun.assert_not_called()
    assert tocar.call_count >= 1
    fala._TEMP.mkdir(parents=True, exist_ok=True)
    temporario = fala._TEMP / "voz_teste_limpeza.mp3"
    temporario.write_bytes(b"x" * 100)
    fala.limpar_cache()
    assert chave not in fala._CACHE_NEURAL
    assert not temporario.exists()
    print("Cache em memória reaproveitado e limpo sem gravar em disco.")


def teste_tema() -> None:
    """O botão alterna claro/escuro e repinta a janela inteira."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    inicial = aplicativo.tema_atual
    fundo_inicial = modulo_app.COR_FUNDO
    ponto_inicial = aplicativo.status_ponto.cget("fg")
    aplicativo.alternar_tema()
    assert aplicativo.tema_atual != inicial
    assert aplicativo.cfg["tema_escuro"] == (aplicativo.tema_atual == "escuro")
    assert modulo_app.COR_FUNDO != fundo_inicial
    assert root.cget("bg") == modulo_app.COR_FUNDO
    assert root.cget("bg") != fundo_inicial
    assert aplicativo.status_ponto.cget("fg") != ponto_inicial
    assert "Claro" in aplicativo.botao_tema.cget("text") or "Escuro" in (
        aplicativo.botao_tema.cget("text")
    )
    aplicativo.alternar_tema()
    assert aplicativo.tema_atual == inicial
    assert modulo_app.COR_FUNDO == fundo_inicial
    assert aplicativo.status_ponto.cget("fg") == ponto_inicial
    aplicativo._fechar()
    print("Alternância entre modo claro e escuro funcionou.")


def teste_limpar_cache_app() -> None:
    """Limpar texto também descarta áudio/cache temporário."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.texto.insert("1.0", "texto")
    with mock.patch.object(modulo_app.fala, "limpar_cache") as limpar:
        aplicativo.limpar_texto()
    limpar.assert_called_once_with()
    aplicativo._fechar()
    print("Limpar texto limpa também o áudio temporário.")


def teste_arraste() -> None:
    """A janela acompanha o mouse 1:1 e para nas bordas da tela."""
    root = tk.Tk()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._finalizar_arraste()
    root.geometry(f"+{aplicativo.root.winfo_screenwidth() // 4}+80")
    root.update()
    wx, wy = root.winfo_x(), root.winfo_y()

    aplicativo._iniciar_arraste(SimpleNamespace(x_root=200, y_root=200))
    aplicativo._arrastar(SimpleNamespace(x_root=220, y_root=220))
    root.update()
    assert (root.winfo_x(), root.winfo_y()) == (wx + 20, wy + 20)
    # Bug antigo: reler winfo_x() a cada motion acumulava o delta (240, 240).
    aplicativo._arrastar(SimpleNamespace(x_root=240, y_root=240))
    root.update()
    assert (root.winfo_x(), root.winfo_y()) == (wx + 40, wy + 40)

    aplicativo._arrastar(SimpleNamespace(x_root=10000, y_root=10000))
    root.update()
    assert root.winfo_x() == root.winfo_screenwidth() - root.winfo_width()
    assert root.winfo_y() == root.winfo_screenheight() - root.winfo_height()
    aplicativo._finalizar_arraste()
    aplicativo._fechar()
    print("Arraste 1:1 e limites da tela funcionaram.")


def teste_minimizar_mantem_audio() -> None:
    """Minimizar não pode interromper a leitura em andamento."""
    root = tk.Tk()
    aplicativo = modulo_app.Aplicativo(root)
    with (
        mock.patch.object(aplicativo, "parar") as parar,
        mock.patch.object(aplicativo, "_minimizar_win32", return_value=True),
        mock.patch.object(aplicativo, "_restaurar_win32", return_value=True),
    ):
        aplicativo.minimizar()
        root.update()
        parar.assert_not_called()
        aplicativo.restaurar()
        root.update()
    aplicativo._fechar()
    print("Minimizar preservou a leitura em andamento.")


def teste_nao_sempre_topo() -> None:
    """O app não deve ficar fixo na frente das outras janelas."""
    from leitor_em_voz_alta import config

    assert config.PADRAO["sempre_topo"] is False
    print("Janela não fica sempre no topo por padrão.")


def teste_retry_neural() -> None:
    """Erro transitório 'No audio' é repetido antes de usar voz local."""
    from leitor_em_voz_alta import fala

    tentativas: list[str] = []

    def transmitir(texto, voz, taxa, fila, estado):
        tentativas.append(voz)
        if len(tentativas) == 1:
            raise RuntimeError("No audio was received")
        estado["bruto"].extend(b"x" * 1000)
        estado["bytes"] = 1000
        fila.put(b"x" * 1000)

    def tocar(fila, configuracao, progresso, estado):
        while fila.get(timeout=2) is not None:
            pass
        estado["leitor_fim"] = True

    with (
        mock.patch.object(fala, "_transmitir", side_effect=transmitir),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar) as reproduziu,
    ):
        fala.falar("Texto", {"modo_neural": True, "voz_neural": "qualquer"})
    assert len(tentativas) >= 2
    assert reproduziu.call_count == 1
    print("Síntese neural repetiu após erro transitório.")


def teste_progresso_neural() -> None:
    """A UI recebe status de síntese antes do áudio ficar pronto."""
    from leitor_em_voz_alta import fala

    progressos: list[str] = []

    def neural(texto: str, configuracao: dict, progresso) -> None:
        progresso("Sintetizando voz neural... 1/1")
        progresso("Reproduzindo voz neural...")

    with (
        mock.patch.object(fala, "_falar_neural", side_effect=neural),
        mock.patch.object(fala, "_falar_sapi"),
    ):
        fala.falar(
            "Texto",
            {"modo_neural": True},
            progresso=progressos.append,
        )
    assert any("Sintetizando voz neural..." in item for item in progressos)
    assert "Reproduzindo voz neural..." in progressos
    print("Progresso da síntese neural enviado para a interface.")


def teste_limpeza_texto() -> None:
    """Símbolos decorativos não devem chegar ao TTS."""
    from leitor_em_voz_alta import fala

    assert (
        fala.limpar_para_leitura('*** Atenção ### "teste" (rápido) @@')
        == "Atenção teste rápido"
    )
    assert fala.limpar_para_leitura("---\n• item\n@usuario") == "item usuario"

    with mock.patch.object(fala, "_falar_sapi") as sapi:
        fala.falar("*** Oi ##", {"modo_neural": False})
    assert sapi.call_args.args[0] == "Oi"
    print("Símbolos (@, #, *, aspas, parênteses) removidos antes da leitura.")


def teste_streaming_neural() -> None:
    """O player recebe os bytes antes de a síntese inteira terminar."""
    import edge_tts

    from leitor_em_voz_alta import fala

    # Não pode haver um arquivo MP3 por frase: cada reinício do decoder engasga.
    assert not hasattr(fala, "_tocar_mp3")
    assert not hasattr(fala, "_novo_segmento")

    tocou = threading.Event()
    terminou = threading.Event()
    quadro = b"\xff\xf3d\xc4" + b"\x00" * (fala.QUADRO_MP3 - 4)
    total_quadros = 40

    class ComunicacaoFalsa:
        def __init__(self, texto, voz, **kwargs):
            pass

        async def stream(self):
            for indice in range(total_quadros):
                if fala.controle.parar_evento.is_set():
                    break
                yield {"type": "audio", "data": quadro}
                if indice == 4:
                    assert tocou.wait(3), "o player esperou a síntese inteira"
                await asyncio.sleep(0)
            terminou.set()

    def tocar(fila, configuracao, progresso, estado):
        # Consome o primeiro bloco enquanto o produtor ainda está gerando.
        tocou.set()
        while fila.get(timeout=3) is not None:
            pass
        estado["leitor_fim"] = True

    with (
        mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
    ):
        fala.falar(
            "Texto longo para reproduzir em streaming. " * 20,
            {"modo_neural": True, "voz_neural": "qualquer"},
        )
    assert terminou.is_set()
    print("Streaming começou a tocar antes do fim da síntese.")


def teste_fallback_voz() -> None:
    """Falha na neural nunca deixa o usuário sem áudio (cai para SAPI)."""
    from leitor_em_voz_alta import fala

    avisos: list[str] = []
    with (
        mock.patch.object(fala, "_falar_neural", side_effect=RuntimeError("sem rede")),
        mock.patch.object(fala, "_falar_sapi") as sapi,
    ):
        fala.falar(
            "Texto",
            {"modo_neural": True, "voz_sapi": "Microsoft Maria Desktop"},
            avisos.append,
        )
    assert sapi.call_count == 1
    assert sapi.call_args.args[1]["modo_neural"] is False
    assert "Voz neural indisponível" in avisos[0]
    print("Fallback automático para voz local funcionou.")


def teste_botao_colar() -> None:
    """O botão cola o texto copiado no painel, sem precisar de atalho."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    texto = "Texto que já estava copiado."
    root.clipboard_clear()
    root.clipboard_append(texto)
    root.update()

    with mock.patch.object(modulo_app.traducao, "detectar", return_value="pt"):
        aplicativo.botao_colar.invoke()
    root.update()

    assert aplicativo.texto.get("1.0", "end-1c") == texto
    assert aplicativo.texto_atual == texto
    assert "colado" in aplicativo.status.get()

    root.clipboard_clear()
    root.update()
    aplicativo.botao_colar.invoke()
    assert "vazia" in aplicativo.status.get()
    aplicativo._fechar()
    print("Botão de colar trouxe o texto da área de transferência.")


def teste_pcm_continuo() -> None:
    """Nenhuma amostra PCM pode ser descartada entre os blocos do decoder."""
    import numpy as np
    import sounddevice as sd

    from leitor_em_voz_alta import fala

    # Testes anteriores chamam fala.parar(); o player direto precisa de eventos limpos.
    fala.controle.parar_evento.clear()
    fala.controle.pausa_evento.clear()

    amostras = np.arange(30000, dtype=np.int16)
    pcm = amostras.tobytes()
    chamadas: list[dict] = []
    streams: list = []
    posicoes: list[str] = []

    class Entrada:
        def write(self, dados):
            return len(dados)

        def flush(self):
            pass

        def close(self):
            pass

    class Saida:
        """Entrega tudo de uma vez para a fila não competir com o callback."""

        def __init__(self, dados):
            self.dados = dados
            self.fim = False

        def read(self, quantidade):
            if self.fim:
                return b""
            self.fim = True
            return self.dados

    class Processo:
        def __init__(self, argumentos, **kwargs):
            chamadas.append(kwargs)
            self.stdin = Entrada()
            self.stdout = Saida(pcm)

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.frames = kwargs["blocksize"]
            self.saidas: list[np.ndarray] = []
            streams.append(self)

        def __enter__(self):
            # Deixa a thread leitora encher a fila antes de disparar o callback.
            time.sleep(0.05)
            # Chamadas suficientes para consumir todo o PCM e o sentinel.
            total = len(amostras) // (self.frames * 2) + 8
            for _ in range(total):
                saida = np.zeros((self.frames, 2), dtype="<i2")
                self.callback(saida, self.frames, 0.0, None)
                self.saidas.append(saida.copy())
                time.sleep(0.01)  # dá tempo à thread que publica a posição
            return self

        def __exit__(self, *argumentos):
            return False

    fila: queue.Queue = queue.Queue(maxsize=8)
    fila.put(None)  # o áudio já vem pronto do stdout falso
    with (
        mock.patch.object(fala, "_caminho_ffmpeg", return_value="ffmpeg"),
        mock.patch.object(fala.subprocess, "Popen", Processo),
        mock.patch.object(sd, "OutputStream", Stream),
    ):
        fala._tocar_stream(
            fila, {"volume": 100}, None,
            {"anunciado": False, "leitor_fim": False},
            destaque=lambda evento, valor: posicoes.append(
                threading.current_thread().name
            ),
        )

    # A posição sai de uma thread própria: nada é colocado na fila dentro do
    # callback de áudio (isso voltou a provocar travadas).
    assert posicoes, "nenhuma posição foi publicada"
    assert all(nome == "tts-posicao" for nome in posicoes), posicoes

    # O pré-carregamento evita o silêncio no primeiro callback.
    assert np.any(streams[0].saidas[0]), "primeiro callback saiu em silêncio"
    recebido = np.concatenate([s.reshape(-1) for s in streams[0].saidas])
    assert np.array_equal(recebido[: len(amostras)], amostras), "PCM perdido entre blocos"
    # O console do ffmpeg não pode aparecer na janela do app.
    assert chamadas[0]["creationflags"] & getattr(
        subprocess, "CREATE_NO_WINDOW", 0x08000000
    )
    print("Decoder contínuo sem perda de amostras e sem janela de terminal.")


def teste_nova_selecao_sem_regiao_antiga() -> None:
    """Cada 'Selecionar região' começa do zero, sem desenhar a região salva."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo.cfg["regiao"] = {
        "x": 10, "y": 20, "largura": 300, "altura": 150,
    }

    with (
        mock.patch.object(modulo_app, "SeletorRegiao") as construtor,
        mock.patch.object(modulo_app.fala, "parar"),
        mock.patch.object(modulo_app, "salvar"),
        mock.patch.object(aplicativo, "_foreground_hwnd", return_value=0),
        mock.patch.object(aplicativo, "_janela_root_hwnd", return_value=0),
    ):
        aplicativo.iniciar_selecao()

    # args: root, regiao_inicial, mostrar_zoom, callback, confirmar_ao_soltar
    assert construtor.call_args.args[1] is None, construtor.call_args
    assert aplicativo.cfg["regiao"]["x"] == 10  # Ctrl+Shift+L ainda a reutiliza
    aplicativo.seletor = None
    with mock.patch.object(modulo_app, "salvar"):
        aplicativo._fechar()
    print("Nova seleção começa sem a região antiga na tela.")


def teste_configuracoes_selecao() -> None:
    """A aba Seleção permite ajustar/remover a lupa, cor e opacidade."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    with (
        mock.patch.object(modulo_app, "salvar"),
        mock.patch.object(aplicativo, "_reinstalar_hotkeys"),
        mock.patch.object(
            modulo_app.fala, "listar_vozes_neural",
            return_value=["pt-BR-ThalitaNeural"],
        ),
    ):
        aplicativo.abrir_configuracoes()
        janelas = [
            w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
        ]
        janela = janelas[-1]

        def descendentes(widget):
            for filho in widget.winfo_children():
                yield filho
                yield from descendentes(filho)

        filhos = list(descendentes(janela))
        cadernos = [
            w for w in filhos
            if isinstance(w, ttk.Notebook)
        ]
        assert cadernos
        textos_abas = [
            cadernos[0].tab(aba, "text") for aba in cadernos[0].tabs()
        ]
        assert "Seleção" in textos_abas, textos_abas

        escalas = [w for w in filhos if isinstance(w, tk.Scale)]
        assert len(escalas) >= 4, len(escalas)
        escalas[0].set(240)  # largura da lupa
        escalas[1].set(140)  # altura da lupa
        escalas[2].set(70)   # opacidade do marquee

        entradas = [
            w for w in filhos
            if isinstance(w, tk.Entry) and str(w.get()).startswith("#")
        ]
        assert entradas
        entradas[0].delete(0, "end")
        entradas[0].insert(0, "#FF0000")

        checar_lupa = [
            w for w in filhos
            if isinstance(w, tk.Checkbutton)
            and "Mostrar lupa de zoom" in str(w.cget("text"))
        ]
        assert checar_lupa
        checar_lupa[0].deselect()

        botoes_salvar = [
            w for w in filhos
            if isinstance(w, tk.Button) and str(w.cget("text")) == "Salvar"
        ]
        assert botoes_salvar
        botoes_salvar[0].invoke()

        assert aplicativo.cfg["zoom_largura"] == 240
        assert aplicativo.cfg["zoom_altura"] == 140
        assert aplicativo.cfg["opacidade_selecao"] == 70
        assert aplicativo.cfg["cor_selecao"] == "#FF0000"
        assert aplicativo.cfg["mostrar_zoom"] is False

        # Valores salvos são aplicados na próxima seleção.
        with mock.patch.object(aplicativo, "iniciar_selecao") as iniciar:
            aplicativo._proteger(iniciar)()
        # _fechar() também chama salvar(): mantemos o mock até o fim para não
        # gravar as preferências alteradas pelo teste no config.json real.
        aplicativo._fechar()
    print("Aba Seleção configurou lupa, cor e opacidade do marquee.")


def teste_configuracoes_destaque() -> None:
    """A aba Leitura ajusta a cor e a opacidade do destaque da narração."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    with (
        mock.patch.object(modulo_app, "salvar"),
        mock.patch.object(aplicativo, "_reinstalar_hotkeys"),
        mock.patch.object(
            modulo_app.fala, "listar_vozes_neural",
            return_value=["pt-BR-ThalitaNeural"],
        ),
    ):
        aplicativo.abrir_configuracoes()
        janela = [
            w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
        ][-1]

        def descendentes(widget):
            for filho in widget.winfo_children():
                yield filho
                yield from descendentes(filho)

        caderno = [
            w for w in descendentes(janela) if isinstance(w, ttk.Notebook)
        ][0]
        textos = [caderno.tab(aba, "text") for aba in caderno.tabs()]
        assert "Leitura" in textos, textos
        aba_id = caderno.tabs()[textos.index("Leitura")]
        aba = caderno.nametowidget(aba_id)

        escalas = [w for w in descendentes(aba) if isinstance(w, tk.Scale)]
        assert len(escalas) == 1, len(escalas)
        escalas[0].set(85)

        entradas = [
            w for w in descendentes(aba)
            if isinstance(w, tk.Entry) and str(w.get()).startswith("#")
        ]
        assert entradas
        entradas[0].delete(0, "end")
        entradas[0].insert(0, "#00FF00")

        botoes = [
            w for w in descendentes(janela)
            if isinstance(w, tk.Button) and str(w.cget("text")) == "Salvar"
        ]
        assert botoes
        botoes[0].invoke()

        assert aplicativo.cfg["opacidade_destaque"] == 85
        assert aplicativo.cfg["cor_destaque"] == "#00FF00"
        esperado = modulo_app._misturar_cor(
            "#00FF00", modulo_app.COR_SUPERFICIE, 0.85
        )
        assert (
            aplicativo.texto.tag_cget("leitura_atual", "background") == esperado
        )
        aplicativo._fechar()
    print("Aba Leitura configurou cor e opacidade do destaque.")


def teste_destaque_incremental() -> None:
    """Os marcadores aparecem antes de a síntese do bloco terminar."""
    import edge_tts

    from leitor_em_voz_alta import fala

    texto = "Primeira frase. Segunda frase."
    eventos: list[tuple[str, object]] = []
    frase_recebida = threading.Event()
    liberar = threading.Event()

    class ComunicacaoFalsa:
        def __init__(self, texto, voz, rate=None, volume=None, boundary=None):
            self.texto = texto

        async def stream(self):
            yield {
                "type": "SentenceBoundary",
                "offset": 500000,
                "duration": 1000000,
                "text": "Primeira frase.",
            }
            frase_recebida.set()
            assert liberar.wait(3), "síntese não foi liberada pelo teste"
            for _ in range(4):
                yield {"type": "audio", "data": b"x" * 720}

        async def aclose(self):
            return None

    def tocar(fila, configuracao, progresso, estado, destaque=None):
        while True:
            item = fila.get(timeout=5)
            if item is None:
                break
        estado["leitor_fim"] = True

    def rodar() -> None:
        with (
            mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
            mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
        ):
            fala.falar(
                texto,
                {"modo_neural": True, "voz_neural": "qualquer"},
                destaque=lambda evento, valor: eventos.append((evento, valor)),
            )

    leitura = threading.Thread(target=rodar, name="teste-destaque", daemon=True)
    leitura.start()
    try:
        assert frase_recebida.wait(3), "o stream não entregou a primeira frase"
        limites = [valor for evento, valor in eventos if evento == "limites"]
        assert limites, "destaque não foi publicado durante a síntese"
        primeiros = [item["texto"] for item in limites[-1][:2]]
        assert primeiros == ["Primeira", "frase"], primeiros
        # O valor estimado (0.0) já foi substituído pelo offset real do evento.
        assert limites[-1][0]["offset"] > 0.0, limites[-1][0]
    finally:
        liberar.set()
        leitura.join(timeout=5)
    assert not leitura.is_alive(), "falar() não terminou"

    chave = (fala.limpar_para_leitura(texto), "qualquer", 0)
    assert fala._CACHE_MARCADORES[chave][0]["offset"] > 0.0
    print("Destaque publicado antes do fim da síntese.")


def teste_bloco_coletado_antes_de_tocar() -> None:
    """Áudio de um bloco só entra no player quando o bloco terminou."""
    import edge_tts

    from leitor_em_voz_alta import fala

    filas: list = []
    tocador_pronto = threading.Event()
    stream_fim = threading.Event()
    vazamentos: list[int] = []

    class ComunicacaoFalsa:
        def __init__(self, texto, voz, rate=None, volume=None, boundary=None):
            assert boundary == "WordBoundary", boundary

        async def stream(self):
            assert tocador_pronto.wait(3), "player não iniciou"
            for _ in range(3):
                yield {
                    "type": "WordBoundary",
                    "offset": 1000000,
                    "duration": 200000,
                    "text": "palavra",
                }
                yield {"type": "audio", "data": b"x" * 720}
                await asyncio.sleep(0.02)
            if filas and filas[0].qsize():
                vazamentos.append(filas[0].qsize())
            stream_fim.set()
            yield {"type": "audio", "data": b"x" * 720}

        async def aclose(self):
            return None

    def tocar(fila, configuracao, progresso, estado, destaque=None):
        filas.append(fila)
        tocador_pronto.set()
        assert stream_fim.wait(5), "stream não terminou"
        while True:
            if fila.get(timeout=5) is None:
                break
        estado["leitor_fim"] = True

    with (
        mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
    ):
        fala.falar(
            "palavra palavra palavra",
            {"modo_neural": True, "voz_neural": "qualquer"},
        )
    assert not vazamentos, vazamentos
    print("Bloco de áudio coletado antes de entrar no player.")


def teste_marcador_sequencial_sem_salto() -> None:
    """Uma marca que some do painel não pode grudar em palavra distante."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    try:
        aplicativo._exibir_texto("A B C D E A")
        limites = [
            {"texto": "A", "offset": 0.0, "duracao": 0.1},
            {"texto": "Z", "offset": 0.1, "duracao": 0.1},
            {"texto": "E", "offset": 0.2, "duracao": 0.1},
        ]
        aplicativo._mapear_destaque(limites)
        trechos = [
            aplicativo.texto.get(f"1.0+{inicio}c", f"1.0+{fim}c")
            for inicio, fim, _, _ in aplicativo._destaque_posicoes
        ]
        assert trechos == ["A", "E"], trechos

        # Republicar só com tempos novos não refaz a busca nem perde o índice.
        aplicativo._destaque_indice = 1
        novos = [
            {"texto": "A", "offset": 0.0, "duracao": 0.2},
            {"texto": "Z", "offset": 0.2, "duracao": 0.2},
            {"texto": "E", "offset": 0.4, "duracao": 0.2},
        ]
        aplicativo._ao_destaque("limites", novos)
        assert aplicativo._destaque_indice == 1
        assert aplicativo._destaque_posicoes[1][2] == 0.4
    finally:
        aplicativo._fechar()
    print("Marcações permanecem na sequência correta do painel.")


def teste_tema_configuracoes() -> None:
    """Janela criada depois do botão de tema também acompanha a paleta."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    if aplicativo.tema_atual != "escuro":
        aplicativo.alternar_tema()

    with mock.patch.object(
        modulo_app.fala, "listar_vozes_neural",
        return_value=["pt-BR-ThalitaNeural"],
    ):
        aplicativo.abrir_configuracoes()
    janelas = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    janela = janelas[-1]

    def descendentes(widget):
        for filho in widget.winfo_children():
            yield filho
            yield from descendentes(filho)

    botoes_atalho = [
        w for w in descendentes(janela)
        if isinstance(w, tk.Button) and "CTRL" in str(w.cget("text"))
    ]
    assert botoes_atalho
    cor_escura = modulo_app.COR_SUPERFICIE
    assert all(b.cget("bg") == cor_escura for b in botoes_atalho)

    # Textos que antes nasciam sem fg ficavam pretos mesmo no tema escuro.
    alvos_fonte = [
        w for w in descendentes(janela)
        if isinstance(w, (tk.Label, tk.Checkbutton))
        and str(w.cget("text")) in {
            "Idioma do OCR:", "Voz neural:", "Largura da lupa:",
            "Altura da lupa:", "Opacidade da área:", "Cor da área:",
            "Mostrar lupa de zoom (desmarque para remover)",
        }
    ]
    assert len(alvos_fonte) >= 7, [w.cget("text") for w in alvos_fonte]
    assert all(w.cget("fg") == modulo_app.COR_TEXTO for w in alvos_fonte)
    assert all(
        w.cget("fg").lower() != "#000000"
        for w in descendentes(janela)
        if isinstance(w, (tk.Label, tk.Checkbutton))
    )

    # O indicador do checkbox precisa de cor explícita: SystemWindow some
    # quando o fundo também é escuro.
    caixas = [w for w in descendentes(janela) if isinstance(w, tk.Checkbutton)]
    assert len(caixas) >= 7, len(caixas)
    azul_escuro = modulo_app.COR_AZUL
    super_escura = modulo_app.COR_SUPERFICIE_2
    assert all(w.cget("selectcolor") == azul_escuro for w in caixas)
    assert all(w.cget("activebackground") == super_escura for w in caixas)

    aplicativo.alternar_tema()
    cor_clara = modulo_app.COR_SUPERFICIE
    assert cor_clara != cor_escura
    assert all(b.cget("bg") == cor_clara for b in botoes_atalho)
    assert all(w.cget("fg") == modulo_app.COR_TEXTO for w in alvos_fonte)
    assert all(w.cget("selectcolor") == modulo_app.COR_AZUL for w in caixas)
    assert all(
        w.cget("activebackground") == modulo_app.COR_SUPERFICIE_2 for w in caixas
    )
    janela.destroy()
    aplicativo._fechar()
    print("Tema escuro cobre também a janela de configurações.")


def teste_posicao_nao_avanca_sem_pcm() -> None:
    """Silêncio de carregamento não pode fazer o destaque passar do áudio."""
    import numpy as np
    import sounddevice as sd

    from leitor_em_voz_alta import fala

    fala.controle.parar_evento.clear()
    fala.controle.pausa_evento.clear()
    posicoes: list[float] = []

    class Entrada:
        def write(self, dados):
            return len(dados)

        def flush(self):
            pass

        def close(self):
            pass

    class Saida:
        def read(self, quantidade):
            return b""

    class Processo:
        def __init__(self, argumentos, **kwargs):
            self.stdin = Entrada()
            self.stdout = Saida()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.frames = kwargs["blocksize"]

        def __enter__(self):
            time.sleep(0.05)
            for _ in range(14):
                saida = np.zeros((self.frames, 2), dtype="<i2")
                self.callback(saida, self.frames, 0.0, None)
                time.sleep(0.02)
            return self

        def __exit__(self, *argumentos):
            return False

    fila: queue.Queue = queue.Queue(maxsize=8)
    fila.put(None)
    with (
        mock.patch.object(fala, "_caminho_ffmpeg", return_value="ffmpeg"),
        mock.patch.object(fala.subprocess, "Popen", Processo),
        mock.patch.object(sd, "OutputStream", Stream),
    ):
        fala._tocar_stream(
            fila, {"volume": 100}, None,
            {"anunciado": False, "leitor_fim": False},
            destaque=lambda evento, valor: (
                posicoes.append(float(valor)) if evento == "posicao" else None
            ),
        )

    assert len(posicoes) <= 1, posicoes
    assert all(valor < 0.02 for valor in posicoes), posicoes
    print("Loading silence does not move the highlight forward.")


def teste_fila_sem_perda() -> None:
    """Fila cheia não pode descartar frames de MP3 no meio da leitura."""
    from leitor_em_voz_alta import fala

    fala.controle.parar_evento.clear()
    fila: queue.Queue = queue.Queue(maxsize=2)
    fila.put(b"a")
    fila.put(b"b")
    resultado: list[bool] = []

    def colocar() -> None:
        resultado.append(fala._colocar_na_fila(fila, b"c"))

    produtor = threading.Thread(target=colocar, daemon=True)
    produtor.start()
    time.sleep(0.15)
    assert not resultado, "frame descartado quando a fila encheu"
    assert fila.get() == b"a"
    produtor.join(timeout=2)
    assert resultado == [True]
    assert list(fila.queue) == [b"b", b"c"]
    # Player morto não pode prender a síntese numa fila que ninguém drena.
    fala._FALHA_PLAYER.set()
    try:
        assert fala._colocar_na_fila(fila, b"d") is False
    finally:
        fala._FALHA_PLAYER.clear()
    print("Fila cheia espera o player em vez de cortar o udio.")


def teste_texto_longo_completo() -> None:
    """Todos os blocos de um texto longo chegam ao player e ao cache."""
    import edge_tts

    from leitor_em_voz_alta import fala

    frase = (
        "Esta e uma frase longa o bastante para ocupar parte de um bloco. "
        "A leitura continua ate o final do texto sem perder nenhuma parte. "
    )
    texto = (frase * 60).strip()
    partes = fala.dividir_texto(texto, limite=fala.BLOCO_TTS)
    assert len(partes) > 2
    enviados: list[str] = []
    recebidos: list[bytes] = []

    class ComunicacaoFalsa:
        def __init__(self, texto_, voz, **kwargs):
            enviados.append(texto_)

        async def stream(self):
            yield {"type": "WordBoundary", "text": enviados[-1]}
            for _ in range(4):
                yield {"type": "audio", "data": b"x" * 720}

        async def aclose(self):
            return None

    def tocar(fila, configuracao, progresso, estado):
        while True:
            item = fila.get(timeout=3)
            if item is None:
                break
            recebidos.append(item)
        estado["leitor_fim"] = True

    with (
        mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
    ):
        fala.falar(texto, {"modo_neural": True, "voz_neural": "qualquer"})

    assert enviados == partes, "algum bloco do texto longo nao foi sintetizado"
    assert recebidos and b"".join(recebidos) == b"x" * (720 * 4 * len(partes))
    chave = (fala.limpar_para_leitura(texto), "qualquer", 0)
    assert len(fala._CACHE_NEURAL[chave]) == 720 * 4 * len(partes)
    print("Texto longo sintetizado por completo, bloco a bloco.")


def teste_deslocamento_bloco_usa_audio_real() -> None:
    """O próximo bloco começa após o áudio real, não após o último marcador."""
    import edge_tts

    from leitor_em_voz_alta import fala

    frase = "Primeira palavra e segunda palavra neste bloco de leitura. "
    texto = (frase * 8).strip()
    assert len(fala.dividir_texto(texto, limite=fala.BLOCO_TTS)) >= 2
    eventos: list[tuple[str, object]] = []

    class ComunicacaoFalsa:
        def __init__(self, texto_, voz, **kwargs):
            return None

        async def stream(self):
            yield {
                "type": "WordBoundary",
                "offset": 0,
                "duration": 1_000_000,  # 0,1 s de marcador
                "text": "palavra",
            }
            yield {"type": "audio", "data": b"x" * fala.MP3_BYTES_POR_SEGUNDO}

        async def aclose(self):
            return None

    def tocar(fila, configuracao, progresso, estado, destaque=None):
        while True:
            if fila.get(timeout=5) is None:
                break
        estado["leitor_fim"] = True

    def registrar(evento: str, valor) -> None:
        eventos.append((evento, valor))

    with (
        mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
    ):
        fala.falar(
            texto,
            {"modo_neural": True, "voz_neural": "qualquer"},
            destaque=registrar,
        )

    finais = [valor for evento, valor in eventos if evento == "limites"][-1]
    # Cada bloco tem 1 s de MP3, mas o marcador termina em 0,1 s. Usar o
    # marcador adiantaria o segundo bloco em quase 0,9 s.
    assert len(finais) >= 2, finais
    assert abs(finais[1]["offset"] - 1.0) < 1e-6, finais[:2]
    print("Block offsets follow real MP3 duration.")


def teste_restante_sem_espacos() -> None:
    """Fronteiras do edge-tts sem espaço não podem silenciar o fallback."""
    from leitor_em_voz_alta import fala

    original = "Primeira frase. Segunda frase. Terceira frase. Quarta frase."
    falado = "Primeira frase.Segunda frase."
    assert fala._restante_apos(original, falado) == "Terceira frase. Quarta frase."
    print("Resto do texto localizado apesar das fronteiras sem espaco.")


def teste_tema_primeira_abertura() -> None:
    """Começar em qualquer tema não pode deixar cores presas na abertura."""
    # Cores repetidas entre paletas/extras quebram o mapa reverso: uma cor
    # criada no escuro pode ser interpretada como outra coisa no claro.
    todas = []
    for paleta in modulo_app.PALETAS.values():
        todas.extend(paleta.values())
    todas.extend(modulo_app._EXTRAS_CLARO.values())
    todas.extend(modulo_app._EXTRAS_ESCO.values())
    assert len(todas) == len(set(todas)), "cor duplicada no mapa de temas"

    for inicial in ("escuro", "claro"):
        cfg = {
            "tema_escuro": inicial == "escuro",
            "sempre_topo": False,
            "atalhos": {},
        }
        with (
            mock.patch.object(modulo_app, "carregar", return_value=cfg),
            mock.patch.object(modulo_app, "salvar"),
        ):
            root = tk.Tk()
            root.withdraw()
            aplicativo = modulo_app.Aplicativo(root)
            assert aplicativo.texto.cget("bg") == modulo_app.COR_SUPERFICIE
            assert aplicativo.texto.cget("fg") == modulo_app.COR_TEXTO
            aplicativo.alternar_tema()
            assert aplicativo.texto.cget("bg") == modulo_app.COR_SUPERFICIE
            assert aplicativo.texto.cget("fg") == modulo_app.COR_TEXTO
            assert root.cget("bg") == modulo_app.COR_FUNDO
            aplicativo._fechar()
    print("Tema correto desde a primeira abertura nos dois modos.")


def teste_ajustes_finos() -> None:
    """Botão de configurações visível, fonte branca no escuro e caixa maior."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    botao = getattr(aplicativo, "botao_configuracoes", None)
    assert botao is not None and botao.winfo_manager() == "grid"
    assert "Configura" in str(botao.cget("text"))
    assert str(botao.cget("command")), "botão de configurações sem ação"
    assert aplicativo.texto.cget("height") >= 14

    if aplicativo.tema_atual != "escuro":
        aplicativo.alternar_tema()
    assert modulo_app.COR_TEXTO == "#FFFFFF"
    def descendentes(widget):
        for filho in widget.winfo_children():
            yield filho
            yield from descendentes(filho)

    botoes_contorno = [
        w for w in descendentes(root)
        if isinstance(w, tk.Button) and getattr(w, "_cor_hover", None)
    ]
    assert any(
        b.cget("fg") == modulo_app.COR_BOTAO_CONTORNO_TEXTO
        for b in botoes_contorno
    )
    aplicativo.alternar_tema()
    aplicativo._fechar()
    print("Configurações visíveis, fonte branca no escuro e caixa de texto maior.")


def teste_destaque_espera_palavra_longa() -> None:
    """A marcação só troca quando a palavra longa terminou de ser falada."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._exibir_texto("paralelepipedo consonante")
    aplicativo._ao_destaque(
        "limites",
        [
            {"texto": "paralelepipedo", "offset": 0.0, "duracao": 1.0},
            {"texto": "consonante", "offset": 1.0, "duracao": 0.4},
        ],
    )

    def selecionado() -> str:
        faixas = aplicativo.texto.tag_ranges("leitura_atual")
        assert faixas, "nenhuma palavra destacada"
        return aplicativo.texto.get(faixas[0], faixas[1])

    aplicativo._ao_destaque("posicao", 0.0)
    assert selecionado() == "paralelepipedo"
    # Dentro da tolerância de início, o código antigo já pulava para a próxima.
    aplicativo._ao_destaque("posicao", 0.98)
    assert selecionado() == "paralelepipedo", selecionado()
    aplicativo._ao_destaque("posicao", 1.0)
    assert selecionado() == "consonante", selecionado()

    # Se uma republicação empurrar a palavra atual para o futuro, o destaque
    # não pode ficar preso nela esperando o novo intervalo.
    aplicativo._ao_destaque(
        "limites",
        [
            {"texto": "paralelepipedo", "offset": 0.2, "duracao": 0.8},
            {"texto": "consonante", "offset": 1.4, "duracao": 0.4},
        ],
    )
    assert selecionado() == "paralelepipedo", selecionado()
    aplicativo._ao_destaque("posicao", 1.4)
    assert selecionado() == "consonante", selecionado()
    aplicativo._fechar()
    print("Long word highlight waits until the word ends.")


def teste_boundary_agrupado_preserva_sequencia() -> None:
    """Um WordBoundary com 'US$ 300' gera marcas para as duas palavras."""
    import queue
    import edge_tts

    from leitor_em_voz_alta import fala

    class ComunicacaoFalsa:
        def __init__(self, texto, voz, **kwargs):
            return None

        async def stream(self):
            yield {
                "type": "WordBoundary",
                "offset": 0,
                "duration": 1_000_000,
                "text": "Sim",
            }
            yield {
                "type": "WordBoundary",
                "offset": 1_000_000,
                "duration": 2_000_000,
                "text": "US$ 300",
            }
            yield {
                "type": "WordBoundary",
                "offset": 3_000_000,
                "duration": 1_000_000,
                "text": "rendem",
            }
            yield {"type": "audio", "data": b"x" * 6000}

        async def aclose(self):
            return None

    # Testes anteriores chamam fala.parar(); o stream precisa dos eventos limpos.
    fala.controle.parar_evento.clear()
    fala.controle.pausa_evento.clear()
    estado = {
        "bytes": 0, "falado": "", "bruto": bytearray(), "limites": [],
        "frases": [], "duracao": 0.0, "pedacos": [], "coletar": True,
    }
    with mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa):
        fala._transmitir(
            "Sim US$ 300 rendem", "qualquer", 0, queue.Queue(maxsize=64), estado
        )

    marcadores = estado["por_palavra"]
    assert [item["texto"] for item in marcadores] == [
        "Sim", "US", "300", "rendem",
    ], marcadores
    assert abs(marcadores[1]["offset"] - 0.1) < 1e-6
    assert abs(marcadores[2]["offset"] - marcadores[1]["offset"]) < 0.2
    assert abs(marcadores[3]["offset"] - 0.3) < 1e-6

    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._exibir_texto("Sim US$ 300 rendem")
    aplicativo._mapear_destaque(marcadores)
    trechos = [
        aplicativo.texto.get(f"1.0+{inicio}c", f"1.0+{fim}c")
        for inicio, fim, _, _ in aplicativo._destaque_posicoes
    ]
    assert trechos == ["Sim", "US", "300", "rendem"], trechos
    aplicativo._fechar()
    print("Grouped WordBoundary keeps the word sequence aligned.")


def teste_destaque_nao_trava_com_duracao_anormal() -> None:
    """Uma duração inválida não pode prender a marcação para sempre."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._exibir_texto("paralelepipedo consonante seguinte")
    aplicativo._ao_destaque(
        "limites",
        [
            {"texto": "paralelepipedo", "offset": 0.0, "duracao": 60.0},
            {"texto": "consonante", "offset": 1.0, "duracao": 0.4},
            {"texto": "seguinte", "offset": 1.4, "duracao": 0.4},
        ],
    )

    def selecionado() -> str:
        faixas = aplicativo.texto.tag_ranges("leitura_atual")
        assert faixas, "nenhuma palavra destacada"
        return aplicativo.texto.get(faixas[0], faixas[1])

    aplicativo._ao_destaque("posicao", 0.0)
    aplicativo._ao_destaque("posicao", 1.4)
    assert selecionado() == "paralelepipedo", selecionado()
    # Mesmo com duração de 60 s, a espera é limitada a 1,5 s.
    aplicativo._ao_destaque("posicao", 1.6)
    assert selecionado() == "seguinte", selecionado()
    aplicativo._fechar()
    print("Abnormal word duration cannot freeze the highlight.")


def teste_destaque_leitura() -> None:
    """A palavra falada fica destacada e acompanha o tempo do player."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._exibir_texto("Ola mundo esta e uma leitura teste.")
    limites = [
        {"texto": "Ola", "offset": 0.0, "duracao": 0.3},
        {"texto": "mundo", "offset": 0.3, "duracao": 0.3},
        {"texto": "esta", "offset": 0.6, "duracao": 0.3},
        {"texto": "leitura", "offset": 1.2, "duracao": 0.4},
    ]
    aplicativo._ao_destaque("limites", limites)
    assert len(aplicativo._destaque_posicoes) == 4

    def selecionado() -> str:
        faixas = aplicativo.texto.tag_ranges("leitura_atual")
        assert faixas, "nenhuma palavra destacada"
        return aplicativo.texto.get(faixas[0], faixas[1])

    aplicativo._ao_destaque("posicao", 0.0)
    assert selecionado().lower().startswith("ola")
    aplicativo._ao_destaque("posicao", 0.4)
    assert selecionado().lower().startswith("mundo")
    aplicativo._ao_destaque("posicao", 0.9)
    assert selecionado().lower().startswith("esta")
    aplicativo._ao_destaque("posicao", 1.25)
    assert selecionado().lower().startswith("leitura")

    # Mensagens antigas nao podem repintar uma leitura nova.
    aplicativo.sequencia += 1
    aplicativo._ao_destaque("fim", None)
    assert not aplicativo.texto.tag_ranges("leitura_atual")
    aplicativo._fechar()
    print("Destaque acompanha a leitura no painel de texto.")


def teste_destaque_neural() -> None:
    """O pipeline neural publica limites, posicao e fim sem tocar no audio."""
    from leitor_em_voz_alta import fala

    eventos: list[tuple[str, object]] = []
    limites = [{"texto": "Oi", "offset": 0.0, "duracao": 0.2}]

    def neural(texto, configuracao, progresso, destaque=None):
        destaque("limites", limites)
        destaque("posicao", 0.1)

    with (
        mock.patch.object(fala, "_falar_neural", side_effect=neural),
        mock.patch.object(fala, "_falar_sapi"),
    ):
        fala.falar(
            "Oi",
            {"modo_neural": True},
            destaque=lambda evento, valor: eventos.append((evento, valor)),
        )
    assert any(evento == "limites" for evento, _ in eventos)
    assert any(evento == "posicao" for evento, _ in eventos)
    assert eventos[-1][0] == "fim"
    print("Marcadores de voz publicados para a interface.")


def teste_destaque_sem_wordboundary() -> None:
    """Vozes que so enviam SentenceBoundary ainda destacam palavra por palavra.

    O texto do evento pode vir agrupado/incompleto e com acentos trocados por
    ``?``; as palavras devem sair do texto original enviado ao TTS.
    """
    import edge_tts

    from leitor_em_voz_alta import fala

    derivados = fala._marcadores_de_frases(
        "Olá mundo", [{"texto": "? ?", "offset": 0.0, "duracao": 2.0}]
    )
    assert [item["texto"] for item in derivados] == ["Olá", "mundo"]
    assert derivados[1]["offset"] > derivados[0]["offset"]
    # O edge-tts devolve "Ol?, isto ?" quando os acentos são corrompidos.
    assert fala._quantidade_palavras_evento("Ol?, isto ?") == 3

    class ComunicacaoFalsa:
        def __init__(self, texto, voz, rate=None, volume=None, boundary=None):
            self.texto = texto

        async def stream(self):
            yield {
                "type": "SentenceBoundary",
                "offset": 0,
                "duration": 2000000,
                "text": "Ol?, isto ?",
            }
            yield {"type": "audio", "data": b"x" * 720}
            yield {"type": "audio", "data": b"y" * 720}

        async def aclose(self):
            return None

    eventos: list[tuple[str, object]] = []

    def tocar(fila, configuracao, progresso, estado, destaque=None):
        while True:
            item = fila.get(timeout=2)
            if item is None:
                break
        estado["leitor_fim"] = True

    texto = "Olá, isto é"
    chave = (texto, "qualquer", 0)
    with (
        mock.patch.object(edge_tts, "Communicate", ComunicacaoFalsa),
        mock.patch.object(fala, "_tocar_stream", side_effect=tocar),
    ):
        fala.falar(
            texto,
            {"modo_neural": True, "voz_neural": "qualquer"},
            destaque=lambda evento, valor: eventos.append((evento, valor)),
        )
    marcadores = fala._CACHE_MARCADORES[chave]
    assert [item["texto"] for item in marcadores] == ["Olá", "isto", "é"]
    assert any(evento == "limites" for evento, _ in eventos)
    print("Marcadores derivados de SentenceBoundary funcionaram.")


def teste_app_recebe_destaque() -> None:
    """A UI repassa o callback de destaque para a leitura neural."""
    root = tk.Tk()
    root.withdraw()
    aplicativo = modulo_app.Aplicativo(root)
    aplicativo._exibir_texto("Ola mundo")
    vistos: list[bool] = []

    def falso_falar(texto, cfg, aviso=None, progresso=None, destaque=None):
        vistos.append(destaque is not None)
        destaque(
            "limites",
            [{"texto": "Ola", "offset": 0.0, "duracao": 0.3}],
        )
        destaque("posicao", 0.0)

    with (
        mock.patch.object(modulo_app.fala, "falar", side_effect=falso_falar),
        mock.patch.object(modulo_app.fala, "parar"),
        mock.patch.object(aplicativo, "_finalizado"),
    ):
        aplicativo._falar("Ola mundo")
        for _ in range(40):
            root.update()
            if vistos and aplicativo.texto.tag_ranges("leitura_atual"):
                break
            time.sleep(0.01)

    assert vistos == [True]
    assert aplicativo.texto.tag_ranges("leitura_atual")
    aplicativo._fechar()
    print("Interface recebe o destaque da narração.")


def main() -> None:
    teste_atalhos()
    teste_gravacao_atalho()
    teste_anexo_ocr()
    teste_preparacao_ocr_letras_pequenas()
    teste_substituicao()
    teste_vozes_neutrais()
    teste_taskbar_estilos()
    teste_minimizar()
    teste_minimizar_mantem_audio()
    teste_nao_sempre_topo()
    teste_cantos_arredondados()
    teste_tema()
    teste_nova_selecao_sem_regiao_antiga()
    teste_configuracoes_selecao()
    teste_configuracoes_destaque()
    teste_tema_configuracoes()
    teste_tema_primeira_abertura()
    teste_ajustes_finos()
    teste_botao_colar()
    teste_cache_memoria()
    teste_limpar_cache_app()
    teste_arraste()
    teste_pcm_continuo()
    teste_posicao_nao_avanca_sem_pcm()
    teste_fila_sem_perda()
    teste_texto_longo_completo()
    teste_deslocamento_bloco_usa_audio_real()
    teste_bloco_coletado_antes_de_tocar()
    teste_restante_sem_espacos()
    teste_retry_neural()
    teste_progresso_neural()
    teste_destaque_leitura()
    teste_destaque_espera_palavra_longa()
    teste_boundary_agrupado_preserva_sequencia()
    teste_destaque_nao_trava_com_duracao_anormal()
    teste_destaque_incremental()
    teste_marcador_sequencial_sem_salto()
    teste_destaque_neural()
    teste_destaque_sem_wordboundary()
    teste_app_recebe_destaque()
    teste_limpeza_texto()
    teste_streaming_neural()
    teste_fallback_voz()
    teste_z_order()
    teste_fluxo()
    print("Todos os recursos novos passaram.")


if __name__ == "__main__":
    main()
