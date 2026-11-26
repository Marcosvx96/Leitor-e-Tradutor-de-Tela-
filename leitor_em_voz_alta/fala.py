"""Leitura em voz alta: SAPI (offline, vozes do Windows) ou voz neural (online)."""

from __future__ import annotations

import asyncio
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .config import VOZ_NEURAL_PADRAO

SVSF_ASYNC = 1
SVSF_PURGE = 2
SVSF_NOT_XML = 16

# O edge-tts entrega MP3 em frames de 720 bytes. Em vez de fechar um arquivo por
# bloco (cada novo WMP reiniciava o decoder e engasgava no início da frase), o
# stream inteiro é alimentado a um único ffmpeg, que devolve PCM contínuo para
# a placa de som via sounddevice.
QUADRO_MP3 = 720
TAXA_PCM = 44100
BLOCO_PCM = 8192
# Bloco curto o bastante para ser baixado enquanto o anterior ainda toca.
BLOCO_TTS = 240
# O edge-tts entrega MP3 CBR de 48 kbps: 6000 bytes por segundo de áudio.
# O último marcador termina antes do fim real do bloco (há silêncio final),
# então contar só a duração dos marcadores adiantava os blocos seguintes.
MP3_BYTES_POR_SEGUNDO = 6000
MAX_CACHE_MEMORIA = 16


class FalhaNeural(Exception):
    """Voz neural indisponível (rede, ffmpeg, player de áudio)."""

    def __init__(self, mensagem: str, restante: str | None = None) -> None:
        super().__init__(mensagem)
        # Texto que ainda não foi falado quando a neural caiu.
        self.restante = restante


class _Controle:
    """Estado compartilhado para parar/pausar a leitura em andamento."""

    def __init__(self) -> None:
        self.parar_evento = threading.Event()
        self.pausa_evento = threading.Event()
        self.ocupado = False


controle = _Controle()

# Serializa as leituras: sínteses simultâneas no edge-tts costumam devolver
# "No audio was received" e travam o áudio por alguns segundos.
_BLOQUEIO_FALA = threading.Lock()

# Se o player morrer (dispositivo de áudio, ffmpeg), a thread que sintetiza não
# pode ficar presa para sempre esperando uma fila que ninguém mais drena.
_FALHA_PLAYER = threading.Event()

# Cache temporário em memória. Nada é gravado em disco; "Limpar texto" chama
# limpar_cache() e descarta tanto o áudio quanto os MP3s em reprodução.
_CACHE_NEURAL: dict[tuple[str, str, int], bytes] = {}
# Marcadores de palavra do edge-tts, guardados junto com o áudio em memória.
# São só metadados: o pipeline de áudio continua exatamente o mesmo.
_CACHE_MARCADORES: dict[tuple[str, str, int], list[dict]] = {}
_TEMP = Path(tempfile.gettempdir()) / "leitor_em_voz_alta"
# Legado das versões que gravavam em dados/: também é limpo por limpar_cache().
_DADOS = Path(__file__).resolve().parent.parent / "dados"


def _caminho_ffmpeg() -> str | None:
    """Encontra o ffmpeg mesmo se o atalho não herdou o PATH do WinGet."""
    encontrado = shutil.which("ffmpeg")
    if encontrado:
        return encontrado
    candidatos = [
        Path(__file__).resolve().parent.parent / "ffmpeg.exe",
        Path.home() / "AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe",
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return str(candidato)
    return None


def limpar_cache() -> None:
    """Descarta áudio em memória e arquivos temporários de reprodução."""
    _CACHE_NEURAL.clear()
    _CACHE_MARCADORES.clear()
    for pasta in (_TEMP, _DADOS):
        try:
            for antigo in pasta.glob("voz_*.mp3"):
                antigo.unlink(missing_ok=True)
        except OSError:
            pass
    antigo_cache = _DADOS / "cache_voz"
    try:
        for arquivo in antigo_cache.glob("*"):
            arquivo.unlink(missing_ok=True)
        antigo_cache.rmdir()
    except OSError:
        pass


def listar_vozes_sapi() -> list[str]:
    try:
        import win32com.client

        voz = win32com.client.Dispatch("SAPI.SpVoice")
        return [item.GetAttribute("Name") for item in voz.GetVoices()]
    except Exception:
        return []


# Vozes pt-BR verificadas com edge-tts 7.x. A lista estática serve de fallback
# quando não há internet para consultar o catálogo online.
VOZES_NEARAIS_PT_BR: dict[str, str] = {
    VOZ_NEURAL_PADRAO: "Thalita — feminina, natural (multilíngue)",
    "pt-BR-ThalitaNeural": "Thalita — feminina, natural",
    "pt-BR-FranciscaNeural": "Francisca — feminina, natural",
    "pt-BR-AntonioNeural": "Antonio — masculino, natural",
}


def listar_vozes_neural() -> list[str]:
    """IDs pt-BR; usa a lista estática se não conseguir consultar a internet."""
    try:
        import edge_tts

        async def buscar() -> list[str]:
            vozes = await edge_tts.list_voices()
            return [
                voz["ShortName"]
                for voz in vozes
                if voz["Locale"].lower() == "pt-br"
            ]

        online = asyncio.run(buscar())
    except Exception:
        online = []
    return list(dict.fromkeys([*VOZES_NEARAIS_PT_BR, *online]))


def nome_amigavel_neural(id_voz: str) -> str:
    """Rótulo legível para a UI; o JSON continua guardando o ID exato."""
    return VOZES_NEARAIS_PT_BR.get(id_voz, f"Neural: {id_voz}")


def limpar_para_leitura(texto: str) -> str:
    """Remove decorações que o TTS leria como ruído, mantendo o conteúdo."""
    if not texto:
        return ""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    linhas: list[str] = []
    for linha in texto.split("\n"):
        # Linhas feitas só de separadores (------, ******, ==== etc.).
        if re.fullmatch(r"[\s*#@_~=+\-•▪◦|>]+", linha):
            continue
        # Marcadores de lista no início da linha.
        linha = re.sub(r"^\s*[-–—•▪◦*+>]\s+", "", linha)
        linhas.append(linha)
    texto = "\n".join(linhas)

    # Negrito/itálico, hashtags, menções e símbolos repetidos.
    texto = re.sub(r"[*#@~^=_+]", " ", texto)
    # Aspas retas e curvas (o usuário não quer ouvir "aspas").
    texto = re.sub(r"[\"“”‘’«»`´]", " ", texto)
    # Apóstrofo só entre letras (mantém "d'água"/"don't", remove os soltos).
    texto = re.sub(r"(?<![A-Za-zÀ-ÿ])'|'(?![A-Za-zÀ-ÿ])", " ", texto)
    # Parênteses/colchetes são pausa, não devem ser pronunciados.
    texto = re.sub(r"[()\[\]{}]", " ", texto)
    texto = re.sub(r"\s*\|\s*", ", ", texto)
    texto = re.sub(r"\s*&\s*", " e ", texto)
    # "!!!", "???", "..." etc. viram uma única pontuação.
    texto = re.sub(r"([!?.,])\1+", r"\1", texto)
    # Descarta o que sobrou de simbologia sem deixar de ler números/datas.
    texto = re.sub(r"[^\w\sÀ-ÿ.,!?;:%$€£/\-']", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def dividir_texto(texto: str, limite: int = 180) -> list[str]:
    """Quebra o texto em pedaços pequenos, respeitando pontuação."""
    texto = " ".join(texto.split())
    if not texto:
        return []
    partes = re.split(r"(?<=[.!?;:])\s+", texto)
    resultado: list[str] = []
    atual = ""
    for parte in partes:
        while len(parte) > limite:
            pedaco = parte[:limite].rsplit(" ", 1)[0]
            if atual:
                resultado.append(atual)
                atual = ""
            resultado.append(pedaco)
            parte = parte[len(pedaco):].lstrip()
        if not atual:
            atual = parte
        elif len(atual) + len(parte) + 1 <= limite:
            atual += " " + parte
        else:
            resultado.append(atual)
            atual = parte
    if atual:
        resultado.append(atual)
    return resultado or [texto]


def _velocidade_sapi(fator: float) -> int:
    return max(-10, min(10, int(round((float(fator) - 1.0) * 8))))


def parar() -> None:
    controle.parar_evento.set()
    controle.pausa_evento.clear()


def pausar_continuar() -> bool:
    """Retorna True se ficou pausado, False se voltou a correr."""
    if controle.pausa_evento.is_set():
        controle.pausa_evento.clear()
        return False
    controle.pausa_evento.set()
    return True


def falar(
    texto: str,
    configuracao: dict,
    aviso: Callable[[str], None] | None = None,
    progresso: Callable[[str], None] | None = None,
    destaque: Callable[[str, object | None], None] | None = None,
) -> None:
    """Fala o texto. Chame dentro de uma thread de trabalho.

    ``destaque`` recebe ``("limites", [...])``, ``("posicao", segundos)`` e
    ``("fim", None)`` para a UI acompanhar a palavra falada sem tocar no áudio.
    """
    texto = limpar_para_leitura(texto)
    if not texto:
        return
    # Cancela a leitura anterior antes de esperar o lock.
    parar()
    with _BLOQUEIO_FALA:
        parar()
        controle.parar_evento = threading.Event()
        controle.pausa_evento = threading.Event()
        _FALHA_PLAYER.clear()
        controle.ocupado = True
        try:
            if configuracao.get("modo_neural"):
                if progresso:
                    progresso("Sintetizando voz neural...")
                try:
                    # Mantém a assinatura antiga quando não há destaque: vários
                    # testes e callers usam apenas os três argumentos originais.
                    if destaque is None:
                        _falar_neural(texto, configuracao, progresso)
                    else:
                        _falar_neural(texto, configuracao, progresso, destaque)
                except Exception as exc:
                    if aviso:
                        aviso(f"Voz neural indisponível ({exc}). Usando voz local.")
                    cfg_local = dict(configuracao, modo_neural=False)
                    restante = (
                        exc.restante if isinstance(exc, FalhaNeural) and exc.restante
                        else texto
                    )
                    _falar_sapi(restante, cfg_local)
            else:
                _falar_sapi(texto, configuracao)
        except Exception as exc:  # pywin32 / SAPI
            if aviso:
                aviso(f"Não foi possível reproduzir a voz: {exc}")
        finally:
            controle.ocupado = False
            if destaque:
                try:
                    destaque("fim", None)
                except Exception:
                    pass


def _falar_sapi(texto: str, configuracao: dict) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        voz = win32com.client.Dispatch("SAPI.SpVoice")
        nome = configuracao.get("voz_sapi", "")
        for item in voz.GetVoices():
            if item.GetAttribute("Name") == nome:
                voz.Voice = item
                break
        voz.Rate = _velocidade_sapi(configuracao.get("velocidade", 1.0))
        voz.Volume = int(configuracao.get("volume", 90))

        pausado_local = False
        for pedaco in dividir_texto(texto):
            if controle.parar_evento.is_set():
                break
            voz.Speak(pedaco, SVSF_ASYNC | SVSF_NOT_XML)
            while True:
                if controle.parar_evento.is_set():
                    break
                if controle.pausa_evento.is_set() and not pausado_local:
                    voz.Pause()
                    pausado_local = True
                elif not controle.pausa_evento.is_set() and pausado_local:
                    voz.Resume()
                    pausado_local = False
                if voz.Status.RunningState == 1 and not pausado_local:
                    break
                time.sleep(0.05)
            if pausado_local:
                voz.Resume()
                pausado_local = False
        voz.Speak("", SVSF_PURGE | SVSF_NOT_XML)
        voz = None
    finally:
        pythoncom.CoUninitialize()


def _restante_apos(texto: str, falado: str) -> str:
    """Descobre o que sobrou do texto usando os eventos de fronteira do TTS.

    O edge-tts concatena o texto das fronteiras sem espaço entre elas
    ("...fim.Próxima..."), então comparar com o original comum falha e o
    fallback local acabava ficando mudo. Aqui a comparação ignora espaços.
    """
    original = " ".join(texto.split())
    dito = " ".join(falado.split())
    if not dito:
        return texto
    compacto_original = "".join(original.split())
    compacto_dito = "".join(dito.split())
    # Posição de cada caractere do texto sem espaços no texto original.
    posicoes = [i for i, caractere in enumerate(original) if not caractere.isspace()]
    for fator in (1.0, 0.75, 0.5):
        tamanho = max(8, int(len(compacto_dito) * fator))
        trecho = compacto_dito[:tamanho]
        posicao = compacto_original.find(trecho)
        if posicao < 0:
            continue
        fim = posicao + len(trecho) - 1
        if fim < len(posicoes):
            return original[posicoes[fim] + 1:].strip()
        return ""
    # Nomes/valores podem ser normalizados pelo TTS; estima a parte não dita
    # em vez de devolver "" (que silenciava o resto de um texto longo).
    if len(compacto_dito) >= len(compacto_original) * 0.95:
        return ""
    proporcao = min(0.9, len(compacto_dito) / max(1, len(compacto_original)))
    return original[int(len(original) * proporcao):].strip()


def _ticks_para_segundos(valor) -> float:
    """edge-tts informa offsets/durações em unidades de 100 ns."""
    try:
        return max(0.0, float(valor or 0) / 10_000_000.0)
    except (TypeError, ValueError):
        return 0.0


def _palavras(texto: str) -> list[str]:
    return re.findall(r"\w+(?:['’]\w+)?", texto or "", re.UNICODE)


def _distribuir_palavras(
    palavras: list[str], inicio: float, duracao: float
) -> list[dict]:
    """Espalha palavras dentro de um intervalo, ponderando pelo tamanho."""
    if not palavras:
        return []
    pesos = [max(1, len(palavra)) for palavra in palavras]
    total = sum(pesos)
    percorrido = 0
    marcadores: list[dict] = []
    for palavra, peso in zip(palavras, pesos):
        marcadores.append(
            {
                "texto": palavra,
                "offset": inicio + duracao * percorrido / total,
                "duracao": duracao * peso / total,
            }
        )
        percorrido += peso
    return marcadores


def _marcadores_de_evento(texto: str, offset=0.0, duracao=0.0) -> list[dict]:
    """Marcadores de um ``WordBoundary``/``SentenceBoundary`` já recebido."""
    return _distribuir_palavras(
        _palavras(texto), float(offset or 0.0), float(duracao or 0.0)
    )


def _quantidade_palavras_evento(texto: str) -> int:
    """Conta palavras mesmo quando o metadata troca acentos por ``?``.

    O edge-tts devolve frases como ``"Ol?, isto ?"`` ou ``"cora??o"``: cada
    ``?`` substitui um caractere acentuado dentro da própria palavra.
    """
    # Trocar ``?`` por uma letra mantém um caractere corrompido colado à
    # palavra: ``cora??o`` vira uma palavra só, e ``Ol?, isto ?`` vira três.
    return len(_palavras((texto or "").replace("?", "a")))


def _marcadores_de_frases(texto: str, frases: list[dict]) -> list[dict]:
    """Gera marcadores por palavra quando o edge-tts só envia frases.

    Algumas versões/vozes emitem apenas ``SentenceBoundary``. O evento agrupa
    palavras e nem sempre corresponde ao texto enviado, então as palavras vêm
    do texto original e apenas o intervalo de tempo vem do evento.
    """
    originais = [
        parte.strip()
        for parte in re.split(r"(?<=[.!?;])\s+", texto)
        if parte.strip()
    ]
    marcadores: list[dict] = []
    for indice, frase in enumerate(frases):
        origem = originais[indice] if indice < len(originais) else frase.get("texto", "")
        marcadores.extend(
            _marcadores_de_evento(
                origem, frase.get("offset", 0.0), frase.get("duracao", 0.0)
            )
        )
    return marcadores


def _marcadores_estimados(texto: str, taxa: int = 0) -> list[dict]:
    """Marcadores provisórios publicados antes da síntese começar.

    O edge-tts demora para entregar as frases de textos longos. Sem isso o
    destaque só aparecia perto do fim, quando o primeiro bloco terminava.
    """
    palavras = _palavras(texto)
    if not palavras:
        return []
    por_segundo = max(0.5, 3.0 * (1.0 + float(taxa or 0) / 100.0))
    return _distribuir_palavras(palavras, 0.0, len(palavras) / por_segundo)


def _deslocar_limites(limites: list[dict], deslocamento: float) -> list[dict]:
    return [
        {
            "texto": item["texto"],
            "offset": float(item["offset"]) + deslocamento,
            "duracao": float(item["duracao"]),
        }
        for item in limites
    ]


def _duracao_limites(limites: list[dict]) -> float:
    return max(
        (float(item["offset"]) + float(item["duracao"]) for item in limites),
        default=0.0,
    )


def _colocar_na_fila(fila: queue.Queue, item) -> bool:
    """Espera até o player aceitar o item: descartar áudio cortava o texto."""
    while True:
        if controle.parar_evento.is_set() or _FALHA_PLAYER.is_set():
            return False
        try:
            fila.put(item, timeout=0.1)
            return True
        except queue.Full:
            continue


async def _gerar_stream(
    texto: str, voz: str, taxa: int, fila: queue.Queue, estado: dict
) -> None:
    """Publica os bytes MP3 do edge-tts assim que cada frame chega."""
    import edge_tts

    # Pedir limites por palavra deixa o serviço enviar WordBoundary (a voz
    # usada responde; antes só chegavam frases agrupadas). Com isso as marcas
    # vêm do próprio serviço em vez de serem estimadas dentro da frase.
    comun = edge_tts.Communicate(
        texto, voz, rate=f"{taxa:+d}%", volume="+0%",
        boundary="WordBoundary",
    )
    fluxo = comun.stream()
    coletar = bool(estado.get("coletar"))
    if coletar:
        estado.setdefault("pedacos", [])
    estado.setdefault("limites", [])
    estado.setdefault("frases", [])
    estado.setdefault("por_palavra", [])
    estado.setdefault("por_frase", [])
    estado.setdefault("duracao", 0.0)
    estado.setdefault("cursor_original", 0)
    estado.setdefault("ultima_publicacao", 0.0)
    originais = _palavras(texto)

    def publicar() -> None:
        # Publica assim que a primeira frase/palavra chega, sem esperar o bloco
        # inteiro. Palavra e frase nunca são misturadas: se o serviço mandou
        # WordBoundary, ele é mais preciso e tem prioridade.
        escolhidos = estado["por_palavra"] or estado["por_frase"]
        estado["limites"] = list(escolhidos)
        callback = estado.get("ao_marcador")
        agora = time.monotonic()
        ultima = estado.get("ultima_publicacao", 0.0)
        # Com WordBoundary há uma fronteira por palavra. Republicar a lista
        # inteira em cada uma sobrecarrega a thread da interface; 0,2 s é fino
        # o suficiente e o bloco completo ainda é confirmado no final.
        if callback and escolhidos and (not ultima or agora - ultima >= 0.2):
            estado["ultima_publicacao"] = agora
            try:
                callback(list(escolhidos))
            except Exception:
                pass

    try:
        async for pedaco in fluxo:
            if controle.parar_evento.is_set():
                break
            tipo = pedaco.get("type")
            if tipo == "audio":
                dado = pedaco["data"]
                estado["bruto"].extend(dado)
                estado["bytes"] += len(dado)
                if coletar:
                    # O serviço entrega o áudio em lotes com pausas de vários
                    # segundos. Guardar o bloco inteiro e enfileirá-lo de uma
                    # vez evita que o player toque silêncio nesses intervalos.
                    estado["pedacos"].append(dado)
                else:
                    _colocar_na_fila(fila, dado)
            elif tipo == "WordBoundary":
                inicio = _ticks_para_segundos(pedaco.get("offset"))
                duracao = _ticks_para_segundos(pedaco.get("duration"))
                texto_evento = pedaco.get("text", "")
                estado["falado"] += texto_evento
                cursor = estado["cursor_original"]
                # Um WordBoundary pode agrupar mais de uma palavra do texto:
                # "US$ 300", "1 GB" e "90 dias" chegam como um único evento.
                # Avançar só uma palavra desalinhava todos os marcadores
                # seguintes e fazia o destaque parar no "US". Contar as palavras
                # do evento mantém a sequência do painel e distribui o tempo
                # entre elas. O texto vem do original porque o metadata pode
                # trocar acentos por "?".
                quantidade = max(1, _quantidade_palavras_evento(texto_evento))
                selecionadas = originais[cursor:cursor + quantidade]
                if not selecionadas:
                    selecionadas = _palavras(texto_evento) or [texto_evento]
                estado["cursor_original"] = cursor + len(selecionadas)
                estado["por_palavra"].extend(
                    _distribuir_palavras(selecionadas, inicio, duracao)
                )
                estado["duracao"] = max(
                    estado["duracao"], inicio + duracao
                )
                publicar()
            elif tipo == "SentenceBoundary":
                inicio = _ticks_para_segundos(pedaco.get("offset"))
                duracao = _ticks_para_segundos(pedaco.get("duration"))
                texto_frase = pedaco.get("text", "")
                estado["falado"] += texto_frase
                estado["frases"].append(
                    {"texto": texto_frase, "offset": inicio, "duracao": duracao}
                )
                quantidade = _quantidade_palavras_evento(texto_frase)
                cursor = estado["cursor_original"]
                selecionadas = originais[cursor:cursor + quantidade]
                if not selecionadas:
                    selecionadas = _palavras(texto_frase)
                estado["cursor_original"] = cursor + len(selecionadas)
                estado["por_frase"].extend(
                    _distribuir_palavras(selecionadas, inicio, duracao)
                )
                estado["duracao"] = max(
                    estado["duracao"], inicio + duracao
                )
                publicar()
    finally:
        # Fecha a sessão HTTP mesmo quando o usuário para no meio da leitura.
        try:
            await asyncio.wait_for(fluxo.aclose(), 1.0)
        except (asyncio.TimeoutError, RuntimeError):
            pass


def _transmitir(
    texto: str, voz: str, taxa: int, fila: queue.Queue, estado: dict
) -> None:
    estado["texto"] = texto
    asyncio.run(_gerar_stream(texto, voz, taxa, fila, estado))
    if estado.get("coletar"):
        # Download terminado: agora sim entregamos o bloco completo ao decoder.
        for pedaco in estado.get("pedacos", []):
            _colocar_na_fila(fila, pedaco)
    escolhidos = estado.get("por_palavra") or estado.get("por_frase")
    if escolhidos:
        estado["limites"] = list(escolhidos)


def _transmitir_com_retry(
    texto: str,
    configuracao: dict,
    taxa: int,
    fila: queue.Queue,
    original: str | None = None,
    ao_marcador: Callable[[list[dict]], None] | None = None,
    coletar: bool = False,
) -> dict:
    principal = configuracao.get("voz_neural") or VOZ_NEURAL_PADRAO
    candidatas = list(dict.fromkeys([principal, *VOZES_NEARAIS_PT_BR]))
    tentativas = [(principal, 0.0), (principal, 0.4)]
    tentativas += [(voz, 0.0) for voz in candidatas if voz != principal]
    ultimo_erro: Exception | None = None
    estado_final: dict = {
        "bytes": 0, "falado": "", "bruto": bytearray(),
        "limites": [], "frases": [], "duracao": 0.0, "pedacos": [],
    }

    for voz, espera in tentativas:
        if controle.parar_evento.is_set():
            return estado_final
        if espera:
            time.sleep(espera)
        estado = {
            "bytes": 0, "falado": "", "bruto": bytearray(),
            "limites": [], "frases": [], "duracao": 0.0, "pedacos": [],
            "coletar": coletar,
        }
        # Os marcadores são publicados pelo próprio stream, assim que a primeira
        # frases chega. Guardar no estado preserva a assinatura de _transmitir()
        # usada por testes e callers antigos.
        estado["ao_marcador"] = ao_marcador
        estado_final = estado
        try:
            _transmitir(texto, voz, taxa, fila, estado)
            return estado
        except Exception as exc:
            ultimo_erro = exc
            # Se algum áudio já entrou na fila, recomeçar repetiria a fala.
            if estado["bytes"] > 1000:
                raise FalhaNeural(
                    f"falha no stream neural: {exc}",
                    restante=_restante_apos(original or texto, estado["falado"]),
                ) from exc

    raise FalhaNeural(
        f"falha na síntese neural: {ultimo_erro}", restante=texto
    )


def _tocar_stream(
    fila_mp3: queue.Queue,
    configuracao: dict,
    progresso: Callable[[str], None] | None,
    estado: dict,
    destaque: Callable[[str, object | None], None] | None = None,
) -> None:
    """Faz ffmpeg+sounddevice tocarem o stream sem fronteiras entre frases."""
    import numpy as np
    import sounddevice as sd

    ffmpeg = _caminho_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado")

    # Sem CREATE_NO_WINDOW o ffmpeg abre uma janela de console preta no Windows
    # (principalmente quando o app é iniciado por pythonw.exe).
    sem_console = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    processo = subprocess.Popen(
        [
            ffmpeg, "-v", "error", "-nostdin",
            "-f", "mp3", "-i", "pipe:0",
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ac", "2", "-ar", str(TAXA_PCM), "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=sem_console,
    )
    # Cada bloco pequeno entra completo; o buffer extra (~15 s de PCM) absorve
    # a virada entre blocos e qualquer pausa maior do serviço de voz.
    fila_pcm: queue.Queue = queue.Queue(maxsize=320)
    fim_audio = threading.Event()
    volume = max(0, min(100, int(configuracao.get("volume", 90)))) / 100.0

    def ler_pcm() -> None:
        try:
            while not controle.parar_evento.is_set():
                bloco = processo.stdout.read(BLOCO_PCM)
                if not bloco:
                    break
                while True:
                    if controle.parar_evento.is_set():
                        return
                    try:
                        fila_pcm.put(bloco, timeout=0.1)
                        break
                    except queue.Full:
                        continue
            while not controle.parar_evento.is_set():
                try:
                    fila_pcm.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue
        finally:
            estado["leitor_fim"] = True

    def alimentar_ffmpeg() -> None:
        """Alimenta o decoder fora da thread do áudio, para poder pré-carregar."""
        try:
            while not controle.parar_evento.is_set():
                try:
                    item = fila_mp3.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                try:
                    processo.stdin.write(item)
                    processo.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
        finally:
            try:
                processo.stdin.close()
            except (OSError, ValueError):
                pass

    leitor = threading.Thread(target=ler_pcm, name="tts-pcm", daemon=True)
    alimentador = threading.Thread(target=alimentar_ffmpeg, name="tts-ffmpeg", daemon=True)
    leitor.start()
    alimentador.start()

    # Os blocos do ffmpeg (8 KB) não coincidem com o bloco da placa de som
    # (1024 frames = 4 KB). Guardar a sobra é essencial: descartá-la fazia o
    # áudio perder metade das amostras e ficar travado/picotado.
    pendente = bytearray()
    # O callback de áudio precisa ser o mais curto possível: colocar eventos na
    # fila dentro dele voltou a provocar travadas. Ele só conta frames; quem
    # publica a posição é a thread abaixo.
    contador = {"frames": 0}
    publicador_parar = threading.Event()

    def callback(outdata, frames, momento, status) -> None:
        nonlocal pendente
        if controle.parar_evento.is_set() or controle.pausa_evento.is_set():
            outdata[:] = 0
            return
        necessario = frames * 4  # int16, 2 canais
        while len(pendente) < necessario:
            try:
                bloco = fila_pcm.get_nowait()
            except queue.Empty:
                break
            if bloco is None:
                fim_audio.set()
                break
            pendente.extend(bloco)
        if len(pendente) < necessario and not pendente:
            # Silêncio por falta de bloco é carregamento, não narração. Se estes
            # frames contassem, o destaque passaria do áudio durante a pausa
            # para baixar o próximo bloco. A posição só avança com PCM real.
            outdata[:] = 0
            return
        pedaco = bytes(pendente[:necessario])
        del pendente[:necessario]
        if progresso and not estado["anunciado"]:
            estado["anunciado"] = True
            progresso("Reproduzindo voz neural...")
        amostras = np.frombuffer(pedaco, dtype="<i2").astype(np.int32)
        if len(amostras) < frames * 2:
            amostras = np.pad(amostras, (0, frames * 2 - len(amostras)))
        outdata[:] = (
            np.clip(amostras * volume, -32768, 32767)
            .astype(np.int16)
            .reshape(-1, 2)
        )
        # O tempo do player é a referência do destaque: nem pausa nem silêncio
        # de carregamento contam; os dois retornam antes daqui.
        contador["frames"] += frames

    def publicar_posicao() -> None:
        ultima = -1.0
        while not publicador_parar.wait(0.05):
            if controle.parar_evento.is_set() or controle.pausa_evento.is_set():
                continue
            segundos = contador["frames"] / TAXA_PCM
            if segundos - ultima < 0.05:
                continue
            ultima = segundos
            try:
                destaque("posicao", segundos)
            except Exception:
                pass

    # Só abre a saída quando há áudio suficiente para os primeiros callbacks.
    # Antes disso o decoder já era alimentado pela thread acima, então o som
    # começa sem aquele "engasgo" de inicialização.
    # O primeiro bloco é baixado por completo antes de entrar no decoder; em
    # uma rede mais lenta isso pode levar alguns segundos. Esperar aqui evita
    # abrir a placa de som com a fila vazia (que seria ouvido como travada).
    limite = time.monotonic() + 20.0
    while (
        fila_pcm.qsize() < 6
        and not estado.get("leitor_fim")
        and not fim_audio.is_set()
        and not controle.parar_evento.is_set()
        and time.monotonic() < limite
    ):
        time.sleep(0.01)

    publicador = None
    if destaque:
        publicador = threading.Thread(
            target=publicar_posicao, name="tts-posicao", daemon=True
        )
        publicador.start()

    try:
        with sd.OutputStream(
            samplerate=TAXA_PCM,
            channels=2,
            dtype="int16",
            blocksize=1024,
            callback=callback,
        ):
            # Enquanto pausado o callback não consome; espera sem timeout curto.
            while not fim_audio.is_set() and not controle.parar_evento.is_set():
                fim_audio.wait(0.05)
    except Exception:
        _FALHA_PLAYER.set()
        raise
    finally:
        publicador_parar.set()
        if publicador is not None:
            publicador.join(timeout=0.5)
        if processo.poll() is None:
            processo.kill()
        try:
            processo.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        alimentador.join(timeout=1)
        leitor.join(timeout=1)


def _tocar_memoria(
    dados: bytes,
    configuracao: dict,
    progresso: Callable[[str], None] | None,
    destaque: Callable[[str, object | None], None] | None = None,
    marcadores: list[dict] | None = None,
) -> None:
    """Replay do cache em memória, alimentando o mesmo decoder contínuo."""
    if destaque and marcadores:
        try:
            destaque("limites", marcadores)
        except Exception:
            pass
    fila: queue.Queue = queue.Queue(maxsize=64)

    def alimentar() -> None:
        for inicio in range(0, len(dados), BLOCO_PCM):
            if not _colocar_na_fila(fila, dados[inicio:inicio + BLOCO_PCM]):
                return
        _colocar_na_fila(fila, None)

    threading.Thread(target=alimentar, name="tts-cache", daemon=True).start()
    argumentos = (fila, configuracao, progresso, {"anunciado": False, "leitor_fim": False})
    if destaque is None:
        _tocar_stream(*argumentos)
    else:
        _tocar_stream(*argumentos, destaque=destaque)


def _falar_neural(
    texto: str,
    configuracao: dict,
    progresso: Callable[[str], None] | None = None,
    destaque: Callable[[str, object | None], None] | None = None,
) -> None:
    """Transmite e reproduz o áudio ao mesmo tempo, sem esperar a síntese."""
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
    except Exception as exc:
        raise FalhaNeural(f"player de áudio indisponível: {exc}", restante=texto) from exc
    if not _caminho_ffmpeg():
        raise FalhaNeural("ffmpeg não encontrado", restante=texto)

    fator = float(configuracao.get("velocidade", 1.0))
    taxa = int(round((fator - 1.0) * 100))
    principal = configuracao.get("voz_neural") or VOZ_NEURAL_PADRAO

    # Releitura do mesmo texto sai do cache em memória, sem tocar na rede.
    chave = (texto, principal, taxa)
    cache = _CACHE_NEURAL.get(chave)
    if cache:
        if destaque is None:
            _tocar_memoria(cache, configuracao, progresso)
        else:
            _tocar_memoria(
                cache, configuracao, progresso,
                destaque=destaque,
                marcadores=_CACHE_MARCADORES.get(chave),
            )
        return

    # O edge-tts entrega o áudio em lotes com pausas de vários segundos. Blocos
    # pequenos (~240 caracteres) são coletados por completo antes de entrar no
    # player: enquanto um toca, o próximo já está sendo baixado. Assim o decoder
    # nunca fica sem dados e o texto continua sendo lido até o fim.
    partes = dividir_texto(texto, limite=BLOCO_TTS)
    fila: queue.Queue = queue.Queue(maxsize=256)
    estado: dict = {"anunciado": False, "leitor_fim": False}
    argumentos = (fila, configuracao, progresso, estado)
    tocador = threading.Thread(
        target=_tocar_stream,
        args=argumentos,
        kwargs={} if destaque is None else {"destaque": destaque},
        name="tts-player",
        daemon=True,
    )
    tocador.start()

    # Cada bloco guarda marcadores LOCAIS. Enquanto o edge-tts não entrega as
    # frases, usamos uma estimativa: o destaque começa junto com o áudio em vez
    # de só aparecer no fim do texto. As frases reais substituem a estimativa do
    # bloco correspondente, sem perder a cobertura dos blocos seguintes.
    locais_por_bloco: list[list[dict]] = [
        _marcadores_estimados(parte, taxa) for parte in partes
    ]
    duracoes_blocos: list[float] = [
        _duracao_limites(locais) for locais in locais_por_bloco
    ]

    def publicar_globais() -> None:
        if destaque is None:
            return
        planos: list[dict] = []
        deslocamento = 0.0
        for locais, duracao_bloco in zip(locais_por_bloco, duracoes_blocos):
            planos.extend(_deslocar_limites(locais, deslocamento))
            deslocamento += duracao_bloco
        try:
            destaque("limites", planos)
        except Exception:
            pass

    publicar_globais()

    gerado: dict = {
        "bytes": 0, "falado": "", "bruto": bytearray(), "frases": [],
    }
    try:
        for indice, parte in enumerate(partes):
            if controle.parar_evento.is_set():
                break

            def receber(marcadores_parte, bloco=indice) -> None:
                if marcadores_parte:
                    locais_por_bloco[bloco] = list(marcadores_parte)
                    publicar_globais()

            try:
                atual = _transmitir_com_retry(
                    parte, configuracao, taxa, fila, original=texto,
                    ao_marcador=receber, coletar=True,
                )
            except FalhaNeural as exc:
                # Só a parte atual e as seguintes precisam da voz local.
                restante = " ".join(
                    [exc.restante or "", *partes[indice + 1:]]
                ).strip()
                raise FalhaNeural(str(exc), restante=restante) from exc
            gerado["bytes"] += atual["bytes"]
            gerado["falado"] += atual["falado"]
            gerado["bruto"].extend(atual["bruto"])

            # Usar o fim do último marcador como duração do bloco adiantava o
            # destaque: o MP3 ainda tem silêncio final depois da última palavra.
            # Os bytes decodificados dão a duração real e eliminam esse desvio
            # antes de publicar os marcadores dos blocos seguintes.
            duracoes_blocos[indice] = max(
                _duracao_limites(locais_por_bloco[indice]),
                float(atual.get("bytes", 0)) / MP3_BYTES_POR_SEGUNDO,
            )

            # Nem toda voz/versão do edge-tts envia WordBoundary. Quando só
            # há SentenceBoundary, derivamos os marcadores do próprio texto,
            # usando os intervalos das frases como referência de tempo (o
            # evento agrupa palavras e não casa palavra por palavra).
            limites_parte = atual.get("limites", [])
            frases_parte = atual.get("frases", [])
            gerado["frases"].extend(frases_parte)
            if not limites_parte and frases_parte:
                limites_parte = _marcadores_de_frases(
                    atual.get("texto") or parte, frases_parte
                )
            # Confirma o bloco mesmo quando o stream não chamou o callback.
            if limites_parte:
                receber(limites_parte)
            else:
                # A duração real do bloco também precisa ser republicada.
                publicar_globais()
    finally:
        _colocar_na_fila(fila, None)
        tocador.join()

    if estado["leitor_fim"] is False and not controle.parar_evento.is_set():
        raise FalhaNeural("player de áudio parou antes do fim", restante=texto)

    # Guarda o stream completo em memória para releituras instantâneas.
    bruto = gerado["bruto"]
    if len(bruto) > 1000:
        marcadores_globais: list[dict] = []
        deslocamento = 0.0
        for locais, duracao_bloco in zip(locais_por_bloco, duracoes_blocos):
            marcadores_globais.extend(_deslocar_limites(locais, deslocamento))
            deslocamento += duracao_bloco
        _CACHE_NEURAL.pop(chave, None)
        _CACHE_NEURAL[chave] = bytes(bruto)
        _CACHE_MARCADORES[chave] = marcadores_globais
        while len(_CACHE_NEURAL) > MAX_CACHE_MEMORIA:
            antiga = next(iter(_CACHE_NEURAL))
            _CACHE_NEURAL.pop(antiga, None)
            _CACHE_MARCADORES.pop(antiga, None)
