import os
import json
import time
import struct
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi
import neurokit2 as nk


class Core:
    """Decode, process and persist crew telemetry as append-only JSONL."""

    SCHEMA_VERSION = 2
    PPG_BUFFER_SIZE = 300
    SIGNAL_BUFFER_SIZE = 300
    MIN_FILTER_SAMPLES = 15
    CONNECTED_HOLD_SECONDS = 15.0

    FILTER_CONFIG = {
        "temperature": {"cutoff": 2.0, "fs": 10.0},
        "pressure": {"cutoff": 2.0, "fs": 10.0},
        "emg": {"lowcut": 20.0, "highcut": 45.0, "fs": 200.0},
    }

    def __init__(self, data_dir="users"):
        self.data_dir = os.fspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.ppg_buffers = defaultdict(
            lambda: {"r1": deque(maxlen=self.PPG_BUFFER_SIZE),
                     "ir1": deque(maxlen=self.PPG_BUFFER_SIZE),
                     "r2": deque(maxlen=self.PPG_BUFFER_SIZE),
                     "ir2": deque(maxlen=self.PPG_BUFFER_SIZE)}
        )
        self.signal_buffers = defaultdict(
            lambda: {
                "temperature": deque(maxlen=self.SIGNAL_BUFFER_SIZE),
                "pressure": deque(maxlen=self.SIGNAL_BUFFER_SIZE),
                "emg": deque(maxlen=self.SIGNAL_BUFFER_SIZE),
            }
        )
        self.latest_values = defaultdict(dict)
        self.field_timestamps = defaultdict(dict)
        # Minute-level derived indicators are intentionally memory-only.
        # They are not included in telemetry records and are never written to .dat.
        self.minute_metrics = defaultdict(dict)
        self.crew_metadata = {}

    def register_crew(self, crew_id: int, name: str, age: int,
                      role: str, gender: str) -> None:
        """Create or update the profile header without deleting telemetry history."""
        crew_id = int(crew_id)
        self.crew_metadata[crew_id] = {
            "schema": self.SCHEMA_VERSION,
            "crew_id": crew_id,
            "name": str(name),
            "age": int(age),
            "role": str(role),
            "gender": str(gender),
            "storage": {
                "format": "jsonl",
                "record_types": ["raw", "telemetry"],
                "retention": "unlimited",
                "timestamps": ["unix", "iso8601_utc"],
            },
        }
        self._write_metadata(crew_id)

    def _file_path(self, crew_id: int) -> str:
        return os.path.join(self.data_dir, f"{int(crew_id)}.dat")

    def _write_metadata(self, crew_id: int) -> None:
        """Rewrite only the metadata header and preserve every valid record."""
        path = self._file_path(crew_id)
        metadata = self.crew_metadata.get(crew_id)
        if metadata is None:
            return

        preserved = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "_meta" not in item:
                        preserved.append(line if line.endswith("\n") else line + "\n")

        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(
                {"_meta": metadata}, ensure_ascii=False, allow_nan=False
            ) + "\n")
            stream.writelines(preserved)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)

    @staticmethod
    def _crc16_ccitt(data: bytes) -> int:
        """CRC-16/CCITT-FALSE, matching the ESP32 firmware."""
        crc = 0xFFFF

        for byte in data:
            crc ^= byte << 8

            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF

        return crc

    def parse_packet(self, data: bytes) -> None:
        """Validate and decode one CCSDS telemetry packet."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return

        packet = bytes(data)

        # 6-byte header + 9-byte sensor prefix + at least 2-byte value + CRC.
        if len(packet) < 19:
            return

        try:
            word1, word2, data_len_field = struct.unpack(">HHH", packet[:6])
        except struct.error:
            return

        apid = word1 & 0x07FF
        crew_id = (apid >> 4) & 0x7F
        sensor_id = apid & 0x0F
        seq_count = word2 & 0x3FFF

        # CCSDS length field = data field length - 1.
        if data_len_field != len(packet) - 7:
            return

        # Verify CRC before decoding.
        received_crc = struct.unpack(">H", packet[-2:])[0]
        calculated_crc = self._crc16_ccitt(packet[:-2])

        if received_crc != calculated_crc:
            return

        # INFO packets are not sensor telemetry.
        if sensor_id == 0x0F:
            return

        # Exclude the 6-byte CCSDS header and 2-byte CRC.
        payload = packet[6:-2]

        received_at = time.time()

        parsed_sensors = self._decode_payload(sensor_id, payload)

        if not parsed_sensors:
            return

        if not all(np.isfinite(value) for value in parsed_sensors.values()):
            return

        raw_record = {
            "record_type": "raw",
            "timestamp": received_at,
            "iso_time": datetime.fromtimestamp(
                received_at, timezone.utc
            ).isoformat(),
            "sensor_id": sensor_id,
            "seq_count": seq_count,
            "data_length_field": data_len_field,
            "payload_length": len(payload),
            "packet_length": len(packet),
            "payload_hex": payload.hex(),
            "packet_hex": packet.hex(),
            "data": parsed_sensors,
        }

        self._update_processed_values(
            crew_id, sensor_id, parsed_sensors, received_at
        )

        telemetry_record = self._build_telemetry_record(
            crew_id, sensor_id, seq_count, received_at
        )

        self._append_records(
            crew_id, (raw_record, telemetry_record)
        )

    def _decode_payload(self, sensor_id: int, payload: bytes) -> dict:
        """Decode the ESP32 packed integer telemetry format."""

        # Payload layout:
        # [0:7] timestamp
        # [7]   scenario ID
        # [8]   flags
        # [9:]  sensor values

        DATA_OFFSET = 9

        if len(payload) < DATA_OFFSET:
            return {}

        try:
            data = payload[DATA_OFFSET:]

            # CAT_TMP117 = 0x1
            # 4 x uint16, temperature * 100.
            if sensor_id == 0x1 and len(data) >= 8:
                values = struct.unpack(">4H", data[:8])

                return {
                    f"t{i + 1}": value / 100.0
                    for i, value in enumerate(values)
                }

            # CAT_BMP390 = 0x2
            # 4 x uint32, pressure in Pa * 100.
            if sensor_id == 0x2 and len(data) >= 16:
                values = struct.unpack(">4I", data[:16])

                return {
                    f"p{i + 1}": value / 100000.0
                    for i, value in enumerate(values)
                }

            # CAT_ADS1298 = 0x3
            # 6 x uint16, EMG in microvolts.
            if sensor_id == 0x3 and len(data) >= 12:
                values = struct.unpack(">6H", data[:12])

                return {
                    f"e{i + 1}": float(value)
                    for i, value in enumerate(values)
                }

            # CAT_MAX86141 = 0x4
            # 4 x uint32, optical sensor counts.
            if sensor_id == 0x4 and len(data) >= 16:
                values = struct.unpack(">4I", data[:16])

                return dict(zip(
                    ("r1", "ir1", "r2", "ir2"),
                    map(float, values)
                ))

            # CAT_SHT45 = 0x5
            # uint16, relative humidity * 100.
            if sensor_id == 0x5 and len(data) >= 2:
                value = struct.unpack(">H", data[:2])[0]
                return {"h1": value / 100.0}

            # CAT_BQ27441 = 0x6
            # uint16, battery voltage in millivolts.
            if sensor_id == 0x6 and len(data) >= 2:
                value = struct.unpack(">H", data[:2])[0]
                return {"vbat": value / 1000.0}

        except struct.error:
            return {}

        return {}

    def _update_processed_values(self, crew_id: int, sensor_id: int,
                                 data: dict, timestamp: float) -> None:
        """Update rolling signal buffers and latest processed telemetry."""
        values = self.latest_values[crew_id]
        stamps = self.field_timestamps[crew_id]
        buffers = self.signal_buffers[crew_id]

        # CAT_TMP117 = 0x1 — temperature
        if sensor_id == 0x1:
            nodes = [data[key] for key in ("t1", "t2", "t3", "t4")]
            buffers["temperature"].append(float(np.mean(nodes)))

            values["temperature_nodes_c"] = dict(data)
            values["temperature_map_c"] = self._get_avg_temp(
                list(buffers["temperature"])
            )

            stamps["temperature_nodes_c"] = timestamp
            stamps["temperature_map_c"] = timestamp

        # CAT_BMP390 = 0x2 — pressure
        elif sensor_id == 0x2:
            nodes = [data[key] for key in ("p1", "p2", "p3", "p4")]
            buffers["pressure"].append(float(np.mean(nodes)))

            values["pressure_nodes_kpa"] = dict(data)
            values["pressure_map_kpa"] = self._get_avg_pressure(
                list(buffers["pressure"])
            )

            stamps["pressure_nodes_kpa"] = timestamp
            stamps["pressure_map_kpa"] = timestamp

        # CAT_ADS1298 = 0x3 — EMG
        elif sensor_id == 0x3:
            nodes = [data[f"e{i}"] for i in range(1, 7)]
            buffers["emg"].append(tuple(float(value) for value in nodes))

            values["emg_nodes_uv"] = dict(data)
            values["emg_rms_uv"] = self._get_emg_rms(
                list(buffers["emg"])
            )

            stamps["emg_nodes_uv"] = timestamp
            stamps["emg_rms_uv"] = timestamp

        # CAT_MAX86141 = 0x4 — PPG
        elif sensor_id == 0x4:
            ppg = self.ppg_buffers[crew_id]

            for key, value in data.items():
                ppg[key].append(float(value))

            pulse, spo2, blood_velocity = self._compute_pulse_spo2(crew_id)

            values["pulse_bpm"] = pulse
            values["spo2_pct"] = spo2
            values["blood_velocity_cm_s"] = blood_velocity
            values["ppg_raw"] = dict(data)

            stamps["ppg_raw"] = timestamp

            if pulse is not None:
                stamps["pulse_bpm"] = timestamp

            if spo2 is not None:
                stamps["spo2_pct"] = timestamp

            if blood_velocity is not None:
                stamps["blood_velocity_cm_s"] = timestamp

        # CAT_SHT45 = 0x5 — humidity
        elif sensor_id == 0x5:
            values["humidity_pct"] = float(data["h1"])
            stamps["humidity_pct"] = timestamp

        # CAT_BQ27441 = 0x6 — battery
        elif sensor_id == 0x6:
            values["battery_voltage_v"] = float(data["vbat"])
            stamps["battery_voltage_v"] = timestamp

    def _build_telemetry_record(self, crew_id: int, sensor_id: int,
                                seq_count: int, timestamp: float) -> dict:
        """Create a timestamped snapshot of the latest known telemetry fields."""
        values = dict(self.latest_values[crew_id])
        return {
            "record_type": "telemetry",
            "timestamp": timestamp,
            "iso_time": datetime.fromtimestamp(
                timestamp, timezone.utc
            ).isoformat(),
            "source": {
                "sensor_id": sensor_id,
                "seq_count": seq_count,
            },
            "data": values,
            "field_timestamps": dict(self.field_timestamps[crew_id]),
            "units": {
                "temperature_nodes_c": "degC",
                "temperature_map_c": "degC",
                "pressure_nodes_kpa": "kPa",
                "pressure_map_kpa": "kPa",
                "emg_nodes_uv": "uV",
                "emg_rms_uv": "uV",
                "pulse_bpm": "bpm",
                "spo2_pct": "%",
                "blood_velocity_cm_s": "cm/s",
                "humidity_pct": "%",
                "battery_voltage_v": "V",
            },
            "processing": {
                "temperature": "Butterworth low-pass then mean",
                "pressure": "Butterworth low-pass then mean",
                "ppg": "NeuroKit2 cleaning and peak detection on r1 at 100 Hz",
                "spo2": "Ratio-of-ratios estimate; not a clinical measurement",
                "blood_velocity": "Legacy heuristic: 10 + pulse_bpm * 0.05",
                "temperature_pressure": "Rolling packet means, then Butterworth filtering",
                "emg": "Channel-wise Butterworth filtering, then window RMS",
                "window_size_packets": self.SIGNAL_BUFFER_SIZE,
                "filter_config": self.FILTER_CONFIG,
            },
        }

    def _append_records(self, crew_id: int, records: tuple) -> None:
        """Append records without truncating or rewriting telemetry history."""
        path = self._file_path(crew_id)
        os.makedirs(self.data_dir, exist_ok=True)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0

        with open(path, "a", encoding="utf-8") as stream:
            if new_file and crew_id in self.crew_metadata:
                stream.write(json.dumps(
                    {"_meta": self.crew_metadata[crew_id]},
                    ensure_ascii=False, allow_nan=False,
                ) + "\n")
            for record in records:
                stream.write(json.dumps(
                    record, ensure_ascii=False, allow_nan=False
                ) + "\n")
            stream.flush()

    def _butter_bandpass(self, lowcut: float, highcut: float,
                         fs: float, order: int = 4):
        """Design Butterworth band-pass coefficients."""
        nyquist = 0.5 * fs
        if not 0 < lowcut < highcut < nyquist:
            raise ValueError("Band-pass frequencies must be below Nyquist")
        return butter(
            order, [lowcut / nyquist, highcut / nyquist], btype="band"
        )

    def _filter_signal(self, data: np.ndarray, lowcut: float,
                       highcut: float, fs: float, order: int = 4) -> np.ndarray:
        """Filter a signal when enough samples are available."""
        values = np.asarray(data, dtype=float)
        if values.size < self.MIN_FILTER_SAMPLES:
            return values
        if not np.all(np.isfinite(values)):
            values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
        b, a = self._butter_bandpass(lowcut, highcut, fs, order)
        initial_state = lfilter_zi(b, a) * values[0]
        filtered, _ = lfilter(b, a, values, zi=initial_state)
        return filtered

    def _lowpass_signal(self, data: np.ndarray, cutoff: float,
                        fs: float, order: int = 4) -> np.ndarray:
        """Apply low-pass filtering without removing the DC level."""
        values = np.asarray(data, dtype=float)
        if values.size < self.MIN_FILTER_SAMPLES:
            return values
        nyquist = 0.5 * fs
        if not 0 < cutoff < nyquist:
            raise ValueError("Low-pass cutoff must be below Nyquist")
        b, a = butter(order, cutoff / nyquist, btype="low")
        initial_state = lfilter_zi(b, a) * values[0]
        filtered, _ = lfilter(b, a, values, zi=initial_state)
        return filtered

    def _get_avg_temp(self, t_list: list) -> float | None:
        """Return the mean of low-pass filtered temperature samples."""
        if not t_list:
            return None
        filtered = self._lowpass_signal(
            np.asarray(t_list, dtype=float),
            **self.FILTER_CONFIG["temperature"],
        )
        return float(np.mean(filtered))

    def _get_avg_pressure(self, p_list: list) -> float | None:
        """Return the mean of low-pass filtered pressure samples."""
        if not p_list:
            return None
        filtered = self._lowpass_signal(
            np.asarray(p_list, dtype=float),
            **self.FILTER_CONFIG["pressure"],
        )
        return float(np.mean(filtered))

    def _get_emg_rms(self, e_list: list) -> float | None:
        """ Return RMS of the filtered EMG """
        if not e_list:
            return None
        samples = np.asarray(e_list, dtype=float)
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        if samples.ndim != 2:
            return None
        if samples.shape[0] < self.MIN_FILTER_SAMPLES:
            filtered = samples
        else:
            config = self.FILTER_CONFIG["emg"]
            b, a = self._butter_bandpass(
                config["lowcut"], config["highcut"], config["fs"]
            )
            initial_state = lfilter_zi(b, a)[:, None] * samples[0][None, :]
            filtered, _ = lfilter(
                b, a, samples, axis=0, zi=initial_state
            )
        return float(np.sqrt(np.mean(np.square(filtered))))

    def _compute_pulse_spo2(self, crew_id: int) -> tuple:
        """Calculate pulse, SpO2 estimate and velocity from the PPG window."""
        buffer = self.ppg_buffers[crew_id]
        red = np.asarray(buffer["r1"], dtype=float)
        infrared = np.asarray(buffer["ir1"], dtype=float)
        if red.size < 30 or infrared.size < 30:
            return None, None, None

        try:
            cleaned = nk.ppg_clean(red, sampling_rate=100)
            _, info = nk.ppg_peaks(cleaned, sampling_rate=100)
            peaks = info.get("PPG_Peaks", [])
            if len(peaks) < 2:
                return None, None, None

            rates = nk.signal_rate(peaks, sampling_rate=100)
            pulse = float(np.mean(rates))
            if not np.isfinite(pulse):
                return None, None, None

            red_dc = float(np.mean(red))
            infrared_dc = float(np.mean(infrared))
            blood_velocity = float(10.0 + pulse * 0.05)
            red_ac = float(np.std(red))
            infrared_ac = float(np.std(infrared))
            if red_dc == 0 or infrared_dc == 0 or infrared_ac == 0:
                return pulse, None, blood_velocity

            ratio = (red_ac / red_dc) / (infrared_ac / infrared_dc)
            spo2 = float(np.clip(110.0 - 25.0 * ratio, 70.0, 100.0))
            return pulse, spo2, blood_velocity
        except Exception:
            return None, None, None

    @staticmethod
    def _bounded_score(value: float, low: float, high: float) -> float:
        """Map a value in [low, high] to a 0..100 score."""
        if high <= low:
            return 0.0
        return float(np.clip((value - low) * 100.0 / (high - low), 0.0, 100.0))

    @staticmethod
    def _distance_score(value: float, ideal_low: float, ideal_high: float,
                        zero_low: float, zero_high: float) -> float:
        """100 inside the ideal interval, linearly falling to zero at limits."""
        if ideal_low <= value <= ideal_high:
            return 100.0
        if value < ideal_low:
            span = ideal_low - zero_low
            return 0.0 if span <= 0 else float(np.clip(
                (value - zero_low) * 100.0 / span, 0.0, 100.0
            ))
        span = zero_high - ideal_high
        return 0.0 if span <= 0 else float(np.clip(
            (zero_high - value) * 100.0 / span, 0.0, 100.0
        ))

    @staticmethod
    def _finite_metric(data: dict, key: str):
        value = data.get(key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None

    def _calculate_overall_health(self) -> dict[int, float | None]:
        """Calculate a heuristic 0..100 condition score for every known crew member.

        The score is not a diagnosis. Missing measurements are omitted and the
        remaining weights are re-normalized. Pressure and humidity use broad
        engineering ranges because this file does not define clinical limits.
        """
        results = {}
        for crew_id, data in self.latest_values.items():
            components = []

            temp = self._finite_metric(data, "temperature_map_c")
            if temp is not None:
                components.append((0.25, self._distance_score(
                    temp, 36.5, 37.5, 30.0, 42.0
                )))

            pressure = self._finite_metric(data, "pressure_map_kpa")
            if pressure is not None:
                components.append((0.10, self._distance_score(
                    pressure, 8.0, 20.0, 3.0, 30.0
                )))

            bpm = self._finite_metric(data, "pulse_bpm")
            if bpm is not None:
                components.append((0.20, self._distance_score(
                    bpm, 60.0, 100.0, 35.0, 180.0
                )))

            spo2 = self._finite_metric(data, "spo2_pct")
            if spo2 is not None:
                components.append((0.30, self._distance_score(
                    spo2, 96.0, 100.0, 70.0, 100.0
                )))

            emg = self._finite_metric(data, "emg_rms_uv")
            if emg is not None:
                # Broad signal-load penalty; tune against the actual sensor
                # calibration/baseline before treating this as physiological.
                components.append((0.10, 100.0 - self._bounded_score(
                    emg, 150.0, 1500.0
                )))

            humidity = self._finite_metric(data, "humidity_pct")
            if humidity is not None:
                components.append((0.05, self._distance_score(
                    humidity, 30.0, 60.0, 0.0, 100.0
                )))

            total_weight = sum(weight for weight, _ in components)
            score = (
                round(sum(weight * value for weight, value in components)
                      / total_weight, 1)
                if total_weight else None
            )
            self.minute_metrics[crew_id]["overall_health_pct"] = score
            results[crew_id] = score
        return results

    def _calculate_stress(self) -> dict[int, float | None]:
        """Estimate a 0..100 stress/load index from the latest available signals.

        This is a heuristic indicator, not a clinical stress measurement.
        """
        results = {}
        for crew_id, data in self.latest_values.items():
            components = []

            bpm = self._finite_metric(data, "pulse_bpm")
            if bpm is not None:
                components.append((0.40, self._bounded_score(
                    bpm, 55.0, 130.0
                )))

            temp = self._finite_metric(data, "temperature_map_c")
            if temp is not None:
                # Temperature deviation can contribute to physiological load.
                temp_load = 100.0 - self._distance_score(
                    temp, 36.5, 37.5, 34.0, 40.0
                )
                components.append((0.20, temp_load))

            emg = self._finite_metric(data, "emg_rms_uv")
            if emg is not None:
                components.append((0.30, self._bounded_score(
                    emg, 100.0, 1200.0
                )))

            humidity = self._finite_metric(data, "humidity_pct")
            if humidity is not None:
                humidity_load = 100.0 - self._distance_score(
                    humidity, 30.0, 60.0, 0.0, 100.0
                )
                components.append((0.10, humidity_load))

            total_weight = sum(weight for weight, _ in components)
            score = (
                round(sum(weight * value for weight, value in components)
                      / total_weight, 1)
                if total_weight else None
            )
            self.minute_metrics[crew_id]["stress_pct"] = score
            results[crew_id] = score
        return results

    def _get_metatags_array(self) -> dict[int, list[str]]:
        """Return lowercase first-aid tags derived from each crew's latest data."""
        stress_by_crew = self._calculate_stress()
        results = {}

        for crew_id, data in self.latest_values.items():
            tags = []

            temp = self._finite_metric(data, "temperature_map_c")
            spo2 = self._finite_metric(data, "spo2_pct")
            bpm = self._finite_metric(data, "pulse_bpm")
            stress = stress_by_crew.get(crew_id)

            if spo2 is not None:
                if spo2 < 90.0:
                    tags.append("hypoxia")
                elif spo2 >= 99.5:
                    # The source only provides SpO2 (not arterial oxygen tension).
                    tags.append("hyperxia")

            if temp is not None:
                if temp < 35.0:
                    tags.append("hypothermia")
                elif temp >= 39.0:
                    tags.append("hyperthermia")

            if stress is not None and stress >= 60.0:
                tags.append("stress")

            if bpm is not None:
                if bpm < 50.0:
                    tags.append("bradycardia")
                elif bpm > 110.0:
                    tags.append("tachycardia")

            self.minute_metrics[crew_id]["metatags"] = tags
            results[crew_id] = tags

        return results
