"""Lectura automática de facturas PDF (AySA, Edesur, MetroGAS).

Cada parser devuelve un dict con:
  service: 'agua' | 'electricidad' | 'gas'
  provider, account, holder, invoice_number, issue_date,
  period_start, period_end (ISO yyyy-mm-dd), consumption (float), unit,
  amount (float, $), split (cantidad de tramos en que se factura la lectura), part
"""
import re
from datetime import datetime

import pypdfium2 as pdfium


class ParseError(Exception):
    pass


class InformativeNote(ParseError):
    """Documento válido pero que no debe sumarse (p. ej. nota de débito informativa)."""


def pdf_text(path_or_bytes):
    doc = pdfium.PdfDocument(path_or_bytes)
    try:
        return "\n".join(p.get_textpage().get_text_range() for p in doc).replace("\r", "")
    finally:
        doc.close()


def _ar_num(s):
    """'4.346.438,90' -> 4346438.90"""
    return float(s.strip().replace(".", "").replace(",", "."))


def _us_num(s):
    """'4,970,497.77' -> 4970497.77"""
    return float(s.strip().replace(",", ""))


def _date(s):
    return datetime.strptime(s.strip(), "%d/%m/%Y").date().isoformat()


def due_date(t):
    """Fecha de vencimiento (primer vencimiento) de una factura de AySA, Edesur o MetroGAS."""
    for pat in (r"FECHA DE VENCIMIENTO:\s*(\d\d/\d\d/\d{4})",          # MetroGAS
                r"Esta liquidaci.n vence el\s*(\d\d/\d\d/\d{4})",      # Edesur
                r"Vencimiento\s+(\d\d/\d\d/\d{4})\s+Total a pagar",     # AySA
                r"L.mite de pago en Banco\s*:\s*(\d\d/\d\d/\d{4})"):
        m = re.search(pat, t, re.I)
        if m:
            try:
                return _date(m.group(1))
            except ValueError:
                pass
    return None


def _find(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- AySA (agua)
def parse_aysa(t):
    account = _find(r"Cuenta de Servicios\s+(\d+)", t)
    total_m3 = _find(r"Total m3\s+([\d\.]+)\s*m3", t)
    if not total_m3:
        raise ParseError("No se encontró 'Total m3' en la factura de AySA")
    # Fechas de lectura: "<fecha ant> <m3> <fecha act> <m3> <dias>"
    reads = re.findall(r"(\d\d/\d\d/\d{4})\s+[\d\.]+\s+(\d\d/\d\d/\d{4})\s+[\d\.]+\s+\d+\s+[\d\.]+\s*m3", t)
    if not reads:
        raise ParseError("No se encontraron las fechas de lectura en la factura de AySA")
    start = min(_date(a) for a, _ in reads)
    end = max(_date(b) for _, b in reads)
    tramo = re.search(r"Tramo\s+(\d)\s+de\s+(\d)", t)
    part, split = (int(tramo.group(1)), int(tramo.group(2))) if tramo else (1, 1)
    amount = _find(r"Total a pagar\s+\$\s*([\d\.,]+)", t) or _find(r"Total a pagar hasta el \S+\s+\$\s*([\d\.,]+)", t)
    holder = _find(r"NOMBRE:\s*NOMBRE:\s*(.+?)\s+NOMBRE:", t) or _find(r"NOMBRE:\s*([A-Z].+)", t)
    return dict(
        service="agua", provider="AySA", account=str(int(account)) if account else None,
        holder=holder, invoice_number=_find(r"N[ºo°]\s*(\w{6,})", t),
        issue_date=_date(_find(r"Fecha de emisi.n:\s*(\S+)", t)) if _find(r"Fecha de emisi.n:\s*(\S+)", t) else None,
        period_start=start, period_end=end,
        consumption=_ar_num(total_m3), unit="m3",
        amount=_ar_num(amount) if amount else None, part=part, split=split,
    )


# ---------------------------------------------------------- Edesur (electricidad)
def parse_edesur(t):
    account = _find(r"cliente es\s+(\d+)", t) or _find(r"Nro de cliente \S+\s+(\d+)", t)
    if re.search(r"Nota de (D.bito|Cr.dito)\s*A", t):
        nota = _find(r"(Nota de (?:D.bito|Cr.dito))", t)
        raise InformativeNote(f"{nota} de Edesur: es un ajuste informativo de una factura anterior, no suma consumo.")
    kwh = re.findall(r"([\d,]+\.\d{3})\s+KWH", t)
    if not kwh:
        raise ParseError("No se encontraron consumos en KWH en la factura de Edesur")
    dates = re.findall(r"Estados al\D{0,40}?(\d\d/\d\d/\d{4})", t)
    if len(dates) < 2:
        raise ParseError("No se encontró el período de lectura en la factura de Edesur")
    d = sorted(_date(x) for x in dates[:2])
    amount = _find(r"Total a pagar hasta\s+\S+\s*\(\*\)\s*\$\s*([\d,\.]+)", t) or _find(r"Total a Pagar Hasta el \S+\s+([\d,\.]+)", t)
    issue = _find(r"Capital Federal (\d\d/\d\d/\d{4})", t)
    return dict(
        service="electricidad", provider="Edesur", account=account,
        holder=_find(r"At\.\s*(.+)", t), invoice_number=_find(r"LSP N.\s*(A \d{4}-\d+)", t),
        issue_date=_date(issue) if issue else None,
        period_start=d[0], period_end=d[1],
        consumption=round(sum(_us_num(k) for k in kwh), 3), unit="kWh",
        amount=_us_num(amount) if amount else None, part=1, split=1,
        tariff=_find(r"Tarifa (T\d)", t),
    )


# -------------------------------------------------------------- MetroGAS (gas)
def parse_metrogas(t):
    account = _find(r"N.mero de cliente\s+(\d+)", t)
    m3 = _find(r"Consumo total en m3\s+([\d\.,]+)", t)
    if m3 is None:
        raise ParseError("No se encontró 'Consumo total en m3' en la factura de MetroGAS")
    per = re.search(r"PERIODO DE LIQUIDACI.N:\s*(\d\d/\d\d/\d{4})\s*A\s*(\d\d/\d\d/\d{4})", t)
    if not per:
        raise ParseError("No se encontró el período de liquidación en la factura de MetroGAS")
    amount = _find(r"TOTAL LIQUIDACION\s+([\d\.,]+)", t)
    issue = _find(r"FECHA DE EMISI.N:\s*(\S+)", t)
    return dict(
        service="gas", provider="MetroGAS", account=str(int(account)) if account else None,
        holder=_find(r"Actividades: \S+\s+\d+\s+(.+)", t), invoice_number=_find(r"(A-\d{4}-\d{8})", t),
        issue_date=_date(issue) if issue else None,
        period_start=_date(per.group(1)), period_end=_date(per.group(2)),
        consumption=_ar_num(m3), unit="m3",
        amount=_ar_num(amount) if amount else None, part=1, split=1,
        tariff=_find(r"TARIFA:\s*(\S+)", t),
    )


def detect_and_parse(path_or_bytes):
    t = pdf_text(path_or_bytes)
    if not t.strip():
        raise ParseError("El PDF no tiene texto (¿es una imagen escaneada?). Cargá los datos a mano.")
    low = t.lower()
    if "aysa" in low or "agua y saneamientos" in low:
        d = parse_aysa(t)
    elif "edesur" in low:
        d = parse_edesur(t)
    elif "metrogas" in low:
        d = parse_metrogas(t)
    else:
        d = None
    if d:
        d["due_date"] = due_date(t)
        return d
    raise ParseError("No se reconoce el proveedor (se esperan facturas de AySA, Edesur o MetroGAS)")
