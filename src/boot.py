import network
import time
from config import WIFI_SSID, WIFI_PASSWORD

def conectar_wifi(timeout_s=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print("Wi-Fi já conectado.", wlan.ifconfig())
        return True
    
    print("Conectando ao Wi-Fi '%s'..." % WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    
    inicio = time.time()
    while not wlan.isconnected():
        if time.time() - inicio > timeout_s:
            print("Falha ao conectar ao Wi-Fi (timeout). "
                  "Seguindo em modo offline / autonomo.")
            return False
        time.sleep(0.5)

    # Antes estas linhas estavam DENTRO do while (retornava "conectado" sem estar)
    print("Wi-Fi conectado: ", wlan.ifconfig())
    return True

conectar_wifi()