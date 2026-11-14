""" Bridge module """

import socket
import logging

logger = logging.getLogger("Bridge")

class BridgeWin:
    """UDP Bridge for Windows"""

    def __init__(self, sys_queue, gui_queue, core_instance, bind_ip="0.0.0.0", port=5005):
        self.sys_queue = sys_queue
        self.gui_queue = gui_queue

        self.core = core_instance
        self.bind_ip = bind_ip
        self.port = port
        self.sock = None
        self.is_running = False

    def start(self):
        """Start UDP listener"""

        if self.is_running:
            return

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1.0)

        try:
            self.sock.bind((self.bind_ip, self.port))
            self.is_running = True

            logger.info(
                f"[BridgeWin] Listening on {self.bind_ip}:{self.port}"
            )

            while self.is_running:
                try:
                    data, addr = self.sock.recvfrom(65535)

                    if not data:
                        print("[BRDG] -")
                        continue

                    logger.info(
                        f"[BridgeWin] Received {len(data)} bytes from {addr}"
                    )

                    print(f"DATA HERE!! {data.hex(' ')}")

                    self.core.parsePacket(data)

                except socket.timeout:
                    continue

                except OSError:
                    if self.is_running:
                        raise

        except Exception:
            logger.exception("[BridgeWin Error]")

        finally:
            self.stop()

    def stop(self):
        """Stop UDP listener"""

        self.is_running = False

        if self.sock is not None:
            self.sock.close()
            self.sock = None

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
