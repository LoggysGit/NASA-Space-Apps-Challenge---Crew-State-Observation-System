"""cSOS desktop interface: crew overview, detailed telemetry and protocol archive."""

import binascii
import math
import random
import struct
import tkinter as tk
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYSTEM_NAME = "cSOS (Crew State Observation System)"
APP_TITLE = SYSTEM_NAME
WINDOW_SIZE = "1360x860"

CARD_BG = "#2b2b2b"
PLOT_BG = "#1a1a1a"
MUTED = "gray60"
GREEN = "#1DB954"
RED = "#e5534b"
BAND_COLOR = "#4fc3f7"
ACCENT = "#1f6aa5"
INACTIVE = "#3a3a3a"
MONO = "Courier New"

# Protocol archive palette
DOC_CARD = "#1c2128"
DOC_CARD_HOVER = "#262d36"
DOC_BORDER = "#30363d"
DOC_MUTED = "#8b949e"
DOC_GOLD = "#f5b301"
DOC_STAR_EMPTY = "#3a3f47"
DOC_PANEL = "#161b22"
DOC_MODAL_BG = "#0d1117"

# Simulation / buffers
SIM_MS = 500
HIST_LEN = 120
LOG_MAX = 300
LOG_VISIBLE = 80
HISTORY_KEYS = ("temp", "pulse", "spo2")
LINK_LOST_MESSAGE = "-- LINK LOST: no packets received --"
PPG_CHANNELS = ((84000, 1.0), (121000, 1.3), (81000, 0.9), (118000, 1.2))

# Heatmap geometry (cells of 11 px -> 330 x 176 canvas)
HEAT_COLS = 30
HEAT_ROWS = 16
HEAT_CELL = 11
HEAT_W = HEAT_COLS * HEAT_CELL
HEAT_H = HEAT_ROWS * HEAT_CELL
ASPECT = HEAT_W / HEAT_H

# Health -> border colour stops (percent, RGB)
HEALTH_STOPS = [
    (8, (0x5C, 0x0A, 0x0A)),   # deep maroon at <= 8 %
    (50, (0xE8, 0xA3, 0x17)),  # amber at 50 %
    (98, (0x1D, 0xB9, 0x54)),  # bright green at >= 98 %
]

# Spectrogram stops (0..1): deep blue (min) -> crimson (max)
SPECTRUM_STOPS = [
    (0.00, (0x0B, 0x1F, 0x8F)),
    (0.25, (0x1E, 0x90, 0xFF)),
    (0.50, (0x2E, 0xCC, 0x71)),
    (0.75, (0xF4, 0xD0, 0x3F)),
    (1.00, (0xD0, 0x02, 0x1B)),
]
PALETTE_SIZE = 64

# Ground link state (replace with a real link monitor later)
EARTH_LINK_ON = False

PAGE_TELEMETRY = 1

# Sidebar items: (button label, page title)
NAV_ITEMS = [
    ("▦  Overview", "Crew Overview"),
    ("∿  Telemetry", "Detailed Telemetry"),
    ("☰  Logs / Docs", "Logs & Docs"),
    ("⚙  Settings", "Settings"),
]


class LayerSpec:
    """One sensor-map layer (temperature, pressure or EMG)."""

    def __init__(self, prefix: str, count: int, unit: str, vmin: float, vmax: float,
                 decimals: int, title: str) -> None:
        self.prefix = prefix
        self.count = count
        self.unit = unit
        self.vmin = vmin
        self.vmax = vmax
        self.decimals = decimals
        self.title = title


LAYERS = {
    "T": LayerSpec("t", 4, "C", 35.0, 40.0, 1, "t Map [C]"),
    "P": LayerSpec("p", 4, "kPa", 99.0, 106.0, 2, "p Map [kPa]"),
    "E": LayerSpec("e", 6, "muV", 0.0, 600.0, 0, "em Map [muV]"),
}

# Node positions on the forearm schematic (x: wrist -> elbow, y: top -> bottom)
NODE_POS = {
    "T": ((0.20, 0.25), (0.20, 0.75), (0.80, 0.25), (0.80, 0.75)),
    "P": ((0.35, 0.50), (0.50, 0.28), (0.50, 0.72), (0.65, 0.50)),
    "E": ((0.10, 0.50), (0.30, 0.25), (0.30, 0.75), (0.70, 0.25), (0.70, 0.75), (0.90, 0.50)),
}
T_OFFSETS = (-0.6, 0.2, 0.5, -0.2)
P_OFFSETS = (-0.3, 0.2, 0.4, -0.1)


class ChartSpec:
    """One strip chart on the telemetry page."""

    def __init__(self, title: str, key: str, unit: str, color: str,
                 min_span: float, decimals: int) -> None:
        self.title = title
        self.key = key
        self.unit = unit
        self.color = color
        self.min_span = min_span
        self.decimals = decimals


CHARTS = (
    ChartSpec("Temperature trend (t_avg)", "temp", "°C", "#ff8a65", 1.0, 1),
    ChartSpec("Pulse rate trend", "pulse", "BPM", "#ef5350", 20.0, 0),
    ChartSpec("SpO2 trend", "spo2", "%", "#4fc3f7", 4.0, 1),
)

# TELEMETRY rows: (label, unit). The three map rows open the matching sensor map.
TELEMETRY_ROWS = (
    ("Avg. t", "C"),
    ("Avg. p", "kPa"),
    ("emRMS (Avg. e)", "muV"),
    ("t Map", "[C]"),
    ("p Map", "[kPa]"),
    ("em Map", "[muV]"),
    ("pulse", "bpm"),
    ("spo2", "%"),
    ("blood vel", "cm/s"),
    ("humidity", "%"),
    ("+charge", "%"),
)
MAP_LAYER_BY_ROW = {"t Map": "T", "p Map": "P", "em Map": "E"}


class RawSensor:
    """A sensor whose parameters refresh together every `period` ticks."""

    def __init__(self, range_label: str, sensor: str, params: tuple, unit: str,
                 decimals: int, period: int) -> None:
        self.range_label = range_label
        self.sensor = sensor
        self.params = params
        self.unit = unit
        self.decimals = decimals
        self.period = period


RAW_SENSORS = (
    RawSensor("t1-t4", "TI TMP117", ("t1", "t2", "t3", "t4"), "C", 2, 4),
    RawSensor("p1-p4", "BMP390", ("p1", "p2", "p3", "p4"), "kPa", 2, 2),
    RawSensor("e1-e6", "TI ADS1298", ("e1", "e2", "e3", "e4", "e5", "e6"), "muV", 0, 1),
    RawSensor("r1, ir1 + r2, ir2", "ADI MAX86141", ("r1", "ir1", "r2", "ir2"), "counts", 0, 1),
    RawSensor("h1", "SHT45", ("h1",), "%", 1, 6),
    RawSensor("+vBat", "BQ27441-G1", ("+vBat",), "V", 3, 10),
)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
def interpolate_color(stops: list, value: float) -> str:
    """Linearly interpolate a hex colour between (position, RGB) stops."""
    if value <= stops[0][0]:
        rgb = stops[0][1]
    elif value >= stops[-1][0]:
        rgb = stops[-1][1]
    else:
        rgb = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= value <= p1:
                t = (value - p0) / (p1 - p0)
                rgb = tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
                break
    red, green, blue = rgb
    return f"#{red:02X}{green:02X}{blue:02X}"


def health_color(health: float) -> str:
    """Map overall health (0-100) to a hex border colour."""
    return interpolate_color(HEALTH_STOPS, health)


SPECTRUM_PALETTE = [
    interpolate_color(SPECTRUM_STOPS, i / (PALETTE_SIZE - 1)) for i in range(PALETTE_SIZE)
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class CrewMember:
    """Static crew data plus the live state updated by the simulation."""

    def __init__(self, crew_id: str, name: str, gender: str, age: int, role: str,
                 overall_health: int, stress: int, temp: float, press: float,
                 pulse: float, spo2: float, connected: bool, battery: int,
                 rssi: int, loss: float, uptime: float, fw: str) -> None:
        self.crew_id = crew_id
        self.name = name
        self.gender = gender
        self.age = age
        self.role = role
        self.overall_health = overall_health
        self.stress = stress
        self.temp = temp
        self.press = press
        self.pulse = pulse
        self.spo2 = spo2
        self.connected = connected
        self.battery = battery
        self.rssi = rssi
        self.loss = loss
        self.uptime = uptime
        self.fw = fw

        # Resting values the simulation oscillates around
        self.base_temp = temp
        self.base_press = press
        self.base_pulse = pulse
        self.base_spo2 = spo2

        # Live state
        self.apid = int(crew_id.split("APID-")[1])
        self.seq = 0
        self.last_packet = datetime.now(timezone.utc)
        self.humidity = 45.0
        self.vel = 0.0
        self.ppg = [0, 0, 0, 0]
        self.nodes = {"t": [], "p": [], "e": []}
        self.hist = {key: deque(maxlen=HIST_LEN) for key in HISTORY_KEYS}
        self.log = deque(maxlen=LOG_MAX)
        self.raw = {}  # parameter name -> (value, timestamp)


def sample_crew() -> list[CrewMember]:
    """Return the demo crew; replace with real data later."""
    return [
        CrewMember("0x01 / APID-016", "Elena Voronova", "F", 34, "Mission Specialist",
                   99, 12, 36.6, 101.4, 68, 98, True, 100, -48, 0.1, 9240, "1.4.2"),
        CrewMember("0x02 / APID-017", "James Carter", "M", 41, "Commander",
                   65, 41, 37.4, 102.6, 88, 95, True, 72, -57, 0.4, 18130, "1.4.2"),
        CrewMember("0x03 / APID-018", "Mark Ivanov", "M", 38, "Flight Engineer",
                   7, 93, 39.1, 105.2, 134, 84, True, 18, -71, 2.7, 4410, "1.4.1"),
        CrewMember("0x04 / APID-019", "Aiko Tanaka", "F", 36, "Medical Officer",
                   82, 25, 36.9, 101.8, 74, 97, False, 54, -90, 100.0, 0, "1.4.2"),
    ]


# ---------------------------------------------------------------------------
# Simulation (swap these for the real telemetry pipeline)
# ---------------------------------------------------------------------------
def jitter_crew(crew: CrewMember, tick: int) -> None:
    """Advance one simulated sample for a crew member."""
    crew.temp = round(crew.base_temp + 0.12 * math.sin(tick / 12) + random.uniform(-0.04, 0.04), 2)
    crew.pulse = round(crew.base_pulse + 3 * math.sin(tick / 7) + random.uniform(-1.5, 1.5))
    crew.spo2 = round(
        min(100.0, crew.base_spo2 + 0.6 * math.sin(tick / 9) + random.uniform(-0.3, 0.3)), 1
    )
    crew.press = round(crew.base_press + 0.4 * math.sin(tick / 10) + random.uniform(-0.08, 0.08), 2)

    crew.nodes["t"] = [crew.temp + o + random.uniform(-0.03, 0.03) for o in T_OFFSETS]
    crew.nodes["p"] = [crew.press + o + random.uniform(-0.05, 0.05) for o in P_OFFSETS]
    e_base = 60 + crew.stress * 3.5
    crew.nodes["e"] = [
        max(0.0, e_base * (1 + 0.5 * math.sin(tick / 5 + i)) + random.uniform(-10, 10))
        for i in range(LAYERS["E"].count)
    ]

    # Placeholder blood velocity (cm/s) and PPG counts (r1, ir1, r2, ir2)
    crew.vel = round(0.30 * crew.pulse + 1.5 * math.sin(tick / 6) + random.uniform(-0.4, 0.4), 1)
    ppg_wave = 2500 * math.sin(tick / 3)
    crew.ppg = [
        round(counts + gain * ppg_wave + random.uniform(-400, 400)) for counts, gain in PPG_CHANNELS
    ]
    crew.humidity = round(40 + crew.stress * 0.2 + 3 * math.sin(tick / 20), 1)

    t_nodes = crew.nodes["t"]
    crew.hist["temp"].append(sum(t_nodes) / len(t_nodes))
    crew.hist["pulse"].append(crew.pulse)
    crew.hist["spo2"].append(crew.spo2)


def raw_values(crew: CrewMember) -> dict:
    """Return the current value of every raw parameter, keyed by parameter name."""
    values = {}
    for prefix in ("t", "p", "e"):
        for i, value in enumerate(crew.nodes[prefix], start=1):
            values[f"{prefix}{i}"] = value
    values.update(zip(("r1", "ir1", "r2", "ir2"), crew.ppg))
    values["h1"] = crew.humidity
    values["+vBat"] = 3.30 + 0.90 * crew.battery / 100
    return values


def update_raw(crew: CrewMember, tick: int, ts: datetime) -> None:
    """Latch a sensor's parameters and timestamp when its update period elapses."""
    current = raw_values(crew)
    for sensor in RAW_SENSORS:
        if tick % sensor.period == 0 or sensor.params[0] not in crew.raw:
            for param in sensor.params:
                crew.raw[param] = (current[param], ts)


def format_packet(crew: CrewMember, ts: datetime) -> str:
    """Build a CCSDS-style packet and render it as a hex log line."""
    payload = struct.pack(
        ">HHHH", int(crew.temp * 100), int(crew.press * 100), int(crew.pulse), int(crew.spo2 * 10)
    )
    header = struct.pack(">HHH", 0x0800 | crew.apid, 0xC000 | (crew.seq & 0x3FFF), len(payload) - 1)
    data = header + payload
    data += struct.pack(">H", binascii.crc_hqx(data, 0xFFFF))
    words = " ".join(data[i:i + 2].hex().upper() for i in range(0, len(data), 2))
    return f"{ts:%H:%M:%S} {words}"


def init_runtime(members: list[CrewMember]) -> None:
    """Fill histories, raw tables and packet logs with HIST_LEN simulated samples."""
    now = datetime.now(timezone.utc)
    for i, member in enumerate(members):
        member.seq = 4100 + 37 * i
        end = now - timedelta(seconds=0 if member.connected else 300)
        for tick in range(HIST_LEN):
            jitter_crew(member, tick)
            ts = end - timedelta(seconds=(HIST_LEN - 1 - tick) * SIM_MS / 1000)
            update_raw(member, tick, ts)
            member.seq += 1
            member.last_packet = ts
            member.log.append(format_packet(member, ts))
        if not member.connected:
            member.log.append(LINK_LOST_MESSAGE)


def value_range(values: list[float], decimals: int) -> str:
    """Format the min-max span of a list of values, e.g. '36.1-37.0'."""
    return f"{min(values):.{decimals}f}-{max(values):.{decimals}f}"


def telemetry_values(crew: CrewMember) -> dict:
    """Return (text, colour or None) for each row of TELEMETRY_ROWS."""
    t_nodes = crew.nodes["t"]
    p_nodes = crew.nodes["p"]
    e_nodes = crew.nodes["e"]
    e_rms = math.sqrt(sum(v * v for v in e_nodes) / len(e_nodes))

    return {
        "Avg. t": (f"{sum(t_nodes) / len(t_nodes):.1f}", None),
        "Avg. p": (f"{sum(p_nodes) / len(p_nodes):.2f}", None),
        "emRMS (Avg. e)": (f"{e_rms:.0f}", None),
        "t Map": (value_range(t_nodes, 1), None),
        "p Map": (value_range(p_nodes, 2), None),
        "em Map": (value_range(e_nodes, 0), None),
        "pulse": (f"{crew.pulse:.0f}", None),
        "spo2": (f"{crew.spo2:.1f}", None),
        "blood vel": (f"{crew.vel:.1f}", None),
        "humidity": (f"{crew.humidity:.1f}", None),
        "+charge": (f"{crew.battery}", RED if crew.battery < 20 else None),
    }


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def draw_silhouette(canvas: tk.Canvas, gender: str) -> None:
    """Draw a front-facing figure ("M" or "F") with a wristband on the right arm."""
    body = "#8a8f98"
    cx = 70
    canvas.create_oval(cx - 13, 8, cx + 13, 36, fill=body, outline="")  # head

    if gender == "F":
        torso = [cx - 24, 46, cx + 24, 46, cx + 14, 96, cx + 28, 135, cx - 28, 135, cx - 14, 96]
        arm_width, leg_width = 8, 12
        arm_l, arm_r = ((cx - 26, 52), (cx - 34, 114)), ((cx + 26, 52), (cx + 34, 114))
        legs = (((cx - 10, 135), (cx - 11, 202)), ((cx + 10, 135), (cx + 11, 202)))
    else:
        torso = [cx - 32, 46, cx + 32, 46, cx + 20, 125, cx - 20, 125]
        arm_width, leg_width = 11, 14
        arm_l, arm_r = ((cx - 36, 52), (cx - 44, 118)), ((cx + 36, 52), (cx + 44, 118))
        legs = (((cx - 10, 125), (cx - 11, 202)), ((cx + 10, 125), (cx + 11, 202)))

    canvas.create_polygon(*torso, fill=body, outline="", smooth=True)
    for (x0, y0), (x1, y1) in (arm_l, arm_r):
        canvas.create_line(x0, y0, x1, y1, width=arm_width, fill=body, capstyle="round")
    for (x0, y0), (x1, y1) in legs:
        canvas.create_line(x0, y0, x1, y1, width=leg_width, fill=body, capstyle="round")

    # Wristband near the wrist of the right arm (the figure's left arm)
    (x0, y0), (x1, y1) = arm_r
    bx = x0 + (x1 - x0) * 0.8
    by = y0 + (y1 - y0) * 0.8
    half = arm_width / 2 + 3
    canvas.create_rectangle(bx - half, by - 4, bx + half, by + 4, fill=BAND_COLOR, outline="")


def draw_strip_chart(canvas: tk.Canvas, values: list[float], color: str,
                     min_span: float, decimals: int) -> None:
    """Draw a scrolling line chart with three y-axis labels."""
    canvas.delete("all")
    width = canvas.winfo_width()
    height = canvas.winfo_height()
    if width < 80 or height < 40 or len(values) < 2:
        return

    left, right, top, bottom = 40, 8, 8, 8
    lo, hi = min(values), max(values)
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    pad = (hi - lo) * 0.1
    lo, hi = lo - pad, hi + pad
    plot_h = height - top - bottom

    for frac in (0, 0.5, 1):
        y = top + plot_h * frac
        canvas.create_line(left, y, width - right, y, fill="#3a3a3a")
        canvas.create_text(
            left - 4, y, text=f"{hi - (hi - lo) * frac:.{decimals}f}",
            anchor="e", fill="gray60", font=("TkDefaultFont", 8),
        )

    step = (width - left - right) / (len(values) - 1)
    points = []
    for i, value in enumerate(values):
        points.append(left + i * step)
        points.append(top + plot_h * (hi - value) / (hi - lo))
    canvas.create_line(*points, fill=color, width=2)


def clip_polygon_x(points: list, x_max: float) -> list:
    """Clip a polygon to the half-plane x <= x_max (Sutherland-Hodgman)."""

    def cross(a: tuple, b: tuple) -> tuple:
        t = (x_max - a[0]) / (b[0] - a[0])
        return (x_max, a[1] + t * (b[1] - a[1]))

    result = []
    for i, cur in enumerate(points):
        prev = points[i - 1]
        cur_in = cur[0] <= x_max
        prev_in = prev[0] <= x_max
        if cur_in:
            if not prev_in:
                result.append(cross(prev, cur))
            result.append(cur)
        elif prev_in:
            result.append(cross(prev, cur))
    return result


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------
class Sidebar(ctk.CTkFrame):
    """Left navigation column."""

    def __init__(self, master: tk.Misc, on_select: Callable[[int], None]) -> None:
        super().__init__(master, width=200, corner_radius=0)
        self.pack_propagate(False)
        self.grid_propagate(False)

        ctk.CTkLabel(
            self, text=SYSTEM_NAME, text_color=MUTED, wraplength=168, justify="left"
        ).pack(padx=16, pady=(54, 24), anchor="w")

        self._buttons = []
        for index, (label, _title) in enumerate(NAV_ITEMS):
            button = ctk.CTkButton(
                self, text=label, anchor="w", height=40, fg_color="transparent",
                hover_color=("gray70", "gray25"),
                command=lambda i=index: on_select(i),
            )
            button.pack(fill="x", padx=12, pady=3)
            self._buttons.append(button)

    def set_active(self, index: int) -> None:
        """Highlight the button of the active page."""
        for i, button in enumerate(self._buttons):
            button.configure(fg_color=("gray75", "gray30") if i == index else "transparent")


class StarRating(tk.Canvas):
    """Five stars; any rating 0.0-5.0 renders as full, half or empty stars."""

    GAP = 3

    def __init__(self, master: tk.Misc, rating: float, bg: str, size: int = 18) -> None:
        try:
            value = max(0.0, min(5.0, round(float(rating) * 2) / 2))
        except (TypeError, ValueError):
            value = 0.0
        radius = size / 1.902  # star width = 1.902 * outer radius
        height = int(radius * 1.809) + 4
        width = 5 * size + 4 * self.GAP + 2
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._draw_stars(value, size, radius)

    def _draw_stars(self, value: float, size: int, radius: float) -> None:
        for i in range(5):
            left = 1 + i * (size + self.GAP)
            cx = left + size / 2
            cy = radius + 2
            points = []
            for k in range(10):
                angle = math.radians(-90 + k * 36)
                r = radius if k % 2 == 0 else radius * 0.382
                points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            self.create_polygon(points, fill=DOC_STAR_EMPTY, outline="")

            fill = max(0.0, min(1.0, value - i))
            if fill >= 1.0:
                self.create_polygon(points, fill=DOC_GOLD, outline="")
            elif fill > 0.0:
                clipped = clip_polygon_x(points, left + fill * size)
                if len(clipped) >= 3:
                    self.create_polygon(clipped, fill=DOC_GOLD, outline="")


# ---------------------------------------------------------------------------
# Page 1: crew overview
# ---------------------------------------------------------------------------
class CrewCard(ctk.CTkFrame):
    """Summary card of a single crew member."""

    def __init__(self, master: tk.Misc, member: CrewMember) -> None:
        super().__init__(
            master, width=300, height=540, corner_radius=18, fg_color=CARD_BG,
            border_width=3, border_color=health_color(member.overall_health),
        )
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        self._build_top(member)
        self._build_figure(member)
        self._build_vitals(member)
        self._build_footer(member)

    def _build_top(self, member: CrewMember) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top, text=member.name, font=ctk.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            top, text="i", width=26, height=26, corner_radius=13,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: print(f"Info clicked for {member.crew_id}"),
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            self, text=f"ID: {member.crew_id}", text_color=MUTED,
            font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkLabel(
            self, text=f"Overall health: {member.overall_health}%",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(10, 0))
        ctk.CTkLabel(
            self, text=f"Stress level: {member.stress}%", font=ctk.CTkFont(size=14), anchor="w"
        ).grid(row=3, column=0, sticky="w", padx=16)

    def _build_figure(self, member: CrewMember) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 0))
        row.grid_columnconfigure(0, weight=1)
        canvas = ctk.CTkCanvas(row, width=140, height=210, bg=CARD_BG, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="w")
        draw_silhouette(canvas, member.gender)
        ctk.CTkButton(
            row, text="More...", width=84,
            command=lambda: print(f"More clicked for {member.crew_id}"),
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _build_vitals(self, member: CrewMember) -> None:
        vitals = ctk.CTkFrame(self, fg_color="transparent")
        vitals.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        vitals.grid_columnconfigure((0, 1), weight=1, uniform="vitals")
        cells = (
            f"Temp: {member.base_temp:.1f}°C", f"Press: {member.base_press:.1f} kPa",
            f"Pulse: {member.base_pulse:.0f}", f"SpO2: {member.base_spo2:g}%",
        )
        for i, text in enumerate(cells):
            ctk.CTkLabel(vitals, text=text, anchor="w", font=ctk.CTkFont(size=13)).grid(
                row=i // 2, column=i % 2, sticky="w", pady=2
            )

    def _build_footer(self, member: CrewMember) -> None:
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="sew", padx=16, pady=(10, 14))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer, text="● Connected" if member.connected else "● Offline",
            text_color=GREEN if member.connected else RED, font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(footer, text=f"{member.battery}%", font=ctk.CTkFont(size=12)).grid(
            row=0, column=1, sticky="e"
        )


class OverviewPage(ctk.CTkFrame):
    """Header with link status and a horizontal strip of crew cards."""

    def __init__(self, master: tk.Misc, crew: list[CrewMember]) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        header.grid_columnconfigure(0, weight=1)

        peers = sum(member.connected for member in crew)
        ctk.CTkLabel(
            header, text=f"Connected: {peers} peers", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text=f"Earth: {'ON' if EARTH_LINK_ON else 'OFF'}",
            font=ctk.CTkFont(size=14, weight="bold"), text_color="white",
            fg_color=GREEN if EARTH_LINK_ON else RED, corner_radius=12, padx=14, pady=3,
        ).grid(row=0, column=1, sticky="e")

        strip = ctk.CTkScrollableFrame(self, orientation="horizontal", fg_color="transparent")
        strip.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 14))
        for member in crew:
            CrewCard(strip, member).pack(side="left", padx=8, pady=4, anchor="n")


# ---------------------------------------------------------------------------
# Page 2: detailed telemetry
# ---------------------------------------------------------------------------
class CrewHeader(ctk.CTkFrame):
    """ID/status on the left, name/details on the right."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, corner_radius=14, fg_color=CARD_BG)
        self.grid_columnconfigure((0, 1), weight=1)

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=18, pady=10)
        self._id_label = ctk.CTkLabel(
            left, text="", font=ctk.CTkFont(size=20, weight="bold"), anchor="w"
        )
        self._id_label.pack(anchor="w")
        self._status_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=13), anchor="w")
        self._status_label.pack(anchor="w")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=18, pady=10)
        self._name_label = ctk.CTkLabel(
            right, text="", font=ctk.CTkFont(size=24, weight="bold"), anchor="e"
        )
        self._name_label.pack(anchor="e")
        self._subinfo_label = ctk.CTkLabel(right, text="", text_color=MUTED, anchor="e")
        self._subinfo_label.pack(anchor="e")

    def show(self, crew: CrewMember) -> None:
        """Display the given crew member."""
        self._id_label.configure(text=f"ID: {crew.crew_id}")
        self._status_label.configure(
            text="● Connected" if crew.connected else "● Offline",
            text_color=GREEN if crew.connected else RED,
        )
        self._name_label.configure(text=crew.name)
        self._subinfo_label.configure(
            text=f"Sex: {crew.gender}  ·  Age: {crew.age}  ·  Role: {crew.role}"
        )


class RawDataPanel(ctk.CTkFrame):
    """Per-parameter table and the scrolling hex packet feed."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self.paused = False
        self._crew = None
        self._cells = {}  # parameter name -> (value label, timestamp label)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(self, text="RAW DATA", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 0)
        )
        self._packet_label = ctk.CTkLabel(
            self, text="Last Packet: --", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w"
        )
        self._packet_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))

        self._build_table()

        ctk.CTkLabel(
            self, text="Packet feed (hex)", text_color=MUTED, font=ctk.CTkFont(size=10), anchor="w"
        ).grid(row=3, column=0, sticky="w", padx=12)
        self._feed = ctk.CTkTextbox(
            self, wrap="char", height=80, fg_color="#0b0f0b", text_color="#3fe06b",
            font=ctk.CTkFont(family=MONO, size=10),
        )
        self._feed.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 6))
        self._feed.configure(state="disabled")
        ctk.CTkButton(self, text="Pause / Resume", height=26, command=self.toggle_pause).grid(
            row=5, column=0, sticky="e", padx=10, pady=(0, 10)
        )

    def _build_table(self) -> None:
        table = ctk.CTkScrollableFrame(self, fg_color="transparent", height=150)
        table.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        table.grid_columnconfigure(1, weight=1)
        mono = ctk.CTkFont(family=MONO, size=11)

        headers = (("param", "w"), ("value", "e"), ("last update UTC", "e"))
        for col, (text, anchor) in enumerate(headers):
            ctk.CTkLabel(
                table, text=text, text_color=MUTED, anchor=anchor, font=ctk.CTkFont(size=10)
            ).grid(row=0, column=col, sticky=anchor, padx=(4, 4))

        row = 1
        for sensor in RAW_SENSORS:
            ctk.CTkLabel(
                table, text=f"{sensor.range_label} [Sensor - {sensor.sensor}]", anchor="w",
                justify="left", wraplength=250, font=ctk.CTkFont(size=11, weight="bold"),
            ).grid(row=row, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))
            row += 1
            for param in sensor.params:
                ctk.CTkLabel(table, text=param, anchor="w", font=mono).grid(
                    row=row, column=0, sticky="w", padx=(4, 8)
                )
                value = ctk.CTkLabel(table, text="--", anchor="e", font=mono)
                value.grid(row=row, column=1, sticky="e", padx=4)
                stamp = ctk.CTkLabel(table, text="--:--:--", anchor="e", text_color=MUTED, font=mono)
                stamp.grid(row=row, column=2, sticky="e", padx=4)
                self._cells[param] = (value, stamp)
                row += 1

    def show(self, crew: CrewMember) -> None:
        """Refresh the table and the packet header for `crew`."""
        self._crew = crew
        self._packet_label.configure(
            text=f"Last Packet: {crew.last_packet:%Y-%m-%d %H:%M:%S} UTC | Seq: #{crew.seq}"
        )
        for sensor in RAW_SENSORS:
            for param in sensor.params:
                value, ts = crew.raw[param]
                value_label, stamp_label = self._cells[param]
                value_label.configure(text=f"{value:.{sensor.decimals}f} {sensor.unit}")
                stamp_label.configure(text=f"{ts:%H:%M:%S}")

    def reload_log(self, crew: CrewMember) -> None:
        """Replace the packet feed with the latest log lines of `crew`."""
        self._crew = crew
        lines = list(crew.log)[-LOG_VISIBLE:]
        self._feed.configure(state="normal")
        self._feed.delete("1.0", "end")
        self._feed.insert("end", "\n".join(lines) + "\n")
        self._feed.see("end")
        self._feed.configure(state="disabled")

    def feed(self, line: str) -> None:
        """Append one packet line unless the feed is paused."""
        if self.paused:
            return
        self._feed.configure(state="normal")
        self._feed.insert("end", line + "\n")
        if int(self._feed.index("end-1c").split(".")[0]) > LOG_VISIBLE + 1:
            self._feed.delete("1.0", "2.0")
        self._feed.see("end")
        self._feed.configure(state="disabled")

    def toggle_pause(self) -> None:
        """Pause or resume the packet feed."""
        self.paused = not self.paused
        print(f"Raw feed: {'paused' if self.paused else 'resumed'}")
        if not self.paused and self._crew is not None:
            self.reload_log(self._crew)


class SensorMapPanel(ctk.CTkFrame):
    """Interpolated heatmap of the selected sensor layer on a forearm schematic."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self._layer = "T"
        self._crew = None
        self._weights = {}  # layer key -> cached per-cell node weights
        self._buttons = {}  # layer key -> button

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._title_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self._title_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        self._build_side()
        self._build_heatmap()
        self._apply_layer()

    def _build_side(self) -> None:
        side = ctk.CTkFrame(self, fg_color="transparent")
        side.grid(row=1, column=0, sticky="n", padx=(10, 4))
        for key in LAYERS:
            button = ctk.CTkButton(
                side, text=f"[ {key} ]", width=56, height=30, fg_color=INACTIVE,
                command=lambda k=key: self.select_layer(k),
            )
            button.pack(pady=2)
            self._buttons[key] = button

        self._legend_max = ctk.CTkLabel(side, text="", font=ctk.CTkFont(size=10))
        self._legend_max.pack(pady=(10, 0))
        legend = ctk.CTkCanvas(side, width=16, height=90, bg=CARD_BG, highlightthickness=0)
        legend.pack(pady=2)
        for y in range(90):
            color = SPECTRUM_PALETTE[round((1 - y / 89) * (PALETTE_SIZE - 1))]
            legend.create_line(0, y, 16, y, fill=color)
        self._legend_min = ctk.CTkLabel(side, text="", font=ctk.CTkFont(size=10))
        self._legend_min.pack()

    def _build_heatmap(self) -> None:
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.grid(row=1, column=1, sticky="n", padx=(4, 10))

        # NOTE: not called "_canvas" - CTkFrame already has a _canvas of its own
        self._heat_canvas = ctk.CTkCanvas(
            holder, width=HEAT_W, height=HEAT_H, bg=PLOT_BG,
            highlightthickness=1, highlightbackground="#888",
        )
        self._heat_canvas.pack()

        self._cells = []        # canvas item ids, row by row
        self._cell_colors = []  # last colour of every cell (to skip redundant redraws)
        for r in range(HEAT_ROWS):
            for c in range(HEAT_COLS):
                x0 = c * HEAT_CELL
                y0 = r * HEAT_CELL
                item = self._heat_canvas.create_rectangle(
                    x0, y0, x0 + HEAT_CELL, y0 + HEAT_CELL, fill=PLOT_BG, outline="", tags="cell"
                )
                self._cells.append(item)
                self._cell_colors.append(None)
        self._heat_canvas.bind("<Motion>", self._on_motion)
        self._heat_canvas.bind("<Leave>", lambda _e: self._heat_canvas.delete("tip"))
        self._heat_canvas.bind("<Button-1>", self._on_click)

        caption = ctk.CTkFrame(holder, fg_color="transparent")
        caption.pack(fill="x")
        ctk.CTkLabel(caption, text="◄ WRIST", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="left")
        ctk.CTkLabel(caption, text="ELBOW ►", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="right")

    # --- layer selection ----------------------------------------------------
    def select_layer(self, key: str) -> None:
        """Switch to layer `key` and redraw."""
        print(f"Sensor layer selected: [ {key} ]")
        self._layer = key
        self._apply_layer()
        self._draw_nodes()
        self._update_heatmap()

    def _apply_layer(self) -> None:
        spec = LAYERS[self._layer]
        for key, button in self._buttons.items():
            button.configure(fg_color=ACCENT if key == self._layer else INACTIVE)
        self._title_label.configure(text=spec.title)
        self._legend_max.configure(text=f"Max\n{spec.vmax:.{spec.decimals}f}{spec.unit}")
        self._legend_min.configure(text=f"Min\n{spec.vmin:.{spec.decimals}f}{spec.unit}")

    # --- public refresh -----------------------------------------------------
    def set_crew(self, crew: CrewMember) -> None:
        """Bind a crew member and redraw node markers and the heatmap."""
        self._crew = crew
        self._draw_nodes()
        self._update_heatmap()

    def show(self, crew: CrewMember) -> None:
        """Update the heatmap colours for `crew`."""
        self._crew = crew
        self._update_heatmap()

    # --- rendering ----------------------------------------------------------
    def _cell_weights(self, key: str) -> list:
        """Normalised inverse-distance weights of every node for each heatmap cell."""
        if key not in self._weights:
            weights = []
            for r in range(HEAT_ROWS):
                y = (r + 0.5) / HEAT_ROWS
                for c in range(HEAT_COLS):
                    x = (c + 0.5) / HEAT_COLS
                    raw = [
                        1.0 / (((x - nx) * ASPECT) ** 2 + (y - ny) ** 2 + 0.003)
                        for nx, ny in NODE_POS[key]
                    ]
                    total = sum(raw)
                    weights.append([w / total for w in raw])
            self._weights[key] = weights
        return self._weights[key]

    def _update_heatmap(self) -> None:
        if self._crew is None:
            return
        spec = LAYERS[self._layer]
        values = self._crew.nodes[spec.prefix]
        for idx, weights in enumerate(self._cell_weights(self._layer)):
            value = sum(w * v for w, v in zip(weights, values))
            level = min(max((value - spec.vmin) / (spec.vmax - spec.vmin), 0.0), 1.0)
            color = SPECTRUM_PALETTE[round(level * (PALETTE_SIZE - 1))]
            if color != self._cell_colors[idx]:
                self._heat_canvas.itemconfigure(self._cells[idx], fill=color)
                self._cell_colors[idx] = color

    def _draw_nodes(self) -> None:
        self._heat_canvas.delete("node")
        self._heat_canvas.delete("tip")
        prefix = LAYERS[self._layer].prefix
        for i, (nx, ny) in enumerate(NODE_POS[self._layer]):
            x = nx * HEAT_W
            y = ny * HEAT_H
            self._heat_canvas.create_oval(
                x - 5, y - 5, x + 5, y + 5, fill="white", outline="#111", width=2, tags="node"
            )
            self._heat_canvas.create_text(
                x, y - 13, text=f"{prefix}{i + 1}", fill="white",
                font=("TkDefaultFont", 9, "bold"), tags="node",
            )

    # --- interaction --------------------------------------------------------
    def _nearest_node(self, x: float, y: float) -> int | None:
        best = None
        best_dist = 12.0
        for i, (nx, ny) in enumerate(NODE_POS[self._layer]):
            dist = math.hypot(x - nx * HEAT_W, y - ny * HEAT_H)
            if dist <= best_dist:
                best = i
                best_dist = dist
        return best

    def _on_motion(self, event: tk.Event) -> None:
        self._heat_canvas.delete("tip")
        i = self._nearest_node(event.x, event.y)
        if i is None or self._crew is None:
            return
        spec = LAYERS[self._layer]
        value = self._crew.nodes[spec.prefix][i]
        nx, ny = NODE_POS[self._layer][i]
        x = nx * HEAT_W
        y = ny * HEAT_H
        east = x > HEAT_W / 2
        south = y > HEAT_H / 2
        anchor = ("s" if south else "n") + ("e" if east else "w")
        text = self._heat_canvas.create_text(
            x + (-12 if east else 12), y + (-12 if south else 12),
            text=f"Node {spec.prefix}{i + 1}: {value:.{spec.decimals}f} {spec.unit}",
            anchor=anchor, fill="white", font=("TkDefaultFont", 10, "bold"), tags="tip",
        )
        x0, y0, x1, y1 = self._heat_canvas.bbox(text)
        box = self._heat_canvas.create_rectangle(
            x0 - 4, y0 - 2, x1 + 4, y1 + 2, fill="#111", outline="#aaa", tags="tip"
        )
        self._heat_canvas.tag_raise(text, box)

    def _on_click(self, event: tk.Event) -> None:
        i = self._nearest_node(event.x, event.y)
        if i is not None and self._crew is not None:
            prefix = LAYERS[self._layer].prefix
            print(f"Sensor node clicked: {prefix}{i + 1} ({self._crew.crew_id})")


class TelemetryListPanel(ctk.CTkFrame):
    """Scrollable list of summary values; map rows open the matching sensor map."""

    def __init__(self, master: tk.Misc, on_layer_select: Callable[[str], None]) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self._default_color = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        self._values = {}  # row name -> value label

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text="TELEMETRY", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        listing = ctk.CTkScrollableFrame(self, fg_color="transparent")
        listing.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        listing.grid_columnconfigure(1, weight=1)

        for row, (name, unit) in enumerate(TELEMETRY_ROWS):
            name_label = ctk.CTkLabel(listing, text=name, text_color=MUTED, anchor="w")
            name_label.grid(row=row, column=0, sticky="w", padx=(6, 8), pady=4)
            value = ctk.CTkLabel(listing, text="--", font=ctk.CTkFont(weight="bold"), anchor="e")
            value.grid(row=row, column=1, sticky="e")
            unit_label = ctk.CTkLabel(listing, text=unit, text_color=MUTED, anchor="w", width=44)
            unit_label.grid(row=row, column=2, sticky="w", padx=(4, 6))
            self._values[name] = value
            if name in MAP_LAYER_BY_ROW:
                layer = MAP_LAYER_BY_ROW[name]
                for widget in (name_label, value, unit_label):
                    widget.bind("<Button-1>", lambda _e, k=layer: on_layer_select(k))

    def show(self, crew: CrewMember) -> None:
        """Refresh every row for `crew`."""
        for name, (text, color) in telemetry_values(crew).items():
            self._values[name].configure(text=text, text_color=color or self._default_color)


class ChartsStrip(ctk.CTkFrame):
    """Row of three trend charts."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, fg_color="transparent")
        self._crew = None
        self._canvases = {}  # chart key -> canvas
        self._values = {}    # chart key -> latest value label
        self.grid_rowconfigure(0, weight=1)

        for col, spec in enumerate(CHARTS):
            self.grid_columnconfigure(col, weight=1, uniform="charts")
            card = ctk.CTkFrame(self, corner_radius=12, fg_color=CARD_BG)
            card.grid(row=0, column=col, sticky="nsew", padx=3)
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(card, text=spec.title, font=ctk.CTkFont(size=13, weight="bold")).grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 0)
            )
            value = ctk.CTkLabel(
                card, text="--", text_color=spec.color, font=ctk.CTkFont(size=13, weight="bold")
            )
            value.grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))
            canvas = ctk.CTkCanvas(card, height=110, bg=PLOT_BG, highlightthickness=0)
            canvas.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
            canvas.bind("<Configure>", lambda _e, s=spec: self._draw_chart(s))
            self._canvases[spec.key] = canvas
            self._values[spec.key] = value

    def show(self, crew: CrewMember) -> None:
        """Redraw all charts and value labels for `crew`."""
        self._crew = crew
        for spec in CHARTS:
            self._draw_chart(spec)
            history = crew.hist[spec.key]
            if history:
                self._values[spec.key].configure(text=f"{history[-1]:.{spec.decimals}f} {spec.unit}")

    # NOTE: not called "_draw" - CTkFrame has its own _draw(no_color_updates) that
    # its __init__ calls, so overriding it with a different signature crashes.
    def _draw_chart(self, spec: ChartSpec) -> None:
        if self._crew is None:
            return
        draw_strip_chart(
            self._canvases[spec.key], list(self._crew.hist[spec.key]),
            spec.color, spec.min_span, spec.decimals,
        )


class TelemetryPage(ctk.CTkFrame):
    """Carousel over crew members with raw data, sensor map, telemetry list and charts."""

    def __init__(self, master: tk.Misc, crew: list[CrewMember]) -> None:
        super().__init__(master, fg_color="transparent")
        self._crew = crew
        self._index = 0
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Carousel pagination dots (top centre)
        dots_row = ctk.CTkFrame(self, fg_color="transparent")
        dots_row.grid(row=0, column=0, columnspan=3, pady=(10, 0))
        self._dots = []
        for i in range(len(crew)):
            dot = ctk.CTkLabel(dots_row, text="○", font=ctk.CTkFont(size=22))
            dot.pack(side="left", padx=5)
            dot.bind("<Button-1>", lambda _e, idx=i: self.select_crew(idx))
            self._dots.append(dot)

        # Side arrows
        arrow_font = ctk.CTkFont(size=28, weight="bold")
        for column, text, command, padx in (
            (0, "<", self.prev_crew, (10, 4)),
            (2, ">", self.next_crew, (4, 10)),
        ):
            ctk.CTkButton(
                self, text=text, width=46, font=arrow_font, fg_color=("gray75", "gray25"),
                hover_color=("gray65", "gray35"), command=command,
            ).grid(row=1, column=column, sticky="ns", padx=padx, pady=10)

        # Body: header card / three panels / charts
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=1, sticky="nsew", pady=(4, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=3)
        body.grid_rowconfigure(2, weight=2)

        self._crew_header = CrewHeader(body)
        self._crew_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        middle = ctk.CTkFrame(body, fg_color="transparent")
        middle.grid(row=1, column=0, sticky="nsew")
        middle.grid_rowconfigure(0, weight=1)
        middle.grid_columnconfigure(0, weight=3, uniform="mid")
        middle.grid_columnconfigure(1, weight=5, uniform="mid")
        middle.grid_columnconfigure(2, weight=3, uniform="mid")

        self._raw_panel = RawDataPanel(middle)
        self._raw_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._sensor_map = SensorMapPanel(middle)
        self._sensor_map.grid(row=0, column=1, sticky="nsew", padx=3)
        self._telemetry_list = TelemetryListPanel(middle, self._sensor_map.select_layer)
        self._telemetry_list.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        self._charts_strip = ChartsStrip(body)
        self._charts_strip.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

    @property
    def current(self) -> CrewMember:
        """Crew member currently shown."""
        return self._crew[self._index]

    def select_crew(self, index: int) -> None:
        """Show the crew member at `index` (wraps around)."""
        self._index = index % len(self._crew)
        print(f"Carousel: crew {self.current.crew_id} (index {self._index})")
        self.show_crew()

    def prev_crew(self) -> None:
        print("Carousel: '<' clicked")
        self.select_crew(self._index - 1)

    def next_crew(self) -> None:
        print("Carousel: '>' clicked")
        self.select_crew(self._index + 1)

    def show_crew(self) -> None:
        """Reload every widget for the selected crew member."""
        crew = self.current
        self._raw_panel.reload_log(crew)
        self._sensor_map.set_crew(crew)
        self.refresh()

    def refresh(self) -> None:
        """Update all widgets with the latest simulated values."""
        crew = self.current
        for i, dot in enumerate(self._dots):
            dot.configure(
                text="●" if i == self._index else "○",
                text_color="white" if i == self._index else "gray40",
            )
        self._crew_header.show(crew)
        self._raw_panel.show(crew)
        self._sensor_map.show(crew)
        self._telemetry_list.show(crew)
        self._charts_strip.show(crew)

    def on_packet(self, member: CrewMember, line: str) -> None:
        """Feed a new packet line if `member` is the one on screen."""
        if member is self.current:
            self._raw_panel.feed(line)


# ---------------------------------------------------------------------------
# Page 3: first aid & protocol archive (mock data)
# ---------------------------------------------------------------------------
class Document:
    """Metadata of a PDF protocol located in fadocs/."""

    def __init__(self, filename: str, title: str, rating: float, tags: tuple, size: str,
                 pages: int, summary: str, points: tuple) -> None:
        self.filename = filename
        self.title = title
        self.rating = rating
        self.tags = tags
        self.size = size
        self.pages = pages
        self.summary = summary
        self.points = points


MOCK_DOCUMENTS = [
    Document(
        "HYPOXIA_EMERGENCY_PROTOCOL.pdf", "Hypoxia Emergency Protocol", 5.0,
        ("hypoxia", "spo2", "oxygen", "critical", "nasa-std-3001"), "4.2 MB", 12,
        "PLACEHOLDER SUMMARY\n\n"
        "Covers recognition and response to hypoxia in a crewmember exposed to a degraded "
        "habitable atmosphere. Describes early indicators (reduced SpO2, elevated heart rate, "
        "impaired judgement), immediate oxygen restoration, and criteria for escalating to "
        "medical support.",
        (
            "Verify SpO2 < 88% on two consecutive readings",
            "Administer 100% O2 and restore a habitable atmosphere",
            "Keep the crewmember at rest and monitor continuously",
            "Log event time and readings for the medical record",
        ),
    ),
    Document(
        "ACUTE_CARDIAC_ARRHYTHMIA_RESPONSE.pdf", "Acute Cardiac Arrhythmia Response", 4.8,
        ("cardiac", "arrhythmia", "heart-rate", "critical", "ppg"), "3.1 MB", 9,
        "PLACEHOLDER SUMMARY\n\n"
        "Outlines the response to irregular or sustained abnormal heart rhythm detected by "
        "wearable telemetry, including confirmation of the reading, crew positioning, and "
        "notification of the flight surgeon.",
        (
            "Confirm the reading with a second sensor channel",
            "Stop activity and position the crewmember safely",
            "Notify the flight surgeon with recent HR trend data",
            "Prepare emergency equipment per the cardiac checklist",
        ),
    ),
    Document(
        "DECOMPRESSION_SICKNESS_SOP.pdf", "Decompression Sickness SOP", 4.5,
        ("dcs", "barotrauma", "pressure", "eva", "nasa-std-3001"), "6.8 MB", 18,
        "PLACEHOLDER SUMMARY\n\n"
        "Standard operating procedure for suspected decompression sickness after pressure "
        "changes or EVA prebreathe deviations. Covers symptom screening, oxygen therapy, and "
        "recompression decision points.",
        (
            "Screen for joint pain, rash, numbness, or fatigue",
            "Begin 100% oxygen breathing immediately",
            "Record the pressure profile preceding symptom onset",
            "Follow the recompression decision tree with ground support",
        ),
    ),
    Document(
        "THERMAL_BURN_TRIAGE.pdf", "Thermal Burn Triage", 3.5,
        ("burn", "thermal", "triage", "skin", "temperature"), "2.4 MB", 7,
        "PLACEHOLDER SUMMARY\n\n"
        "Describes assessment and first response to thermal burns in a closed habitat, "
        "including burn depth estimation, cooling, dressing, and infection prevention.",
        (
            "Remove the heat source and cool the area per protocol",
            "Estimate burn depth and affected surface area",
            "Apply a sterile dressing and avoid popping blisters",
            "Monitor skin temperature trend on the wearable",
        ),
    ),
    Document(
        "MUSCLE_FATIGUE_TREMOR_ASSESSMENT.pdf", "Muscle Fatigue & Tremor Assessment", 2.7,
        ("emg", "fatigue", "tremor", "muscle", "assessment"), "1.9 MB", 6,
        "PLACEHOLDER SUMMARY\n\n"
        "Guidance for interpreting sustained EMG fatigue markers and tremor patterns, with "
        "rest, hydration, and workload adjustment recommendations.",
        (
            "Compare current EMG features against the personal baseline",
            "Reduce workload and schedule rest",
            "Check hydration and skin-electrode contact quality",
            "Escalate if tremor persists after rest",
        ),
    ),
]


def render_mock_page(canvas: tk.Canvas, doc: Document, page_num: int) -> None:
    """Draw a placeholder PDF page that scales to fit the canvas."""
    canvas.delete("all")
    cw = canvas.winfo_width()
    ch = canvas.winfo_height()
    if cw < 60 or ch < 60:
        return

    # PDF RENDER HOOK: replace the mock drawing below with a real page render, e.g.
    #   import fitz  # pymupdf
    #   page = fitz.open(f"fadocs/{doc.filename}")[page_num - 1]
    #   pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
    #   -> PIL.Image -> ImageTk.PhotoImage (keep a reference) -> canvas.create_image(...)
    ph = ch - 24
    pw = ph * 8.5 / 11
    if pw > cw - 24:
        pw = cw - 24
        ph = pw * 11 / 8.5
    x0 = (cw - pw) / 2
    y0 = (ch - ph) / 2
    margin = pw * 0.08

    canvas.create_rectangle(x0 + 5, y0 + 5, x0 + pw + 5, y0 + ph + 5, fill="#15181d", outline="")
    canvas.create_rectangle(x0, y0, x0 + pw, y0 + ph, fill="#f4f4f2", outline="#c9c9c4")
    canvas.create_text(
        x0 + margin, y0 + margin, anchor="nw", width=pw - 2 * margin, text=doc.title.upper(),
        fill="#1b1f24", font=("Helvetica", max(8, int(pw * 0.035)), "bold"),
    )
    rule_y = y0 + margin + ph * 0.07
    canvas.create_rectangle(x0 + margin, rule_y, x0 + pw - margin, rule_y + 2, fill="#b02a2a", outline="")

    line_h = max(6.0, ph * 0.025)
    y = rule_y + line_h
    i = 0
    while y < y0 + ph - margin * 1.6:
        frac = 0.55 + 0.45 * abs(math.sin(i * 1.7 + page_num))
        canvas.create_rectangle(
            x0 + margin, y, x0 + margin + (pw - 2 * margin) * frac, y + line_h * 0.45,
            fill="#c4c7cc", outline="",
        )
        y += line_h
        i += 1
        if i % 6 == 0:
            y += line_h * 0.8
    canvas.create_text(
        x0 + pw / 2, y0 + ph - margin * 0.7, text=f"- {page_num} -  MOCK PAGE",
        fill="#6b7280", font=(MONO, 9),
    )


class DocumentCard(ctk.CTkFrame):
    """Clickable list entry for one document."""

    def __init__(self, master: tk.Misc, doc: Document, on_open: Callable[[Document], None]) -> None:
        super().__init__(
            master, fg_color=DOC_CARD, corner_radius=10, border_width=1, border_color=DOC_BORDER
        )
        self.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            self, text=doc.title.upper(), anchor="w", font=ctk.CTkFont(size=15, weight="bold")
        )
        title.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(12, 2))

        self._stars = StarRating(self, doc.rating, DOC_CARD)
        self._stars.grid(row=0, column=1, sticky="e", padx=(8, 14), pady=(12, 2))

        tags = ctk.CTkLabel(
            self, text="Tags: " + " ".join(f"#{t}" for t in doc.tags), anchor="w",
            text_color=DOC_MUTED, font=ctk.CTkFont(size=11),
        )
        tags.grid(row=1, column=0, sticky="w", padx=(14, 8), pady=(0, 12))

        size = ctk.CTkLabel(
            self, text=f"Size: {doc.size} | PDF", text_color=DOC_MUTED, font=ctk.CTkFont(size=11)
        )
        size.grid(row=1, column=1, sticky="e", padx=(8, 14), pady=(0, 12))

        for widget in (self, title, self._stars, tags, size):
            widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<Button-1>", lambda _e, d=doc: on_open(d), add="+")

    def _set_hover(self, active: bool) -> None:
        color = DOC_CARD_HOVER if active else DOC_CARD
        self.configure(fg_color=color)
        self._stars.configure(bg=color)

    def _on_leave(self, event: tk.Event) -> None:
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            under = None
        while under is not None:
            if under is self:
                return  # pointer moved onto a child of this card
            under = getattr(under, "master", None)
        self._set_hover(False)


class DocumentModal(ctk.CTkToplevel):
    """Document viewer: summary, key points and a paged PDF preview."""

    def __init__(self, root: ctk.CTk, doc: Document) -> None:
        super().__init__(root)
        self._doc = doc
        self._current_page = 1
        self._is_expanded = False

        self.title(doc.title)
        self.configure(fg_color=DOC_MODAL_BG)
        self.minsize(760, 560)

        width, height = 1020, 800
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        self.transient(root)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(200, self._grab_when_ready)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=2)

        self._build_top_bar()
        self._build_info()
        self._build_pdf_viewer()

    def _grab_when_ready(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass  # window not viewable yet or already closed

    def _build_top_bar(self) -> None:
        top = ctk.CTkFrame(self, fg_color=DOC_PANEL, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(top, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        ctk.CTkLabel(
            left, text=self._doc.title.upper(), anchor="w", font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ctk.CTkLabel(
            left, text=f"Size: {self._doc.size} | PDF", text_color=DOC_MUTED,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        StarRating(left, self._doc.rating, DOC_PANEL, size=16).grid(
            row=1, column=1, sticky="w", padx=(14, 0), pady=(4, 0)
        )

        ctk.CTkButton(
            top, text="[ ✕ Close ]", width=110, height=36, fg_color="#b62324",
            hover_color="#da3633", command=self.destroy,
        ).grid(row=0, column=1, sticky="e", padx=16)

    def _build_info(self) -> None:
        self._info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._info_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 8))
        self._info_frame.grid_columnconfigure((0, 1), weight=1, uniform="info")
        self._info_frame.grid_rowconfigure(0, weight=1)

        self._read_only_section(0, "BRIEF SUMMARY", self._doc.summary)
        points = "\n".join(f"• Step {i}: {p}" for i, p in enumerate(self._doc.points, start=1))
        self._read_only_section(1, "KEY ACTIONABLE POINTS", points)

    def _read_only_section(self, col: int, heading: str, text: str) -> None:
        section = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        section.grid(row=0, column=col, sticky="nsew", padx=(0, 6) if col == 0 else (6, 0))
        section.grid_columnconfigure(0, weight=1)
        section.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            section, text=heading, anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
            text_color=DOC_MUTED,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        box = ctk.CTkTextbox(
            section, wrap="word", fg_color=DOC_PANEL, border_width=1, border_color=DOC_BORDER
        )
        box.grid(row=1, column=0, sticky="nsew")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _build_pdf_viewer(self) -> None:
        pdf = ctk.CTkFrame(self, fg_color="transparent")
        pdf.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        pdf.grid_columnconfigure(0, weight=1)
        pdf.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            pdf, text=f"DOCUMENT PREVIEW (fadocs/{self._doc.filename})", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=DOC_MUTED,
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        nav = ctk.CTkFrame(pdf, fg_color=DOC_PANEL, corner_radius=8)
        nav.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        nav.grid_columnconfigure(2, weight=1)

        self._prev_btn = ctk.CTkButton(
            nav, text="Prev Page", width=100, height=30, command=lambda: self._go(-1)
        )
        self._prev_btn.grid(row=0, column=0, padx=(8, 6), pady=6)
        self._page_label = ctk.CTkLabel(
            nav, text="", width=110, font=ctk.CTkFont(family=MONO, size=13)
        )
        self._page_label.grid(row=0, column=1, padx=6)
        self._next_btn = ctk.CTkButton(
            nav, text="Next Page", width=100, height=30, command=lambda: self._go(1)
        )
        self._next_btn.grid(row=0, column=2, sticky="w", padx=6)
        self._expand_btn = ctk.CTkButton(
            nav, text="[ ⤢ Expand PDF ]", width=140, height=30, fg_color="#30363d",
            hover_color="#3d444d", command=self._toggle_expand,
        )
        self._expand_btn.grid(row=0, column=3, sticky="e", padx=8)

        viewport = ctk.CTkFrame(
            pdf, fg_color="#2b2f36", corner_radius=8, border_width=1, border_color=DOC_BORDER
        )
        viewport.grid(row=2, column=0, sticky="nsew")
        viewport.grid_columnconfigure(0, weight=1)
        viewport.grid_rowconfigure(0, weight=1)
        self._pdf_canvas = tk.Canvas(viewport, bg="#2b2f36", highlightthickness=0, bd=0)
        self._pdf_canvas.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._pdf_canvas.bind("<Configure>", lambda _e: self._refresh())
        self._refresh()

    def _refresh(self) -> None:
        total = self._doc.pages
        self._page_label.configure(text=f"Page {self._current_page} / {total}")
        self._prev_btn.configure(state="normal" if self._current_page > 1 else "disabled")
        self._next_btn.configure(state="normal" if self._current_page < total else "disabled")
        render_mock_page(self._pdf_canvas, self._doc, self._current_page)

    def _go(self, delta: int) -> None:
        self._current_page = max(1, min(self._doc.pages, self._current_page + delta))
        print(f"PDF page -> {self._current_page} / {self._doc.pages}")
        self._refresh()

    def _toggle_expand(self) -> None:
        if self._is_expanded:
            self._info_frame.grid()  # restores the remembered grid options
            self.grid_rowconfigure(1, weight=1)
            self.grid_rowconfigure(2, weight=2)
            self._expand_btn.configure(text="[ ⤢ Expand PDF ]")
        else:
            self._info_frame.grid_remove()
            self.grid_rowconfigure(1, weight=0)
            self.grid_rowconfigure(2, weight=1)
            self._expand_btn.configure(text="[ ⤡ Restore ]")
        self._is_expanded = not self._is_expanded
        print(f"PDF viewer expanded: {self._is_expanded}")


class ArchivePage(ctk.CTkFrame):
    """Searchable list of protocol documents."""

    def __init__(self, master: tk.Misc, app_window: ctk.CTk) -> None:
        super().__init__(master, fg_color="transparent")
        # NOTE: not called "_root" - tkinter widgets have a _root() method of their own
        self._app_window = app_window
        self._docs = MOCK_DOCUMENTS
        self._modal = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        bar.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(
            bar, height=40, corner_radius=10,
            placeholder_text="Search emergency protocols, symptoms, or tags...",
        )
        self._entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._entry.bind("<Return>", lambda _e: self._on_search())
        ctk.CTkButton(bar, text="Search", width=110, height=40, command=self._on_search).grid(
            row=0, column=1
        )
        ctk.CTkLabel(
            bar, text="Source Directory: fadocs/", text_color=DOC_MUTED,
            font=ctk.CTkFont(family=MONO, size=12),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
        self._list.grid_columnconfigure(0, weight=1)
        self._render(self._docs)

    def _on_search(self) -> None:
        query = self._entry.get().strip()
        print(f"Searching: {query}")
        needle = query.lower().lstrip("#")
        if needle:
            docs = [
                d for d in self._docs
                if needle in d.title.lower()
                or needle in d.filename.lower()
                or any(needle in tag for tag in d.tags)
            ]
        else:
            docs = self._docs
        self._render(docs)

    def _render(self, docs: list[Document]) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        if not docs:
            ctk.CTkLabel(self._list, text="No matching documents.", text_color=DOC_MUTED).grid(
                row=0, column=0, pady=30
            )
            return
        for row, doc in enumerate(docs):
            DocumentCard(self._list, doc, self._open_document).grid(
                row=row, column=0, sticky="ew", pady=6, padx=2
            )

    def _open_document(self, doc: Document) -> None:
        print(f"Opening document: {doc.filename}")
        if self._modal is not None:
            try:
                if self._modal.winfo_exists():
                    self._modal.destroy()
            except tk.TclError:
                pass
        self._modal = DocumentModal(self._app_window, doc)


# ---------------------------------------------------------------------------
# Page 4: placeholder
# ---------------------------------------------------------------------------
class PlaceholderPage(ctk.CTkFrame):
    """Stub page with a test button."""

    def __init__(self, master: tk.Misc, title: str, number: int) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=22, weight="bold")).pack(
            anchor="w", padx=20, pady=(20, 8)
        )
        ctk.CTkLabel(self, text="Not implemented yet.", text_color=MUTED).pack(anchor="w", padx=20)
        ctk.CTkButton(
            self, text="Test button", width=120,
            command=lambda: print(f"Test button clicked on page {number}"),
        ).pack(anchor="w", padx=20, pady=16)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
class Interface(ctk.CTk):
    """Main window, router and application controller."""

    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(1280, 780)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.crew = self.load_mock_data()
        init_runtime(self.crew)
        self.tick = HIST_LEN
        self.current_page = None
        self.pages = {}

        self.sidebar = Sidebar(self, self._on_nav)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.content = ctk.CTkFrame(
            self, corner_radius=0, fg_color="transparent"
        )
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.page_builders = {
            "overview": self.show_overview,
            "telemetry": self.show_telemetry,
            "archive": self.show_archive,
            "settings": self.show_settings,
        }

        self.navigate("overview")
        self.after(SIM_MS, self._simulate_tick)

    # ------------------------------------------------------------------
    # Data adapters
    # ------------------------------------------------------------------
    def load_mock_data(self) -> list[CrewMember]:
        """Load demo crew data."""
        return sample_crew()

    def update_data(self) -> None:
        """Replace with the real telemetry pipeline."""
        self.tick += 1
        now = datetime.now(timezone.utc)

        for member in self.crew:
            if not member.connected:
                continue

            jitter_crew(member, self.tick)
            update_raw(member, self.tick, now)
            member.seq += 1
            member.uptime += SIM_MS / 1000
            member.last_packet = now

            line = format_packet(member, now)
            member.log.append(line)

            if self.current_page == "telemetry":
                self.telemetry.on_packet(member, line)

        if self.current_page == "telemetry":
            self.telemetry.refresh()

    # ------------------------------------------------------------------
    # Router
    # ------------------------------------------------------------------
    def _on_nav(self, index: int) -> None:
        page_names = ("overview", "telemetry", "archive", "settings")
        if 0 <= index < len(page_names):
            self.navigate(page_names[index])

    def navigate(self, page_name: str) -> None:
        """Switch the active page."""
        builder = self.page_builders.get(page_name)
        if builder is None:
            raise ValueError(f"Unknown page: {page_name}")

        if page_name not in self.pages:
            self.pages[page_name] = builder()

        for page in self.pages.values():
            page.grid_remove()

        self.pages[page_name].grid(
            row=0, column=0, sticky="nsew"
        )
        self.pages[page_name].tkraise()

        self.current_page = page_name
        self.sidebar.set_active(
            ("overview", "telemetry", "archive", "settings").index(page_name)
        )

        if page_name == "telemetry":
            self.telemetry.show_crew()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    def show_overview(self) -> ctk.CTkFrame:
        """Build the crew overview page."""
        return OverviewPage(self.content, self.crew)

    def show_telemetry(self) -> ctk.CTkFrame:
        """Build the detailed telemetry page."""
        self.telemetry = TelemetryPage(self.content, self.crew)
        return self.telemetry

    def show_archive(self) -> ctk.CTkFrame:
        """Build the protocol archive page."""
        return ArchivePage(self.content, self)

    def show_settings(self) -> ctk.CTkFrame:
        """Build the settings placeholder."""
        return PlaceholderPage(self.content, NAV_ITEMS[3][1], 4)

    # ------------------------------------------------------------------
    # Runtime
    # ------------------------------------------------------------------
    def _simulate_tick(self) -> None:
        """Run one simulation update."""
        self.update_data()
        self.after(SIM_MS, self._simulate_tick)


if __name__ == "__main__":
    interface = Interface()
    interface.mainloop()
