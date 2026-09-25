import usocket as socket
import ustruct as struct
from ubinascii import hexlify

class MQTTException(Exception):
    pass

class MQTTClient:
    def __init__(self, client_id, server, port=0, user=None, password=None,
                 keepalive=0, ssl=False, ssl_params=None):
        if port == 0:
            port = 8883 if ssl else 1883
        self.client_id = client_id
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.keepalive = keepalive
        self.ssl = ssl
        self.ssl_params = ssl_params or {}
        self.sock = None
        self.cb = None
        self._pid = 0
        self.lw_topic = None
        self.lw_msg = None
        self.lw_qos = 0
        self.lw_retain = False

    def set_last_will(self, topic, msg, retain=False, qos=0):
        """Mensagem que o BROKER publica se a placa cair sem se despedir."""
        assert 0 <= qos <= 2
        assert topic
        self.lw_topic = topic.encode() if isinstance(topic, str) else topic
        self.lw_msg = msg.encode() if isinstance(msg, str) else msg
        self.lw_qos = qos
        self.lw_retain = retain

    def set_callback(self, f):
        self.cb = f

    def _send_str(self, s):
        if isinstance(s, str):
            s = s.encode()
        self.sock.write(struct.pack("!H", len(s)))
        self.sock.write(s)

    def _next_pid(self):
        self._pid = (self._pid + 1) % 65536
        if self._pid == 0:
            self._pid = 1
        return self._pid

    def connect(self, clean_session=True):
        addr = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.settimeout(10)
        self.sock.connect(addr)

        if self.ssl:
            import ssl
            self.sock = ssl.wrap_socket(
                self.sock, server_hostname=self.ssl_params.get(
                    "server_hostname", self.server))

        premsg = bytearray(b"\x10\0\0\0\0\0")
        msg = bytearray(b"\x04MQTT\x04\x02\0\0")  

        sz = 10 + 2 + len(self.client_id)
        msg[6] |= 0x02 if clean_session else 0

        if self.user is not None:
            sz += 2 + len(self.user) + 2 + len(self.password)
            msg[6] |= 0xC0
        if self.lw_topic:
            sz += 2 + len(self.lw_topic) + 2 + len(self.lw_msg)
            msg[6] |= 0x4 | (self.lw_qos & 0x1) << 3 | (self.lw_qos & 0x2) << 3
            msg[6] |= self.lw_retain << 5
        if self.keepalive:
            msg[7] |= self.keepalive >> 8
            msg[8] |= self.keepalive & 0x00FF

        i = 1
        while sz > 0x7F:
            premsg[i] = (sz & 0x7F) | 0x80
            sz >>= 7
            i += 1
        premsg[i] = sz

        self.sock.write(premsg[: i + 2])
        self.sock.write(msg)
        self._send_str(self.client_id)
        if self.lw_topic:
            self._send_str(self.lw_topic)
            self._send_str(self.lw_msg)
        if self.user is not None:
            self._send_str(self.user)
            self._send_str(self.password)

        resp = self.sock.read(4)
        if resp[0] != 0x20 or resp[3] != 0:
            raise MQTTException(
                "Falha na conexao MQTT (CONNACK code=%d)" % resp[3])
        return resp[2] & 1  

    def disconnect(self):
        self.sock.write(b"\xe0\0")
        self.sock.close()

    def ping(self):
        self.sock.write(b"\xc0\0")

    def publish(self, topic, msg, retain=False, qos=0):
        if isinstance(msg, str):
            msg = msg.encode()
        pkt = bytearray(b"\x30\0\0\0")
        pkt[0] |= qos << 1 | (1 if retain else 0)
        sz = 2 + len(topic) + len(msg)
        if qos > 0:
            sz += 2
        if sz >= 2097152:
            raise MQTTException("Mensagem grande demais")

        i = 1
        while sz > 0x7F:
            pkt[i] = (sz & 0x7F) | 0x80
            sz >>= 7
            i += 1
        pkt[i] = sz

        self.sock.write(pkt[: i + 1])
        self._send_str(topic)
        pid = 0
        if qos > 0:
            pid = self._next_pid()
            self.sock.write(struct.pack("!H", pid))
        self.sock.write(msg)

        if qos == 1:
            while True:
                op = self.wait_msg()
                if op == 0x40:  # PUBACK
                    return
        return

    def subscribe(self, topic, qos=0):
        if self.cb is None:
            raise MQTTException(
                "Defina set_callback() antes de assinar um topico")
        pkt = bytearray(b"\x82\0\0\0")
        pid = self._next_pid()
        struct.pack_into("!BH", pkt, 1, 2 + 2 + len(topic) + 1, pid)
        self.sock.write(pkt)
        self._send_str(topic)
        self.sock.write(struct.pack("B", qos))
        while True:
            op = self.wait_msg()
            if op == 0x90:  # SUBACK
                return

    def _read_remaining_length(self):
        n = 0
        sh = 0
        while True:
            b = self.sock.read(1)[0]
            n |= (b & 0x7F) << sh
            if not b & 0x80:
                return n
            sh += 7

    def wait_msg(self):
        """Bloqueia ate a proxima mensagem chegar e a processa."""
        op = self.sock.read(1)
        if op is None:
            return None
        return self._process_packet(op[0])

    def check_msg(self):
        self.sock.setblocking(False)
        try:
            op = self.sock.read(1)
        finally:
            self.sock.setblocking(True)
        if op is None or op == b"":
            return None
        return self._process_packet(op[0])

    def _process_packet(self, op):
        if op == 0xD0:  # PINGRESP
            self.sock.read(1)
            return op
        if op & 0xF0 != 0x30:  
            sz = self._read_remaining_length()
            self.sock.read(sz)
            return op

        sz = self._read_remaining_length()
        topic_len = struct.unpack("!H", self.sock.read(2))[0]
        topic = self.sock.read(topic_len)
        sz -= topic_len + 2
        if op & 0x06: 
            pid = struct.unpack("!H", self.sock.read(2))[0]
            sz -= 2
        msg = self.sock.read(sz)

        if op & 0x06 == 0x02:  
            self.sock.write(struct.pack("!BBH", 0x40, 2, pid))

        if self.cb is not None:
            self.cb(topic, msg)
        return op