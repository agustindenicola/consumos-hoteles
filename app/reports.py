"""Informes para entregar a los dueños: PDF de presentación (con gráficos) y Excel.

Estructura del PDF por hotel:
  1. Portada
  2. Resumen ejecutivo (indicadores, conclusiones y semáforo por servicio)
  3. Costos y huéspedes
  4. Una página por servicio (agua, electricidad, gas)
  5. Anexo: detalle mensual, facturas consideradas y metodología
"""
import calendar
import io
import os
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager, ticker  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.chart import BarChart, LineChart, Reference  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from reportlab.graphics.shapes import Drawing, Line, Rect, String  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak,  # noqa: E402
                                Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from . import core  # noqa: E402
from .db import SERVICES, get_settings, reference  # noqa: E402

# ------------------------------------------------------------------ paleta
SERVICE_COLOR = {"agua": "#2a78d6", "electricidad": "#eb6834", "gas": "#1baf7a"}
HOTEL_COLORS = ["#6250d6", "#e87ba4", "#008300", "#eda100"]
HOTEL_MARKERS = ["o", "s", "^", "D"]
DARK, GOLD, CREAM, PAPER = "#1f2b28", "#c9a96a", "#f6f1e7", "#fbf8f2"
INK, INK2, MUTED, GRID, PREV = "#1d1b17", "#5b564d", "#958e81", "#e6e0d4", "#cfc9bd"
CRITICAL, GOOD = "#c62828", "#1b7f4b"
UNITS = {"agua": "m³", "electricidad": "kWh", "gas": "m³"}

WARN_C = "#8f5d00"

# ------------------------------------------------------------------ marca
# Todos los informes llevan la marca Hoteles Demo (no la de la gestora).
MARCAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "marcas")
LOGO_RATIO = 458 / 720   # alto / ancho de los PNG recortados
UP_NAVY = "#252d45"      # azul de la marca Hoteles Demo (fondo de portada)


def brand_logo(on_dark):
    """Ruta del logo Hoteles Demo (versión blanca para fondo oscuro), o None si falta el archivo."""
    path = os.path.join(MARCAS_DIR, "marca_blanco.png" if on_dark else "marca_azul.png")
    return path if os.path.exists(path) else None

# ------------------------------------------------------------------ tipografías
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
F = {"serif": "Times-Roman", "serif_i": "Times-Italic", "sans": "Helvetica", "light": "Helvetica",
     "semi": "Helvetica-Bold", "bold": "Helvetica-Bold"}
for key, name, fn in [("serif", "Georgia", "georgia.ttf"), ("serif_i", "Georgia-Italic", "georgiai.ttf"),
                      ("sans", "SegoeUI", "segoeui.ttf"), ("light", "SegoeUI-Light", "segoeuil.ttf"),
                      ("semi", "SegoeUI-Semibold", "seguisb.ttf"), ("bold", "SegoeUI-Bold", "segoeuib.ttf")]:
    path = os.path.join(FONT_DIR, fn)
    if os.path.exists(path):
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            F[key] = name
        except Exception:
            pass


# negrita/itálica dentro de los textos (<b>, <i>)
from reportlab.pdfbase.pdfmetrics import registerFontFamily as _rff
if F["sans"] == "SegoeUI":
    _rff("SegoeUI", normal="SegoeUI", bold=F["semi"], italic="SegoeUI", boldItalic=F["semi"])
if F["serif"] == "Georgia":
    _rff("Georgia", normal="Georgia", bold="Georgia", italic=F["serif_i"], boldItalic=F["serif_i"])

_mpl_fonts = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams.update({
    "font.family": ["Segoe UI"] if "Segoe UI" in _mpl_fonts else ["DejaVu Sans"],
    "font.size": 8.5, "axes.labelsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "legend.fontsize": 8,
})


# ------------------------------------------------------------------ formato
def fnum(v, dec=0):
    if v is None:
        return "—"
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmoney(v):
    return "—" if v is None else "$ " + fnum(v, 0)


def fmoney_short(v):
    if v is None:
        return "—"
    if abs(v) >= 1e6:
        return "$ " + fnum(v / 1e6, 1) + " M"
    if abs(v) >= 1e3:
        return "$ " + fnum(v / 1e3, 0) + " mil"
    return "$ " + fnum(v, 0)


def fpct(v, dec=1):
    if v is None:
        return "—"
    return ("+" if v > 0 else "") + fnum(v, dec) + " %"


def per_dec(service):
    return 3 if service in ("agua", "gas") else 2


def month_range(rows_lists):
    months = set()
    for rows in rows_lists:
        for r in rows:
            if r["consumption"] is not None or r["guests"]:
                months.add(r["month"])
    if not months:
        return None
    a, b = min(months), max(months)
    return core.MONTHS[a - 1].lower() if a == b else f"{core.MONTHS[a - 1].lower()} a {core.MONTHS[b - 1].lower()}"


def cost_compare(rows, prev):
    pairs = [(a, b) for a, b in zip(rows, prev) if a["complete"] and b["complete"] and a["amount"] and b["amount"]]
    if not pairs:
        return None
    cur, old = sum(a["amount"] for a, _ in pairs), sum(b["amount"] for _, b in pairs)
    return round((cur - old) / old * 100, 1) if old else None


# ------------------------------------------------------------------ gráficos (matplotlib)
W = A4[0] - 3.6 * cm          # ancho útil (puntos)
CHART_W = W / 72              # en pulgadas


def _ax_style(ax, dec=0):
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0, pad=5)
    ax.yaxis.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: fnum(v, dec)))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(5, steps=[1, 2, 2.5, 5, 10]))


def _img(fig, h_in):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=CHART_W * 72, height=h_in * 72)


def _legend(ax, handles, ncol=4):
    ax.legend(handles=handles, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=ncol,
              handlelength=1.2, handleheight=0.9, columnspacing=1.6, borderaxespad=0)


def chart_monthly_bars(cur, prev, color, year, unit_label, dec=0, partial=None, h=2.9, labels=None):
    """Barras del año (color) frente al año anterior (gris). Etiqueta solo el máximo."""
    fig, ax = plt.subplots(figsize=(CHART_W, h), layout="constrained")
    x = list(range(len(cur)))
    labels = labels or core.MONTHS_SHORT[:len(cur)]
    has_prev = any(v for v in prev)
    w = 0.38 if has_prev else 0.6
    partial = partial or [False] * len(cur)
    light = matplotlib.colors.to_rgba(color, 0.38)
    if has_prev:
        ax.bar([i - w / 2 for i in x], [v or 0 for v in prev], w, color=PREV, edgecolor="white", linewidth=1.2)
    xs = [i + w / 2 for i in x] if has_prev else x
    ax.bar(xs, [v or 0 for v in cur], w, color=[light if p else color for p in partial], edgecolor="white", linewidth=1.2)
    vals = [(v, i) for i, v in enumerate(cur) if v and not partial[i]]
    if vals:
        v, i = max(vals)
        ax.annotate(fnum(v, dec), (xs[i], v), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.5, color=INK, fontweight="semibold")
    ax.set_xticks(x, labels)
    ax.set_ylabel(unit_label)
    _ax_style(ax, dec)
    ax.margins(y=0.12)
    handles = ([Patch(color=PREV, label=str(year - 1))] if has_prev else []) + [Patch(color=color, label=str(year))]
    if any(p and c for p, c in zip(partial, cur)):
        handles.append(Patch(color=light, label="Mes parcial (faltan facturas)"))
    _legend(ax, handles)
    return _img(fig, h)


def chart_per_guest(rows, prev_rows, service, year, ref, h=2.8):
    fig, ax = plt.subplots(figsize=(CHART_W, h), layout="constrained")
    x = list(range(len(rows)))
    color = SERVICE_COLOR[service]

    def val(rr):
        return [r["per_guest"] if (r["per_guest"] is not None and r["complete"]) else float("nan") for r in rr]

    cur, prev = val(rows), val(prev_rows)
    handles = []
    if any(v == v for v in prev):
        ax.plot(x, prev, color=PREV, lw=1.8, marker="o", ms=3.5)
        handles.append(Line2D([], [], color=PREV, lw=2, marker="o", ms=4, label=str(year - 1)))
    ax.plot(x, cur, color=color, lw=2.2, marker="o", ms=5, markeredgecolor="white", markeredgewidth=1.2, zorder=3)
    handles.append(Line2D([], [], color=color, lw=2.2, marker="o", ms=5, label=str(year)))
    if ref:
        ax.axhline(ref, color=INK2, lw=1, ls=(0, (4, 3)), zorder=2)
        valid = [v == v for v in cur]
        ax.fill_between(x, ref, [v if v == v and v > ref else ref for v in cur], where=valid, interpolate=True,
                        color=CRITICAL, alpha=0.08, lw=0)
        hi = [(i, v) for i, v in enumerate(cur) if v == v and v > ref]
        if hi:
            ax.scatter([i for i, _ in hi], [v for _, v in hi], s=46, color=CRITICAL, edgecolor="white", linewidth=1.2, zorder=4)
            i, v = max(hi, key=lambda t: t[1])
            ax.annotate(fnum(v, per_dec(service)), (i, v), xytext=(0, 6), textcoords="offset points", ha="center",
                        fontsize=7.5, color=CRITICAL, fontweight="semibold")
            handles.append(Line2D([], [], color=CRITICAL, lw=0, marker="o", ms=5, label="Sobre la referencia"))
        ax.annotate(f"Referencia {fnum(ref, per_dec(service))}", xy=(1, ref), xycoords=("axes fraction", "data"),
                    xytext=(-2, 4), textcoords="offset points", ha="right", va="bottom", fontsize=7.5, color=INK2)
        handles.append(Line2D([], [], color=INK2, lw=1, ls=(0, (4, 3)), label="Referencia UTGRA"))
    ax.set_xticks(x, [r["short"] for r in rows])
    ax.set_ylabel(f"{UNITS[service]} por huésped")
    ax.set_ylim(bottom=0)
    _ax_style(ax, 0 if service == "electricidad" else 2)
    ax.set_ylim(top=ax.get_ylim()[1] * 1.12)
    _legend(ax, handles)
    return _img(fig, h)


def chart_cost_stack(data, h=2.8):
    """Costo mensual apilado por servicio (millones de $)."""
    fig, ax = plt.subplots(figsize=(CHART_W, h), layout="constrained")
    first = data[next(iter(data))]["rows"]
    x = list(range(len(first)))
    bottom = [0] * len(first)
    handles = []
    for svc in data:
        vals = [(r["amount"] or 0) / 1e6 for r in data[svc]["rows"]]
        ax.bar(x, vals, 0.62, bottom=bottom, color=SERVICE_COLOR[svc], edgecolor="white", linewidth=1.2)
        bottom = [a + b for a, b in zip(bottom, vals)]
        handles.append(Patch(color=SERVICE_COLOR[svc], label=SERVICES[svc]["label"]))
    ax.set_xticks(x, [r["short"] for r in first])
    ax.set_ylabel("millones de $")
    _ax_style(ax, 1)
    ax.margins(y=0.1)
    _legend(ax, handles)
    return _img(fig, h)


def chart_hotel_ranking(items, service, ref, h=2.1):
    """Barras horizontales del consumo por huésped de cada hotel, con la referencia."""
    items = sorted([it for it in items if it["value"] is not None], key=lambda it: it["value"])
    fig, ax = plt.subplots(figsize=(CHART_W, h), layout="constrained")
    if not items:
        ax.axis("off")
        return _img(fig, h)
    y = list(range(len(items)))
    ax.barh(y, [it["value"] for it in items], 0.58, color=[it["color"] for it in items], edgecolor="white", linewidth=1.2)
    for i, it in enumerate(items):
        over = ref and it["value"] > ref
        ax.annotate(fnum(it["value"], per_dec(service)), (it["value"], i), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8, fontweight="semibold", color=CRITICAL if over else INK)
    ax.set_yticks(y, [it["name"] for it in items])
    if ref:
        ax.axvline(ref, color=INK2, lw=1, ls=(0, (4, 3)))
        ax.annotate(f"Referencia {fnum(ref, per_dec(service))}", xy=(ref, 1), xycoords=("data", "axes fraction"),
                    xytext=(0, 3), textcoords="offset points", fontsize=7.5, color=INK2, va="bottom", ha="center",
                    annotation_clip=False)
    ax.spines[["top", "right", "bottom"]].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.tick_params(length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: fnum(v, 0 if service == "electricidad" else 2)))
    ax.set_xlabel(f"{UNITS[service]} por huésped")
    ax.margins(x=0.15)
    return _img(fig, h)


def chart_hotels_lines(hotels, service, ref, h=2.9):
    fig, ax = plt.subplots(figsize=(CHART_W, h), layout="constrained")
    x = list(range(12))
    handles = []
    for i, hh in enumerate(hotels):
        c = HOTEL_COLORS[i % 4]
        vals = [r["per_guest"] if (r["per_guest"] is not None and r["complete"]) else float("nan") for r in hh["rows"]]
        ax.plot(x, vals, color=c, lw=2, marker=HOTEL_MARKERS[i % 4], ms=5, markeredgecolor="white", markeredgewidth=1)
        handles.append(Line2D([], [], color=c, lw=2, marker=HOTEL_MARKERS[i % 4], ms=5, label=hh["hotel"]))
    if ref:
        ax.axhline(ref, color=INK2, lw=1, ls=(0, (4, 3)))
        handles.append(Line2D([], [], color=INK2, lw=1, ls=(0, (4, 3)), label=f"Referencia {fnum(ref, per_dec(service))}"))
    ax.set_xticks(x, core.MONTHS_SHORT)
    ax.set_ylabel(f"{UNITS[service]} por huésped")
    ax.set_ylim(bottom=0)
    _ax_style(ax, 0 if service == "electricidad" else 2)
    ax.margins(y=0.12)
    _legend(ax, handles, ncol=5)
    return _img(fig, h)


# ------------------------------------------------------------------ piezas reportlab
def S(name, **kw):
    base = dict(fontName=F["sans"], fontSize=9, leading=13, textColor=colors.HexColor(INK))
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    "h1": S("h1", fontName=F["serif"], fontSize=24, leading=28, spaceAfter=2),
    "h2": S("h2", fontName=F["serif"], fontSize=15, leading=19, spaceBefore=12, spaceAfter=6),
    "kicker": S("kicker", fontName=F["semi"], fontSize=7.5, leading=10, textColor=colors.HexColor(MUTED)),
    "lead": S("lead", fontSize=10, leading=15, textColor=colors.HexColor(INK2)),
    "body": S("body", spaceAfter=3),
    "bullet": S("bullet", fontSize=9.5, leading=14, leftIndent=12, bulletIndent=0, spaceAfter=5),
    "small": S("small", fontSize=7.5, leading=10.5, textColor=colors.HexColor(MUTED)),
    "cap": S("cap", fontName=F["semi"], fontSize=9.5, leading=12, textColor=colors.HexColor(INK), spaceBefore=10, spaceAfter=2),
    "kv": S("kv", fontName=F["semi"], fontSize=17, leading=21),
    "kl": S("kl", fontSize=7.5, leading=10, textColor=colors.HexColor(INK2)),
    "kd": S("kd", fontSize=7.5, leading=10, textColor=colors.HexColor(MUTED)),
    "cell": S("cell", fontSize=8.5, leading=11),
}


def kpi_row(items):
    """items: (valor, etiqueta, detalle[, color del valor])"""
    n = len(items)
    gap = 0.3 * cm
    w = (W - gap * (n - 1)) / n
    row, cw = [], []
    for i, it in enumerate(items):
        v, l, d = it[0], it[1], it[2] if len(it) > 2 else ""
        col = it[3] if len(it) > 3 and it[3] else INK
        inner = Table([[Paragraph(f'<font color="{col}">{v}</font>', ST["kv"])], [Paragraph(l, ST["kl"])],
                       [Paragraph(d or "&nbsp;", ST["kd"])]], colWidths=[w], cornerRadii=[6, 6, 6, 6])
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(CREAM)),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
            ("TOPPADDING", (0, 1), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -2), 1),
        ]))
        row.append(inner); cw.append(w)
        if i < n - 1:
            row.append(""); cw.append(gap)
    t = Table([row], colWidths=cw)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def meter(value, ref, color, width=5.2 * cm, height=0.75 * cm):
    """Barra de medición: consumo por huésped frente a la referencia.
    Hasta la referencia en el color del servicio; el exceso en rojo."""
    d = Drawing(width, height)
    if value is None or not ref:
        return d
    scale_max = max(value, ref) * 1.3
    th, y = 7, height / 2
    d.add(Rect(0, y, width, th, rx=3.5, ry=3.5, fillColor=colors.HexColor("#ece6da"), strokeColor=None))
    within = min(value, ref)
    d.add(Rect(0, y, max(within / scale_max * width, 4), th, rx=3.5, ry=3.5, fillColor=colors.HexColor(color), strokeColor=None))
    if value > ref:
        x0 = ref / scale_max * width
        d.add(Rect(x0, y, (value - ref) / scale_max * width, th, rx=3.5, ry=3.5, fillColor=colors.HexColor(CRITICAL), strokeColor=None))
        d.add(Rect(x0, y, 4, th, fillColor=colors.HexColor(CRITICAL), strokeColor=None))
    xr = ref / scale_max * width
    d.add(Line(xr, y - 3, xr, y + th + 3, strokeColor=colors.HexColor(INK), strokeWidth=1.3))
    d.add(String(xr, y - 10, "referencia", fontName=F["sans"], fontSize=6.5, fillColor=colors.HexColor(INK2), textAnchor="middle"))
    return d


def data_table(data, widths, num_from=1, extra=None, header=True, font_size=8.5):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [
        ("FONT", (0, 0), (-1, -1), F["sans"], font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
        ("ALIGN", (num_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1 if header else 0), (-1, -1), 0.4, colors.HexColor(GRID)),
    ]
    if header:
        st += [("FONT", (0, 0), (-1, 0), F["semi"], font_size - 1), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(MUTED)),
               ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK2)), ("BOTTOMPADDING", (0, 0), (-1, 0), 5)]
    t.setStyle(TableStyle(st + (extra or [])))
    return t


def section_header(title, kicker, color):
    bar = Drawing(W, 4)
    bar.add(Rect(0, 0, 1.6 * cm, 3, fillColor=colors.HexColor(color), strokeColor=None))
    return [bar, Spacer(1, 7), Paragraph(kicker.upper(), ST["kicker"]), Paragraph(title, ST["h1"]), Spacer(1, 4)]


# ------------------------------------------------------------------ portada y encabezados
def draw_logo(canvas, path, x, y, height):
    """Dibuja el logo del hotel con la esquina inferior izquierda en (x, y). Devuelve el ancho usado."""
    width = height / LOGO_RATIO
    canvas.drawImage(path, x, y, width=width, height=height, mask="auto")
    return width


def _cover(canvas, info):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(colors.HexColor(CREAM)); canvas.rect(0, 0, w, h, stroke=0, fill=1)
    top = h * 0.44
    logo = brand_logo(on_dark=True)
    canvas.setFillColor(colors.HexColor(UP_NAVY if logo else DARK)); canvas.rect(0, top, w, h - top, stroke=0, fill=1)
    # anillos decorativos finos
    p = canvas.beginPath(); p.rect(0, top, w, h - top); canvas.clipPath(p, stroke=0)
    canvas.setStrokeColor(colors.HexColor(GOLD)); canvas.setLineWidth(0.6)
    for i, r in enumerate((4.2, 5.6, 7.0, 8.4)):
        canvas.setStrokeAlpha(0.35 - i * 0.07)
        canvas.circle(w - 1.2 * cm, h - 4.2 * cm, r * cm, stroke=1, fill=0)
    canvas.setStrokeAlpha(1)
    canvas.restoreState(); canvas.saveState()
    canvas.setFillColor(colors.HexColor(info["accent"])); canvas.rect(0, top - 6, w, 6, stroke=0, fill=1)
    x = 2.2 * cm
    if logo:
        draw_logo(canvas, logo, x, h - 2.25 * cm, 1.3 * cm)
    canvas.setFillColor(colors.HexColor(GOLD)); canvas.setFont(F["semi"], 9)
    canvas.drawString(x, h - 3.2 * cm, info["kicker"].upper(), charSpace=2.5)
    canvas.setFillColor(colors.white); canvas.setFont(F["serif"], 40)
    y = h - 7.6 * cm
    for line in info["title_lines"]:
        canvas.drawString(x, y, line); y -= 46
    canvas.setFillColor(colors.HexColor(GOLD)); canvas.setFont(F["serif_i"], 32)
    canvas.drawString(x, y - 8, info["subtitle"])
    canvas.setFillColor(colors.HexColor("#d9dfdb")); canvas.setFont(F["sans"], 11)
    canvas.drawString(x, top + 1.9 * cm, info["period"])
    if info.get("legal"):
        canvas.setFont(F["sans"], 8.5); canvas.setFillColor(colors.HexColor("#94a19b"))
        canvas.drawString(x, top + 1.2 * cm, info["legal"])
    # tarjetas: consumo por huésped de cada servicio
    canvas.setFillColor(colors.HexColor(INK2)); canvas.setFont(F["semi"], 8.5)
    canvas.drawString(x, top - 1.4 * cm, info.get("cards_title", "CONSUMO POR HUÉSPED"))
    gap = 0.45 * cm
    cw = (w - 2 * x - 2 * gap) / 3
    yb = top - 5.6 * cm
    for i, card in enumerate(info["cards"]):
        cx = x + i * (cw + gap)
        canvas.setFillColor(colors.white); canvas.roundRect(cx, yb, cw, 3.7 * cm, 8, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor(card["color"])); canvas.rect(cx + 12, yb + 3.7 * cm - 14, 18, 3, stroke=0, fill=1)
        canvas.setFillColor(colors.HexColor(INK2)); canvas.setFont(F["semi"], 9.5)
        canvas.drawString(cx + 12, yb + 2.75 * cm, card["label"])
        canvas.setFillColor(colors.HexColor(INK)); canvas.setFont(F["semi"], 22)
        canvas.drawString(cx + 12, yb + 1.7 * cm, card["value"])
        canvas.setFillColor(colors.HexColor(MUTED)); canvas.setFont(F["sans"], 8)
        canvas.drawString(cx + 12, yb + 1.15 * cm, card["unit"])
        canvas.setFillColor(colors.HexColor(card.get("note_color", INK2))); canvas.setFont(F["semi"], 8)
        canvas.drawString(cx + 12, yb + 0.5 * cm, card["note"])
    canvas.setStrokeColor(colors.HexColor(GRID)); canvas.line(x, 3.1 * cm, w - x, 3.1 * cm)
    canvas.setFillColor(colors.HexColor(INK2)); canvas.setFont(F["sans"], 9)
    canvas.drawString(x, 2.5 * cm, info["prepared"])
    canvas.setFillColor(colors.HexColor(MUTED)); canvas.setFont(F["sans"], 8.5)
    canvas.drawRightString(w - x, 2.5 * cm, f"Emitido el {date.today():%d/%m/%Y}")
    canvas.restoreState()


def _page_deco(title, right, accent, brand="Hoteles Demo"):
    def draw(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(accent)); canvas.circle(1.8 * cm + 3, h - 1.25 * cm + 3, 3, stroke=0, fill=1)
        canvas.setFont(F["semi"], 8); canvas.setFillColor(colors.HexColor(INK))
        canvas.drawString(1.8 * cm + 11, h - 1.25 * cm, title)
        canvas.setFont(F["sans"], 8); canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawRightString(w - 1.8 * cm, h - 1.25 * cm, right)
        canvas.setStrokeColor(colors.HexColor(GRID)); canvas.setLineWidth(0.5)
        canvas.line(1.8 * cm, h - 1.5 * cm, w - 1.8 * cm, h - 1.5 * cm)
        canvas.line(1.8 * cm, 1.35 * cm, w - 1.8 * cm, 1.35 * cm)
        logo = brand_logo(on_dark=False)
        if logo:
            draw_logo(canvas, logo, 1.8 * cm, 0.5 * cm, 0.68 * cm)
        elif brand:
            canvas.drawString(1.8 * cm, 0.9 * cm, brand)
        canvas.drawRightString(w - 1.8 * cm, 0.9 * cm, f"{doc.page}")
        canvas.restoreState()
    return draw


def _doc(buf, title, author):
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=2.1 * cm,
                             bottomMargin=1.9 * cm, title=title, author=author)


def _split_title(name, max_chars=18):
    lines, cur = [], ""
    for wd in name.split():
        if len(cur) + len(wd) + 1 > max_chars and cur:
            lines.append(cur); cur = wd
        else:
            cur = (cur + " " + wd).strip()
    lines.append(cur)
    return lines


# ------------------------------------------------------------------ conclusiones automáticas
def insights(data, year, guests, guests_prev_same, money=True):
    out = []
    for svc in data:
        meta = SERVICES[svc]
        d = data[svc]; s = d["summary"]; ref = d["reference"]; u = meta["unit"]
        if s["per_guest"] is None:
            continue
        txt = f"<b>{meta['label']}:</b> {fnum(s['per_guest'], per_dec(svc))} {u} por huésped"
        if ref:
            diff = (s["per_guest"] / ref - 1) * 100
            if diff > 0:
                txt += (f", <font color='{CRITICAL}'><b>{fnum(diff, 0)} % por encima</b></font> de la referencia "
                        f"({fnum(ref, per_dec(svc))}); la superó en {s['alerts']} de {s['months_complete']} meses.")
            else:
                txt += f", <font color='{GOOD}'><b>{fnum(-diff, 0)} % por debajo</b></font> de la referencia ({fnum(ref, per_dec(svc))})."
        else:
            txt += " (no hay referencia oficial para Argentina)."
        c = d["compare"]
        if c and c.get("per_guest_change") is not None:
            ch = c["per_guest_change"]
            txt += f" Frente a los mismos meses de {year - 1}, el consumo por huésped {'subió' if ch > 0 else 'bajó'} {fnum(abs(ch), 1)} %."
        out.append(txt)
    costs = {svc: data[svc]["summary"]["cost"] or 0 for svc in data}
    total = sum(costs.values())
    if total and money and len(costs) > 1:
        top = max(costs, key=costs.get)
        txt = f"<b>Costo total de servicios:</b> {fmoney(total)}"
        if guests:
            txt += f" ({fmoney(total / guests)} por huésped)"
        txt += f". {SERVICES[top]['label']} representa el {fnum(costs[top] / total * 100, 0)} % del gasto."
        out.append(txt)
    if guests and guests_prev_same:
        ch = (guests / guests_prev_same - 1) * 100
        out.append(f"<b>Huéspedes:</b> {fnum(guests)} en el período, {fpct(ch)} respecto de los mismos meses de {year - 1}.")
    return out


def _status_card(svc, s, ref):
    if s["per_guest"] is None:
        return ("faltan facturas", WARN_C) if s.get("months_partial") else ("sin datos", MUTED)
    if not ref:
        return "sin referencia oficial", MUTED
    diff = (s["per_guest"] / ref - 1) * 100
    return (f"▲ {fnum(diff, 0)} % sobre la referencia", CRITICAL) if diff > 0 else (f"▼ {fnum(-diff, 0)} % bajo la referencia", GOOD)


# ------------------------------------------------------------------ opciones del informe
# Qué partes lleva el informe. La interfaz deja prender y apagar cada una.
REPORT_SECTIONS = ["portada", "resumen", "costos", "servicios_pag", "anexo", "facturas", "metodologia"]
REPORT_CONTENT = ["graficos", "numeros", "texto", "importes"]


def report_options(raw=None):
    """Normaliza las opciones que llegan de la interfaz (lo que falte, va en sí)."""
    raw = raw or {}
    o = {k: bool(raw.get(k, True)) for k in REPORT_SECTIONS + REPORT_CONTENT}
    svcs = [s for s in (raw.get("servicios") or list(SERVICES)) if s in SERVICES]
    o["servicios"] = [s for s in SERVICES if s in svcs] or list(SERVICES)
    o["comentario"] = str(raw.get("comentario") or "").strip()[:3000]
    try:
        a, b = int(raw.get("desde") or 1), int(raw.get("hasta") or 12)
    except (TypeError, ValueError):
        a, b = 1, 12
    a, b = max(1, min(12, a)), max(1, min(12, b))
    o["desde"], o["hasta"] = min(a, b), max(a, b)
    return o


def _esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _note_box(title, text):
    t = Table([[[Paragraph(f"<b>{title}</b>", ST["body"]), Paragraph(text, ST["body"])]]], colWidths=[W], cornerRadii=[6, 6, 6, 6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fcf0d6")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))
    return KeepTogether([Spacer(1, 4), t, Spacer(1, 4)])


def _comment_box(text):
    """Comentario escrito por la administración, en un recuadro."""
    paras = [Paragraph(_esc(p).replace("\n", "<br/>"), ST["body"]) for p in text.split("\n\n") if p.strip()]
    inner = [Paragraph("Comentarios de la administración", ST["cap"]), Spacer(1, 3)] + paras
    t = Table([[inner]], colWidths=[W], cornerRadii=[6, 6, 6, 6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(CREAM)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return KeepTogether([Spacer(1, 6), t, Spacer(1, 6)])


def report_gaps(con, hotel_id, year, opts=None):
    """Texto con lo que falta cargar en los meses elegidos, o None si está todo."""
    o = report_options(opts)
    full = core.hotel_year(con, hotel_id, year)
    m1, m2 = o["desde"], o["hasta"]
    today = date.today()
    months = list(range(m1, m2 + 1))
    parts = []
    for svc in o["servicios"]:
        rows = {r["month"]: r for r in full[svc]["rows"]}
        miss = [core.MONTHS[m - 1].lower() for m in months if not rows[m]["complete"]]
        if miss:
            parts.append(f"{SERVICES[svc]['label'].lower()} ({', '.join(miss)})")
    g = {r["month"]: r["guests"] for r in full["agua"]["rows"]}
    gmiss = [core.MONTHS[m - 1].lower() for m in months if not g.get(m)]
    txt = []
    if parts:
        txt.append("Todavía faltan facturas que cubran " + "; ".join(parts) + ".")
    if gmiss:
        txt.append("Faltan los huéspedes de " + ", ".join(gmiss) + ".")
    if not txt:
        return None
    recent = year > today.year or (year == today.year and m2 >= today.month - 1)
    note = " Las facturas de un mes suelen llegar el mes siguiente." if recent else ""
    return " ".join(txt) + note


# ------------------------------------------------------------------ PDF por hotel
def pdf_report(con, hotel, year, opts=None):
    o = report_options(opts)
    G, N, T, M = o["graficos"], o["numeros"], o["texto"], o["importes"]
    settings = get_settings(con)
    brand = "Hoteles Demo"   # el informe va con la marca Hoteles Demo, no la de la gestora
    full = core.hotel_year(con, hotel["id"], year)
    m1, m2 = o["desde"], o["hasta"]
    whole_year = (m1, m2) == (1, 12)
    if not whole_year:   # solo los meses elegidos (y los mismos meses del año anterior para comparar)
        for svc in SERVICES:
            d = full[svc]
            d["rows"], d["prev_rows"] = d["rows"][m1 - 1:m2], d["prev_rows"][m1 - 1:m2]
            d["summary"] = core.summarize(d["rows"], d["reference"])
            d["compare"] = core.compare_rows(d["rows"], d["prev_rows"])
    kind = "Informe anual de consumos" if whole_year else "Informe de consumos"
    data = {s: full[s] for s in o["servicios"]}          # solo los servicios elegidos
    _l = [SERVICES[s]["label"].lower() for s in data]
    labels = ", ".join(_l[:-1]) + " y " + _l[-1] if len(_l) > 1 else _l[0]
    ids = [r["id"] for r in con.execute("SELECT id FROM hotels ORDER BY sort,id")]
    accent = HOTEL_COLORS[ids.index(hotel["id"]) % 4] if hotel["id"] in ids else GOLD
    rows_g, prev_g = full["agua"]["rows"], full["agua"]["prev_rows"]
    period = month_range([data[s]["rows"] for s in data])
    guests = sum(r["guests"] or 0 for r in rows_g)
    prev_by_m = {r["month"]: r for r in prev_g}
    guests_prev_same = sum(prev_by_m[r["month"]]["guests"] or 0 for r in rows_g if r["guests"])
    total_cost = sum(data[s]["summary"]["cost"] or 0 for s in data)

    buf = io.BytesIO()
    doc = _doc(buf, f"Informe de consumos {hotel['name']} {year}", brand)
    sections = []   # cada sección es una lista de elementos; entre secciones va un salto de página

    # ---------- 1. Portada
    cover = None
    if o["portada"]:
        cards = []
        for svc in data:
            s = data[svc]["summary"]
            note, nc = _status_card(svc, s, data[svc]["reference"])
            cards.append(dict(label=SERVICES[svc]["label"], value=fnum(s["per_guest"], per_dec(svc)), unit=f"{UNITS[svc]} por huésped",
                              note=note, note_color=nc, color=SERVICE_COLOR[svc]))
        cover = dict(kicker=kind, title_lines=_split_title(hotel["name"]), subtitle=str(year),
                     period=f"Período analizado: {period} de {year}" if period else f"Año {year}",
                     legal=hotel.get("legal_name") or "", cards=cards, accent=accent,
                     prepared=" · ".join(v for v in (hotel["name"], hotel.get("address") or "") if v))

    # ---------- 2. Resumen ejecutivo
    gaps = report_gaps(con, hotel["id"], year, o)
    if o["resumen"]:
        el = section_header("Resumen ejecutivo", f"{hotel['name']} · {year}", accent)
        if gaps:
            el.append(_note_box("Información incompleta", gaps + " Por eso algunos valores aparecen vacíos o parciales; "
                                "el informe se completa solo cuando se carguen."))
        if T and period:
            el.append(Paragraph(f"Consumos de {labels} de {period} de {year}, relacionados con la cantidad de "
                                "huéspedes y comparados con los valores de referencia del sector y con el año anterior.", ST["lead"]))
        if N:
            el.append(Spacer(1, 10))
            alerts = sum(data[s]["summary"]["alerts"] or 0 for s in data)
            kp = [(fnum(guests), "Huéspedes", f"{fpct((guests / guests_prev_same - 1) * 100)} vs {year - 1}" if guests_prev_same else "")]
            if M:
                kp += [(fmoney_short(total_cost), "Costo total de servicios", labels),
                       (fmoney(total_cost / guests) if guests else "—", "Costo por huésped", "")]
            kp.append((str(alerts), "Meses sobre la referencia", "", CRITICAL if alerts else GOOD))
            el.append(kpi_row(kp))
        if o["comentario"]:
            el.append(_comment_box(o["comentario"]))
        concl = insights(data, year, guests, guests_prev_same, money=M) if T else []
        if concl:
            el.append(Paragraph("Conclusiones", ST["h2"]))
            for t in concl:
                el.append(Paragraph(t, ST["bullet"], bulletText="•"))
        if N or G:
            el.append(Paragraph("Consumo por huésped frente a la referencia", ST["h2"]))
            rows = [["Servicio", "Por huésped", "Referencia", "", "Situación", f"vs {year - 1}"]]
            extra = []
            for i, svc in enumerate(data, start=1):
                s = data[svc]["summary"]; ref = data[svc]["reference"]; c = data[svc]["compare"]
                sit, col = _status_card(svc, s, ref)
                sit = sit.replace(" la referencia", "").replace("sin referencia oficial", "sin referencia")
                extra.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor(col)))
                rows.append([Paragraph(f"<font color='{SERVICE_COLOR[svc]}'>●</font> <b>{SERVICES[svc]['label']}</b><br/>"
                                       f"<font size=7 color='{MUTED}'>{UNITS[svc]} por huésped</font>", ST["cell"]),
                             fnum(s["per_guest"], per_dec(svc)), fnum(ref, per_dec(svc)) if ref else "—",
                             meter(s["per_guest"], ref, SERVICE_COLOR[svc]) if G else "", sit,
                             fpct(c.get("per_guest_change")) if c else "—"])
            el.append(data_table(rows, [3.4 * cm, 2 * cm, 2 * cm, 5.6 * cm, 2.6 * cm, W - 15.6 * cm], num_from=1,
                                 extra=extra + [("ALIGN", (3, 0), (4, -1), "LEFT"), ("FONT", (1, 1), (1, -1), F["semi"], 10.5),
                                                ("TOPPADDING", (0, 1), (-1, -1), 9), ("BOTTOMPADDING", (0, 1), (-1, -1), 9),
                                                ("FONT", (4, 1), (4, -1), F["semi"], 8.5)]))
            if T and G:
                el.append(Spacer(1, 4))
                el.append(Paragraph("La barra muestra el consumo por huésped: en color hasta la referencia y en rojo el exceso. "
                                    "Referencias UTGRA 2022 por persona-noche.", ST["small"]))
        sections.append(el)
    elif o["comentario"]:
        sections.append([_comment_box(o["comentario"])])

    # ---------- 3. Costos y huéspedes
    if o["costos"]:
        el = section_header("Costos y huéspedes" if M and (G or N) else "Huéspedes", f"{hotel['name']} · {year}", accent)
        if M and G:
            el.append(Paragraph("Costo facturado por mes", ST["cap"]))
            el.append(chart_cost_stack(data))
        if M and N:
            crow = [["Servicio", "Costo total", "% del total", "Promedio mensual", "Por huésped", f"vs {year - 1}"]]
            for svc in data:
                s = data[svc]["summary"]
                mc = [r["amount"] for r in data[svc]["rows"] if r["amount"] and r["complete"]]
                crow.append([Paragraph(f"<font color='{SERVICE_COLOR[svc]}'>●</font> {SERVICES[svc]['label']}", ST["cell"]),
                             fmoney(s["cost"]), (fnum((s["cost"] or 0) / total_cost * 100, 0) + " %") if total_cost else "—",
                             fmoney(sum(mc) / len(mc)) if mc else "—", fmoney(s["cost_per_guest"]),
                             fpct(cost_compare(data[svc]["rows"], data[svc]["prev_rows"]))])
            crow.append(["Total", fmoney(total_cost), "100 %", "", fmoney(total_cost / guests) if guests else "—", ""])
            el.append(Spacer(1, 6))
            el.append(data_table(crow, [4.2 * cm, 2.9 * cm, 2.2 * cm, 3 * cm, 2.6 * cm, W - 14.9 * cm],
                                 extra=[("FONT", (0, -1), (-1, -1), F["semi"], 8.5), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor(INK2))]))
            if T:
                el.append(Spacer(1, 3))
                el.append(Paragraph("La variación de costos compara los mismos meses del año anterior; incluye cambios de consumo y de tarifa.", ST["small"]))
        if G:
            el.append(Paragraph("Huéspedes por mes", ST["cap"]))
            el.append(chart_monthly_bars([r["guests"] for r in rows_g], [r["guests"] for r in prev_g], accent, year, "huéspedes", h=2.6,
                                         labels=[r["short"] for r in rows_g]))
        gv = [r for r in rows_g if r["guests"]]
        if gv and (T or N):
            mx = max(gv, key=lambda r: r["guests"]); mn = min(gv, key=lambda r: r["guests"])
            el.append(Paragraph(f"Promedio mensual: <b>{fnum(guests / len(gv))}</b> huéspedes · mes más alto: <b>{mx['label'].lower()}</b> "
                                f"({fnum(mx['guests'])}) · mes más bajo: <b>{mn['label'].lower()}</b> ({fnum(mn['guests'])}).", ST["body"]))
        if len(el) > 2:
            sections.append(el if G else ["SEGUIDO"] + el)

    # ---------- 4. Páginas por servicio
    if o["servicios_pag"]:
        for svc in data:
            meta = SERVICES[svc]
            d = data[svc]; s = d["summary"]; ref = d["reference"]; u = meta["unit"]; c = d["compare"]
            el = section_header(meta["label"], f"{hotel['name']} · {year} · {meta['provider']}", SERVICE_COLOR[svc])
            if N:
                pg_col = CRITICAL if (ref and s["per_guest"] and s["per_guest"] > ref) else (GOOD if ref and s["per_guest"] else INK)
                kp = [
                    (fnum(s["total"]), f"Consumo total ({u})", f"{fpct(c['change'])} vs {year - 1} ({c['months']} {'mes' if c['months'] == 1 else 'meses'})" if c and c.get("change") is not None else ""),
                    (fnum(s["avg"]), f"Promedio mensual ({u})", f"mín. {fnum(s['min'])} · máx. {fnum(s['max'])}" if s["min"] is not None else ""),
                    (fnum(s["per_guest"], per_dec(svc)), f"{u} por huésped", f"referencia {fnum(ref, per_dec(svc))}" if ref else "sin referencia oficial", pg_col),
                ]
                if M:
                    kp.append((fmoney_short(s["cost"]), "Costo facturado", f"{fmoney(s['cost_per_guest'])} por huésped" if s["cost_per_guest"] else ""))
                el.append(kpi_row(kp))
            if G:
                el.append(Paragraph(f"Consumo mensual ({u})", ST["cap"]))
                el.append(chart_monthly_bars([r["consumption"] for r in d["rows"]], [r["consumption"] for r in d["prev_rows"]],
                                             SERVICE_COLOR[svc], year, u, partial=[r["consumption"] is not None and not r["complete"] for r in d["rows"]],
                                             labels=[r["short"] for r in d["rows"]]))
                el.append(Paragraph(f"Consumo por huésped ({u} por huésped)", ST["cap"]))
                el.append(chart_per_guest(d["rows"], d["prev_rows"], svc, year, ref))
                el.append(Spacer(1, 4))
            if T:
                if s["max_month"]:
                    el.append(Paragraph(f"Mayor consumo en <b>{s['max_month'].lower()}</b> ({fnum(s['max'])} {u}) y menor en "
                                        f"<b>{s['min_month'].lower()}</b> ({fnum(s['min'])} {u}).", ST["body"]))
                if s["per_guest_max"] is not None:
                    el.append(Paragraph(f"Por huésped, el mes más alto fue {s['per_guest_max_month'].lower()} ({fnum(s['per_guest_max'], per_dec(svc))}) "
                                        f"y el más bajo {s['per_guest_min_month'].lower()} ({fnum(s['per_guest_min'], per_dec(svc))}).", ST["body"]))
                if s["months_partial"] and G:
                    el.append(Paragraph("Los meses en tono claro son parciales: todavía falta alguna factura que los cubra, por eso no se usan en los promedios.", ST["small"]))
            if len(el) > 2:
                sections.append(el if G else ["SEGUIDO"] + el)

    # ---------- 5. Anexo
    el = []
    if o["anexo"]:
        el += section_header("Detalle mes a mes", f"{hotel['name']} · {year}", accent)
        for svc in data:
            meta = SERVICES[svc]
            d = data[svc]; s = d["summary"]; u = meta["unit"]
            head = ["Mes", f"Consumo ({u})", f"{year - 1}", "Huéspedes", "Por huésped"] + (["Costo"] if M else []) + ["Estado"]
            st_col = len(head) - 1
            tbl = [head]
            extra = []
            for i, (r, p) in enumerate(zip(d["rows"], d["prev_rows"]), start=1):
                if r["consumption"] is None:
                    est = "falta factura" if r["guests"] else ""
                elif not r["complete"]:
                    est = "parcial"
                elif r["guests"] is None:
                    est = "faltan huéspedes"
                elif r["status"] == "alto":
                    est = "▲ sobre ref."
                    extra += [("TEXTCOLOR", (4, i), (4, i), colors.HexColor(CRITICAL)), ("TEXTCOLOR", (st_col, i), (st_col, i), colors.HexColor(CRITICAL))]
                elif r["status"] == "ok":
                    est = "dentro de ref."
                    extra.append(("TEXTCOLOR", (st_col, i), (st_col, i), colors.HexColor(GOOD)))
                else:
                    est = ""
                tbl.append([r["label"], fnum(r["consumption"]), fnum(p["consumption"]), fnum(r["guests"]),
                            fnum(r["per_guest"], per_dec(svc))] + ([fmoney(r["amount"])] if M else []) + [est])
            tbl.append(["Total", fnum(s["total"]), fnum(sum(p["consumption"] or 0 for p in d["prev_rows"]) or None),
                        fnum(sum(r["guests"] or 0 for r in d["rows"]) or None), fnum(s["per_guest"], per_dec(svc))]
                       + ([fmoney(s["cost"])] if M else []) + [""])
            extra += [("FONT", (0, -1), (-1, -1), F["semi"], 7.8), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor(INK2)),
                      ("TEXTCOLOR", (2, 1), (2, -1), colors.HexColor(MUTED)), ("ALIGN", (st_col, 0), (st_col, -1), "LEFT")]
            widths = [2.4 * cm, 2.6 * cm, 2.2 * cm, 2.2 * cm, 2.3 * cm] + ([2.9 * cm] if M else [])
            widths.append(W - sum(widths))
            el.append(KeepTogether([Paragraph(f"<font color='{SERVICE_COLOR[svc]}'>●</font> {meta['label']}", ST["cap"]),
                                    data_table(tbl, widths, extra=extra, font_size=7.8), Spacer(1, 6)]))

    if o["facturas"]:
        svc_list = list(data)
        invs = con.execute(f"""SELECT * FROM invoices WHERE hotel_id=? AND deleted_at IS NULL
                               AND service IN ({','.join('?' * len(svc_list))})
                               AND period_end >= ? AND period_start <= ?
                               ORDER BY service, period_start, part""",
                            (hotel["id"], *svc_list, f"{year}-{m1:02d}-01",
                             f"{year}-{m2:02d}-{calendar.monthrange(year, m2)[1]:02d}")).fetchall()
        if invs:
            if el:
                el.append(CondPageBreak(6 * cm))
                el.append(Paragraph("Facturas consideradas", ST["h2"]))
            else:
                el += section_header("Facturas consideradas", f"{hotel['name']} · {year}", accent)
            dm = lambda s: f"{s[8:10]}/{s[5:7]}/{s[:4]}"  # noqa: E731
            tbl = [["Servicio", "Cuenta", "Período de lectura", "Consumo", "Tramo"] + (["Importe"] if M else [])]
            for inv in invs:
                tbl.append([SERVICES[inv["service"]]["label"], inv["account_number"] or "—", f"{dm(inv['period_start'])} – {dm(inv['period_end'])}",
                            f"{fnum(inv['consumption'] / (inv['split'] or 1))} {UNITS[inv['service']]}",
                            f"{inv['part']} de {inv['split']}" if inv["split"] > 1 else ""] + ([fmoney(inv["amount"])] if M else []))
            widths = [2.6 * cm, 2.6 * cm, 4.4 * cm, 2.6 * cm, 1.8 * cm]
            widths.append(W - sum(widths)) if M else None
            el.append(data_table(tbl, widths, num_from=3, font_size=7.2, extra=[("ALIGN", (4, 0), (4, -1), "CENTER")]))

    if o["metodologia"]:
        if el:
            el.append(CondPageBreak(7 * cm))
            el.append(Paragraph("Metodología", ST["h2"]))
        else:
            el += section_header("Metodología", f"{hotel['name']} · {year}", accent)
        ra, re_, rg = reference(settings, "agua"), reference(settings, "electricidad"), reference(settings, "gas")
        for t in [
            "<b>Fuentes.</b> Facturas de AySA (agua), Edesur (electricidad) y MetroGAS (gas), y cantidad mensual de huéspedes informada por el hotel.",
            "<b>Asignación mensual.</b> Cada factura cubre un período de lectura; su consumo y su importe se distribuyen entre los meses según los días "
            "que caen en cada uno. Las lecturas de agua son bimestrales y se facturan en dos tramos: cada lectura se cuenta una sola vez.",
            "<b>Consumo por huésped.</b> Consumo del mes dividido por la cantidad de huéspedes del mes. El valor anual es el consumo total "
            "sobre el total de huéspedes de los meses completos.",
            f"<b>Referencias (UTGRA 2022).</b> Agua {fnum(ra, 2)} m³ y electricidad {fnum(re_, 2)} kWh por persona-noche. "
            + (f"Gas {fnum(rg, 3)} m³ por huésped." if rg else "Gas: sin valor de referencia para Argentina."),
            "<b>Meses parciales.</b> Si falta alguna factura que cubra todos los días del mes, el mes se marca como parcial y no se usa en mínimos, máximos ni promedios.",
        ] + (["<b>Costos.</b> Importe total de cada factura, con impuestos incluidos."] if M else []):
            el.append(Paragraph(t, ST["body"]))
            el.append(Spacer(1, 3))
    if el:
        sections.append(el)

    if not sections and not cover:
        raise ValueError("El informe quedó vacío: elegí al menos una sección.")
    story = [Spacer(1, 1)] if cover else []
    for i, sec in enumerate(sections):
        if sec and sec[0] == "SEGUIDO":
            sec = sec[1:]
            story.append(CondPageBreak(4.5 * cm) if (i or cover) else Spacer(1, 1))
            story.append(Spacer(1, 14))
        elif i or cover:
            story.append(PageBreak())
        story += sec
    deco = _page_deco(hotel["name"], f"{kind} · {year}", accent, brand)
    doc.build(story, onFirstPage=(lambda c, d: _cover(c, cover)) if cover else deco, onLaterPages=deco)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF comparativo (cartera)
def pdf_comparison(con, year):
    settings = get_settings(con)
    hotels_db = con.execute("SELECT * FROM hotels ORDER BY sort,id").fetchall()
    per = {}
    for svc in SERVICES:
        per[svc] = []
        for i, h in enumerate(hotels_db):
            rows, ref = core.monthly(con, h["id"], svc, year)
            per[svc].append(dict(hotel=h["name"], rows=rows, summary=core.summarize(rows, ref), color=HOTEL_COLORS[i % 4]))
    period = month_range([hh["rows"] for svc in SERVICES for hh in per[svc]])
    cards = []
    for svc in SERVICES:
        ref = reference(settings, svc)
        vals = [(hh["summary"]["per_guest"], hh["hotel"]) for hh in per[svc] if hh["summary"]["per_guest"] is not None]
        over = sum(1 for v, _ in vals if ref and v > ref)
        best = min(vals)[1] if vals else "—"
        if ref:
            cards.append(dict(label=SERVICES[svc]["label"], value=f"{over} de {len(vals)}", unit="hoteles sobre la referencia",
                              note=f"Menor consumo: {best}", note_color=INK2, color=SERVICE_COLOR[svc]))
        else:
            cards.append(dict(label=SERVICES[svc]["label"], value=fnum(min(vals)[0], per_dec(svc)) if vals else "—",
                              unit=f"{UNITS[svc]} por huésped (el menor)", note=best, note_color=INK2, color=SERVICE_COLOR[svc]))
    cover = dict(kicker="Informe comparativo", title_lines=["Cartera de", "hoteles"], subtitle=str(year),
                 period=f"Período analizado: {period} de {year}" if period else f"Año {year}",
                 legal=" · ".join(h["name"] for h in hotels_db), cards=cards, accent=GOLD, cards_title="SITUACIÓN DE LA CARTERA",
                 prepared=f"{len(hotels_db)} hoteles")
    buf = io.BytesIO()
    doc = _doc(buf, f"Comparativo de hoteles {year}", "Hoteles Demo")
    el = [Spacer(1, 1), PageBreak()]

    el += section_header("Resumen de la cartera", f"{len(hotels_db)} hoteles · {year}", GOLD)
    el.append(Paragraph("Consumo por huésped y costo de servicios de cada hotel en el período.", ST["lead"]))
    tbl = [["Hotel", "Huéspedes", "Agua\nm³/huésp.", "Electricidad\nkWh/huésp.", "Gas\nm³/huésp.", "Costo total", "Costo por\nhuésped"]]
    extra = []
    for i, h in enumerate(hotels_db):
        g = per["agua"][i]["summary"]["guests"]
        cost = sum(per[s][i]["summary"]["cost"] or 0 for s in SERVICES)
        row = [Paragraph(f"<font color='{HOTEL_COLORS[i % 4]}'>●</font> <b>{h['name']}</b>", ST["cell"]), fnum(g)]
        for j, svc in enumerate(SERVICES):
            v = per[svc][i]["summary"]["per_guest"]; ref = reference(settings, svc)
            row.append(fnum(v, per_dec(svc)))
            if ref and v and v > ref:
                extra += [("TEXTCOLOR", (2 + j, i + 1), (2 + j, i + 1), colors.HexColor(CRITICAL)),
                          ("FONT", (2 + j, i + 1), (2 + j, i + 1), F["semi"], 8.5)]
        row += [fmoney(cost), fmoney(cost / g) if g else "—"]
        tbl.append(row)
    el.append(Spacer(1, 8))
    el.append(data_table(tbl, [4.4 * cm, 2 * cm, 2 * cm, 2.3 * cm, 2 * cm, 2.6 * cm, W - 15.3 * cm],
                         extra=extra + [("TOPPADDING", (0, 1), (-1, -1), 7), ("BOTTOMPADDING", (0, 1), (-1, -1), 7)]))
    el.append(Spacer(1, 4))
    el.append(Paragraph(f"En rojo: por encima de la referencia UTGRA 2022 (agua {fnum(reference(settings, 'agua'), 2)} m³ · "
                        f"electricidad {fnum(reference(settings, 'electricidad'), 2)} kWh por huésped).", ST["small"]))
    el.append(Paragraph("Conclusiones", ST["h2"]))
    for svc, meta in SERVICES.items():
        ref = reference(settings, svc)
        vals = [(hh["summary"]["per_guest"], hh["hotel"]) for hh in per[svc] if hh["summary"]["per_guest"] is not None]
        if not vals:
            continue
        hi, lo = max(vals), min(vals)
        txt = (f"<b>{meta['label']}:</b> el mayor consumo por huésped es de {hi[1]} ({fnum(hi[0], per_dec(svc))} {meta['unit']}) "
               f"y el menor de {lo[1]} ({fnum(lo[0], per_dec(svc))} {meta['unit']}).")
        if ref:
            over = [n for v, n in vals if v > ref]
            months_over = [(hh["hotel"], hh["summary"]["alerts"]) for hh in per[svc] if hh["summary"]["alerts"]]
            if over:
                txt += f" <font color='{CRITICAL}'>Por encima de la referencia en el promedio del período: {', '.join(over)}.</font>"
            else:
                txt += f" <font color='{GOOD}'>En el promedio del período todos los hoteles están dentro de la referencia.</font>"
                if months_over:
                    txt += " Meses puntuales por encima: " + ", ".join(f"{n} ({k} {'mes' if k == 1 else 'meses'})" for n, k in months_over) + "."
        el.append(Paragraph(txt, ST["bullet"], bulletText="•"))
    costs = [(sum(per[s][i]["summary"]["cost"] or 0 for s in SERVICES), h["name"]) for i, h in enumerate(hotels_db)]
    tot = sum(c for c, _ in costs)
    if tot:
        el.append(Paragraph(f"<b>Costo total de la cartera:</b> {fmoney(tot)}. El hotel con mayor gasto es {max(costs)[1]} "
                            f"({fnum(max(costs)[0] / tot * 100, 0)} % del total).", ST["bullet"], bulletText="•"))
    el.append(Paragraph("Costo de servicios por hotel", ST["cap"]))
    fig, ax = plt.subplots(figsize=(CHART_W, 2.1), layout="constrained")
    names = [h["name"] for h in hotels_db][::-1]
    left = [0] * len(names)
    handles = []
    for svc in SERVICES:
        vals = [(per[svc][i]["summary"]["cost"] or 0) / 1e6 for i in range(len(hotels_db))][::-1]
        ax.barh(names, vals, 0.58, left=left, color=SERVICE_COLOR[svc], edgecolor="white", linewidth=1.5)
        left = [a + b for a, b in zip(left, vals)]
        handles.append(Patch(color=SERVICE_COLOR[svc], label=SERVICES[svc]["label"]))
    for i, v in enumerate(left):
        ax.annotate(fnum(v, 1) + " M", (v, i), xytext=(5, 0), textcoords="offset points", va="center", fontsize=8, fontweight="semibold")
    ax.spines[["top", "right", "bottom"]].set_visible(False); ax.spines["left"].set_color(GRID)
    ax.tick_params(length=0); ax.xaxis.grid(True, color=GRID, linewidth=0.7); ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: fnum(v, 0))); ax.set_xlabel("millones de $")
    ax.margins(x=0.12)
    _legend(ax, handles)
    el.append(_img(fig, 2.1))

    for svc, meta in SERVICES.items():
        ref = reference(settings, svc)
        el.append(PageBreak())
        el += section_header(meta["label"], f"Comparativo de hoteles · {year}", SERVICE_COLOR[svc])
        el.append(Paragraph(f"Consumo por huésped en el período ({meta['unit']})", ST["cap"]))
        el.append(chart_hotel_ranking([dict(name=hh["hotel"], value=hh["summary"]["per_guest"], color=hh["color"]) for hh in per[svc]], svc, ref))
        el.append(Paragraph("Evolución mensual por hotel", ST["cap"]))
        el.append(chart_hotels_lines(per[svc], svc, ref))
        tbl = [["Hotel", f"Consumo ({meta['unit']})", "Prom. mensual", "Mín. / Máx.", "Por huésped", "Meses sobre ref.", "Costo"]]
        extra = []
        for i, hh in enumerate(per[svc], start=1):
            s = hh["summary"]
            tbl.append([Paragraph(f"<font color='{hh['color']}'>●</font> {hh['hotel']}", ST["cell"]), fnum(s["total"]), fnum(s["avg"]),
                        f"{fnum(s['min'])} / {fnum(s['max'])}", fnum(s["per_guest"], per_dec(svc)),
                        f"{s['alerts']} de {s['months_complete']}" if ref else "—", fmoney(s["cost"])])
            if ref and s["per_guest"] and s["per_guest"] > ref:
                extra.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor(CRITICAL)))
        el.append(Spacer(1, 6))
        el.append(data_table(tbl, [4.0 * cm, 2.3 * cm, 2.1 * cm, 2.6 * cm, 2.0 * cm, 2.2 * cm, W - 15.2 * cm], extra=extra))

    deco = _page_deco("Cartera de hoteles", f"Informe comparativo · {year}", GOLD)
    doc.build(el, onFirstPage=lambda c, d: _cover(c, cover), onLaterPages=deco)
    return buf.getvalue()


# ------------------------------------------------------------------ Excel
THIN = Side(style="thin", color="E6E0D4")


def _hdr(ws, row, values, fill="1F2B28"):
    for i, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=i, value=v)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=fill)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _title(ws, text, sub, accent):
    ws["A1"] = text
    ws["A1"].font = Font(name="Georgia", size=16, color="1D1B17")
    ws["A2"] = sub
    ws["A2"].font = Font(color="958E81", size=10)
    for col in range(1, 10):
        ws.cell(row=3, column=col).fill = PatternFill("solid", fgColor=accent.lstrip("#"))
    ws.row_dimensions[3].height = 4
    ws.sheet_view.showGridLines = False


def excel_report(con, hotel, year):
    data = core.hotel_year(con, hotel["id"], year)
    ids = [r["id"] for r in con.execute("SELECT id FROM hotels ORDER BY sort,id")]
    accent = HOTEL_COLORS[ids.index(hotel["id"]) % 4] if hotel["id"] in ids else GOLD
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    _title(ws, f"{hotel['name']} — Informe de consumos {year}", f"Generado el {date.today():%d/%m/%Y}", accent)
    _hdr(ws, 5, ["Indicador", "Agua (m³)", "Electricidad (kWh)", "Gas (m³)"])
    lines = [
        ("Consumo total", "total", "#,##0"), ("Promedio mensual", "avg", "#,##0"), ("Mínimo mensual", "min", "#,##0"),
        ("Mes mínimo", "min_month", None), ("Máximo mensual", "max", "#,##0"), ("Mes máximo", "max_month", None),
        ("Huéspedes", "guests", "#,##0"), ("Consumo por huésped", "per_guest", "0.000"),
        ("Referencia por huésped", "reference", "0.000"), ("Meses sobre referencia", "alerts", "0"),
        ("Costo total ($)", "cost", "#,##0"), ("Costo por huésped ($)", "cost_per_guest", "#,##0.00"),
    ]
    for i, (label, key, fmt) in enumerate(lines, start=6):
        ws.cell(row=i, column=1, value=label).font = Font(color="5B564D")
        for j, svc in enumerate(SERVICES, start=2):
            s = data[svc]["summary"]
            c = ws.cell(row=i, column=j, value=s.get(key))
            if fmt:
                c.number_format = fmt
            c.alignment = Alignment(horizontal="right")
            if key == "per_guest" and s["per_guest"] and s["reference"] and s["per_guest"] > s["reference"]:
                c.font = Font(bold=True, color="C62828")
        for col in range(1, 5):
            ws.cell(row=i, column=col).border = Border(bottom=THIN)
    r = 6 + len(lines)
    for k, (label, key) in enumerate([(f"Variación vs {year - 1} (consumo)", "change"), (f"Variación vs {year - 1} (por huésped)", "per_guest_change")]):
        ws.cell(row=r + k, column=1, value=label).font = Font(color="5B564D")
        for j, svc in enumerate(SERVICES, start=2):
            cmp_ = data[svc]["compare"]
            v = cmp_.get(key) if cmp_ else None
            c = ws.cell(row=r + k, column=j, value=v / 100 if v is not None else None)
            c.number_format = "+0.0%;-0.0%"
            c.border = Border(bottom=THIN)
    ws.column_dimensions["A"].width = 34
    for col in "BCD":
        ws.column_dimensions[col].width = 20

    for svc, meta in SERVICES.items():
        d = data[svc]
        sh = wb.create_sheet(meta["label"])
        unit = meta["unit"]
        _title(sh, f"{meta['label']} {year} — {hotel['name']}", f"Proveedor: {meta['provider']}", SERVICE_COLOR[svc])
        _hdr(sh, 5, ["Mes", f"Consumo ({unit})", f"Consumo {year - 1}", "Huéspedes", f"{unit} por huésped",
                     "Referencia", "Costo ($)", "Cobertura del mes", "Estado"], fill=SERVICE_COLOR[svc].lstrip("#"))
        for i, (row, prev) in enumerate(zip(d["rows"], d["prev_rows"]), start=6):
            sh.cell(row=i, column=1, value=row["label"])
            sh.cell(row=i, column=2, value=row["consumption"]).number_format = "#,##0"
            sh.cell(row=i, column=3, value=prev["consumption"]).number_format = "#,##0"
            sh.cell(row=i, column=4, value=row["guests"]).number_format = "#,##0"
            sh.cell(row=i, column=5, value=f"=IF(AND(ISNUMBER(B{i}),N(D{i})>0),B{i}/D{i},NA())").number_format = "0.000"
            sh.cell(row=i, column=6, value=d["reference"]).number_format = "0.000"
            sh.cell(row=i, column=7, value=row["amount"]).number_format = "#,##0"
            sh.cell(row=i, column=8, value=row["coverage"]).number_format = "0%"
            estado = ("parcial" if row["consumption"] is not None and not row["complete"] else
                      "sobre referencia" if row["status"] == "alto" else "dentro de referencia" if row["status"] == "ok" else "")
            sh.cell(row=i, column=9, value=estado)
            if row["status"] == "alto":
                for col in (5, 9):
                    sh.cell(row=i, column=col).font = Font(color="C62828", bold=True)
            for col in range(1, 10):
                sh.cell(row=i, column=col).border = Border(bottom=THIN)
        sh.cell(row=18, column=1, value="Total").font = Font(bold=True)
        for col in (2, 3, 4, 7):
            L = get_column_letter(col)
            c = sh.cell(row=18, column=col, value=f"=SUM({L}6:{L}17)")
            c.number_format = "#,##0"; c.font = Font(bold=True)
        sh.cell(row=18, column=5, value="=IF(D18>0,B18/D18,\"\")").number_format = "0.000"
        for k, (lbl, fn) in enumerate([("Promedio mensual", "AVERAGE"), ("Mínimo", "MIN"), ("Máximo", "MAX")], start=19):
            sh.cell(row=k, column=1, value=lbl).font = Font(color="5B564D")
            sh.cell(row=k, column=2, value=f"={fn}(B6:B17)").number_format = "#,##0"
        sh.cell(row=23, column=1, value="Promedio, mínimo y máximo de esta hoja incluyen meses parciales; los del Resumen usan solo meses completos.").font = Font(italic=True, color="958E81", size=9)
        for i, w in enumerate([14, 16, 16, 12, 16, 12, 16, 12, 20], start=1):
            sh.column_dimensions[get_column_letter(i)].width = w

        bc = BarChart()
        bc.title = f"Consumo mensual ({unit})"
        bc.add_data(Reference(sh, min_col=2, max_col=3, min_row=5, max_row=17), titles_from_data=True)
        bc.set_categories(Reference(sh, min_col=1, min_row=6, max_row=17))
        bc.series[0].graphicalProperties.solidFill = SERVICE_COLOR[svc][1:]
        bc.series[0].graphicalProperties.line.noFill = True
        bc.series[1].graphicalProperties.solidFill = PREV[1:]
        bc.series[1].graphicalProperties.line.noFill = True
        bc.gapWidth = 60
        bc.legend.position = "t"
        bc.height, bc.width = 7.5, 18
        sh.add_chart(bc, "K5")
        lc = LineChart()
        lc.title = f"{unit} por huésped vs referencia"
        lc.add_data(Reference(sh, min_col=5, max_col=6, min_row=5, max_row=17), titles_from_data=True)
        lc.set_categories(Reference(sh, min_col=1, min_row=6, max_row=17))
        lc.series[0].graphicalProperties.line.solidFill = SERVICE_COLOR[svc][1:]
        lc.series[0].graphicalProperties.line.width = 28000
        lc.series[0].marker.symbol = "circle"
        lc.series[1].graphicalProperties.line.solidFill = "5B564D"
        lc.series[1].graphicalProperties.line.dashStyle = "dash"
        lc.legend.position = "t"
        lc.height, lc.width = 7.5, 18
        sh.add_chart(lc, "K21")

    gs = wb.create_sheet("Huéspedes")
    _title(gs, f"Huéspedes {year} — {hotel['name']}", "", accent)
    _hdr(gs, 5, ["Mes", str(year), str(year - 1)], fill=accent.lstrip("#"))
    for i, (r, p) in enumerate(zip(data["agua"]["rows"], data["agua"]["prev_rows"]), start=6):
        gs.cell(row=i, column=1, value=r["label"])
        gs.cell(row=i, column=2, value=r["guests"]).number_format = "#,##0"
        gs.cell(row=i, column=3, value=p["guests"]).number_format = "#,##0"
    gs.cell(row=18, column=1, value="Total").font = Font(bold=True)
    gs["B18"], gs["C18"] = "=SUM(B6:B17)", "=SUM(C6:C17)"
    gs["B18"].number_format = gs["C18"].number_format = "#,##0"
    gs.column_dimensions["A"].width = 14
    gc = BarChart()
    gc.title = "Huéspedes por mes"
    gc.add_data(Reference(gs, min_col=2, max_col=3, min_row=5, max_row=17), titles_from_data=True)
    gc.set_categories(Reference(gs, min_col=1, min_row=6, max_row=17))
    gc.series[0].graphicalProperties.solidFill = accent.lstrip("#")
    gc.series[1].graphicalProperties.solidFill = PREV[1:]
    gc.legend.position = "t"
    gc.height, gc.width = 7.5, 18
    gs.add_chart(gc, "E5")

    fs = wb.create_sheet("Facturas")
    _title(fs, f"Facturas consideradas — {hotel['name']} {year}", "", accent)
    _hdr(fs, 5, ["Servicio", "Proveedor", "Cuenta", "Factura", "Tramo", "Emisión", "Desde", "Hasta",
                 "Consumo lectura", "Consumo imputado", "Unidad", "Importe ($)", "Archivo"])
    rows = con.execute("""SELECT * FROM invoices WHERE hotel_id=? AND deleted_at IS NULL AND (substr(period_start,1,4)=? OR substr(period_end,1,4)=?)
                          ORDER BY service, period_start""", (hotel["id"], str(year), str(year))).fetchall()
    for i, inv in enumerate(rows, start=6):
        vals = [SERVICES[inv["service"]]["label"], inv["provider"], inv["account_number"], inv["invoice_number"],
                f"{inv['part']} de {inv['split']}" if inv["split"] > 1 else "", inv["issue_date"], inv["period_start"],
                inv["period_end"], inv["consumption"], inv["consumption"] / (inv["split"] or 1), inv["unit"], inv["amount"], inv["file_name"]]
        for j, v in enumerate(vals, start=1):
            c = fs.cell(row=i, column=j, value=v)
            c.border = Border(bottom=THIN)
            if j in (9, 10, 12):
                c.number_format = "#,##0"
    for i, w in enumerate([13, 11, 13, 17, 8, 11, 11, 11, 14, 14, 8, 14, 40], start=1):
        fs.column_dimensions[get_column_letter(i)].width = w
    fs.freeze_panes = "A6"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
