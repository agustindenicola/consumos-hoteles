"""Instructivo de 1 página para la clienta.  Uso: python instalador/crear_instructivo.py "<carpeta de salida>" """
import os
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "..", "app", "static", "marcas", "marca_azul.png")
OUT = os.path.join(sys.argv[1] if len(sys.argv) > 1 else HERE, "Instructivo - Consumos Hoteles.pdf")

NAVY, INK, INK2, BRONZE, PAPER, BORDER = "#252d45", "#2b2620", "#645b4f", "#a2793a", "#f7f1e6", "#eadfcc"
FD = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
F = {"serif": "Times-Roman", "sans": "Helvetica", "semi": "Helvetica-Bold"}
for k, name, fn in [("serif", "Georgia", "georgia.ttf"), ("sans", "SegoeUI", "segoeui.ttf"), ("semi", "SegoeUI-Semibold", "seguisb.ttf")]:
    if os.path.exists(os.path.join(FD, fn)):
        pdfmetrics.registerFont(TTFont(name, os.path.join(FD, fn))); F[k] = name
if F["sans"] == "SegoeUI" and F["semi"] == "SegoeUI-Semibold":   # para que <b> use la semibold
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily("SegoeUI", normal="SegoeUI", bold="SegoeUI-Semibold", italic="SegoeUI", boldItalic="SegoeUI-Semibold")

ST = {
    "h1": ParagraphStyle("h1", fontName=F["serif"], fontSize=24, leading=28, textColor=colors.HexColor(NAVY)),
    "lead": ParagraphStyle("lead", fontName=F["sans"], fontSize=10.5, leading=15, textColor=colors.HexColor(INK2)),
    "h2": ParagraphStyle("h2", fontName=F["semi"], fontSize=12.5, leading=16, textColor=colors.HexColor(NAVY), spaceBefore=4),
    "p": ParagraphStyle("p", fontName=F["sans"], fontSize=10.5, leading=15, textColor=colors.HexColor(INK)),
    "n": ParagraphStyle("n", fontName=F["semi"], fontSize=13, leading=15, textColor=colors.HexColor(BRONZE)),
    "small": ParagraphStyle("s", fontName=F["sans"], fontSize=9, leading=13, textColor=colors.HexColor(INK2)),
}
W = A4[0] - 4 * cm


def block(title, rows, numbered=False):
    """Recuadro con título y renglones (numerados con círculo o con viñeta)."""
    data = []
    for i, r in enumerate(rows, 1):
        mark = Paragraph(str(i), ST["n"]) if numbered else Paragraph("•", ParagraphStyle("b", parent=ST["p"], textColor=colors.HexColor(BRONZE), fontSize=14))
        data.append([mark, Paragraph(r, ST["p"])])
    t = Table(data, colWidths=[0.9 * cm, W - 1.6 * cm])
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 0)]
    t.setStyle(TableStyle(style))
    box = Table([[Paragraph(title, ST["h2"])], [t]], colWidths=[W])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(PAPER)), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(BORDER)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                             ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 8)]))
    return [box, Spacer(1, 10)]


el = [Image(LOGO, width=3.0 * cm, height=3.0 * cm * 458 / 720, hAlign="LEFT"), Spacer(1, 10),
      Paragraph("Consumos Hoteles · Instructivo", ST["h1"]), Spacer(1, 4),
      Paragraph("Todo lo que necesitás en una página. El detalle está en la <b>Guía completa</b>.", ST["lead"]), Spacer(1, 14)]
el += block("Instalar (una sola vez)", [
    "Clic derecho en el zip → <b>Extraer todo</b>.",
    "Abrí la carpeta y hacé doble clic en <b>Instalar Consumos Hoteles</b>.<br/>"
    "<font size=9 color='%s'>Si aparece “Windows protegió su PC”: <b>Más información</b> → <b>Ejecutar de todas formas</b>.</font>" % INK2,
    "Listo: la app se abre sola y queda el ícono <b>Consumos Hoteles</b> en el escritorio. "
    "Si ya la tenías, se actualiza <b>sin tocar tus datos</b>. Después podés borrar la carpeta descargada.",
], numbered=True)
el += block("Usar", [
    "Abrila siempre con el ícono <b>Consumos Hoteles</b> del escritorio.",
    "Se abre una <b>ventana negra</b> (no la cierres) y la app en el navegador.",
    "Para cerrar: cerrá la pestaña del navegador y la ventana negra.",
])
el += block("Cada mes", [
    "<b>Cargar facturas:</b> arrastrá los PDF de AySA, Edesur y MetroGAS.",
    "<b>Huéspedes:</b> cargá los del mes de cada hotel.",
    "<b>Alertas:</b> si el menú tiene un número rojo, hay algo para atender. Creá las tuyas con <b>+ Nueva alerta</b>.",
    "<b>Informes:</b> elegí el hotel, mirá la vista previa y descargá el PDF.",
])
el += block("Que no se pierda nada", [
    "<b>Una vez:</b> Configuración → Respaldo → elegí <b>Google Drive</b>. Desde ahí se copia sola todos los días.",
    "<b>Cuando quieras:</b> “Descargar respaldo completo (.zip)” y guardalo en tu mail o en el Drive.",
])

SimpleDocTemplate(OUT, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm, bottomMargin=1.4 * cm,
                  title="Instructivo - Consumos Hoteles").build(el)
print("Instructivo creado:", OUT)
