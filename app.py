""" Main app file """

import threading
from queue import Queue

from modules import lib, bridge, interface

def system_thread(
        sys_buffer: Queue,
        gui_buffer: Queue,
        interface: interface.Interface,
        bridge: bridge.BridgeWin):
    """ System thread """

    bridge.start()

    while True:
        item = sys_buffer.get()

        try:
            command, payload = item

            match command:
                # Unknown command #
                case _:
                    lib.dbg(f"Unknown command: {command}")

        except ValueError:
            lib.log(f"Wrong command structure (CMD, PAYLOAD): {item}")

        except Exception as e:
            lib.log(f"System thread error: {e}")

        finally:
            sys_buffer.task_done()

if __name__ == "__main__":
    # App command queues
    sys_command_buffer = Queue()
    gui_command_buffer = Queue()

    # App objects
    app = interface.Interface()

    # Start system daemon
    sys_thread = threading.Thread(
        target=system_thread,
        args=(sys_command_buffer, gui_command_buffer),
        daemon=True)
    sys_thread.start()

    # Start app loop
    def on_closing():
        """ All-threads close function """
        lib.log("App closing...")
        sys_command_buffer.put(("CLOSE", None))
        app.destroy()

    app.mainloop()
