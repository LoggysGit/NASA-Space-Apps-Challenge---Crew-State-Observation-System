"""cSOS desktop interface: crew overview, detailed telemetry and protocol archive."""

try:
    import pymupdf as fitz
except ImportError:
    fitz = None
import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from collections import deque
from datetime import datetime, timezone
import json
import os
import glob
import time
import re
from typing import Callable
from PIL import Image
import customtkinter as ctk                                                              
                                                                             
SYSTEM_NAME = "cSOS (Crew State Observation System)"
APP_TITLE = SYSTEM_NAME
WINDOW_SIZE = "1360x860"

CARD_BG = "#171f2b"
PLOT_BG = "#0d1420"
MUTED = "#91a0b5"
GREEN = "#39d98a"
RED = "#ff647c"
BAND_COLOR = "#58c7fa"
ACCENT = "#3978f6"
INACTIVE = "#344154"
MONO = "Cascadia Mono"
APP_BG = "#0b1220"
PANEL_BG = "#121c2b"
PANEL_BORDER = "#26364b"
FIELD_BG = "#0d1726"
               
DOC_CARD = "#1c2128"
DOC_CARD_HOVER = "#262d36"
DOC_BORDER = "#30363d"
DOC_MUTED = "#8b949e"
DOC_GOLD = "#f5b301"
DOC_STAR_EMPTY = "#3a3f47"
DOC_PANEL = "#161b22"
DOC_MODAL_BG = "#0d1117"

                 
REFRESH_INTERVAL_MS = 500
CONNECTED_HOLD_SECONDS = 15
HIST_LEN = 120
LOG_MAX = 300
LOG_VISIBLE = 80
HISTORY_KEYS = ("temperature", "pulse", "spo2")

                                                       
HEAT_COLS = 16
HEAT_ROWS = 30
HEAT_CELL = 11
HEAT_W = HEAT_COLS * HEAT_CELL
HEAT_H = HEAT_ROWS * HEAT_CELL
ASPECT = HEAT_W / HEAT_H

                                                            
SPECTRUM_STOPS = [
    (0.00, (0x0B, 0x1F, 0x8F)),
    (0.25, (0x1E, 0x90, 0xFF)),
    (0.50, (0x2E, 0xCC, 0x71)),
    (0.75, (0xF4, 0xD0, 0x3F)),
    (1.00, (0xD0, 0x02, 0x1B)),
]
PALETTE_SIZE = 64

PAGE_TELEMETRY = 1

                                           
NAV_ITEMS = [
    ("Overview", "Crew Overview", "grid"),
    ("Telemetry", "Detailed Telemetry", "activity"),
    ("Logs / Docs", "Logs & Docs", "file"),
    ("Settings", "Settings", "settings"),
]

SVG_ICONS = {
    "grid": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d7e4f5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    "activity": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d7e4f5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>',
    "file": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d7e4f5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/></svg>',
    "settings": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d7e4f5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.7-1.4-2.4 1.4-1.1a7 7 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.7 1.4 2.4-1.4 1.1a7 7 0 0 1 0 2z" transform="translate(-1 -1) scale(1.08)"/></svg>',
    "user-plus": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="10" cy="7" r="4"/><path d="M19 8v6M16 11h6"/></svg>',
    "trash": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/></svg>',
    "check": '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></svg>',
}

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

                                                                               
NODE_POS = {
    "T": ((0.35, 0.18), (0.65, 0.18), (0.35, 0.82), (0.65, 0.82)),
    "P": ((0.50, 0.22), (0.30, 0.50), (0.70, 0.50), (0.50, 0.78)),
    "E": ((0.50, 0.10), (0.30, 0.28), (0.70, 0.42), (0.30, 0.58), (0.70, 0.72), (0.50, 0.90)),
}


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
    ChartSpec("Temperature · t1", "temperature", "°C", "#f5b942", 2.0, 1),
    ChartSpec("Pulse rate", "pulse", "BPM", "#ef5350", 20.0, 0),
    ChartSpec("SpO2", "spo2", "%", "#4fc3f7", 4.0, 1),
)

                                                                                 
TELEMETRY_ROWS = (
    ("Temperature nodes", "raw"),
    ("Pressure nodes", "raw"),
    ("EMG nodes", "raw"),
    ("t Map", "[C]"),
    ("p Map", "[kPa]"),
    ("em Map", "[muV]"),
    ("pulse", "bpm"),
    ("spo2", "%"),
    ("blood vel", "cm/s"),
    ("humidity", "%"),
    ("Battery voltage", "V"),
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
    RawSensor("vbat", "BQ27441-G1", ("vbat",), "V", 3, 10),
)


                                                                             
                
                                                                             





                                                                             
            
                                                                             
class CrewMember:
    """Crew identity and values supplied by Core."""

    def __init__(self, crew_id, name="Unknown crew member", gender="—", age=None, role="—"):
        self.crew_id = str(crew_id)
        self.name = name
        self.gender = gender
        self.age = age
        self.role = role
        match = re.search(r"(\d+)$", self.crew_id)
        self.apid = int(match.group(1)) if match else 0
        self.seq = None
        self.last_packet = None
        self.last_seen = 0.0
        self.last_signal_monotonic = 0.0
        self.connected = False
        self.pulse = None
        self.spo2 = None
        self.vel = None
        self.humidity = None
        self.ppg = [None] * 4
        self.nodes = {"t": [], "p": [], "e": []}
        self.hist = {key: deque(maxlen=HIST_LEN) for key in HISTORY_KEYS}
        self.log = deque(maxlen=LOG_MAX)
        self.raw = {}
        self.source_file = ""

class UIHelpers:
    """Static helpers for interface rendering."""

    _gender_images = {}

    @staticmethod
    def svg_icon(name: str, size: int = 20):
        return None

    @staticmethod
    def gender_image(gender: str, size: tuple[int, int] = (132, 198)):
        """Load the project's supplied PNG portrait for the selected gender."""
        key = str(gender or "").strip().upper()
        filename = "f_icon.png" if key in {"F", "FEMALE", "WOMAN", "Ж"} else "m_icon.png" if key in {"M", "MALE", "MAN", "М"} else None
        if filename is None or Image is None:
            return None
        cache_key = (filename, size)
        if cache_key in UIHelpers._gender_images:
            return UIHelpers._gender_images[cache_key]

        module_dir = Path(__file__).resolve().parent
        asset_dirs = (module_dir / "assets", module_dir.parent / "assets", Path.cwd() / "assets")
        for asset_dir in asset_dirs:
            asset_path = asset_dir / filename
            if asset_path.is_file():
                try:
                    with Image.open(asset_path) as source:
                        portrait = source.convert("RGBA")
                        portrait.thumbnail(size, getattr(Image, "Resampling", Image).LANCZOS)
                        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                        canvas.alpha_composite(portrait, ((size[0] - portrait.width) // 2, (size[1] - portrait.height) // 2))
                    result = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=size)
                    UIHelpers._gender_images[cache_key] = result
                    return result
                except (OSError, ValueError):
                    return None
        return None

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def telemetry_values(crew):
        values = {}
        for prefix, label in (("t", "Temperature nodes"), ("p", "Pressure nodes"), ("e", "EMG nodes")):
            nodes = crew.nodes[prefix]
            values[label] = (", ".join(f"{value:g}" for value in nodes if value is not None) or "—", None)
        processed = {key.removeprefix("telemetry:"): value for key, value in crew.raw.items() if key.startswith("telemetry:")}
        mappings = (
            ("t Map", "temperature_map_c", "°C", 2),
            ("p Map", "pressure_map_kpa", "kPa", 2),
            ("em Map", "emg_rms_uv", "µV", 1),
            ("pulse", "pulse_bpm", "bpm", 0),
            ("spo2", "spo2_pct", "%", 1),
            ("blood vel", "blood_velocity_cm_s", "cm/s", 1),
            ("humidity", "humidity_pct", "%", 1),
            ("Battery voltage", "battery_voltage_v", "V", 3),
        )
        for label, key, unit, decimals in mappings:
            item = processed.get(key)
            value = item[0] if item else None
            if value is None and label == "Battery voltage":
                raw_battery = crew.raw.get("vbat")
                value = raw_battery[0] if raw_battery else None
            try:
                text = f"{float(value):.{decimals}f} {unit}" if value is not None else "—"
            except (TypeError, ValueError):
                text = "—"
            values[label] = (text, None)
        return values

    @staticmethod
    def render_pdf_page(canvas, doc, page_num):
        canvas.delete("all")
        try:
            if fitz is None:
                raise RuntimeError("PyMuPDF is not installed. Run: pip install PyMuPDF")
            if not doc.pdf_path or not Path(doc.pdf_path).is_file():
                raise FileNotFoundError("The PDF file is missing")
            with fitz.open(doc.pdf_path) as pdf:
                if page_num < 1 or page_num > len(pdf):
                    raise IndexError("PDF page is out of range")
                page = pdf.load_page(page_num - 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.4, 2.4), alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            image.thumbnail((max(1, canvas.winfo_width() - 24), max(1, canvas.winfo_height() - 24)))
            from PIL import ImageTk
            canvas._page_image = ImageTk.PhotoImage(image)
            canvas.create_image(canvas.winfo_width() // 2, canvas.winfo_height() // 2, image=canvas._page_image)
        except Exception as exc:
            canvas.create_text(canvas.winfo_width() // 2, canvas.winfo_height() // 2,
                               text=f"PDF preview unavailable\n{exc}", fill=RED, justify="center")


SPECTRUM_PALETTE = [UIHelpers.interpolate_color(SPECTRUM_STOPS, i / (PALETTE_SIZE - 1)) for i in range(PALETTE_SIZE)]


                                                                             
                
                                                                             
class Sidebar(ctk.CTkFrame):
    """Compact navigation rail with SVG iconography."""

    def __init__(self, master: tk.Misc, on_select: Callable[[int], None]) -> None:
        super().__init__(master, width=228, corner_radius=0, fg_color="#101a29")
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._icons = {}

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(30, 32))
        mark = ctk.CTkFrame(brand, width=42, height=42, corner_radius=13, fg_color=ACCENT)
        mark.pack(side="left")
        mark.pack_propagate(False)
        ctk.CTkLabel(mark, text="cS", text_color="white", font=ctk.CTkFont(size=17, weight="bold")).place(relx=.5, rely=.5, anchor="center")
        brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        brand_text.pack(side="left", padx=11)
        ctk.CTkLabel(brand_text, text="cSOS", font=ctk.CTkFont(size=19, weight="bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(brand_text, text="CREW MONITOR", text_color=MUTED, font=ctk.CTkFont(size=9, weight="bold"), anchor="w").pack(anchor="w", pady=(1, 0))

        ctk.CTkLabel(self, text="WORKSPACE", text_color="#65758c", font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=22, pady=(0, 10))
        self._buttons = []
        for index, (label, _title, icon_name) in enumerate(NAV_ITEMS):
            icon = UIHelpers.svg_icon(icon_name, 19)
            if icon:
                self._icons[icon_name] = icon
            button = ctk.CTkButton(
                self, text=label, image=icon, compound="left", anchor="w", height=46,
                corner_radius=11, border_spacing=13, font=ctk.CTkFont(size=13, weight="bold"),
                fg_color="transparent", hover_color="#1b2b40", text_color="#b9c8da",
                command=lambda i=index: on_select(i),
            )
            button.pack(fill="x", padx=12, pady=4)
            self._buttons.append(button)
        ctk.CTkLabel(self, text="CORE DATA  ·  .DAT", text_color="#65758c", font=ctk.CTkFont(size=9, weight="bold")).pack(side="bottom", anchor="w", padx=22, pady=22)

    def set_active(self, index: int) -> None:
        for i, button in enumerate(self._buttons):
            active = i == index
            button.configure(
                fg_color="#203858" if active else "transparent",
                text_color="#ffffff" if active else "#b9c8da",
                hover_color="#29466d" if active else "#1b2b40",
            )


class StarRating(tk.Canvas):
    """Five stars; any rating 0.0-5.0 renders as full, half or empty stars."""

    GAP = 3

    def __init__(self, master: tk.Misc, rating: float, bg: str, size: int = 18) -> None:
        try:
            value = max(0.0, min(5.0, round(float(rating) * 2) / 2))
        except (TypeError, ValueError):
            value = 0.0
        radius = size / 1.902                                     
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
                clipped = UIHelpers.clip_polygon_x(points, left + fill * size)
                if len(clipped) >= 3:
                    self.create_polygon(clipped, fill=DOC_GOLD, outline="")


                                                                             
                       
                                                                             
class CrewCard(ctk.CTkFrame):
    """Summary card of a single crew member."""

    def __init__(self, master: tk.Misc, member: CrewMember, on_details: Callable[[CrewMember], None] | None = None) -> None:
        self._on_details = on_details
        self._member = member
        super().__init__(
            master, width=300, height=540, corner_radius=18, fg_color=CARD_BG,
            border_width=1, border_color=INACTIVE,
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
            command=lambda: self._on_details(member) if self._on_details else None,
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            self, text=f"ID: {member.crew_id}", text_color=MUTED,
            font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=16)
        ctk.CTkLabel(self, text="Health and stress: not provided by Core", text_color=MUTED, font=ctk.CTkFont(size=12), anchor="w").grid(row=2, column=0, sticky="w", padx=16, pady=(10, 0))

    def _build_figure(self, member: CrewMember) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 0))
        row.grid_columnconfigure(0, weight=1)
        portrait = UIHelpers.gender_image(member.gender)
        if portrait is not None:
            portrait_label = ctk.CTkLabel(row, text="", image=portrait, width=140, height=210)
            portrait_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            row, text="More...", width=84,
            command=lambda: self._on_details(member) if self._on_details else None,
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _build_vitals(self, member: CrewMember) -> None:
        vitals = ctk.CTkFrame(self, fg_color="transparent")
        vitals.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        vitals.grid_columnconfigure((0, 1), weight=1, uniform="vitals")
        cells = (
            f"t nodes: {len(member.nodes['t'])}/4",
            f"p nodes: {len(member.nodes['p'])}/4",
            f"Pulse: {member.pulse:.0f}" if member.pulse is not None else "Pulse: —",
            f"SpO2: {member.spo2:g}%" if member.spo2 is not None else "SpO2: —",
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
            footer, text="● Receiving" if member.connected else "● No recent data",
            text_color=GREEN if member.connected else RED, font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w")
        battery = member.raw.get("vbat")
        battery_text = f"{float(battery[0]):.3f} V" if battery else "Battery —"
        ctk.CTkLabel(footer, text=battery_text, font=ctk.CTkFont(size=12)).grid(row=0, column=1, sticky="e")

    def refresh(self, member: CrewMember) -> None:
        """Refresh existing card labels without rebuilding the card."""
        self._member = member
        labels = self.winfo_children()
        top = labels[0] if labels else None
        if top is not None:
            top_labels = top.winfo_children()
            if top_labels:
                top_labels[0].configure(text=member.name)
        for widget in self.winfo_children():
            if isinstance(widget, ctk.CTkLabel):
                text = widget.cget("text")
                if str(text).startswith("ID:"):
                    widget.configure(text=f"ID: {member.crew_id}")
                elif text == "Health and stress: not provided by Core":
                    continue
        rows = [child for child in self.winfo_children() if isinstance(child, ctk.CTkFrame)]
        if len(rows) > 2:
            vitals = rows[2].winfo_children()
            cells = (
                f"t nodes: {len(member.nodes['t'])}/4",
                f"p nodes: {len(member.nodes['p'])}/4",
                f"Pulse: {member.pulse:.0f}" if member.pulse is not None else "Pulse: —",
                f"SpO2: {member.spo2:g}%" if member.spo2 is not None else "SpO2: —",
            )
            for widget, text in zip(vitals, cells):
                widget.configure(text=text)
        if rows:
            footer = rows[-1]
            footer_labels = [w for w in footer.winfo_children() if isinstance(w, ctk.CTkLabel)]
            if footer_labels:
                footer_labels[0].configure(
                    text="● Receiving" if member.connected else "● No recent data",
                    text_color=GREEN if member.connected else RED,
                )
            battery = member.raw.get("vbat")
            if len(footer_labels) > 1:
                footer_labels[1].configure(text=f"{float(battery[0]):.3f} V" if battery else "Battery —")


class OverviewPage(ctk.CTkFrame):
    """Header with link status and a horizontal strip of crew cards."""

    def __init__(self, master: tk.Misc, crew: list[CrewMember], on_details=None) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        header.grid_columnconfigure(0, weight=1)

        peers = sum(member.connected for member in crew)
        self._peers_label = ctk.CTkLabel(
            header, text=f"Connected: {peers} peers", font=ctk.CTkFont(size=16, weight="bold")
        )
        self._peers_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="Ground link: not reported by Core",
            font=ctk.CTkFont(size=14, weight="bold"), text_color="white",
            fg_color=INACTIVE, corner_radius=12, padx=14, pady=3,
        ).grid(row=0, column=1, sticky="e")

        strip = ctk.CTkScrollableFrame(self, orientation="horizontal", fg_color="transparent")
        strip.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 14))
        if not crew:
            ctk.CTkLabel(strip, text="No .dat telemetry files found. Waiting for Core...", text_color=MUTED).pack(padx=24, pady=30)
        self._strip = strip
        self._on_details = on_details
        self._cards = []
        self._member_ids = tuple(member.crew_id for member in crew)
        for member in crew:
            card = CrewCard(strip, member, on_details)
            card.pack(side="left", padx=8, pady=4, anchor="n")
            self._cards.append(card)

    def set_members(self, crew: list[CrewMember]) -> None:
        """Keep overview widgets mounted during data polling."""
        self._peers_label.configure(text=f"Connected: {sum(member.connected for member in crew)} peers")
        member_ids = tuple(member.crew_id for member in crew)
        if member_ids != self._member_ids:
            for card in self._cards:
                card.destroy()
            self._cards = []
            self._member_ids = member_ids
            for member in crew:
                card = CrewCard(self._strip, member, self._on_details)
                card.pack(side="left", padx=8, pady=4, anchor="n")
                self._cards.append(card)
        else:
            for card, member in zip(self._cards, crew):
                card.refresh(member)


                                                                             
                            
                                                                             
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
            text=f"Gender: {crew.gender}  ·  Age: {crew.age if crew.age is not None else '—'}  ·  Role: {crew.role}"
        )


class RawDataPanel(ctk.CTkFrame):
    """Per-parameter table and the scrolling hex packet feed."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self.paused = False
        self._crew = None
        self._cells = {}                                                    

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
            self, text="Core record log", text_color=MUTED, font=ctk.CTkFont(size=10), anchor="w"
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
        packet_time = (crew.last_packet.strftime("%Y-%m-%d %H:%M:%S UTC")
                       if isinstance(crew.last_packet, datetime) else "—")
        sequence = crew.seq if crew.seq is not None else "—"
        self._packet_label.configure(text=f"Last Packet: {packet_time} | Seq: #{sequence}")
        for sensor in RAW_SENSORS:
            for param in sensor.params:
                value_label, stamp_label = self._cells[param]
                item = crew.raw.get(param)
                if item is None:
                    value_label.configure(text="—"); stamp_label.configure(text="—")
                else:
                    value, ts = item
                    value_label.configure(text=f"{float(value):.{sensor.decimals}f} {sensor.unit}")
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
        if not self.paused and self._crew is not None:
            self.reload_log(self._crew)


class SensorMapPanel(ctk.CTkFrame):
    """Interpolated heatmap of the selected sensor layer on a forearm schematic."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self._layer = "T"
        self._crew = None
        self._weights = {}                                             
        self._buttons = {}                       

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
                side, text=key, width=56, height=30, fg_color=INACTIVE,
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

                                                                                
        self._heat_canvas = ctk.CTkCanvas(
            holder, width=HEAT_W, height=HEAT_H, bg=PLOT_BG,
            highlightthickness=1, highlightbackground="#888",
        )
        self._heat_canvas.pack()

        self._cells = []                                     
        self._cell_colors = []                                                         
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
        ctk.CTkLabel(caption, text="WRIST ↑", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="top")
        ctk.CTkLabel(caption, text="ELBOW ↓", text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="bottom")

                                                                              
    def select_layer(self, key: str) -> None:
        """Switch to layer `key` and redraw."""
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

                                                                              
    def set_crew(self, crew: CrewMember) -> None:
        """Bind a crew member and redraw node markers and the heatmap."""
        self._crew = crew
        self._draw_nodes()
        self._update_heatmap()

    def show(self, crew: CrewMember) -> None:
        """Update the heatmap colours for `crew`."""
        self._crew = crew
        self._update_heatmap()

                                                                              
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
        if len(values) != spec.count or any(v is None for v in values):
            for item in self._cells: self._heat_canvas.itemconfigure(item, fill=PLOT_BG)
            self._cell_colors = [PLOT_BG] * len(self._cells)
            return
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
        values = self._crew.nodes[spec.prefix]
        if i >= len(values) or values[i] is None: return
        value = values[i]
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
            self._heat_canvas.event_generate("<Motion>", x=int(NODE_POS[self._layer][i][0]*HEAT_W), y=int(NODE_POS[self._layer][i][1]*HEAT_H))


class TelemetryListPanel(ctk.CTkFrame):
    """Scrollable list of summary values; map rows open the matching sensor map."""

    def __init__(self, master: tk.Misc, on_layer_select: Callable[[str], None]) -> None:
        super().__init__(master, corner_radius=12, fg_color=CARD_BG)
        self._default_color = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        self._values = {}                           

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
        for name, (text, color) in UIHelpers.telemetry_values(crew).items():
            self._values[name].configure(text=text, text_color=color or self._default_color)


class ChartsStrip(ctk.CTkFrame):
    """Row of Core-provided metric charts."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, fg_color="transparent")
        self._crew = None
        self._canvases = {}                       
        self._values = {}
        self._history_state = {}
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
            history = tuple(crew.hist[spec.key])
            if self._history_state.get(spec.key) != history:
                self._draw_chart(spec)
                self._history_state[spec.key] = history
            value_text = f"{history[-1]:.{spec.decimals}f} {spec.unit}" if history else "—"
            if self._values[spec.key].cget("text") != value_text:
                self._values[spec.key].configure(text=value_text)

                                                                                  
                                                                              
    def _draw_chart(self, spec: ChartSpec) -> None:
        if self._crew is None:
            return
        UIHelpers.draw_strip_chart(
            self._canvases[spec.key], list(self._crew.hist[spec.key]),
            spec.color, spec.min_span, spec.decimals,
        )


class TelemetryPage(ctk.CTkFrame):
    """Carousel over crew members with raw data, sensor map, telemetry list and charts."""

    def __init__(self, master: tk.Misc, crew: list[CrewMember]) -> None:
        super().__init__(master, fg_color="transparent")
        self._crew = crew
        self._index = 0
        self._last_render_signature = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        if not crew:
            ctk.CTkLabel(self, text="No crew telemetry files found.\nWaiting for Core data in the configured .dat directory.",
                         text_color=MUTED, justify="center").grid(row=0, column=0, columnspan=3, padx=30, pady=60)
            return

                                               
        dots_row = ctk.CTkFrame(self, fg_color="transparent")
        dots_row.grid(row=0, column=0, columnspan=3, pady=(10, 0))
        self._dots = []
        for i in range(len(crew)):
            dot = ctk.CTkLabel(dots_row, text="○", font=ctk.CTkFont(size=22))
            dot.pack(side="left", padx=5)
            dot.bind("<Button-1>", lambda _e, idx=i: self.select_crew(idx))
            self._dots.append(dot)

                     
        arrow_font = ctk.CTkFont(size=28, weight="bold")
        for column, text, command, padx in (
            (0, "<", self.prev_crew, (10, 4)),
            (2, ">", self.next_crew, (4, 10)),
        ):
            ctk.CTkButton(
                self, text=text, width=46, font=arrow_font, fg_color=("gray75", "gray25"),
                hover_color=("gray65", "gray35"), command=command,
            ).grid(row=1, column=column, sticky="ns", padx=padx, pady=10)

                                                   
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=1, sticky="nsew", pady=(4, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1, minsize=440)
        body.grid_rowconfigure(2, weight=3, minsize=200)

        self._crew_header = CrewHeader(body)
        self._crew_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        middle = ctk.CTkFrame(body, fg_color="transparent")
        middle.grid(row=1, column=0, sticky="nsew")
        middle.grid_rowconfigure(0, weight=1)
        middle.grid_columnconfigure(0, weight=3, uniform="mid")
        middle.grid_columnconfigure(1, weight=2, uniform="mid")
        middle.grid_columnconfigure(2, weight=4, uniform="mid")

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
        if not self._crew: return
        self._index = index % len(self._crew)
        self.show_crew()

    def prev_crew(self) -> None:
        self.select_crew(self._index - 1)

    def next_crew(self) -> None:
        self.select_crew(self._index + 1)

    def show_crew(self) -> None:
        """Reload every widget for the selected crew member."""
        crew = self.current
        if crew is None: return
        self._raw_panel.reload_log(crew)
        self._sensor_map.set_crew(crew)
        self.refresh()

    def set_members(self, crew: list[CrewMember]) -> None:
        """Update data references and refresh only changed selection."""
        selected_id = self.current.crew_id if self._crew else None
        self._crew = crew
        if not crew:
            return
        self._index = next(
            (i for i, member in enumerate(crew) if member.crew_id == selected_id),
            min(self._index, len(crew) - 1),
        )
        current = self.current
        signature = self._render_signature(current)
        if signature != self._last_render_signature:
            self.refresh()

    @staticmethod
    def _render_signature(crew):
        return (
            crew.crew_id, crew.seq, crew.last_packet, crew.connected,
            tuple(crew.nodes["t"]), tuple(crew.nodes["p"]), tuple(crew.nodes["e"]),
            crew.pulse, crew.spo2, crew.vel, crew.humidity,
            tuple((key, tuple(value)) for key, value in crew.raw.items()),
            tuple(tuple(crew.hist[key]) for key in HISTORY_KEYS),
        )

    def refresh(self) -> None:
        """Update all widgets with the latest values."""
        crew = self.current
        if crew is None: return
        self._last_render_signature = self._render_signature(crew)
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
        if member is self.current and self.current is not None:
            self._raw_panel.feed(line)


                                                                             
                                              
                                                                             
class Document:
    """PDF entry with optional sidecar metadata."""

    def __init__(self, pdf_path: Path, pages: int, size: str, pdf_title: str | None = None) -> None:
        self.pdf_path = Path(pdf_path)
        self.filename = self.pdf_path.name
        self.meta_path = self._find_meta_path()
        self._fallback_title = pdf_title or self.pdf_path.stem.replace("_", " ")
        self.title = self._fallback_title
        self.rating = 0.0
        self.tags = ()
        self.keywords = ()
        self.size = size
        self.pages = pages
        self.summary = ""
        self.points = ()
        self.metadata = {}
        self.metadata_available = False
        self.metadata_error = None
        self.search_text = self.filename.lower()
        self.load_metadata()

    def _find_meta_path(self) -> Path | None:
        candidates = (Path(str(self.pdf_path) + ".meta"), self.pdf_path.with_suffix(".meta"))
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def load_metadata(self) -> None:
        """Load the JSON sidecar when the document is opened or indexed."""
        self.meta_path = self._find_meta_path()
        self.metadata = {}
        self.metadata_available = False
        self.metadata_error = None
        self.summary = ""
        self.points = ()
        self.tags = ()
        self.keywords = ()
        self.title = self._fallback_title
        if self.meta_path is None:
            self.search_text = self.filename.lower()
            return
        try:
            with self.meta_path.open("r", encoding="utf-8-sig") as stream:
                metadata = json.load(stream)
            if not isinstance(metadata, dict):
                raise ValueError("The .meta root must be a JSON object")
            self.metadata = metadata
            self.metadata_available = True
            self.title = str(metadata.get("title") or self._fallback_title)
            self.summary = str(metadata.get("summary") or metadata.get("abstract") or "")
            points = metadata.get("points", metadata.get("key_points", metadata.get("actionable_points", [])))
            if isinstance(points, str):
                points = [points]
            self.points = tuple(
                str(item.get("text") or item.get("title") or item.get("description") or item)
                if isinstance(item, dict) else str(item)
                for item in points if item is not None
            ) if isinstance(points, list) else ()
            tags = metadata.get("tags", [])
            keywords = metadata.get("keywords", [])
            if isinstance(tags, str):
                tags = [tags]
            if isinstance(keywords, str):
                keywords = [keywords]
            self.tags = tuple(str(item).strip().lstrip("#") for item in tags if str(item).strip()) if isinstance(tags, list) else ()
            self.keywords = tuple(str(item).strip() for item in keywords if str(item).strip()) if isinstance(keywords, list) else ()
            searchable = []
            pending = [metadata]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    pending.extend(value.values())
                elif isinstance(value, (list, tuple)):
                    pending.extend(value)
                elif value is not None:
                    searchable.append(str(value).lower())
            self.search_text = " ".join([self.filename.lower(), self.title.lower(), *searchable])
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.metadata_error = str(exc)
            self.search_text = self.filename.lower()




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

        self._stars = ctk.CTkLabel(self, text=f"{doc.pages} pages", text_color=DOC_MUTED)
        self._stars.grid(row=0, column=1, sticky="e", padx=(8, 14), pady=(12, 2))

        meta_terms = [*(f"#{tag}" for tag in doc.tags), *doc.keywords]
        tags = ctk.CTkLabel(
            self, text="Tags / keywords: " + (" · ".join(meta_terms) or "—"), anchor="w",
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
                return                                           
            under = getattr(under, "master", None)
        self._set_hover(False)


class DocumentModal(ctk.CTkToplevel):
    """Document viewer: summary, key points and a paged PDF preview."""

    def __init__(self, root: ctk.CTk, doc: Document) -> None:
        super().__init__(root)
        self._doc = doc
        self._doc.load_metadata()
        self._current_page = 1
        self._is_expanded = False
        self._normal_geometry = None

        self.title(doc.title)
        self.configure(fg_color=DOC_MODAL_BG)
        self.minsize(760, 560)

        root.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(max(1180, int(screen_w * 0.94)), screen_w - 32)
        height = min(max(820, int(screen_h * 0.92)), screen_h - 64)
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.transient(root)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(200, self._grab_when_ready)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1, minsize=170)
        self.grid_rowconfigure(2, weight=5, minsize=360)

        self._build_top_bar()
        self._build_info()
        self._build_pdf_viewer()

    def _grab_when_ready(self) -> None:
        try:
            self.grab_set()
            self.focus_set()
        except tk.TclError:
            pass                                             

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
        ctk.CTkLabel(left, text=f"{self._doc.pages} pages", text_color=DOC_MUTED).grid(
            row=1, column=1, sticky="w", padx=(14, 0), pady=(4, 0))

        ctk.CTkButton(
            top, text="[ ✕ Close ]", width=110, height=36, fg_color="#b62324",
            hover_color="#da3633", command=self.destroy,
        ).grid(row=0, column=1, sticky="e", padx=16)

    def _build_info(self) -> None:
        self._info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._info_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 8))
        self._info_frame.grid_columnconfigure((0, 1), weight=1, uniform="info")
        self._info_frame.grid_rowconfigure(0, weight=1)

        if self._doc.summary:
            summary = self._doc.summary
        elif self._doc.metadata_error:
            summary = f"Summary unavailable. Metadata file could not be read: {self._doc.metadata_error}"
        elif self._doc.metadata_available:
            summary = "No summary provided in the .meta file."
        else:
            summary = "No summary available — .meta file not found."
        if self._doc.points:
            points = "\n".join(f"• {point}" for point in self._doc.points)
        elif self._doc.metadata_error:
            points = "Key points unavailable because the .meta file could not be read."
        elif self._doc.metadata_available:
            points = "No key points provided in the .meta file."
        else:
            points = "No key points available — .meta file not found."
        self._read_only_section(0, "BRIEF SUMMARY", summary)
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
        UIHelpers.render_pdf_page(self._pdf_canvas, self._doc, self._current_page)

    def _go(self, delta: int) -> None:
        self._current_page = max(1, min(self._doc.pages, self._current_page + delta))
        self._refresh()

    def _toggle_expand(self) -> None:
        if self._is_expanded:
            self._info_frame.grid()
            self.grid_rowconfigure(1, weight=1, minsize=170)
            self.grid_rowconfigure(2, weight=5, minsize=360)
            if self._normal_geometry:
                self.geometry(self._normal_geometry)
            self._expand_btn.configure(text="Expand PDF")
        else:
            self._normal_geometry = self.geometry()
            self._info_frame.grid_remove()
            self.grid_rowconfigure(1, weight=0, minsize=0)
            self.grid_rowconfigure(2, weight=1, minsize=500)
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            self.geometry(f"{screen_w - 20}x{screen_h - 50}+10+10")
            self._expand_btn.configure(text="Restore details")
        self._is_expanded = not self._is_expanded
        self.after(80, self._refresh)


class ArchivePage(ctk.CTkFrame):
    """Searchable list of protocol documents."""

    def __init__(self, master: tk.Misc, app_window: ctk.CTk) -> None:
        super().__init__(master, fg_color="transparent")
                                                                                       
        self._app_window = app_window
        self._docs = self._discover_documents()
        self._modal = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        bar.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(
            bar, height=40, corner_radius=10,
            placeholder_text="Search title, tags, keywords, author...",
        )
        self._entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._entry.bind("<Return>", self._on_search)
        self._entry.bind("<KeyRelease>", self._on_search)
        ctk.CTkButton(bar, text="Search", width=110, height=40, command=self._on_search).grid(
            row=0, column=1
        )
        ctk.CTkLabel(
            bar, text=f"Source Directory: {Path(__file__).resolve().parent.parent / 'fadocs'}  ·  Metadata: PDF sidecar .meta (JSON)", text_color=DOC_MUTED,
            font=ctk.CTkFont(family=MONO, size=12),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 16))
        self._list.grid_columnconfigure(0, weight=1)
        self._render(self._docs)

    def _discover_documents(self) -> list[Document]:
        directory = Path(__file__).resolve().parent.parent / "fadocs"
        directory.mkdir(parents=True, exist_ok=True)
        self._documents_dir = directory
        result = []
        for path in sorted(directory.glob("*.pdf"), key=lambda item: item.name.lower()):
            size = path.stat().st_size
            pdf_title = path.stem.replace("_", " ")
            try:
                if fitz is None:
                    raise RuntimeError("PyMuPDF is not installed")
                with fitz.open(path) as pdf:
                    pages = len(pdf)
                    pdf_title = pdf.metadata.get("title") or pdf_title
            except Exception:
                pages = 0
            result.append(Document(path, pages, f"{size / 1024 / 1024:.2f} MB", pdf_title))
        return result

    def refresh_documents(self) -> None:
        """Reload PDFs and sidecar metadata from fadocs/."""
        self._docs = self._discover_documents()
        self._on_search()

    def _on_search(self, _event=None) -> None:
        query = self._entry.get().strip().lower().lstrip("#")
        if query:
            docs = [doc for doc in self._docs if query in doc.search_text]
        else:
            docs = self._docs
        self._render(docs)

    def _render(self, docs: list[Document]) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        if not docs:
            ctk.CTkLabel(self._list, text="No matching PDF files in fadocs/.", text_color=DOC_MUTED).grid(
                row=0, column=0, pady=30
            )
            return
        for row, doc in enumerate(docs):
            DocumentCard(self._list, doc, self._open_document).grid(
                row=row, column=0, sticky="ew", pady=6, padx=2
            )

    def _open_document(self, doc: Document) -> None:
        doc.load_metadata()
        if self._modal is not None:
            try:
                if self._modal.winfo_exists():
                    self._modal.destroy()
            except tk.TclError:
                pass
        self._modal = DocumentModal(self._app_window, doc)


                                                                             
                          
                                                                             
class SettingsPage(ctk.CTkFrame):
    """Runtime configuration and crew registration backed by .dat metadata."""
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color="#34445b")
        self.body.pack(fill="both", expand=True, padx=22, pady=18)

        ctk.CTkLabel(self.body, text="Settings", font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(self.body, text="Configure the Core data stream and manage crew profiles.", text_color=MUTED,
                     font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(3, 18))

        self._build_data_panel()
        self._build_registration()
        self._build_roster()

    def _panel(self, title, subtitle=None):
        panel = ctk.CTkFrame(self.body, fg_color=PANEL_BG, corner_radius=16, border_width=1, border_color=PANEL_BORDER)
        panel.pack(fill="x", pady=(0, 14))
        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(17, 12))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(head, text=subtitle, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(3, 0))
        return panel

    def _build_data_panel(self):
        panel = self._panel("Data connection", "The interface reads telemetry records written by Core.")
        fields = ctk.CTkFrame(panel, fg_color="transparent")
        fields.pack(fill="x", padx=20, pady=(0, 12))
        fields.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fields, text=".dat directory", text_color="#c7d3e2").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=8)
        self.path = ctk.CTkEntry(fields, height=38, fg_color=FIELD_BG, border_color=PANEL_BORDER, corner_radius=9)
        self.path.insert(0, self.app.data_dir)
        self.path.grid(row=0, column=1, sticky="ew", pady=8)
        ctk.CTkLabel(fields, text="Refresh interval", text_color="#c7d3e2").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=8)
        self.interval = ctk.CTkEntry(fields, width=150, height=38, fg_color=FIELD_BG, border_color=PANEL_BORDER, corner_radius=9)
        self.interval.insert(0, str(self.app.refresh_ms))
        self.interval.grid(row=1, column=1, sticky="w", pady=8)
        ctk.CTkLabel(fields, text="ms", text_color=MUTED).grid(row=1, column=1, sticky="w", padx=(160, 0))
        check_icon = UIHelpers.svg_icon("check", 16)
        ctk.CTkButton(fields, text="Apply changes", image=check_icon, compound="left", height=38,
                      corner_radius=9, fg_color=ACCENT, hover_color="#2d65d8", command=self.apply).grid(row=0, column=2, rowspan=2, padx=(18, 0))
        self.status = ctk.CTkLabel(panel, text="Core entry point: Core.parse_packet(data: bytes)  →  <crew_id>.dat",
                                   text_color=MUTED, font=ctk.CTkFont(size=11))
        self.status.pack(anchor="w", padx=20, pady=(0, 16))

    def _build_registration(self):
        panel = self._panel("Register crew member", "Profile metadata is stored in the first JSONL line as the _meta header.")
        grid = ctk.CTkFrame(panel, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(0, 14))
        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(3, weight=1)
        fields = [
            ("Crew ID · 0–127", "crew_id", 0, 0),
            ("Full name", "name", 0, 2),
            ("Age", "age", 1, 0),
            ("Position / role", "role", 1, 2),
            ("Gender", "gender", 2, 0),
        ]
        self.fields = {}
        for label, key, row, col in fields:
            ctk.CTkLabel(grid, text=label, text_color="#c7d3e2", font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=row * 2, column=col, sticky="w", padx=(0, 10), pady=(8, 3))
            if key == "gender":
                entry = ctk.CTkOptionMenu(
                    grid, values=["Male", "Female"], height=39, fg_color=FIELD_BG,
                    button_color=ACCENT, button_hover_color="#2d65d8",
                    dropdown_fg_color=PANEL_BG, corner_radius=9,
                )
                entry.set("Male")
            else:
                entry = ctk.CTkEntry(
                    grid, height=39, fg_color=FIELD_BG, border_color=PANEL_BORDER,
                    corner_radius=9, placeholder_text=label,
                )
            entry.grid(row=row * 2 + 1, column=col, sticky="ew", padx=(0, 16), pady=(0, 7))
            self.fields[key] = entry
        user_icon = UIHelpers.svg_icon("user-plus", 17)
        ctk.CTkButton(grid, text="Register profile", image=user_icon, compound="left", height=40,
                      corner_radius=9, fg_color=ACCENT, hover_color="#2d65d8", command=self.register).grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _build_roster(self):
        panel = self._panel("Crew directory", "Registered profiles found in the configured .dat directory.")
        self.roster = ctk.CTkFrame(panel, fg_color="transparent")
        self.roster.pack(fill="x", padx=12, pady=(0, 12))
        self._refresh_roster()

    def _refresh_roster(self):
        for child in self.roster.winfo_children():
            child.destroy()
        if not self.app.crew:
            ctk.CTkLabel(self.roster, text="No crew profiles found yet.", text_color=MUTED).pack(anchor="w", padx=10, pady=14)
            return
        for member in self.app.crew:
            row = ctk.CTkFrame(self.roster, fg_color="#0e1826", corner_radius=11)
            row.pack(fill="x", padx=4, pady=4)
            avatar = ctk.CTkFrame(row, width=38, height=38, corner_radius=11, fg_color="#203858")
            avatar.pack(side="left", padx=(10, 12), pady=9)
            avatar.pack_propagate(False)
            initials = "".join(part[0] for part in member.name.split()[:2]).upper() or "?"
            ctk.CTkLabel(avatar, text=initials, text_color="#dceaff", font=ctk.CTkFont(size=12, weight="bold")).place(relx=.5, rely=.5, anchor="center")
            details = ctk.CTkFrame(row, fg_color="transparent")
            details.pack(side="left", fill="x", expand=True, pady=8)
            ctk.CTkLabel(details, text=member.name, font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(anchor="w")
            sub = f"ID {member.crew_id}  ·  {member.age if member.age is not None else '—'} y.o.  ·  {member.role}  ·  {member.gender}"
            ctk.CTkLabel(details, text=sub, text_color=MUTED, font=ctk.CTkFont(size=10), anchor="w").pack(anchor="w", pady=(2, 0))
            trash_icon = UIHelpers.svg_icon("trash", 15)
            ctk.CTkButton(row, text="Delete", image=trash_icon, compound="left", width=92, height=32,
                          corner_radius=8, fg_color="#572a38", hover_color="#7a3448",
                          command=lambda m=member: self.delete_member(m)).pack(side="right", padx=12, pady=10)

    def apply(self):
        try:
            path = self.path.get().strip()
            interval = int(self.interval.get())
            if not path:
                raise ValueError("Data directory is required")
            if interval < 100:
                raise ValueError("Refresh interval must be at least 100 ms")
            self.app.data_dir = path
            self.app.refresh_ms = interval
            self.app.refresh_data(force=True)
            self.status.configure(text="Settings applied · Core.parse_packet(data: bytes) → .dat", text_color=GREEN)
            self._refresh_roster()
            if hasattr(self.app, "_poll_job") and self.app._poll_job:
                self.app.after_cancel(self.app._poll_job)
            self.app._poll_job = self.app.after(self.app.refresh_ms, self.app._poll_data)
        except (OSError, ValueError) as exc:
            self.status.configure(text=str(exc), text_color=RED)

    def register(self):
        try:
            crew_id = int(self.fields["crew_id"].get().strip())
            name = self.fields["name"].get().strip()
            age = int(self.fields["age"].get().strip())
            role = self.fields["role"].get().strip()
            gender = self.fields["gender"].get().strip()
            if not 0 <= crew_id <= 127:
                raise ValueError("Crew ID must be between 0 and 127")
            if not name or not role or not gender:
                raise ValueError("Name, position and gender are required")
            if not 1 <= age <= 120:
                raise ValueError("Age must be between 1 and 120")
            self.app.register_crew(crew_id, name, age, role, gender)
            for key, entry in self.fields.items():
                if key == "gender":
                    entry.set("Male")
                else:
                    entry.delete(0, "end")
            self.status.configure(text=f"Profile saved · {name} (ID {crew_id})", text_color=GREEN)
            self._refresh_roster()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.status.configure(text=str(exc), text_color=RED)

    def delete_member(self, member):
        if not messagebox.askyesno("Delete crew member", f"Delete {member.name} (ID {member.crew_id}) and its .dat file?\nThis action cannot be undone.", parent=self):
            return
        try:
            self.app.delete_crew(member.crew_id)
            self.status.configure(text=f"Profile deleted · {member.name}", text_color=GREEN)
            self._refresh_roster()
        except OSError as exc:
            self.status.configure(text=str(exc), text_color=RED)


class CrewDataRepository:
    """Read and maintain Core JSONL files."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self._cache = {}

    def set_directory(self, directory):
        new_directory = Path(directory)
        if new_directory != self.directory:
            self.directory = new_directory
            self._cache.clear()

    def register(self, crew_id, name, age, role, gender):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{int(crew_id)}.dat"
        records = []
        if path.exists():
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if "_meta" not in item:
                        records.append(item)
        metadata = {"schema": 1, "crew_id": int(crew_id), "name": name,
                    "age": int(age), "role": role, "gender": gender}
        with path.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps({"_meta": metadata}, ensure_ascii=False) + "\n")
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cache.pop(path, None)

    def delete(self, crew_id):
        path = self.directory / f"{int(crew_id)}.dat"
        if path.exists():
            path.unlink()
        self._cache.pop(path, None)

    def load_all(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        members = []
        active_paths = set(self.directory.glob("*.dat"))
        for stale_path in set(self._cache) - active_paths:
            self._cache.pop(stale_path, None)
        for path in sorted(active_paths):
            try:
                members.append(self._read_incremental(path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                cached = self._cache.get(path)
                if cached is not None:
                    members.append(cached["member"])
        return members

    def _read_incremental(self, path):
        stat = path.stat()
        with path.open("r", encoding="utf-8") as stream:
            meta_line = stream.readline()
            cached = self._cache.get(path)
            reset = (
                cached is None
                or cached["meta_line"] != meta_line
                or stat.st_size < cached["offset"]
                or cached["inode"] != getattr(stat, "st_ino", None)
            )
            if reset:
                metadata_record = json.loads(meta_line) if meta_line.strip() else {}
                metadata = metadata_record.get("_meta", {})
                crew_id = metadata.get("crew_id", path.stem)
                member = CrewMember(
                    crew_id, name=metadata.get("name", "Unknown crew member"),
                    gender=metadata.get("gender", "—"), age=metadata.get("age"),
                    role=metadata.get("role", "—"),
                )
                self._cache[path] = {
                    "member": member, "meta_line": meta_line,
                    "offset": stream.tell(), "inode": getattr(stat, "st_ino", None),
                }
                cached = self._cache[path]
            member = cached["member"]
            stream.seek(cached["offset"])
            while True:
                line = stream.readline()
                if not line or not line.endswith("\n"):
                    break
                if not line.strip():
                    cached["offset"] = stream.tell()
                    continue
                record = json.loads(line)
                self._apply_record(member, record)
                cached["offset"] = stream.tell()
        member.connected = bool(
            member.last_signal_monotonic
            and (time.monotonic() - member.last_signal_monotonic) < CONNECTED_HOLD_SECONDS
        )
        member.source_file = str(path)
        return member

    def _read_member(self, path):
        metadata = {}
        records = []
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                item = json.loads(line)
                if "_meta" in item:
                    metadata.update(item["_meta"])
                else:
                    records.append(item)
        crew_id = metadata.get("crew_id", path.stem)
        member = CrewMember(crew_id, name=metadata.get("name", "Unknown crew member"),
                            gender=metadata.get("gender", "—"), age=metadata.get("age"),
                            role=metadata.get("role", "—"))
        for record in records:
            self._apply_record(member, record)
        member.source_file = str(path)
        return member

    @staticmethod
    def _apply_record(member, record):
        """Apply either a raw packet or a processed Core telemetry snapshot."""
        record_type = record.get("record_type", "raw")
        data = record.get("data") or {}
        try:
            timestamp = float(record.get("timestamp"))
            stamp = datetime.fromtimestamp(timestamp, timezone.utc)
        except (TypeError, ValueError, OSError):
            return

        source = record.get("source") or {}
        if record_type == "raw":
            member.seq = record.get("seq_count", member.seq)
            member.last_packet = stamp
            member.last_seen = timestamp
            signal_age = max(0.0, time.time() - timestamp)
            member.last_signal_monotonic = time.monotonic() - signal_age
            for key, value in data.items():
                member.raw[key] = (value, stamp)
                if key in ("vbat", "+vBat"):
                    member.raw["vbat"] = (value, stamp)
                elif key in ("r1", "ir1", "r2", "ir2"):
                    member.ppg[("r1", "ir1", "r2", "ir2").index(key)] = value
                elif key == "h1":
                    member.humidity = value
                elif key[:1] in ("t", "p", "e") and key[1:].isdigit():
                    prefix = key[0]
                    index = int(key[1:]) - 1
                    nodes = member.nodes[prefix]
                    while len(nodes) <= index:
                        nodes.append(None)
                    nodes[index] = value
                if key == "t1" and isinstance(value, (int, float)):
                    member.hist["temperature"].append(value)
            member.log.append(
                f"{stamp:%H:%M:%S} RAW sensor={record.get('sensor_id')} "
                f"seq={member.seq} data={json.dumps(data, ensure_ascii=False)}"
            )

        elif record_type == "telemetry":
            member.seq = source.get("seq_count", member.seq)
            field_stamps = record.get("field_timestamps") or {}
            def value_stamp(key):
                try:
                    return datetime.fromtimestamp(float(field_stamps.get(key, timestamp)), timezone.utc)
                except (TypeError, ValueError, OSError):
                    return stamp

            temp_nodes = data.get("temperature_nodes_c")
            if isinstance(temp_nodes, dict):
                member.nodes["t"] = [temp_nodes.get(f"t{i}") for i in range(1, 5)]
            pressure_nodes = data.get("pressure_nodes_kpa")
            if isinstance(pressure_nodes, dict):
                member.nodes["p"] = [pressure_nodes.get(f"p{i}") for i in range(1, 5)]
            emg_nodes = data.get("emg_nodes_uv")
            if isinstance(emg_nodes, dict):
                member.nodes["e"] = [emg_nodes.get(f"e{i}") for i in range(1, 7)]

            pulse = data.get("pulse_bpm")
            spo2 = data.get("spo2_pct")
            velocity = data.get("blood_velocity_cm_s")
            humidity = data.get("humidity_pct")
            if pulse is not None:
                member.pulse = pulse
                member.hist["pulse"].append(pulse)
            if spo2 is not None:
                member.spo2 = spo2
                member.hist["spo2"].append(spo2)
            if velocity is not None:
                member.vel = velocity
            if humidity is not None:
                member.humidity = humidity

            for key, value in data.items():
                member.raw[f"telemetry:{key}"] = (value, value_stamp(key))
            if "battery_voltage_v" in data and data["battery_voltage_v"] is not None:
                member.raw["vbat"] = (data["battery_voltage_v"], value_stamp("battery_voltage_v"))
            if "temperature_map_c" in data and data["temperature_map_c"] is not None:
                member.hist["temperature"].append(data["temperature_map_c"])
            member.log.append(
                f"{stamp:%H:%M:%S} TELEMETRY source={source} "
                f"data={json.dumps(data, ensure_ascii=False)}"
            )

        member.connected = bool(
            member.last_signal_monotonic
            and (time.monotonic() - member.last_signal_monotonic) < CONNECTED_HOLD_SECONDS
        )



                                                                             
             
                                                                             
class Interface(ctk.CTk):
    """Poll Core's per-crew .dat JSONL files and render their actual contents."""
    def __init__(self):
        ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue"); super().__init__()
        self.configure(fg_color=APP_BG)
        self.title(APP_TITLE); self.geometry(WINDOW_SIZE); self.minsize(1280,780)
        self.grid_columnconfigure(1,weight=1); self.grid_rowconfigure(0,weight=1)
        self.data_dir=os.environ.get("CSOS_DATA_DIR", "users"); self.refresh_ms=REFRESH_INTERVAL_MS
        self.repository = CrewDataRepository(self.data_dir)
        self.crew=[]; self.current_page=None; self.pages={}
        self.sidebar=Sidebar(self,self._on_nav); self.sidebar.grid(row=0,column=0,sticky="nsew")
        self.content=ctk.CTkFrame(self,corner_radius=0,fg_color="transparent")
        self.content.grid(row=0,column=1,sticky="nsew"); self.content.grid_columnconfigure(0,weight=1); self.content.grid_rowconfigure(0,weight=1)
        self.page_builders={"overview":self.show_overview,"telemetry":self.show_telemetry,"archive":self.show_archive,"settings":self.show_settings}
        self._poll_job = None
        self.refresh_data(); self.navigate("overview"); self._poll_job = self.after(self.refresh_ms,self._poll_data)

    def register_crew(self, crew_id, name, age, role, gender):
        self.repository.register(crew_id, name, age, role, gender)
        self.refresh_data(force=True)

    def delete_crew(self, crew_id):
        self.repository.delete(crew_id)
        self.refresh_data(force=True)

    def refresh_data(self, force=False):
        self.repository.set_directory(self.data_dir)
        incoming = self.repository.load_all()
        self.crew = incoming

        overview = self.pages.get("overview")
        if overview is not None and self.current_page == "overview":
            overview.set_members(self.crew)

        telemetry = self.pages.get("telemetry")
        if telemetry is not None:
            if len(self.crew) != len(getattr(telemetry, "_dots", ())):
                selected_id = telemetry.current.crew_id if getattr(telemetry, "_crew", None) else None
                was_visible = self.current_page == "telemetry"
                telemetry.destroy()
                self.pages["telemetry"] = self.show_telemetry()
                if selected_id and self.crew:
                    self.telemetry.select_crew(
                        next((i for i, item in enumerate(self.crew) if item.crew_id == selected_id), 0)
                    )
                if was_visible:
                    self.pages["telemetry"].grid(row=0, column=0, sticky="nsew")
            elif self.current_page == "telemetry":
                telemetry.set_members(self.crew)

    def _poll_data(self):
        self.refresh_data()
        self._poll_job = self.after(self.refresh_ms, self._poll_data)
    def _on_nav(self,index):
        names=("overview","telemetry","archive","settings")
        if 0<=index<len(names): self.navigate(names[index])
    def navigate(self,name):
        if name not in self.pages: self.pages[name]=self.page_builders[name]()
        for page in self.pages.values(): page.grid_remove()
        self.pages[name].grid(row=0,column=0,sticky="nsew"); self.pages[name].tkraise(); self.current_page=name
        self.sidebar.set_active(("overview","telemetry","archive","settings").index(name))
        if name=="telemetry": self.telemetry.show_crew()
        elif name=="overview": self.pages[name].set_members(self.crew)
        elif name=="archive": self.pages[name].refresh_documents()
    def show_overview(self): return OverviewPage(self.content,self.crew,self.open_member_details)
    def open_member_details(self, member):
        self.navigate("telemetry")
        if self.crew and hasattr(self, "telemetry"):
            self.telemetry.select_crew(next((i for i, item in enumerate(self.crew) if item.crew_id == member.crew_id), 0))
    def show_telemetry(self): self.telemetry=TelemetryPage(self.content,self.crew); return self.telemetry
    def show_archive(self): return ArchivePage(self.content,self)
    def show_settings(self): return SettingsPage(self.content,self)


if __name__ == "__main__":
    interface = Interface()
    interface.mainloop()
