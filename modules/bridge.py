""" Bridge module """

import socket
import logging

logger = logging.getLogger("Bridge")

class BridgeWin:
    """ Bridge for windows """
    def __init__(self, core_instance, bind_ip="0.0.0.0", port=8888):
        self.core = core_instance
        self.bind_ip = bind_ip
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.is_running = False

    def start(self):
        """ Start listening """
        self.sock.bind((self.bind_ip, self.port))
        self.is_running = True
        logger.info(f"[BridgeWin] Started UDP listener on {self.bind_ip}:{self.port}")

        try:
            while self.is_running:
                data, addr = self.sock.recvfrom(65535)
                if data:
                    print(f"DATA HERE!! {data}")
                    self.core.parsePacket(data)
        except Exception as e:
            logger.error(f"[BridgeWin Error]: {e}")
        finally:
            self.stop()

    def stop(self):
        """ Stop thread """
        self.is_running = False
        self.sock.close()
        logger.info("[BridgeWin] Stopped.")


class BridgeLinux:
    """ Bridge for linux """
    def __init__(self, core_instance, bind_ip="0.0.0.0", port=8888):
        self.core = core_instance
        self.bind_ip = bind_ip
        self.port = port

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        self.is_running = False

    def start(self):
        """ Start listening """
        self.sock.bind((self.bind_ip, self.port))
        self.is_running = True
        logger.info(f"[BridgeLinux] Started high-performance listener on {self.bind_ip}:{self.port}")

        try:
            while self.is_running:
                data, addr = self.sock.recvfrom(65535)
                if data:
                    self.core.parse_packet(data)
        except Exception as e:
            logger.error(f"[BridgeLinux Error]: {e}")
        finally:
            self.stop()

    def stop(self):
        self.is_running = False
        self.sock.close()
        logger.info("[BridgeLinux] Stopped.")
