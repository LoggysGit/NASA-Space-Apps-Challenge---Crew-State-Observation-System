""" Interface file """

from __future__ import annotations

import binascii
import math
import random
import struct
import tkinter as tk
from collections import deque
from datetime import datetime, timedelta, timezone

import customtkinter as ctk

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

# Simulation / buffers
SIM_MS = 500
HIST_LEN = 120
LOG_MAX = 300
LOG_VISIBLE = 80

# Heatmap geometry (cells of 11 px -> 330 x 176 canvas)
HEAT_COLS, HEAT_ROWS, HEAT_CELL = 30, 16, 11
HEAT_W, HEAT_H = HEAT_COLS * HEAT_CELL, HEAT_ROWS * HEAT_CELL
ASPECT = HEAT_W / HEAT_H

# Health -> border colour stops (percent, RGB).
HEALTH_STOPS = [
    (8, (0x5C, 0x0A, 0x0A)),    # deep maroon at <= 8 %
    (50, (0xE8, 0xA3, 0x17)),   # amber at 50 %
    (98, (0x1D, 0xB9, 0x54)),   # bright green at >= 98 %
]

# Spectrogram stops (0..1): deep blue (min) -> crimson (max).
SPECTRUM_STOPS = [
    (0.00, (0x0B, 0x1F, 0x8F)),
    (0.25, (0x1E, 0x90, 0xFF)),
    (0.50, (0x2E, 0xCC, 0x71)),
    (0.75, (0xF4, 0xD0, 0x3F)),
    (1.00, (0xD0, 0x02, 0x1B)),
]
PALETTE_SIZE = 64

# Sensor layers: key -> (node prefix, count, unit, min, max, decimals, title)
LAYERS = {
    "T": ("t", 4, "C", 35.0, 40.0, 1, "t Map [C]"),
    "P": ("p", 4, "kPa", 99.0, 106.0, 2, "p Map [kPa]"),
    "E": ("e", 6, "muV", 0.0, 600.0, 0, "em Map [muV]"),
}

# Node positions on the forearm schematic (x: wrist -> elbow, y: top -> bottom)
NODE_POS = {
    "T": [(0.20, 0.25), (0.20, 0.75), (0.80, 0.25), (0.80, 0.75)],
    "P": [(0.35, 0.50), (0.50, 0.28), (0.50, 0.72), (0.65, 0.50)],
    "E": [(0.10, 0.50), (0.30, 0.25), (0.30, 0.75), (0.70, 0.25), (0.70, 0.75), (0.90, 0.50)],
}
T_OFFSETS = [-0.6, 0.2, 0.5, -0.2]
P_OFFSETS = [-0.3, 0.2, 0.4, -0.1]

# Strip charts: (title, history key, unit, colour, min span, decimals)
CHARTS = [
    ("Temperature trend (t_avg)", "temp", "°C", "#ff8a65", 1.0, 1),
    ("Pulse rate trend", "pulse", "BPM", "#ef5350", 20.0, 0),
    ("SpO2 trend", "spo2", "%", "#4fc3f7", 4.0, 1),
]

# TELEMETRY rows: (label, unit). The three map rows open the matching sensor map.
TELEMETRY_ROWS = [
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
]
MAP_LAYER_BY_ROW = {"t Map": "T", "p Map": "P", "em Map": "E"}

# RAW DATA groups: (parameter range, sensor, parameters, unit, decimals, update period in ticks).
# Each sensor refreshes at its own rate, so every parameter keeps its own last-update time.
RAW_SENSORS = [
    ("t1-t4", "TI TMP117", ("t1", "t2", "t3", "t4"), "C", 2, 4),
    ("p1-p4", "BMP390", ("p1", "p2", "p3", "p4"), "kPa", 2, 2),
    ("e1-e6", "TI ADS1298", ("e1", "e2", "e3", "e4", "e5", "e6"), "muV", 0, 1),
    ("r1, ir1 + r2, ir2", "ADI MAX86141", ("r1", "ir1", "r2", "ir2"), "counts", 0, 1),
    ("h1", "SHT45", ("h1",), "%", 1, 6),
    ("+vBat", "BQ27441-G1", ("+vBat",), "V", 3, 10),
]

# Ground link state (replace with real link monitor later).
EARTH_LINK_ON = False

NAV_ITEMS = [
    ("▦  Overview", "Crew Overview"),
    ("∿  Telemetry", "Detailed Telemetry"),
    ("☰  Logs / Docs", "Logs & Docs"),
    ("⚙  Settings", "Settings"),
]


def interpolate_color(stops: list, value: float) -> str:
    """Linearly interpolate a hex colour between (position, RGB) stops."""
    if value <= stops[0][0]:
        rgb = stops[0][1]
    elif value >= stops[-1][0]:
        rgb = stops[-1][1]
    else:
        rgb = stops[-1][1]
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= value <= p1:
                t = (value - p0) / (p1 - p0)
                rgb = tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
                break
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def health_color(health: float) -> str:
    """Map overall health (0-100) to a hex border colour."""
    return interpolate_color(HEALTH_STOPS, health)


SPECTRUM_PALETTE = [
    interpolate_color(SPECTRUM_STOPS, i / (PALETTE_SIZE - 1)) for i in range(PALETTE_SIZE)
]


class Interface(ctk.cTk):
    """Application lifecycle, sidebar navigation and page construction."""

    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(1280, 780)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # State
        self.crew = self._sample_crew()
        self.tick = 0
        self.current_page = -1
        self.p2_index = 0
        self.p2_layer = "T"
        self.log_paused = False
        self.nav_buttons: list[ctk.CTkButton] = []
        self._weights: dict[str, list[list[float]]] = {}
        self._init_runtime()

        self.sidebar = self.create_sidebar()
        self.content = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        builders = [
            self.create_page_1,
            self.create_page_2,
            self.create_page_3,
            self.create_page_4,
        ]
        self.pages: list[ctk.CTkFrame] = []
        for builder in builders:
            page = builder()
            page.grid(row=0, column=0, sticky="nsew")
            self.pages.append(page)

        self.load_crew_view()
        self.show_page(0)
        self.root.after(SIM_MS, self.simulate_tick)

    # ------------------------------------------------------------------
    # Sample data and simulation
    # ------------------------------------------------------------------
    @staticmethod
    def _sample_crew() -> list[dict]:
        return [
            {"crew_id": "0x01 / APID-016", "name": "Elena Voronova", "gender": "F",
             "age": 34, "role": "Mission Specialist",
             "overall_health": 99, "stress": 12, "temp": 36.6, "press": 101.4,
             "pulse": 68, "spo2": 98, "connected": True, "battery": 100,
             "rssi": -48, "loss": 0.1, "uptime": 9240, "fw": "1.4.2"},
            {"crew_id": "0x02 / APID-017", "name": "James Carter", "gender": "M",
             "age": 41, "role": "Commander",
             "overall_health": 65, "stress": 41, "temp": 37.4, "press": 102.6,
             "pulse": 88, "spo2": 95, "connected": True, "battery": 72,
             "rssi": -57, "loss": 0.4, "uptime": 18130, "fw": "1.4.2"},
            {"crew_id": "0x03 / APID-018", "name": "Mark Ivanov", "gender": "M",
             "age": 38, "role": "Flight Engineer",
             "overall_health": 7, "stress": 93, "temp": 39.1, "press": 105.2,
             "pulse": 134, "spo2": 84, "connected": True, "battery": 18,
             "rssi": -71, "loss": 2.7, "uptime": 4410, "fw": "1.4.1"},
            {"crew_id": "0x04 / APID-019", "name": "Aiko Tanaka", "gender": "F",
             "age": 36, "role": "Medical Officer",
             "overall_health": 82, "stress": 25, "temp": 36.9, "press": 101.8,
             "pulse": 74, "spo2": 97, "connected": False, "battery": 54,
             "rssi": -90, "loss": 100.0, "uptime": 0, "fw": "1.4.2"},
        ]

    def _init_runtime(self) -> None:
        """Prepare simulation state, histories and packet logs for each crew member."""
        now = datetime.now(timezone.utc)
        for i, crew in enumerate(self.crew):
            crew["apid"] = int(crew["crew_id"].split("APID-")[1])
            crew["base"] = {k: crew[k] for k in ("temp", "press", "pulse", "spo2")}
            crew["nodes"] = {"t": [], "p": [], "e": []}
            crew["hist"] = {k: deque(maxlen=HIST_LEN) for k in ("temp", "pulse", "spo2")}
            crew["log"] = deque(maxlen=LOG_MAX)
            crew["seq"] = 4100 + 37 * i
            crew["humidity"] = 45.0
            crew["vel"] = 0.0
            crew["ppg"] = [0, 0, 0, 0]
            crew["raw"] = {}
            end = now - timedelta(seconds=0 if crew["connected"] else 300)
            for k in range(HIST_LEN):
                self.tick = k
                self._jitter_crew(crew)
                ts = end - timedelta(seconds=(HIST_LEN - 1 - k) * SIM_MS / 1000)
                self._update_raw(crew, ts)
                crew["seq"] += 1
                crew["last_packet"] = ts
                crew["log"].append(self._format_packet(crew, ts))
            if not crew["connected"]:
                crew["log"].append("-- LINK LOST: no packets received --")
        self.tick = HIST_LEN

    def _jitter_crew(self, crew: dict) -> None:
        """Advance one simulated sample for a crew member."""
        t = self.tick
        base = crew["base"]
        crew["temp"] = round(base["temp"] + 0.12 * math.sin(t / 12) + random.uniform(-0.04, 0.04), 2)
        crew["pulse"] = round(base["pulse"] + 3 * math.sin(t / 7) + random.uniform(-1.5, 1.5))
        crew["spo2"] = round(min(100.0, base["spo2"] + 0.6 * math.sin(t / 9) + random.uniform(-0.3, 0.3)), 1)
        crew["press"] = round(base["press"] + 0.4 * math.sin(t / 10) + random.uniform(-0.08, 0.08), 2)
        crew["nodes"]["t"] = [crew["temp"] + o + random.uniform(-0.03, 0.03) for o in T_OFFSETS]
        crew["nodes"]["p"] = [crew["press"] + o + random.uniform(-0.05, 0.05) for o in P_OFFSETS]
        e_base = 60 + crew["stress"] * 3.5
        crew["nodes"]["e"] = [
            max(0.0, e_base * (1 + 0.5 * math.sin(t / 5 + i)) + random.uniform(-10, 10))
            for i in range(6)
        ]
        # Placeholder blood velocity (cm/s) and PPG counts (r1, ir1, r2, ir2).
        crew["vel"] = round(0.30 * crew["pulse"] + 1.5 * math.sin(t / 6) + random.uniform(-0.4, 0.4), 1)
        ppg_wave = 2500 * math.sin(t / 3)
        crew["ppg"] = [
            round(base_counts + gain * ppg_wave + random.uniform(-400, 400))
            for base_counts, gain in ((84000, 1.0), (121000, 1.3), (81000, 0.9), (118000, 1.2))
        ]
        crew["humidity"] = round(40 + crew["stress"] * 0.2 + 3 * math.sin(t / 20), 1)
        crew["hist"]["temp"].append(sum(crew["nodes"]["t"]) / 4)
        crew["hist"]["pulse"].append(crew["pulse"])
        crew["hist"]["spo2"].append(crew["spo2"])

    @staticmethod
    def _format_packet(crew: dict, ts: datetime) -> str:
        """Build a CCSDS-style packet and render it as a hex log line."""
        payload = struct.pack(
            ">HHHH", int(crew["temp"] * 100), int(crew["press"] * 100),
            int(crew["pulse"]), int(crew["spo2"] * 10),
        )
        header = struct.pack(
            ">HHH", 0x0800 | crew["apid"], 0xC000 | (crew["seq"] & 0x3FFF), len(payload) - 1
        )
        data = header + payload
        data += struct.pack(">H", binascii.crc_hqx(data, 0xFFFF))
        words = " ".join(data[i:i + 2].hex().upper() for i in range(0, len(data), 2))
        return f"{ts:%H:%M:%S} {words}"

    def simulate_tick(self) -> None:
        """Simulated telemetry tick; swap for real pipeline output."""
        self.tick += 1
        now = datetime.now(timezone.utc)
        selected = self.crew[self.p2_index]
        for crew in self.crew:
            if not crew["connected"]:
                continue
            self._jitter_crew(crew)
            self._update_raw(crew, now)
            crew["seq"] += 1
            crew["uptime"] += SIM_MS / 1000
            crew["last_packet"] = now
            line = self._format_packet(crew, now)
            crew["log"].append(line)
            if crew is selected and self.current_page == 1 and not self.log_paused:
                self._append_log_line(line)
        if self.current_page == 1:
            self.refresh_page_2()
        self.root.after(SIM_MS, self.simulate_tick)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def create_sidebar(self) -> ctk.CTkFrame:
        sidebar = ctk.CTkFrame(self.root, width=200, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        # Title slot is intentionally blank (keeps the original spacing).
        ctk.CTkLabel(
            sidebar, text="", font=ctk.CTkFont(size=22, weight="bold")
        ).pack(padx=16, pady=(24, 0), anchor="w")
        ctk.CTkLabel(
            sidebar, text=SYSTEM_NAME, text_color=MUTED, wraplength=168, justify="left"
        ).pack(padx=16, pady=(0, 24), anchor="w")
        for index, (label, _title) in enumerate(NAV_ITEMS):
            button = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=40,
                fg_color="transparent",
                hover_color=("gray70", "gray25"),
                command=lambda i=index: self.on_nav_click(i),
            )
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons.append(button)
        return sidebar

    def on_nav_click(self, index: int) -> None:
        print(f"Sidebar: page {index + 1} selected (index {index})")
        self.show_page(index)

    def show_page(self, index: int) -> None:
        """Raise the page frame at ``index``; the root window is kept alive."""
        self.pages[index].tkraise()
        self.current_page = index
        for i, button in enumerate(self.nav_buttons):
            button.configure(fg_color=("gray75", "gray30") if i == index else "transparent")
        if index == 1:
            self.load_crew_view()

    # ------------------------------------------------------------------
    # Page 1: Crew overview
    # ------------------------------------------------------------------
    def create_page_1(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        # Header bar: connection counter (left) and Earth link (right)
        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        header.grid_columnconfigure(0, weight=1)

        peers = sum(1 for member in self.crew if member["connected"])
        self.peers_label = ctk.CTkLabel(
            header, text=f"Connected: {peers} peers", font=ctk.CTkFont(size=16, weight="bold")
        )
        self.peers_label.grid(row=0, column=0, sticky="w")

        self.earth_label = ctk.CTkLabel(
            header,
            text=f"Earth: {'ON' if EARTH_LINK_ON else 'OFF'}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="white",
            fg_color=GREEN if EARTH_LINK_ON else RED,
            corner_radius=12,
            padx=14,
            pady=3,
        )
        self.earth_label.grid(row=0, column=1, sticky="e")

        # Horizontal row of crew cards
        strip = ctk.CTkScrollableFrame(page, orientation="horizontal", fg_color="transparent")
        strip.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 14))
        for member in self.crew:
            card = self.build_crew_card(strip, member)
            card.pack(side="left", padx=8, pady=4, anchor="n")
        return page

    def build_crew_card(self, parent: ctk.CTkBaseClass, crew: dict) -> ctk.CTkFrame:
        crew_id = crew["crew_id"]
        card = ctk.CTkFrame(
            parent,
            width=300,
            height=540,
            corner_radius=18,
            fg_color=CARD_BG,
            border_width=3,
            border_color=health_color(crew["overall_health"]),
        )
        card.pack_propagate(False)
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)

        # Header: name + info button
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top, text=crew["name"], font=ctk.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            top, text="i", width=26, height=26, corner_radius=13,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: print(f"Info clicked for {crew_id}"),
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            card, text=f"ID: {crew_id}", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=1, column=0, sticky="w", padx=16)

        # Primary metrics
        ctk.CTkLabel(
            card, text=f"Overall health: {crew['overall_health']}%",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(10, 0))
        ctk.CTkLabel(
            card, text=f"Stress level: {crew['stress']}%", font=ctk.CTkFont(size=14), anchor="w"
        ).grid(row=3, column=0, sticky="w", padx=16)

        # Figure + More button (~40 % of card height)
        figure_row = ctk.CTkFrame(card, fg_color="transparent")
        figure_row.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 0))
        figure_row.grid_columnconfigure(0, weight=1)
        canvas = ctk.CTkCanvas(
            figure_row, width=140, height=210, bg=CARD_BG, highlightthickness=0
        )
        canvas.grid(row=0, column=0, sticky="w")
        self.draw_silhouette(canvas, crew["gender"])
        ctk.CTkButton(
            figure_row, text="More...", width=84,
            command=lambda: print(f"More clicked for {crew_id}"),
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

        # 2x2 vitals grid
        vitals = ctk.CTkFrame(card, fg_color="transparent")
        vitals.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        vitals.grid_columnconfigure((0, 1), weight=1, uniform="vitals")
        cells = [
            f"Temp: {crew['base']['temp']:.1f}°C", f"Press: {crew['base']['press']:.1f} kPa",
            f"Pulse: {crew['base']['pulse']}", f"SpO2: {crew['base']['spo2']}%",
        ]
        for i, text in enumerate(cells):
            ctk.CTkLabel(vitals, text=text, anchor="w", font=ctk.CTkFont(size=13)).grid(
                row=i // 2, column=i % 2, sticky="w", pady=2
            )

        # Footer: connection status + battery
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="sew", padx=16, pady=(10, 14))
        footer.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(6, weight=1)
        online = crew["connected"]
        ctk.CTkLabel(
            footer,
            text="● Connected" if online else "● Offline",
            text_color=GREEN if online else RED,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(footer, text=f"{crew['battery']}%", font=ctk.CTkFont(size=12)).grid(
            row=0, column=1, sticky="e"
        )
        return card

    @staticmethod
    def draw_silhouette(canvas: ctk.CTkCanvas, gender: str) -> None:
        """Draw a front-facing figure; ``gender`` is ``"M"`` or ``"F"``.

        The wristband is drawn on the figure's left arm (right side of the
        canvas, as the viewer faces the figure).
        """
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

        # Wristband on the figure's left arm (viewer's right), near the wrist
        (x0, y0), (x1, y1) = arm_r
        t = 0.8
        bx, by = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        half = arm_width / 2 + 3
        canvas.create_rectangle(
            bx - half, by - 4, bx + half, by + 4, fill=BAND_COLOR, outline=""
        )

    # ------------------------------------------------------------------
    # Page 2: Detailed telemetry
    # ------------------------------------------------------------------
    def create_page_2(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        # Carousel pagination dots (top centre)
        dots_row = ctk.CTkFrame(page, fg_color="transparent")
        dots_row.grid(row=0, column=0, columnspan=3, pady=(10, 0))
        self.dot_labels: list[ctk.CTkLabel] = []
        for i in range(len(self.crew)):
            dot = ctk.CTkLabel(dots_row, text="○", font=ctk.CTkFont(size=22))
            dot.pack(side="left", padx=5)
            dot.bind("<Button-1>", lambda _e, idx=i: self.select_crew(idx))
            self.dot_labels.append(dot)

        # Side arrows at the left/right edges
        arrow_font = ctk.CTkFont(size=28, weight="bold")
        ctk.CTkButton(
            page, text="<", width=46, font=arrow_font, fg_color=("gray75", "gray25"),
            hover_color=("gray65", "gray35"), command=self.prev_crew,
        ).grid(row=1, column=0, sticky="ns", padx=(10, 4), pady=10)
        ctk.CTkButton(
            page, text=">", width=46, font=arrow_font, fg_color=("gray75", "gray25"),
            hover_color=("gray65", "gray35"), command=self.next_crew,
        ).grid(row=1, column=2, sticky="ns", padx=(4, 10), pady=10)

        # Body: header card / three panels / charts
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=1, sticky="nsew", pady=(4, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=3)
        body.grid_rowconfigure(2, weight=2)

        self._build_p2_header(body).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        middle = ctk.CTkFrame(body, fg_color="transparent")
        middle.grid(row=1, column=0, sticky="nsew")
        middle.grid_rowconfigure(0, weight=1)
        middle.grid_columnconfigure(0, weight=3, uniform="mid")
        middle.grid_columnconfigure(1, weight=5, uniform="mid")
        middle.grid_columnconfigure(2, weight=3, uniform="mid")
        self._build_panel_a(middle).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_panel_b(middle).grid(row=0, column=1, sticky="nsew", padx=3)
        self._build_panel_c(middle).grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        self._build_charts(body).grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        return page

    def _build_p2_header(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color=CARD_BG)
        card.grid_columnconfigure((0, 1), weight=1)

        left = ctk.CTkFrame(card, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=18, pady=10)
        self.id_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=20, weight="bold"), anchor="w")
        self.id_label.pack(anchor="w")
        self.status_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=13), anchor="w")
        self.status_label.pack(anchor="w")

        right = ctk.CTkFrame(card, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=18, pady=10)
        self.name_label = ctk.CTkLabel(right, text="", font=ctk.CTkFont(size=24, weight="bold"), anchor="e")
        self.name_label.pack(anchor="e")
        self.subinfo_label = ctk.CTkLabel(right, text="", text_color=MUTED, anchor="e")
        self.subinfo_label.pack(anchor="e")
        return card

    def _build_panel_a(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(parent, corner_radius=12, fg_color=CARD_BG)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(panel, text="RAW DATA", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 0)
        )
        self.packet_label = ctk.CTkLabel(
            panel, text="Last Packet: --", text_color=MUTED, font=ctk.CTkFont(size=11), anchor="w"
        )
        self.packet_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        # Per-parameter table: value and last update time (UTC), grouped by sensor.
        table = ctk.CTkScrollableFrame(panel, fg_color="transparent", height=150)
        table.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        table.grid_columnconfigure(1, weight=1)
        mono = ctk.CTkFont(family="Courier", size=11)
        for col, (text, anchor) in enumerate((("param", "w"), ("value", "e"), ("last update UTC", "e"))):
            ctk.CTkLabel(
                table, text=text, text_color=MUTED, anchor=anchor, font=ctk.CTkFont(size=10)
            ).grid(row=0, column=col, sticky=anchor, padx=(4, 4))
        self.raw_labels: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        row = 1
        for rng, sensor, params, _unit, _dec, _period in RAW_SENSORS:
            ctk.CTkLabel(
                table, text=f"{rng} [Sensor - {sensor}]", anchor="w", justify="left",
                wraplength=250, font=ctk.CTkFont(size=11, weight="bold"),
            ).grid(row=row, column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))
            row += 1
            for param in params:
                ctk.CTkLabel(table, text=param, anchor="w", font=mono).grid(
                    row=row, column=0, sticky="w", padx=(4, 8)
                )
                value = ctk.CTkLabel(table, text="--", anchor="e", font=mono)
                value.grid(row=row, column=1, sticky="e", padx=4)
                stamp = ctk.CTkLabel(table, text="--:--:--", anchor="e", text_color=MUTED, font=mono)
                stamp.grid(row=row, column=2, sticky="e", padx=4)
                self.raw_labels[param] = (value, stamp)
                row += 1

        ctk.CTkLabel(
            panel, text="Packet feed (hex)", text_color=MUTED, font=ctk.CTkFont(size=10), anchor="w"
        ).grid(row=3, column=0, sticky="w", padx=12)
        self.raw_box = ctk.CTkTextbox(
            panel, wrap="char", height=80, fg_color="#0b0f0b", text_color="#3fe06b",
            font=ctk.CTkFont(family="Courier", size=10),
        )
        self.raw_box.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.raw_box.configure(state="disabled")
        ctk.CTkButton(
            panel, text="Pause / Resume", height=26, command=self.toggle_log_pause
        ).grid(row=5, column=0, sticky="e", padx=10, pady=(0, 10))
        return panel

    def _build_panel_b(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(parent, corner_radius=12, fg_color=CARD_BG)
        panel.grid_columnconfigure(1, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        self.layer_title = ctk.CTkLabel(panel, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.layer_title.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        # Left column: selector toolbar + legend
        side = ctk.CTkFrame(panel, fg_color="transparent")
        side.grid(row=1, column=0, sticky="n", padx=(10, 4))
        self.layer_buttons: dict[str, ctk.CTkButton] = {}
        for key in LAYERS:
            button = ctk.CTkButton(
                side, text=f"[ {key} ]", width=56, height=30, fg_color=INACTIVE,
                command=lambda k=key: self.select_layer(k),
            )
            button.pack(pady=2)
            self.layer_buttons[key] = button

        self.legend_max = ctk.CTkLabel(side, text="", font=ctk.CTkFont(size=10))
        self.legend_max.pack(pady=(10, 0))
        legend = ctk.CTkCanvas(side, width=16, height=90, bg=CARD_BG, highlightthickness=0)
        legend.pack(pady=2)
        for y in range(90):
            color = SPECTRUM_PALETTE[round((1 - y / 89) * (PALETTE_SIZE - 1))]
            legend.create_line(0, y, 16, y, fill=color)
        self.legend_min = ctk.CTkLabel(side, text="", font=ctk.CTkFont(size=10))
        self.legend_min.pack()

        # Right: heatmap schematic
        holder = ctk.CTkFrame(panel, fg_color="transparent")
        holder.grid(row=1, column=1, sticky="n", padx=(4, 10))
        self.heat_canvas = ctk.CTkCanvas(
            holder, width=HEAT_W, height=HEAT_H, bg=PLOT_BG, highlightthickness=1,
            highlightbackground="#888",
        )
        self.heat_canvas.pack()
        self.heat_cells: list[int] = []
        self._cell_colors: list[str | None] = []
        for r in range(HEAT_ROWS):
            for c in range(HEAT_COLS):
                x0, y0 = c * HEAT_CELL, r * HEAT_CELL
                self.heat_cells.append(self.heat_canvas.create_rectangle(
                    x0, y0, x0 + HEAT_CELL, y0 + HEAT_CELL, fill=PLOT_BG, outline="", tags="cell"
                ))
                self._cell_colors.append(None)
        self.heat_canvas.bind("<Motion>", self.on_heat_motion)
        self.heat_canvas.bind("<Leave>", lambda _e: self.heat_canvas.delete("tip"))
        self.heat_canvas.bind("<Button-1>", self.on_heat_click)

        caption = ctk.CTkFrame(holder, fg_color="transparent")
        caption.pack(fill="x")
        ctk.CTkLabel(caption, text="◄ WRIST", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="left")
        ctk.CTkLabel(caption, text="ELBOW ►", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="right")

        self.select_layer("T", quiet=True)
        return panel

    def _build_panel_c(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        panel = ctk.CTkFrame(parent, corner_radius=12, fg_color=CARD_BG)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(panel, text="TELEMETRY", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        listing = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        listing.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        listing.grid_columnconfigure(1, weight=1)
        self.tel_labels: dict[str, ctk.CTkLabel] = {}
        for row, (name, unit) in enumerate(TELEMETRY_ROWS):
            name_label = ctk.CTkLabel(listing, text=name, text_color=MUTED, anchor="w")
            name_label.grid(row=row, column=0, sticky="w", padx=(6, 8), pady=4)
            value = ctk.CTkLabel(listing, text="--", font=ctk.CTkFont(weight="bold"), anchor="e")
            value.grid(row=row, column=1, sticky="e")
            unit_label = ctk.CTkLabel(listing, text=unit, text_color=MUTED, anchor="w", width=44)
            unit_label.grid(row=row, column=2, sticky="w", padx=(4, 6))
            self.tel_labels[name] = value
            if name in MAP_LAYER_BY_ROW:  # clicking a map row opens that map
                for widget in (name_label, value, unit_label):
                    widget.bind("<Button-1>", lambda _e, k=MAP_LAYER_BY_ROW[name]: self.select_layer(k))
        return panel

    def _build_charts(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.grid_rowconfigure(0, weight=1)
        self.chart_canvases: dict[str, ctk.CTkCanvas] = {}
        self.chart_values: dict[str, ctk.CTkLabel] = {}
        for col, (title, key, _unit, color, _span, _dec) in enumerate(CHARTS):
            strip.grid_columnconfigure(col, weight=1, uniform="charts")
            card = ctk.CTkFrame(strip, corner_radius=12, fg_color=CARD_BG)
            card.grid(row=0, column=col, sticky="nsew", padx=3)
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=13, weight="bold")).grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 0)
            )
            value = ctk.CTkLabel(card, text="--", text_color=color, font=ctk.CTkFont(size=13, weight="bold"))
            value.grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))
            canvas = ctk.CTkCanvas(card, height=110, bg=PLOT_BG, highlightthickness=0)
            canvas.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
            canvas.bind("<Configure>", lambda _e: self.refresh_charts())
            self.chart_canvases[key] = canvas
            self.chart_values[key] = value
        return strip

    # --- Carousel ------------------------------------------------------
    def current_crew(self) -> dict:
        return self.crew[self.p2_index]

    def select_crew(self, index: int) -> None:
        self.p2_index = index % len(self.crew)
        crew = self.current_crew()
        print(f"Carousel: crew {crew['crew_id']} (index {self.p2_index})")
        self.load_crew_view()

    def prev_crew(self) -> None:
        print("Carousel: '<' clicked")
        self.select_crew(self.p2_index - 1)

    def next_crew(self) -> None:
        print("Carousel: '>' clicked")
        self.select_crew(self.p2_index + 1)

    def load_crew_view(self) -> None:
        """Reload every Page 2 widget for the selected crew member."""
        self._reload_log()
        self._draw_nodes()
        self.refresh_page_2()

    def refresh_page_2(self) -> None:
        crew = self.current_crew()
        for i, dot in enumerate(self.dot_labels):
            dot.configure(text="●" if i == self.p2_index else "○",
                          text_color="white" if i == self.p2_index else "gray40")
        online = crew["connected"]
        self.id_label.configure(text=f"ID: {crew['crew_id']}")
        self.status_label.configure(
            text="● Connected" if online else "● Offline", text_color=GREEN if online else RED
        )
        self.name_label.configure(text=crew["name"])
        self.subinfo_label.configure(
            text=f"Sex: {crew['gender']}  ·  Age: {crew['age']}  ·  Role: {crew['role']}"
        )
        self.packet_label.configure(
            text=f"Last Packet: {crew['last_packet']:%Y-%m-%d %H:%M:%S} UTC | Seq: #{crew['seq']}"
        )
        self._update_heatmap(crew)
        self._refresh_telemetry(crew)
        self._refresh_raw(crew)
        self.refresh_charts()

    # --- Raw feed ------------------------------------------------------
    def _reload_log(self) -> None:
        lines = list(self.current_crew()["log"])[-LOG_VISIBLE:]
        self.raw_box.configure(state="normal")
        self.raw_box.delete("1.0", "end")
        self.raw_box.insert("end", "\n".join(lines) + "\n")
        self.raw_box.see("end")
        self.raw_box.configure(state="disabled")

    def _append_log_line(self, line: str) -> None:
        self.raw_box.configure(state="normal")
        self.raw_box.insert("end", line + "\n")
        if int(self.raw_box.index("end-1c").split(".")[0]) > LOG_VISIBLE + 1:
            self.raw_box.delete("1.0", "2.0")
        self.raw_box.see("end")
        self.raw_box.configure(state="disabled")

    def toggle_log_pause(self) -> None:
        self.log_paused = not self.log_paused
        print(f"Raw feed: {'paused' if self.log_paused else 'resumed'}")
        if not self.log_paused:
            self._reload_log()

    # --- Sensor map ----------------------------------------------------
    def select_layer(self, key: str, quiet: bool = False) -> None:
        if not quiet:
            print(f"Sensor layer selected: [ {key} ]")
        self.p2_layer = key
        _prefix, _count, unit, vmin, vmax, dec, title = LAYERS[key]
        for k, button in self.layer_buttons.items():
            button.configure(fg_color=ACCENT if k == key else INACTIVE)
        self.layer_title.configure(text=title)
        self.legend_max.configure(text=f"Max\n{vmax:.{dec}f}{unit}")
        self.legend_min.configure(text=f"Min\n{vmin:.{dec}f}{unit}")
        if not quiet:
            self._draw_nodes()
            self._update_heatmap(self.current_crew())

    def _cell_weights(self, key: str) -> list[list[float]]:
        """Normalised inverse-distance weights of each node for every heatmap cell."""
        if key not in self._weights:
            weights = []
            for r in range(HEAT_ROWS):
                y = (r + 0.5) / HEAT_ROWS
                for c in range(HEAT_COLS):
                    x = (c + 0.5) / HEAT_COLS
                    raw = [1.0 / (((x - nx) * ASPECT) ** 2 + (y - ny) ** 2 + 0.003)
                           for nx, ny in NODE_POS[key]]
                    total = sum(raw)
                    weights.append([w / total for w in raw])
            self._weights[key] = weights
        return self._weights[key]

    def _update_heatmap(self, crew: dict) -> None:
        key = self.p2_layer
        _p, _n, _u, vmin, vmax, _d, _t = LAYERS[key]
        values = crew["nodes"][key.lower()]
        for idx, weights in enumerate(self._cell_weights(key)):
            value = sum(w * v for w, v in zip(weights, values))
            level = min(max((value - vmin) / (vmax - vmin), 0.0), 1.0)
            color = SPECTRUM_PALETTE[round(level * (PALETTE_SIZE - 1))]
            if color != self._cell_colors[idx]:
                self.heat_canvas.itemconfigure(self.heat_cells[idx], fill=color)
                self._cell_colors[idx] = color

    def _draw_nodes(self) -> None:
        canvas = self.heat_canvas
        canvas.delete("node")
        canvas.delete("tip")
        prefix = LAYERS[self.p2_layer][0]
        for i, (nx, ny) in enumerate(NODE_POS[self.p2_layer]):
            x, y = nx * HEAT_W, ny * HEAT_H
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="white", outline="#111",
                               width=2, tags="node")
            canvas.create_text(x, y - 13, text=f"{prefix}{i + 1}", fill="white",
                               font=("TkDefaultFont", 9, "bold"), tags="node")

    def _nearest_node(self, x: float, y: float) -> int | None:
        best, best_d = None, 12.0
        for i, (nx, ny) in enumerate(NODE_POS[self.p2_layer]):
            d = math.hypot(x - nx * HEAT_W, y - ny * HEAT_H)
            if d <= best_d:
                best, best_d = i, d
        return best

    def on_heat_motion(self, event) -> None:
        canvas = self.heat_canvas
        canvas.delete("tip")
        i = self._nearest_node(event.x, event.y)
        if i is None:
            return
        prefix, _n, unit, _lo, _hi, dec, _t = LAYERS[self.p2_layer]
        value = self.current_crew()["nodes"][self.p2_layer.lower()][i]
        nx, ny = NODE_POS[self.p2_layer][i]
        x, y = nx * HEAT_W, ny * HEAT_H
        east, south = x > HEAT_W / 2, y > HEAT_H / 2
        anchor = ("s" if south else "n") + ("e" if east else "w")
        text = canvas.create_text(
            x + (-12 if east else 12), y + (-12 if south else 12),
            text=f"Node {prefix}{i + 1}: {value:.{dec}f} {unit}", anchor=anchor,
            fill="white", font=("TkDefaultFont", 10, "bold"), tags="tip",
        )
        x0, y0, x1, y1 = canvas.bbox(text)
        box = canvas.create_rectangle(x0 - 4, y0 - 2, x1 + 4, y1 + 2, fill="#111",
                                      outline="#aaa", tags="tip")
        canvas.tag_raise(text, box)

    def on_heat_click(self, event) -> None:
        i = self._nearest_node(event.x, event.y)
        if i is not None:
            prefix = LAYERS[self.p2_layer][0]
            print(f"Sensor node clicked: {prefix}{i + 1} ({self.current_crew()['crew_id']})")

    # --- TELEMETRY list ------------------------------------------------
    @staticmethod
    def _telemetry_values(crew: dict) -> dict[str, tuple[str, str | None]]:
        """Display text (and optional colour) for each row of ``TELEMETRY_ROWS``."""
        nodes = crew["nodes"]
        e_rms = math.sqrt(sum(v * v for v in nodes["e"]) / len(nodes["e"]))

        def span(key: str, dec: int) -> str:
            return f"{min(nodes[key]):.{dec}f}-{max(nodes[key]):.{dec}f}"

        return {
            "Avg. t": (f"{sum(nodes['t']) / len(nodes['t']):.1f}", None),
            "Avg. p": (f"{sum(nodes['p']) / len(nodes['p']):.2f}", None),
            "emRMS (Avg. e)": (f"{e_rms:.0f}", None),
            "t Map": (span("t", 1), None),
            "p Map": (span("p", 2), None),
            "em Map": (span("e", 0), None),
            "pulse": (f"{crew['pulse']:.0f}", None),
            "spo2": (f"{crew['spo2']:.1f}", None),
            "blood vel": (f"{crew['vel']:.1f}", None),
            "humidity": (f"{crew['humidity']:.1f}", None),
            "+charge": (f"{crew['battery']}", RED if crew["battery"] < 20 else None),
        }

    def _refresh_telemetry(self, crew: dict) -> None:
        default = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        for name, (text, color) in self._telemetry_values(crew).items():
            self.tel_labels[name].configure(text=text, text_color=color or default)

    # --- RAW DATA table ------------------------------------------------
    @staticmethod
    def _raw_values(crew: dict) -> dict[str, float]:
        """Current value of every raw parameter, keyed by parameter name."""
        values: dict[str, float] = {}
        for prefix in ("t", "p", "e"):
            for i, value in enumerate(crew["nodes"][prefix]):
                values[f"{prefix}{i + 1}"] = value
        values.update(zip(("r1", "ir1", "r2", "ir2"), crew["ppg"]))
        values["h1"] = crew["humidity"]
        values["+vBat"] = 3.30 + 0.90 * crew["battery"] / 100
        return values

    def _update_raw(self, crew: dict, ts: datetime) -> None:
        """Latch a sensor's parameters and their timestamp when its update period elapses."""
        current = self._raw_values(crew)
        for _rng, _sensor, params, _unit, _dec, period in RAW_SENSORS:
            if self.tick % period == 0 or params[0] not in crew["raw"]:
                for param in params:
                    crew["raw"][param] = (current[param], ts)

    def _refresh_raw(self, crew: dict) -> None:
        for _rng, _sensor, params, unit, dec, _period in RAW_SENSORS:
            for param in params:
                value, ts = crew["raw"][param]
                value_label, stamp_label = self.raw_labels[param]
                value_label.configure(text=f"{value:.{dec}f} {unit}")
                stamp_label.configure(text=f"{ts:%H:%M:%S}")

    # --- Strip charts --------------------------------------------------
    def refresh_charts(self) -> None:
        crew = self.current_crew()
        for _title, key, unit, color, span, dec in CHARTS:
            values = list(crew["hist"][key])
            self._draw_chart(self.chart_canvases[key], values, color, span, dec)
            if values:
                self.chart_values[key].configure(text=f"{values[-1]:.{dec}f} {unit}")

    @staticmethod
    def _draw_chart(canvas: ctk.CTkCanvas, values: list[float], color: str,
                    min_span: float, decimals: int) -> None:
        canvas.delete("all")
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 80 or h < 40 or not values:
            return
        left, right, top, bottom = 40, 8, 8, 8
        lo, hi = min(values), max(values)
        if hi - lo < min_span:
            mid = (hi + lo) / 2
            lo, hi = mid - min_span / 2, mid + min_span / 2
        pad = (hi - lo) * 0.1
        lo, hi = lo - pad, hi + pad
        plot_h = h - top - bottom
        for frac in (0, 0.5, 1):
            y = top + plot_h * frac
            canvas.create_line(left, y, w - right, y, fill="#3a3a3a")
            canvas.create_text(left - 4, y, text=f"{hi - (hi - lo) * frac:.{decimals}f}",
                               anchor="e", fill="gray60", font=("TkDefaultFont", 8))
        step = (w - left - right) / max(len(values) - 1, 1)
        points: list[float] = []
        for i, v in enumerate(values):
            points += [left + i * step, top + plot_h * (hi - v) / (hi - lo)]
        canvas.create_line(*points, fill=color, width=2)

    # ------------------------------------------------------------------
    # Page 3: First aid & protocol archive (mock data, print callbacks)
    # ------------------------------------------------------------------
    # ==================================================================
    # PAGE 3 START
    # ==================================================================
    P3_CARD = "#1c2128"
    P3_CARD_HOVER = "#262d36"
    P3_BORDER = "#30363d"
    P3_MUTED = "#8b949e"
    P3_GOLD = "#f5b301"
    P3_STAR_EMPTY = "#3a3f47"
    P3_PANEL = "#161b22"
    P3_MODAL_BG = "#0d1117"
    P3_MONO = "Courier New"

    # ---------------------------- mock data ---------------------------
    def _get_mock_documents(self):
        """Mock entries standing in for PDF files located in fadocs/."""
        return [
            {
                "filename": "HYPOXIA_EMERGENCY_PROTOCOL.pdf",
                "title": "Hypoxia Emergency Protocol",
                "rating": 5.0,
                "tags": ["hypoxia", "spo2", "oxygen", "critical", "nasa-std-3001"],
                "size": "4.2 MB",
                "pages": 12,
                "summary": (
                    "PLACEHOLDER SUMMARY\n\n"
                    "Covers recognition and response to hypoxia in a crewmember "
                    "exposed to a degraded habitable atmosphere. Describes early "
                    "indicators (reduced SpO2, elevated heart rate, impaired "
                    "judgement), immediate oxygen restoration, and criteria for "
                    "escalating to medical support."
                ),
                "points": [
                    "Verify SpO2 < 88% on two consecutive readings",
                    "Administer 100% O2 and restore a habitable atmosphere",
                    "Keep the crewmember at rest and monitor continuously",
                    "Log event time and readings for the medical record",
                ],
            },
            {
                "filename": "ACUTE_CARDIAC_ARRHYTHMIA_RESPONSE.pdf",
                "title": "Acute Cardiac Arrhythmia Response",
                "rating": 4.8,
                "tags": ["cardiac", "arrhythmia", "heart-rate", "critical", "ppg"],
                "size": "3.1 MB",
                "pages": 9,
                "summary": (
                    "PLACEHOLDER SUMMARY\n\n"
                    "Outlines the response to irregular or sustained abnormal "
                    "heart rhythm detected by wearable telemetry, including "
                    "confirmation of the reading, crew positioning, and "
                    "notification of the flight surgeon."
                ),
                "points": [
                    "Confirm the reading with a second sensor channel",
                    "Stop activity and position the crewmember safely",
                    "Notify the flight surgeon with recent HR trend data",
                    "Prepare emergency equipment per the cardiac checklist",
                ],
            },
            {
                "filename": "DECOMPRESSION_SICKNESS_SOP.pdf",
                "title": "Decompression Sickness SOP",
                "rating": 4.5,
                "tags": ["dcs", "barotrauma", "pressure", "eva", "nasa-std-3001"],
                "size": "6.8 MB",
                "pages": 18,
                "summary": (
                    "PLACEHOLDER SUMMARY\n\n"
                    "Standard operating procedure for suspected decompression "
                    "sickness after pressure changes or EVA prebreathe "
                    "deviations. Covers symptom screening, oxygen therapy, and "
                    "recompression decision points."
                ),
                "points": [
                    "Screen for joint pain, rash, numbness, or fatigue",
                    "Begin 100% oxygen breathing immediately",
                    "Record the pressure profile preceding symptom onset",
                    "Follow the recompression decision tree with ground support",
                ],
            },
            {
                "filename": "THERMAL_BURN_TRIAGE.pdf",
                "title": "Thermal Burn Triage",
                "rating": 3.5,
                "tags": ["burn", "thermal", "triage", "skin", "temperature"],
                "size": "2.4 MB",
                "pages": 7,
                "summary": (
                    "PLACEHOLDER SUMMARY\n\n"
                    "Describes assessment and first response to thermal burns "
                    "in a closed habitat, including burn depth estimation, "
                    "cooling, dressing, and infection prevention."
                ),
                "points": [
                    "Remove the heat source and cool the area per protocol",
                    "Estimate burn depth and affected surface area",
                    "Apply a sterile dressing and avoid popping blisters",
                    "Monitor skin temperature trend on the wearable",
                ],
            },
            {
                "filename": "MUSCLE_FATIGUE_TREMOR_ASSESSMENT.pdf",
                "title": "Muscle Fatigue & Tremor Assessment",
                "rating": 2.7,
                "tags": ["emg", "fatigue", "tremor", "muscle", "assessment"],
                "size": "1.9 MB",
                "pages": 6,
                "summary": (
                    "PLACEHOLDER SUMMARY\n\n"
                    "Guidance for interpreting sustained EMG fatigue markers "
                    "and tremor patterns, with rest, hydration, and workload "
                    "adjustment recommendations."
                ),
                "points": [
                    "Compare current EMG features against the personal baseline",
                    "Reduce workload and schedule rest",
                    "Check hydration and skin-electrode contact quality",
                    "Escalate if tremor persists after rest",
                ],
            },
        ]

    # ------------------------- star rating widget ---------------------
    @staticmethod
    def _p3_clip_polygon_x(points, x_max):
        """Clip a polygon to the half-plane x <= x_max (Sutherland-Hodgman)."""
        def cross(a, b):
            t = (x_max - a[0]) / (b[0] - a[0])
            return (x_max, a[1] + t * (b[1] - a[1]))

        out = []
        for i, cur in enumerate(points):
            prev = points[i - 1]
            cur_in = cur[0] <= x_max
            prev_in = prev[0] <= x_max
            if cur_in:
                if not prev_in:
                    out.append(cross(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(cross(prev, cur))
        return out

    def _p3_build_star_widget(self, parent, rating, bg, size=18):
        """Canvas showing 5 stars; any float 0.0-5.0 renders full/half/empty."""
        try:
            value = max(0.0, min(5.0, round(float(rating) * 2) / 2))
        except (TypeError, ValueError):
            value = 0.0
        gap = 3
        radius = size / 1.902  # star width = 1.902 * outer radius
        height = int(radius * 1.809) + 4
        width = 5 * size + 4 * gap + 2
        canvas = tk.Canvas(parent, width=width, height=height, bg=bg,
                           highlightthickness=0, bd=0)
        for i in range(5):
            left = 1 + i * (size + gap)
            cx, cy = left + size / 2, radius + 2
            pts = []
            for k in range(10):
                ang = math.radians(-90 + k * 36)
                r = radius if k % 2 == 0 else radius * 0.382
                pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
            canvas.create_polygon(pts, fill=self.P3_STAR_EMPTY, outline="")
            fill = max(0.0, min(1.0, value - i))
            if fill >= 1.0:
                canvas.create_polygon(pts, fill=self.P3_GOLD, outline="")
            elif fill > 0.0:
                clipped = self._p3_clip_polygon_x(pts, left + fill * size)
                if len(clipped) >= 3:
                    canvas.create_polygon(clipped, fill=self.P3_GOLD, outline="")
        return canvas

    # ----------------------------- page 3 -----------------------------
    def create_page_3(self):
        """Build Page 3 and return its frame (placement is left to the caller)."""
        parent = self.content
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        bar.grid_columnconfigure(0, weight=1)

        self.p3_search_entry = ctk.CTkEntry(
            bar, height=40, corner_radius=10,
            placeholder_text="Search emergency protocols, symptoms, or tags...")
        self.p3_search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.p3_search_entry.bind("<Return>", lambda e: self._p3_on_search())

        ctk.CTkButton(bar, text="Search", width=110, height=40,
                      command=self._p3_on_search).grid(row=0, column=1)

        ctk.CTkLabel(
            bar, text="Source Directory: fadocs/", text_color=self.P3_MUTED,
            font=ctk.CTkFont(family=self.P3_MONO, size=12)
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.p3_doc_list = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.p3_doc_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
        self.p3_doc_list.grid_columnconfigure(0, weight=1)

        self.p3_docs = self._get_mock_documents()
        self._p3_render_cards(self.p3_docs)
        self.page_3 = page
        return page

    def _p3_on_search(self):
        query = self.p3_search_entry.get().strip()
        print(f"Searching: {query}")
        needle = query.lower().lstrip("#")
        if needle:
            docs = [d for d in self.p3_docs
                    if needle in d["title"].lower()
                    or needle in d["filename"].lower()
                    or any(needle in t for t in d["tags"])]
        else:
            docs = self.p3_docs
        self._p3_render_cards(docs)

    def _p3_render_cards(self, docs):
        for child in self.p3_doc_list.winfo_children():
            child.destroy()
        if not docs:
            ctk.CTkLabel(self.p3_doc_list, text="No matching documents.",
                         text_color=self.P3_MUTED).grid(row=0, column=0, pady=30)
            return
        for row, doc in enumerate(docs):
            card = self._p3_build_card(self.p3_doc_list, doc)
            card.grid(row=row, column=0, sticky="ew", pady=6, padx=2)

    def _p3_build_card(self, parent, doc):
        card = ctk.CTkFrame(parent, fg_color=self.P3_CARD, corner_radius=10,
                            border_width=1, border_color=self.P3_BORDER)
        card.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(card, text=doc["title"].upper(), anchor="w",
                             font=ctk.CTkFont(size=15, weight="bold"))
        title.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(12, 2))

        stars = self._p3_build_star_widget(card, doc["rating"], self.P3_CARD)
        stars.grid(row=0, column=1, sticky="e", padx=(8, 14), pady=(12, 2))

        tags_text = "Tags: " + " ".join(f"#{t}" for t in doc["tags"])
        tags = ctk.CTkLabel(card, text=tags_text, anchor="w",
                            text_color=self.P3_MUTED, font=ctk.CTkFont(size=11))
        tags.grid(row=1, column=0, sticky="w", padx=(14, 8), pady=(0, 12))

        size = ctk.CTkLabel(card, text=f"Size: {doc['size']} | PDF",
                            text_color=self.P3_MUTED, font=ctk.CTkFont(size=11))
        size.grid(row=1, column=1, sticky="e", padx=(8, 14), pady=(0, 12))

        def set_hover(active):
            color = self.P3_CARD_HOVER if active else self.P3_CARD
            card.configure(fg_color=color)
            stars.configure(bg=color)

        def on_leave(event):
            try:
                under = card.winfo_containing(event.x_root, event.y_root)
            except (KeyError, tk.TclError):
                under = None
            while under is not None:
                if under is card:
                    return  # pointer moved onto a child of this card
                under = getattr(under, "master", None)
            set_hover(False)

        for widget in (card, title, stars, tags, size):
            widget.bind("<Enter>", lambda e: set_hover(True), add="+")
            widget.bind("<Leave>", on_leave, add="+")
            widget.bind("<Button-1>",
                        lambda e, d=doc: self.open_document_modal(d), add="+")
        return card

    # --------------------------- modal viewer -------------------------
    def open_document_modal(self, doc_data):
        print(f"Opening document: {doc_data['filename']}")

        previous = getattr(self, "_p3_modal", None)
        if previous is not None:
            try:
                if previous.winfo_exists():
                    previous.destroy()
            except tk.TclError:
                pass

        modal = ctk.CTkToplevel(self.root)
        self._p3_modal = modal
        modal.title(doc_data["title"])
        modal.configure(fg_color=self.P3_MODAL_BG)
        modal.minsize(760, 560)

        w, h = 1020, 800
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        modal.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
        modal.transient(self.root)
        modal.bind("<Escape>", lambda e: modal.destroy())

        def grab_when_ready():
            try:
                modal.grab_set()
                modal.focus_set()
            except tk.TclError:
                pass  # window not viewable yet / already closed

        modal.after(200, grab_when_ready)

        modal.grid_columnconfigure(0, weight=1)
        modal.grid_rowconfigure(1, weight=1)
        modal.grid_rowconfigure(2, weight=2)

        # ---- top bar ----
        top = ctk.CTkFrame(modal, fg_color=self.P3_PANEL, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(top, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        ctk.CTkLabel(left, text=doc_data["title"].upper(), anchor="w",
                     font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, columnspan=2, sticky="w")
        ctk.CTkLabel(left, text=f"Size: {doc_data['size']} | PDF",
                     text_color=self.P3_MUTED, font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._p3_build_star_widget(left, doc_data["rating"], self.P3_PANEL,
                                   size=16).grid(row=1, column=1, sticky="w",
                                                 padx=(14, 0), pady=(4, 0))

        ctk.CTkButton(top, text="[ ✕ Close ]", width=110, height=36,
                      fg_color="#b62324", hover_color="#da3633",
                      command=modal.destroy
                      ).grid(row=0, column=1, sticky="e", padx=16)

        # ---- summary + key points ----
        info = ctk.CTkFrame(modal, fg_color="transparent")
        info.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 8))
        info.grid_columnconfigure((0, 1), weight=1, uniform="info")
        info.grid_rowconfigure(0, weight=1)

        def read_only_section(col, heading, text):
            section = ctk.CTkFrame(info, fg_color="transparent")
            section.grid(row=0, column=col, sticky="nsew",
                         padx=(0, 6) if col == 0 else (6, 0))
            section.grid_columnconfigure(0, weight=1)
            section.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(section, text=heading, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=self.P3_MUTED
                         ).grid(row=0, column=0, sticky="w", pady=(0, 4))
            box = ctk.CTkTextbox(section, wrap="word", fg_color=self.P3_PANEL,
                                 border_width=1, border_color=self.P3_BORDER)
            box.grid(row=1, column=0, sticky="nsew")
            box.insert("1.0", text)
            box.configure(state="disabled")

        read_only_section(0, "BRIEF SUMMARY", doc_data["summary"])
        points_text = "\n".join(f"• Step {i}: {p}"
                                for i, p in enumerate(doc_data["points"], 1))
        read_only_section(1, "KEY ACTIONABLE POINTS", points_text)

        # ---- PDF viewer ----
        pdf = ctk.CTkFrame(modal, fg_color="transparent")
        pdf.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        pdf.grid_columnconfigure(0, weight=1)
        pdf.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(pdf, text=f"DOCUMENT PREVIEW (fadocs/{doc_data['filename']})",
                     anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.P3_MUTED
                     ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        nav = ctk.CTkFrame(pdf, fg_color=self.P3_PANEL, corner_radius=8)
        nav.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        nav.grid_columnconfigure(2, weight=1)

        total = int(doc_data["pages"])
        state = {"page": 1, "expanded": False}

        prev_btn = ctk.CTkButton(nav, text="Prev Page", width=100, height=30)
        prev_btn.grid(row=0, column=0, padx=(8, 6), pady=6)
        page_label = ctk.CTkLabel(nav, text=f"Page 1 / {total}", width=110,
                                  font=ctk.CTkFont(family=self.P3_MONO, size=13))
        page_label.grid(row=0, column=1, padx=6)
        next_btn = ctk.CTkButton(nav, text="Next Page", width=100, height=30)
        next_btn.grid(row=0, column=2, sticky="w", padx=6)
        expand_btn = ctk.CTkButton(nav, text="[ ⤢ Expand PDF ]", width=140,
                                   height=30, fg_color="#30363d",
                                   hover_color="#3d444d")
        expand_btn.grid(row=0, column=3, sticky="e", padx=8)

        viewport = ctk.CTkFrame(pdf, fg_color="#2b2f36", corner_radius=8,
                                border_width=1, border_color=self.P3_BORDER)
        viewport.grid(row=2, column=0, sticky="nsew")
        viewport.grid_columnconfigure(0, weight=1)
        viewport.grid_rowconfigure(0, weight=1)
        canvas = tk.Canvas(viewport, bg="#2b2f36", highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

        def refresh():
            page_label.configure(text=f"Page {state['page']} / {total}")
            prev_btn.configure(state="normal" if state["page"] > 1 else "disabled")
            next_btn.configure(state="normal" if state["page"] < total else "disabled")
            self._p3_render_mock_page(canvas, doc_data, state["page"])

        def go(delta):
            state["page"] = max(1, min(total, state["page"] + delta))
            print(f"PDF page -> {state['page']} / {total}")
            refresh()

        def toggle_expand():
            if state["expanded"]:
                info.grid()  # restores the remembered grid options
                modal.grid_rowconfigure(1, weight=1)
                modal.grid_rowconfigure(2, weight=2)
                expand_btn.configure(text="[ ⤢ Expand PDF ]")
            else:
                info.grid_remove()
                modal.grid_rowconfigure(1, weight=0)
                modal.grid_rowconfigure(2, weight=1)
                expand_btn.configure(text="[ ⤡ Restore ]")
            state["expanded"] = not state["expanded"]
            print(f"PDF viewer expanded: {state['expanded']}")

        prev_btn.configure(command=lambda: go(-1))
        next_btn.configure(command=lambda: go(1))
        expand_btn.configure(command=toggle_expand)
        canvas.bind("<Configure>", lambda e: refresh())
        refresh()

    def _p3_render_mock_page(self, canvas, doc_data, page_num):
        """Draw a placeholder PDF page that scales to fit the viewport."""
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw < 60 or ch < 60:
            return

        # --- PDF RENDER HOOK -------------------------------------------
        # Replace the mock drawing below with a real page render, e.g.:
        #   import fitz  # pymupdf
        #   from PIL import Image, ImageTk
        #   pdf = fitz.open(f"fadocs/{doc_data['filename']}")
        #   pix = pdf[page_num - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        #   img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        #   self._p3_photo = ImageTk.PhotoImage(img)  # keep a reference
        #   canvas.create_image(cw / 2, ch / 2, image=self._p3_photo)
        # ----------------------------------------------------------------
        ph = ch - 24
        pw = ph * 8.5 / 11
        if pw > cw - 24:
            pw = cw - 24
            ph = pw * 11 / 8.5
        x0, y0 = (cw - pw) / 2, (ch - ph) / 2
        m = pw * 0.08

        canvas.create_rectangle(x0 + 5, y0 + 5, x0 + pw + 5, y0 + ph + 5,
                                fill="#15181d", outline="")
        canvas.create_rectangle(x0, y0, x0 + pw, y0 + ph,
                                fill="#f4f4f2", outline="#c9c9c4")
        canvas.create_text(x0 + m, y0 + m, anchor="nw", width=pw - 2 * m,
                           text=doc_data["title"].upper(), fill="#1b1f24",
                           font=("Helvetica", max(8, int(pw * 0.035)), "bold"))
        rule_y = y0 + m + ph * 0.07
        canvas.create_rectangle(x0 + m, rule_y, x0 + pw - m, rule_y + 2,
                                fill="#b02a2a", outline="")

        line_h = max(6.0, ph * 0.025)
        y, i = rule_y + line_h, 0
        while y < y0 + ph - m * 1.6:
            frac = 0.55 + 0.45 * abs(math.sin(i * 1.7 + page_num))
            canvas.create_rectangle(x0 + m, y, x0 + m + (pw - 2 * m) * frac,
                                    y + line_h * 0.45, fill="#c4c7cc", outline="")
            y += line_h
            i += 1
            if i % 6 == 0:
                y += line_h * 0.8
        canvas.create_text(x0 + pw / 2, y0 + ph - m * 0.7,
                           text=f"- {page_num} -  MOCK PAGE", fill="#6b7280",
                           font=(self.P3_MONO, 9))
    # ==================================================================
    # PAGE 3 END
    # ==================================================================

    # ------------------------------------------------------------------
    # Page 4: placeholder
    # ------------------------------------------------------------------
    def _placeholder_page(self, index: int) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        title = NAV_ITEMS[index][1]
        ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=22, weight="bold")).pack(
            anchor="w", padx=20, pady=(20, 8)
        )
        ctk.CTkLabel(page, text="Not implemented yet.", text_color=MUTED).pack(
            anchor="w", padx=20
        )
        ctk.CTkButton(
            page, text="Test button", width=120,
            command=lambda: print(f"Test button clicked on page {index + 1}"),
        ).pack(anchor="w", padx=20, pady=16)
        return page

    def create_page_4(self) -> ctk.CTkFrame:
        return self._placeholder_page(3)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Interface().run()
