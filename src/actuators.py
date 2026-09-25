import time
from machine import Pin
import config

ESTADO_LIGADO = 0
ESTADO_DESLIGADO = 1

# Tempo MAXIMO que a bomba pode ficar ligada em cada acionamento (manual ou
# automatico). Se ainda faltar agua, a proxima medicao percebe e rega de novo.
TEMPO_MAX_REGA_S = getattr(config, "TEMPO_MAX_REGA_S", 3)

_rele = Pin(config.PINO_RELE_BOMBA, Pin.OUT)
_rele.value(ESTADO_DESLIGADO)

_bomba_ligada = False


def _ligar_bomba():
    # Privado: a bomba so deve ser ligada via regar(), que garante o desligamento.
    global _bomba_ligada
    _rele.value(ESTADO_LIGADO)
    _bomba_ligada = True
    print("Bomba d'agua: LIGADA")


def desligar_bomba():
    global _bomba_ligada
    _rele.value(ESTADO_DESLIGADO)
    if _bomba_ligada:
        print("Bomba d'agua: DESLIGADA")
    _bomba_ligada = False


def regar(duracao_s=None, ao_mudar_estado=None):
    """Liga a bomba por no maximo TEMPO_MAX_REGA_S e SEMPRE desliga no final.

    ao_mudar_estado(ligada: bool) e chamado ao ligar e ao desligar
    (usado pelo controller para avisar o app via MQTT).
    Retorna True se regou, False se a bomba ja estava ligada.
    """
    if _bomba_ligada:
        return False

    if duracao_s is None or duracao_s > TEMPO_MAX_REGA_S:
        duracao_s = TEMPO_MAX_REGA_S

    _ligar_bomba()
    _avisar(ao_mudar_estado, True)
    try:
        time.sleep(duracao_s)
    finally:
        # finally: desliga mesmo se der erro ou Ctrl+C durante a rega
        desligar_bomba()
        _avisar(ao_mudar_estado, False)
    return True


def _avisar(callback, ligada):
    if callback is None:
        return
    try:
        callback(ligada)
    except Exception as e:
        print("Aviso de estado da bomba falhou:", e)


def estado_bomba():
    return _bomba_ligada
