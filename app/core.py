"""Cálculos: reparto mensual de facturas, estadísticas y alertas.

Reparto mensual: cada factura cubre un período de lectura (p. ej. 06/06 al 05/07).
Su consumo y su importe se reparten entre los meses calendario en proporción a
los días del período que caen en cada mes. Así una factura bimestral de agua se
distribuye correctamente entre los dos (o tres) meses que abarca.

Cobertura: fracción de días del mes cubiertos por alguna factura. Un mes con
cobertura < 97 % se marca como "parcial" (falta cargar la próxima factura) y no
se usa para mínimos, máximos ni promedios.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta

from .db import SERVICES, get_settings, reference

MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
          "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
MONTHS_SHORT = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
COMPLETE = 0.97


def _d(s):
    return date.fromisoformat(s)


def allocate(invoices):
    """-> {(year, month): {'cons','amount','days': set(ordinal days covered)}}"""
    out = defaultdict(lambda: {"cons": 0.0, "amount": 0.0, "days": defaultdict(float)})
    for inv in invoices:
        start, end = _d(inv["period_start"]), _d(inv["period_end"])
        if end < start:
            start, end = end, start
        total_days = (end - start).days + 1
        cons = (inv["consumption"] or 0) / (inv["split"] or 1)
        amt = inv["amount"] or 0
        day = start
        while day <= end:
            key = (day.year, day.month)
            last = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
            seg_end = min(last, end)
            n = (seg_end - day).days + 1
            out[key]["cons"] += cons * n / total_days
            out[key]["amount"] += amt * n / total_days
            # Cobertura por día: una factura en 2 tramos cubre medio día por tramo, así
            # un mes con un solo tramo cargado queda como "parcial".
            for o in range(day.toordinal(), seg_end.toordinal() + 1):
                out[key]["days"][o] += 1 / (inv["split"] or 1)
            day = seg_end + timedelta(days=1)
    return out


def _accounts_coverage(invoices):
    """Cobertura por mes considerando cada cuenta por separado (si un hotel tiene 2
    medidores de gas, el mes está completo solo si ambos están cubiertos)."""
    by_acc = defaultdict(list)
    for inv in invoices:
        by_acc[inv["account_number"] or "manual"].append(inv)
    cov = defaultdict(list)
    for acc_invs in by_acc.values():
        for key, v in allocate(acc_invs).items():
            ndays = calendar.monthrange(*key)[1]
            cov[key].append(sum(min(1.0, x) for x in v["days"].values()) / ndays)
    accounts = len(by_acc)
    return {k: (sum(v) / accounts if accounts else 0) for k, v in cov.items()}


def load(con, hotel_id, service):
    invs = [dict(r) for r in con.execute(
        "SELECT * FROM invoices WHERE hotel_id=? AND service=? AND deleted_at IS NULL", (hotel_id, service))]
    guests = {(r["year"], r["month"]): r["count"] for r in con.execute(
        "SELECT year,month,count FROM guests WHERE hotel_id=?", (hotel_id,))}
    return invs, guests


def monthly(con, hotel_id, service, year):
    """Tabla de 12 meses con consumo, costo, huéspedes, por huésped y alertas."""
    settings = get_settings(con)
    ref = reference(settings, service)
    invs, guests = load(con, hotel_id, service)
    # Solo las facturas que tocan este año (y la cobertura de cada cuenta)
    alloc = allocate(invs)
    cov = _accounts_coverage(invs)
    rows = []
    for m in range(1, 13):
        a = alloc.get((year, m))
        g = guests.get((year, m))
        cons = round(a["cons"], 2) if a else None
        c = cov.get((year, m), 0)
        complete = c >= COMPLETE
        per = (cons / g) if (cons is not None and g) else None
        status = None
        if per is not None and ref:
            status = "alto" if per > ref else "ok"
        rows.append(dict(
            month=m, label=MONTHS[m - 1], short=MONTHS_SHORT[m - 1],
            consumption=cons, amount=round(a["amount"], 2) if a else None,
            guests=g, per_guest=round(per, 4) if per is not None else None,
            coverage=round(c, 3), complete=complete, status=status,
        ))
    return rows, ref


def summarize(rows, ref):
    full = [r for r in rows if r["complete"] and r["consumption"] is not None]
    with_g = [r for r in full if r["guests"]]
    total = sum(r["consumption"] or 0 for r in rows)
    cost = sum(r["amount"] or 0 for r in rows)
    guests_total = sum(r["guests"] or 0 for r in rows)
    s = dict(
        total=round(total, 2) if any(r["consumption"] is not None for r in rows) else None,
        cost=round(cost, 2) if cost else None,
        guests=guests_total or None,
        months_complete=len(full),
        months_partial=sum(1 for r in rows if r["consumption"] is not None and not r["complete"]),
        avg=None, min=None, max=None, min_month=None, max_month=None,
        per_guest=None, per_guest_avg=None, per_guest_min=None, per_guest_max=None,
        per_guest_min_month=None, per_guest_max_month=None,
        cost_per_guest=None, alerts=0, reference=ref,
    )
    if full:
        vals = [r["consumption"] for r in full]
        s["avg"] = round(sum(vals) / len(vals), 2)
        mn = min(full, key=lambda r: r["consumption"])
        mx = max(full, key=lambda r: r["consumption"])
        s.update(min=mn["consumption"], min_month=mn["label"], max=mx["consumption"], max_month=mx["label"])
    if with_g:
        cons = sum(r["consumption"] for r in with_g)
        gs = sum(r["guests"] for r in with_g)
        s["per_guest"] = round(cons / gs, 4)
        pg = [r["per_guest"] for r in with_g]
        s["per_guest_avg"] = round(sum(pg) / len(pg), 4)
        mn = min(with_g, key=lambda r: r["per_guest"])
        mx = max(with_g, key=lambda r: r["per_guest"])
        s.update(per_guest_min=mn["per_guest"], per_guest_min_month=mn["label"],
                 per_guest_max=mx["per_guest"], per_guest_max_month=mx["label"])
        amt = sum(r["amount"] or 0 for r in with_g)
        s["cost_per_guest"] = round(amt / gs, 2) if amt else None
        if ref:
            s["alerts"] = sum(1 for r in with_g if r["per_guest"] > ref)
    return s


def _pct_change(cur, old, limit=150):
    """Variación %; si es mayor al límite no se considera comparable (p. ej. cambio de medidor)."""
    if not old:
        return None
    ch = (cur - old) / old * 100
    return round(ch, 1) if abs(ch) <= limit else None


def compare_prev(con, hotel_id, service, year, rows):
    """Variación contra el año anterior usando solo los meses completos en ambos años."""
    prev, _ = monthly(con, hotel_id, service, year - 1)
    return compare_rows(rows, prev), prev


def compare_rows(rows, prev):
    """Variación entre dos listas de meses alineadas (mismos meses)."""
    pairs = [(a, b) for a, b in zip(rows, prev) if a["complete"] and b["complete"]
             and a["consumption"] is not None and b["consumption"] is not None]
    if not pairs:
        return None
    cur = sum(a["consumption"] for a, _ in pairs)
    old = sum(b["consumption"] for _, b in pairs)
    res = dict(months=len(pairs), current=round(cur, 2), previous=round(old, 2),
               change=_pct_change(cur, old))
    gp = [(a, b) for a, b in pairs if a["guests"] and b["guests"]]
    if gp:
        pc = sum(a["consumption"] for a, _ in gp) / sum(a["guests"] for a, _ in gp)
        po = sum(b["consumption"] for _, b in gp) / sum(b["guests"] for _, b in gp)
        res["per_guest_current"] = round(pc, 4)
        res["per_guest_previous"] = round(po, 4)
        res["per_guest_change"] = _pct_change(pc, po)
    return res


def hotel_year(con, hotel_id, year):
    """Todo lo necesario para un informe de un hotel en un año."""
    out = {}
    for svc in SERVICES:
        rows, ref = monthly(con, hotel_id, svc, year)
        cmp_, prev = compare_prev(con, hotel_id, svc, year, rows)
        out[svc] = dict(rows=rows, summary=summarize(rows, ref), compare=cmp_, prev_rows=prev, reference=ref)
    return out


def years_available(con):
    ys = set()
    for r in con.execute("SELECT period_start, period_end FROM invoices WHERE deleted_at IS NULL"):
        ys.add(int(r[0][:4]))
        ys.add(int(r[1][:4]))
    for r in con.execute("SELECT DISTINCT year FROM guests"):
        ys.add(r[0])
    ys.add(date.today().year)
    return sorted(ys, reverse=True)


def pending(con, year):
    """Recordatorios: meses pasados sin huéspedes o sin factura cargada."""
    today = date.today()
    last_month = 12 if year < today.year else today.month - 1
    items = []
    hotels = con.execute("SELECT id,name FROM hotels ORDER BY sort").fetchall()
    for h in hotels:
        _, guests = load(con, h["id"], "agua")
        missing_g = [MONTHS_SHORT[m - 1] for m in range(1, last_month + 1) if not guests.get((year, m))]
        if missing_g:
            items.append(dict(hotel=h["name"], kind="huéspedes", level="warn", detail="faltan huéspedes de: " + ", ".join(missing_g)))
        for svc in SERVICES:
            invs, _ = load(con, h["id"], svc)
            if not invs:
                items.append(dict(hotel=h["name"], kind=SERVICES[svc]["label"].lower(), level="warn", detail="no hay facturas cargadas"))
                continue
            cov = _accounts_coverage(invs)
            # Huecos: meses incompletos anteriores al último período cargado (falta una factura
            # intermedia). Los meses posteriores solo esperan la próxima factura.
            last_start = date.fromisoformat(min(max(i["period_start"] for i in invs if (i["account_number"] or "") == a)
                                                for a in {(i["account_number"] or "") for i in invs}))
            last_end = max(i["period_end"] for i in invs)
            gaps = [MONTHS_SHORT[m - 1] for m in range(1, last_month + 1)
                    if cov.get((year, m), 0) < COMPLETE and (year, m) < (last_start.year, last_start.month)]
            if gaps:
                items.append(dict(hotel=h["name"], kind=SERVICES[svc]["label"].lower(), level="warn",
                                  detail=f"faltan facturas de: {', '.join(gaps)}"))
            else:
                items.append(dict(hotel=h["name"], kind=SERVICES[svc]["label"].lower(), level="info",
                                  detail=f"al día hasta {date.fromisoformat(last_end):%d/%m/%Y}"))
    return items
