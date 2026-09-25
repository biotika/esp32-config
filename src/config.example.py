# Configurações do Sistema
# Wi-Fi
WIFI_SSID = "SUA_REDE"
WIFI_PASSWORD = "SUA_SENHA"

# Broker MQTT
MQTT_BROKER = "SEU_CLUSTER.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_CLIENT_ID = "estufa_esp32_device"
MQTT_USER = "SEU_USUARIO"
MQTT_PASSWORD = "SUA_SENHA_MQTT"

# Tópicos MQTT
TOPICO_SENSORES = "estufa/sensores"
TOPICO_CONFIG = "estufa/config"
TOPICO_COMANDOS = "estufa/comandos"
TOPICO_BOMBA = "estufa/bomba"      # ESP32 -> app: LIGADA / DESLIGADA
TOPICO_STATUS = "estufa/status"    # ESP32 -> app: online / offline (last will)

# Pinos do ESP32
PINO_DHT11 = 4          # Temperatura/umidade do ar
PINO_SOLO_ADC = 34      # Umidade do solo (entrada analógica)
PINO_I2C_SDA = 21       # BH1750 (luminosidade) - I2C
PINO_I2C_SCL = 22
PINO_RELE_BOMBA = 26    # Relé da bomba

# Calibração sensor de umidade do solo
SOLO_VALOR_SECO = 3300
SOLO_VALOR_MOLHADO = 1200

INTERVALO_LEITURA_S = 10

# Irrigacao
TEMPO_MAX_REGA_S = 3         # cada acionamento (manual ou automatico) dura no maximo isso
PAUSA_ENTRE_REGAS_S = 60     # espera minima entre regas automaticas (agua se espalhar no solo)

# Reconexao
INTERVALO_TENTATIVA_WIFI_S = 30
INTERVALO_TENTATIVA_MQTT_S = 30
REINICIAR_APOS_OFFLINE_S = 600   # sem Wi-Fi por 10 min -> reinicia a placa

LIMITES_PADRAO = {
    "nome": "Padrao",
    "temp_min": 15.0,
    "temp_max": 30.0,
    "umi_min": 30.0,
    "umi_max": 70.0,
}