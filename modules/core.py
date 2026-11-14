import os
import json
import time
import struct
from datetime import datetime
import numpy as np
from scipy.signal import butter, lfilter
import neurokit2 as nk

class Core:
    def __init__(self, data_dir="users"):
        self.data_dir = data_dir
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        # Buffer for signal processing (per member)
        self.ppg_buffers = {}

    def parse_packet(self, data: bytes) -> None:
        """Parse raw telemetry packet."""
        if len(data) < 6:
            return
        
        timestamp = time.time()
        
        word1, word2, data_len_field = struct.unpack('>HHH', data[:6])
        apid = word1 & 0x7FF
        crew_id = (apid >> 4) & 0x7F
        sensor_id = apid & 0x0F
        seq_count = word2 & 0x3FFF
        
        payload = data[6:]
        parsed_sensors = self._decode_payload(sensor_id, payload)
        
        # Accumulate real buffer for PPG sensor
        if sensor_id == 0x3:
            if crew_id not in self.ppg_buffers:
                self.ppg_buffers[crew_id] = {"r1": [], "ir1": [], "r2": [], "ir2": []}

            for k, v in parsed_sensors.items():
                if k in self.ppg_buffers[crew_id]:
                    self.ppg_buffers[crew_id][k].append(v)
                    if len(self.ppg_buffers[crew_id][k]) > 300:
                        self.ppg_buffers[crew_id][k].pop(0)

            parsed_sensors["computed_metrics"] = self._compute_pulse_spo2(crew_id)

        self._save_point_to_file(crew_id, sensor_id, timestamp, seq_count, parsed_sensors)

    def _decode_payload(self, sensor_id: int, payload: bytes) -> dict:
        """Decode raw payload bytes into units"""
        if sensor_id == 0x0:
            vals = struct.unpack('4f', payload[:16]) if len(payload) >= 16 else (0,0,0,0)
            return {"t1": vals[0], "t2": vals[1], "t3": vals[2], "t4": vals[3]}
        elif sensor_id == 0x1:
            vals = struct.unpack('4f', payload[:16]) if len(payload) >= 16 else (0,0,0,0)
            return {"p1": vals[0], "p2": vals[1], "p3": vals[2], "p4": vals[3]}
        elif sensor_id == 0x2:
            vals = struct.unpack('6f', payload[:24]) if len(payload) >= 24 else (0,0,0,0,0,0)
            return {f"e{i+1}": vals[i] for i in range(6)}
        elif sensor_id == 0x3:
            vals = struct.unpack('4f', payload[:16]) if len(payload) >= 16 else (0,0,0,0)
            return {"r1": vals[0], "ir1": vals[1], "r2": vals[2], "ir2": vals[3]}
        elif sensor_id == 0x4:
            val = struct.unpack('f', payload[:4])[0] if len(payload) >= 4 else 0.0
            return {"h1": val}
        elif sensor_id == 0x5:
            val = struct.unpack('f', payload[:4])[0] if len(payload) >= 4 else 0.0
            return {"vbat": val}
        return {}

    def _butter_bandpass(self, lowcut: float, highcut: float, fs: float, order: int = 4):
        """ Design Butterworth filter """
        nyq = 0.5 * fs
        low = lowcut / nyq
        high = highcut / nyq
        b, a = butter(order, [low, high], btype='band')
        return b, a

    def _filter_signal(self, data: np.ndarray, lowcut: float = 0.5, highcut: float = 10.0, fs: float = 100.0) -> np.ndarray:
        """ Apply digital filter """
        if len(data) < 15:
            return data
        b, a = self._butter_bandpass(lowcut, highcut, fs, order=4)
        return lfilter(b, a, data)

    def _get_avg_temp(self, t_list: list) -> float:
        """ Average temperature """
        if not t_list: return 0.0
        arr = np.array(t_list)
        filtered = self._filter_signal(arr, lowcut=0.1, highcut=2.0, fs=10.0)
        return float(np.mean(filtered))

    def _get_avg_pressure(self, p_list: list) -> float:
        """ Average pressure """
        if not p_list: return 0.0
        arr = np.array(p_list)
        filtered = self._filter_signal(arr, lowcut=0.1, highcut=2.0, fs=10.0)
        return float(np.mean(filtered))

    def _get_emg_rms(self, e_list: list) -> float:
        """ Calculate RMS of EMG signal """
        if not e_list: return 0.0
        arr = np.array(e_list)
        filtered = self._filter_signal(arr, lowcut=20.0, highcut=45.0, fs=200.0)
        return float(np.sqrt(np.mean(filtered ** 2)))

    def _compute_pulse_spo2(self, crew_id: int) -> tuple:
        """ Process accumulated PPG buffer """
        buf = self.ppg_buffers.get(crew_id, {})
        r1_arr = np.array(buf.get("r1", []))
        ir1_arr = np.array(buf.get("ir1", []))
        
        if len(r1_arr) < 30:
            return 72.0, 98.0, 12.0 # Mock values (first milliseconds, we need to gain enough data to process)
            
        try:
            cleaned = nk.ppg_clean(r1_arr, sampling_rate=100)
            signals, info = nk.ppg_peaks(cleaned, sampling_rate=100)
            peaks = info.get("PPG_Peaks", [])
            
            hr = float(np.mean(nk.signal_rate(peaks, sampling_rate=100))) if len(peaks) > 0 else 72.0
            if np.isnan(hr): hr = 72.0
            
            ac_r, dc_r = np.std(r1_arr), np.mean(r1_arr) if np.mean(r1_arr) != 0 else 1.0
            ac_ir, dc_ir = np.std(ir1_arr), np.mean(ir1_arr) if np.mean(ir1_arr) != 0 else 1.0
            r_ratio = (ac_r / dc_r) / (ac_ir / dc_ir) if dc_ir != 0 and dc_r != 0 else 1.0
            spo2 = float(max(70.0, min(100.0, 110.0 - 25.0 * r_ratio)))
            
            blood_vel = float(10.0 + (hr * 0.05))
            return hr, spo2, blood_vel
        except Exception:
            return None, None, None

    def _save_point_to_file(self, crew_id: int, sensor_id: int, timestamp: float, seq_count: int, data: dict) -> None:
        """ Append point to dat file (24-hour window) """
        file_path = os.path.join(self.data_dir, f"{crew_id}.dat")
        
        record = {
            "timestamp": timestamp,
            "iso_time": datetime.fromtimestamp(timestamp).isoformat(),
            "sensor_id": sensor_id,
            "seq_count": seq_count,
            "data": data
        }
        
        cutoff_time = timestamp - 86400
        valid_records = []
        
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            item = json.loads(line)
                            if item.get("timestamp", 0) >= cutoff_time:
                                valid_records.append(item)
            except Exception:
                pass
                
        valid_records.append(record)
        
        with open(file_path, "w", encoding="utf-8") as f:
            for item in valid_records:
                f.write(json.dumps(item) + "\n")

    def get_metatags(telemetry: list) -> list:
        """ Returns an array of metatags depending on member's state """
        #AVAILABLE: hypoxia,hyperxia,hightemperature,highpressure,lowpressure,