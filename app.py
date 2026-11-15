""" Main app file """

import threading
from queue import Queue

from modules import bridge, interface, core

def system_thread(
        sys_buffer: Queue,
        gui_buffer: Queue,
        interface: interface.Interface,
        bridge: bridge.BridgeWin):
    """ System thread """

    bridge.start()

if __name__ == "__main__":
    # App command queues (RESERVED)
    sys_command_buffer = Queue()
    gui_command_buffer = Queue()

    # App objects
    app_core = core.Core()
    esp_bridge = bridge.BridgeWin(sys_command_buffer, gui_command_buffer, app_core)
    app = interface.Interface()

    # Start system daemon
    sys_thread = threading.Thread(
        target=system_thread,
        args=(sys_command_buffer, gui_command_buffer, app, esp_bridge),
        daemon=True)
    sys_thread.start()

    # Start app loop
    app.mainloop()
