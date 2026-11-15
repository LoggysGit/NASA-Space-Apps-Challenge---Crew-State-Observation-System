# Crew State Observation System (CSOS)

The Crew State Observation System (CSOS) is a wearable telemetry and health monitoring platform designed for people working in extreme environments, particularly astronauts during long-term space missions. 

The system consists of two parts:
1. **Desktop Application (Python):** Receives local wireless telemetry, filters signal noise, evaluates crew health indicators, and provides an offline medical reference archive.
2. **ESP32 Firmware Simulator (C++ / PlatformIO):** Emulates wearable sensor hardware by generating synthetic physiological and environmental telemetry and transmitting it over a local Wi-Fi network.

**Note on Hardware:** In the current prototype, the ESP32 firmware acts strictly as a **value simulator**, generating synthetic data streams to test and demonstrate the desktop software.

---

## Key Features

- **Completely Offline Operation:** All signal processing, anomaly detection, data logging, and medical guidance run locally without depending on an internet connection or cloud services.
- **CCSDS Space Packet Protocol:** Telemetry frames are structured using the CCSDS Primary Header format and validated with CRC-16/CCITT-FALSE checksums over local UDP.
- **Real-Time Signal Processing:** Filters incoming temperature, pressure, muscle activity (EMG), and optical PPG signals to estimate heart rate, blood oxygen saturation (SpO2), overall health, and stress levels.
- **Built-In Medical Reference Database:** Automatically suggests relevant first-aid tags (e.g., hypoxia, hypothermia, tachycardia) based on detected anomalies and includes an integrated PDF viewer for protocol documents.
- **Local Data Persistence:** Records raw packets and processed telemetry snapshots into per-crew JSONL `.dat` files for historical tracking.

---

## Installation and Setup

### 1. Desktop Application (Python)

**Requirements:**
- Python 3.10 to 3.12 (Windows or Linux)

**Steps:**
1. Open a terminal in the desktop application directory.
2. Create and activate a virtual environment (optional, recommended):
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux:
   source venv/bin/activate
   ```
3. Install the required dependencies:
   ```bash
   pip install .
   ```
   *(Alternatively: `pip install PyMuPDF customtkinter pillow pyqtgraph matplotlib pyserial "numpy>=1.26.0,<2.0.0" pandas scipy neurokit2 biosppy`)*

### 2. ESP32 Simulator Firmware (PlatformIO)

**Requirements:**
- ESP32 development board
- [Visual Studio Code](https://code.visualstudio.com/) with the [PlatformIO IDE](https://platformio.org/) extension

**Steps:**
1. Open the firmware directory in VS Code.
2. Open `include/config.h` and configure the network settings:
   - Enter your local Wi-Fi SSID and password.
   - Set `DEST_IP` to the local IP address of the computer running the desktop app.
   - Set `UDP_DEST_PORT` to match the app port (`5005` by default on Windows).
   - Set `DEVICE_ID_U8` (`0–127`) to assign a unique Crew ID to the device.
3. Connect the ESP32 via USB and upload the firmware:
   ```bash
   pio run --target upload
   ```

---

## Running the System

1. Ensure both the host computer and the ESP32 board are connected to the same local Wi-Fi network.
2. **Start the Desktop Application:**
   ```bash
   python app.py
   ```
   *(Note: `app.py` uses `BridgeWin` on UDP port `5005` by default. On Linux, switch to `BridgeLinux` in `app.py`, which listens on port `8888`.)*
3. **Register a Crew Profile:**
   - Open the **Settings** tab in the application.
   - Create a profile using the exact **Crew ID** (`0–127`) set in the ESP32 firmware.
4. **Start Telemetry Simulation:**
   - Power on the ESP32. You can monitor connection and packet logs via the Serial Monitor at `115200` baud.
   - If your board has buttons/display attached, use Button A and Button B to switch simulation scenarios and verify that packet transmission (`tx`) is enabled.
5. **Medical Protocols:**
   - Place any reference `.pdf` files (and optional `.meta` JSON files) inside the `fadocs/` directory to view and search them in the **Logs / Docs** tab.

---

## Repository Overview

- `app.py` — Application entry point launching the GUI and UDP listener thread.
- `modules/bridge.py` — Local UDP socket listeners for Windows and Linux.
- `modules/core.py` — CCSDS packet decoder, CRC validator, DSP filters, health/stress scoring, and `.dat` logger.
- `modules/interface.py` — CustomTkinter GUI, forearm sensor heatmaps, live charts, and PDF protocol archive.
- `src/main.cpp` — Main loop for the ESP32 telemetry simulator firmware.
- `users/` — Storage folder for per-crew `.dat` telemetry logs.
- `fadocs/` — Storage folder for offline medical PDF protocols.

---

_Code was written with SI tools_

Developed by Team30 for NASA Space Apps Challenge 2026