import time
import ujson
import network
import machine
import ubinascii
from umqttsimple import MQTTClient
import config
import sensors
import actuators
import config_manager

# Valores novos com padrao: funcionam mesmo com um config.py antigo na placa.
TOPICO_BOMBA = getattr(config, "TOPICO_BOMBA", "estufa/bomba")
TOPICO_STATUS = getattr(config, "TOPICO_STATUS", "estufa/status")
PAUSA_ENTRE_REGAS_S = getattr(config, "PAUSA_ENTRE_REGAS_S", 60)
INTERVALO_TENTATIVA_MQTT_S = getattr(config, "INTERVALO_TENTATIVA_MQTT_S", 30)
INTERVALO_TENTATIVA_WIFI_S = getattr(config, "INTERVALO_TENTATIVA_WIFI_S", 30)
REINICIAR_APOS_OFFLINE_S = getattr(config, "REINICIAR_APOS_OFFLINE_S", 600)

_limites_atuais = {}
_mqtt = None
_ultima_tentativa_mqtt = None
_ultima_tentativa_wifi = None
_offline_desde = None
_ultima_rega_auto = None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _segundos_desde(ticks):
    if ticks is None:
        return None
    return time.ticks_diff(time.ticks_ms(), ticks) / 1000


def _fmt(valor, formato):
    return "--" if valor is None else formato % valor


def _descrever_leituras(l):
    return "Temperatura: %s | Umidade do ar: %s | Umidade do solo: %s | Luminosidade: %s" % (
        _fmt(l.get("temperatura"), "%.1f C"),
        _fmt(l.get("umidade_ar"), "%.0f %%"),
        _fmt(l.get("umidade_solo"), "%.1f %%"),
        _fmt(l.get("luminosidade"), "%.0f lx"),
    )


# ---------------------------------------------------------------------------
# Mensagens recebidas (app -> ESP32)
# ---------------------------------------------------------------------------

def _on_mensagem(topic, msg):
    topico = topic.decode()
    print("Mensagem recebida em '%s': %s" % (topico, msg))

    if topico == config.TOPICO_CONFIG:
        _processar_config(msg)
    elif topico == config.TOPICO_COMANDOS:
        _processar_comando(msg)


def _limite(dados, chave):
    """Le um limite do JSON; se faltar ou vier invalido, mantem o valor atual
    (antes caia para 0, o que desligava a irrigacao sem ninguem perceber)."""
    try:
        return float(dados[chave])
    except Exception:
        return float(_limites_atuais.get(chave, config.LIMITES_PADRAO[chave]))


def _processar_config(msg):
    global _limites_atuais
    try:
        dados = ujson.loads(msg)
        novos = {
            "nome": dados.get("nome", _limites_atuais.get("nome", "")),
            "temp_min": _limite(dados, "temp_min"),
            "temp_max": _limite(dados, "temp_max"),
            "umi_min": _limite(dados, "umi_min"),
            "umi_max": _limite(dados, "umi_max"),
        }
    except Exception as e:
        print("Erro ao interpretar JSON de estufa/config:", e)
        return

    if not (0 <= novos["umi_min"] < novos["umi_max"] <= 100):
        print("Config REJEITADA: umi_min (%s) deve ser menor que umi_max (%s), entre 0 e 100."
              % (novos["umi_min"], novos["umi_max"]))
        return

    if novos == _limites_atuais:
        # O broker reenvia a config "retida" a cada reconexao: nao regrava a flash.
        print("Config recebida igual a atual (%s)." % novos["nome"])
        return

    _limites_atuais = novos
    config_manager.salvar_limites(_limites_atuais)
    print("Nova cultura aplicada: %s | solo min %.0f%% / max %.0f%% | temp %.0f-%.0f C" % (
        novos["nome"], novos["umi_min"], novos["umi_max"],
        novos["temp_min"], novos["temp_max"]))


def _processar_comando(msg):
    comando = msg.decode().strip()
    if comando == "LIGAR_BOMBA":
        print("Comando manual recebido: rega de %ds" % actuators.TEMPO_MAX_REGA_S)
        _regar("manual")
    elif comando == "DESLIGAR_BOMBA":
        print("Comando manual recebido: desligar bomba")
        actuators.desligar_bomba()
        _publicar(TOPICO_BOMBA, "DESLIGADA", retain=True)
    else:
        print("Comando desconhecido:", comando)


# ---------------------------------------------------------------------------
# Irrigacao
# ---------------------------------------------------------------------------

def _avisar_bomba(ligada):
    _publicar(TOPICO_BOMBA, "LIGADA" if ligada else "DESLIGADA", retain=True)


def _regar(origem):
    global _ultima_rega_auto
    if actuators.regar(ao_mudar_estado=_avisar_bomba):
        # Qualquer rega (manual ou automatica) reinicia a pausa da automatica,
        # para dar tempo da agua se espalhar antes de medir de novo.
        _ultima_rega_auto = time.ticks_ms()
        print("Rega %s concluida (%ds)." % (origem, actuators.TEMPO_MAX_REGA_S))


def _executar_edge_computing(leituras):
    umidade_solo = leituras.get("umidade_solo")
    umi_min = _limites_atuais.get("umi_min")
    umi_max = _limites_atuais.get("umi_max")

    if umidade_solo is None or umi_min is None or umi_max is None:
        print("Irrigacao automatica pausada: sem leitura do solo ou sem limites.")
        return

    if umidade_solo >= umi_min:
        return  # solo ok (ou acima do maximo): nao rega

    decorrido = _segundos_desde(_ultima_rega_auto)
    if decorrido is not None and decorrido < PAUSA_ENTRE_REGAS_S:
        print("Solo seco (%.1f%% < %.0f%%), mas aguardando %ds para a agua se espalhar antes de regar de novo."
              % (umidade_solo, umi_min, PAUSA_ENTRE_REGAS_S - decorrido))
        return

    print("Solo seco (%.1f%% < minimo %.0f%%): regando por %ds."
          % (umidade_solo, umi_min, actuators.TEMPO_MAX_REGA_S))
    _regar("automatica")


# ---------------------------------------------------------------------------
# Wi-Fi e MQTT
# ---------------------------------------------------------------------------

def _garantir_wifi():
    """Retorna True se ha Wi-Fi. Se nao houver, tenta reconectar sem travar
    o loop: a estufa continua lendo sensores e irrigando offline."""
    global _ultima_tentativa_wifi, _offline_desde
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        _offline_desde = None
        return True

    if _offline_desde is None:
        _offline_desde = time.ticks_ms()

    offline_ha = _segundos_desde(_offline_desde)
    if offline_ha > REINICIAR_APOS_OFFLINE_S:
        print("Sem Wi-Fi ha %ds. Reiniciando a placa para restaurar a rede..." % offline_ha)
        time.sleep(1)
        machine.reset()

    decorrido = _segundos_desde(_ultima_tentativa_wifi)
    if decorrido is None or decorrido >= INTERVALO_TENTATIVA_WIFI_S:
        _ultima_tentativa_wifi = time.ticks_ms()
        print("Wi-Fi fora do ar: tentando reconectar em segundo plano...")
        try:
            wlan.active(True)
            try:
                wlan.disconnect()
            except Exception:
                pass
            wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        except Exception as e:
            print("Erro ao reconectar Wi-Fi:", e)
    return False


def _conectar_mqtt():
    mac = ubinascii.hexlify(machine.unique_id()).decode()
    cliente = MQTTClient(
        client_id=config.MQTT_CLIENT_ID + "_" + mac,
        server=config.MQTT_BROKER,
        port=config.MQTT_PORT,
        user=config.MQTT_USER,
        password=config.MQTT_PASSWORD,
        keepalive=30,
        ssl=True,
        ssl_params={"server_hostname": config.MQTT_BROKER},
    )
    cliente.set_callback(_on_mensagem)
    # Se a placa cair (energia, Wi-Fi), o proprio broker publica "offline".
    cliente.set_last_will(TOPICO_STATUS, "offline", retain=True, qos=1)

    try:
        cliente.connect()
        cliente.publish(TOPICO_STATUS, "online", retain=True)
        cliente.publish(TOPICO_BOMBA,
                        "LIGADA" if actuators.estado_bomba() else "DESLIGADA",
                        retain=True)
        cliente.subscribe(config.TOPICO_CONFIG)
        cliente.subscribe(config.TOPICO_COMANDOS)
        print("MQTT conectado e inscrito em '%s' e '%s'" % (
            config.TOPICO_CONFIG, config.TOPICO_COMANDOS))
        return cliente
    except Exception as e:
        print("Falha ao conectar ao broker MQTT:", e)
        try:
            cliente.sock.close()
        except Exception:
            pass
        return None


def _fechar_mqtt():
    global _mqtt
    if _mqtt is not None:
        try:
            _mqtt.sock.close()
        except Exception:
            pass
    _mqtt = None


def _publicar(topico, texto, retain=False):
    if _mqtt is None:
        return False
    try:
        _mqtt.publish(topico, texto, retain=retain)
        return True
    except Exception as e:
        print("Erro ao publicar em %s (conexao perdida): %s" % (topico, e))
        _fechar_mqtt()
        return False


def _verificar_mensagens():
    if _mqtt is None:
        return
    try:
        _mqtt.check_msg()
    except Exception as e:
        print("Conexao MQTT perdida no check_msg:", e)
        _fechar_mqtt()


# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def iniciar():
    global _limites_atuais, _mqtt, _ultima_tentativa_mqtt
    _limites_atuais = config_manager.carregar_limites()
    print("Limites ativos: solo min %s%% / max %s%%" % (
        _limites_atuais.get("umi_min"), _limites_atuais.get("umi_max")))
    if network.WLAN(network.STA_IF).isconnected():
        _ultima_tentativa_mqtt = time.ticks_ms()
        _mqtt = _conectar_mqtt()
    else:
        print("Iniciando em modo OFFLINE (autonomo).")


def executar_loop():
    global _mqtt, _ultima_tentativa_mqtt
    inicio = time.ticks_ms()

    wifi_ok = _garantir_wifi()
    if not wifi_ok:
        _fechar_mqtt()
    elif _mqtt is None:
        decorrido = _segundos_desde(_ultima_tentativa_mqtt)
        if decorrido is None or decorrido >= INTERVALO_TENTATIVA_MQTT_S:
            _ultima_tentativa_mqtt = time.ticks_ms()
            _mqtt = _conectar_mqtt()

    # Antes, uma falha aqui fazia "return" e o ciclo pulava a leitura.
    _verificar_mensagens()

    leituras = sensors.ler_todos_sensores()
    _executar_edge_computing(leituras)

    if _publicar(config.TOPICO_SENSORES, ujson.dumps(leituras)):
        print("[ONLINE] Leitura enviada -> " + _descrever_leituras(leituras))
    else:
        print("[OFFLINE] Leitura registrada -> " + _descrever_leituras(leituras))

    # Espera o resto do intervalo ouvindo o broker, para o comando de rega
    # manual responder na hora (antes podia demorar ate 10 s).
    while time.ticks_diff(time.ticks_ms(), inicio) < config.INTERVALO_LEITURA_S * 1000:
        _verificar_mensagens()
        time.sleep_ms(200)
