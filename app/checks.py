"""Controles para que no se cuelen errores de carga y lista de "Para hacer".

- check_invoice / check_guests: detectan números raros comparando con lo que ya
  está cargado (no bloquean: la app pregunta "¿Seguro?" y ella decide).
- todo: todo lo que falta o hay que revisar, en un solo lugar.
"""
import json
from datetime import date, datetime, timedelta
from statistics import median

from .core import MONTHS, MONTHS_SHORT
from .db import SERVICES, get_settings


def fnum(x, d=0):
    """Número con formato argentino: 1.234,5"""
    s = f"{x:,.{d}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fdate(s):
    d = s if isinstance(s, date) else date.fromisoformat(str(s)[:10])
    return d.strftime("%d/%m/%Y")


def _daily(inv):
    a, b = date.fromisoformat(inv["period_start"]), date.fromisoformat(inv["period_end"])
    return (inv["consumption"] or 0) / (inv["split"] or 1) / ((b - a).days + 1)


# ------------------------------------------------------------------ números raros
def check_invoice(con, d, hotel_id, exclude_id=None):
    """Devuelve un texto con lo que parece raro de la factura, o None si está todo bien."""
    out = []
    ps, pe = date.fromisoformat(d["period_start"]), date.fromisoformat(d["period_end"])
    days = (pe - ps).days + 1
    if pe > date.today() + timedelta(days=40):
        out.append(f"La fecha “hasta” ({fdate(pe)}) es del futuro.")
    if days < 10:
        out.append(f"El período es de solo {days} días ({fdate(ps)} al {fdate(pe)}).")
    acc = d.get("account") or d.get("account_number")
    q = "SELECT * FROM invoices WHERE hotel_id=? AND service=? AND deleted_at IS NULL"
    args = [hotel_id, d["service"]]
    if acc:
        q += " AND account_number=?"; args.append(acc)
    if exclude_id:
        q += " AND id<>?"; args.append(exclude_id)
    rows = [dict(r) for r in con.execute(q, args)]
    hist = [_daily(r) for r in rows if (r["consumption"] or 0) > 0]
    zeros = sum(1 for r in rows if not r["consumption"])
    daily = (d["consumption"] or 0) / (d.get("split") or 1) / days
    unit = SERVICES[d["service"]]["unit"]
    if len(hist) >= 4:
        med, mx, mn = median(hist), max(hist), min(hist)
        if daily > 2 * mx and daily > 3 * med:
            out.append(f"El consumo es mucho más alto que en las facturas anteriores: da unos {fnum(daily * 30)} {unit} por mes "
                       f"y lo normal es {fnum(med * 30)} (el máximo hasta ahora fue {fnum(mx * 30)}). ¿Se escribió bien?")
        elif daily == 0 and zeros == 0:
            out.append("El consumo es 0, y en las facturas anteriores nunca fue 0.")
        elif 0 < daily < mn / 3 and daily < med / 5:
            out.append(f"El consumo es mucho más bajo que en las facturas anteriores: da unos {fnum(daily * 30)} {unit} por mes "
                       f"y lo normal es {fnum(med * 30)}. ¿Se escribió bien?")
    return " ".join(out) or None


def check_guests(con, items):
    """Lista de avisos para cantidades de huéspedes fuera de lo habitual."""
    out = []
    names = {r["id"]: r["name"] for r in con.execute("SELECT id,name FROM hotels")}
    for it in items:
        c = it.get("count")
        if c in (None, ""):
            continue
        c = float(c)
        hist = [r["count"] for r in con.execute(
            "SELECT count FROM guests WHERE hotel_id=? AND NOT (year=? AND month=?) AND count>0",
            (it["hotel_id"], it["year"], it["month"]))]
        if len(hist) < 4:
            continue
        med, mx, mn = median(hist), max(hist), min(hist)
        when = f"{MONTHS[it['month'] - 1].lower()} {it['year']}"
        h = names.get(it["hotel_id"], "")
        rng = f"(normalmente entre {fnum(mn)} y {fnum(mx)} por mes)"
        if c > 2 * mx and c > 3 * med:
            out.append(f"{h}: {fnum(c)} huéspedes en {when} es mucho más de lo habitual {rng}.")
        elif c == 0:
            out.append(f"{h}: 0 huéspedes en {when} {rng}.")
        elif c < mn / 2 and c < med / 3:
            out.append(f"{h}: {fnum(c)} huéspedes en {when} es mucho menos de lo habitual {rng}.")
    return out


# ------------------------------------------------------------------ para hacer
def _months_back(today, n):
    """Los n meses completos anteriores a hoy, del más viejo al más nuevo: [(año, mes)]."""
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append((y, m))
    return out[::-1]


def hidden_ids(settings):
    try:
        return set(json.loads(settings.get("todo_hidden") or "[]"))
    except ValueError:
        return set()


def todo(con):
    today = date.today()
    settings = get_settings(con)
    hidden = hidden_ids(settings)
    hotels = con.execute("SELECT id,name FROM hotels ORDER BY sort,id").fetchall()
    items = []

    def add(**kw):
        if kw["id"] not in hidden:
            items.append(kw)

    # --- huéspedes: los últimos 12 meses (desde que hay datos de ese hotel)
    window = _months_back(today, 12)
    for h in hotels:
        have = {(r["year"], r["month"]) for r in con.execute("SELECT year,month FROM guests WHERE hotel_id=?", (h["id"],))}
        if not have:
            add(id=f"g0:{h['id']}", group="huespedes", level="warn", hotel_id=h["id"], hotel=h["name"],
                title=f"Huéspedes de {h['name']}", detail="Todavía no hay huéspedes cargados para este hotel.", hideable=False)
            continue
        first = min(have)
        missing = [(y, m) for (y, m) in window if (y, m) >= first and (y, m) not in have]
        if missing:
            add(id=f"g:{h['id']}:{missing[-1][0]}-{missing[-1][1]}", group="huespedes", level="warn",
                hotel_id=h["id"], hotel=h["name"], year=missing[-1][0], title=f"Huéspedes de {h['name']}",
                detail="Faltan: " + ", ".join(f"{MONTHS_SHORT[m - 1]} {y}" for y, m in missing), hideable=False)

    # --- facturas: por cada cuenta, huecos, tramos que faltan y facturas atrasadas
    since = today - timedelta(days=455)   # últimos ~15 meses
    for h in hotels:
        for svc, meta in SERVICES.items():
            accs = {r["number"] for r in con.execute("SELECT number FROM accounts WHERE hotel_id=? AND service=?", (h["id"], svc))}
            invs = [dict(r) for r in con.execute(
                "SELECT * FROM invoices WHERE hotel_id=? AND service=? AND deleted_at IS NULL", (h["id"], svc))]
            accs |= {i["account_number"] for i in invs if i["account_number"]}
            label = meta["label"].lower()
            base = dict(group="facturas", hotel_id=h["id"], hotel=h["name"], service=svc, hideable=True)
            if not invs and not accs:
                continue
            for acc in sorted(accs) or [None]:
                ai = [i for i in invs if i["account_number"] == acc] if acc else invs
                acc_txt = f" (cuenta {acc})" if acc and len(accs) > 1 else ""
                if not ai:
                    add(id=f"none:{svc}:{acc}", level="warn", title=f"{meta['label']} de {h['name']}{acc_txt}",
                        detail=f"No hay ninguna factura de {label} cargada para la cuenta {acc}.", **base)
                    continue
                periods = {}
                for i in ai:
                    p = periods.setdefault((i["period_start"], i["period_end"]), dict(parts=set(), split=i["split"] or 1))
                    p["parts"].add(i["part"] or 1)
                    p["split"] = max(p["split"], i["split"] or 1)
                last_pe = max(pe for _, pe in periods)
                cad = median([(date.fromisoformat(b) - date.fromisoformat(a)).days + 1 for a, b in periods])
                late = today > date.fromisoformat(last_pe) + timedelta(days=cad + 25)
                for (ps, pe), p in sorted(periods.items()):
                    if late and pe == last_pe:
                        continue   # ya lo cubre el aviso de "debería haber llegado la siguiente"
                    # el último tramo llega un mes después: se reclama recién si ya pasó su fecha
                    if (len(p["parts"]) < p["split"] and date.fromisoformat(pe) >= since
                            and today > date.fromisoformat(pe) + timedelta(days=30 * (p["split"] - 1) + 25)):
                        falta = sorted(set(range(1, p["split"] + 1)) - p["parts"])
                        add(id=f"tramo:{svc}:{acc}:{ps}", level="warn", title=f"{meta['label']} de {h['name']}{acc_txt}",
                            detail=f"Falta el tramo {', '.join(map(str, falta))} de {p['split']} de la factura del {fdate(ps)} al {fdate(pe)}.", **base)
                spans = sorted((date.fromisoformat(a), date.fromisoformat(b)) for a, b in periods)
                reach = None
                for a, b in spans:
                    if reach and (a - reach).days > 7 and a >= since:
                        add(id=f"gap:{svc}:{acc}:{reach.isoformat()}", level="warn", title=f"{meta['label']} de {h['name']}{acc_txt}",
                            detail=f"Falta la factura del {fdate(reach + timedelta(days=1))} al {fdate(a - timedelta(days=1))}.", **base)
                    reach = max(reach, b) if reach else b
                cadence = median([(b - a).days + 1 for a, b in spans])
                if today > reach + timedelta(days=cadence + 25):
                    add(id=f"late:{svc}:{acc}:{reach.isoformat()}", level="warn", title=f"{meta['label']} de {h['name']}{acc_txt}",
                        detail=f"La última factura cargada llega hasta el {fdate(reach)}. Ya debería haber llegado la siguiente.", **base)

    # --- respaldo
    backup = backup_status(settings)
    if backup["level"] == "warn":
        items.insert(0, dict(id="backup", group="respaldo", level="warn", title=backup["title"], detail=backup["detail"], hideable=False))

    inbox = [dict(r) for r in con.execute("SELECT token, filename, kind, message, data, created_at FROM pending ORDER BY created_at")]
    for p in inbox:
        p["data"] = json.loads(p["data"]) if p["data"] else None
    review = [dict(r) for r in con.execute(
        "SELECT i.*, h.name AS hotel FROM invoices i JOIN hotels h ON h.id=i.hotel_id "
        "WHERE i.review IS NOT NULL AND i.deleted_at IS NULL ORDER BY i.period_end DESC")]
    return dict(inbox=inbox, review=review, items=items, hidden=len(hidden),
                count=len(inbox) + len(review) + len(items), backup=backup)


def backup_status(settings):
    """Estado de la copia de seguridad, para mostrar y para avisar si hace falta."""
    now = datetime.now()
    folder = (settings.get("backup_folder") or "").strip()
    last_ext = settings.get("last_external_backup") or ""
    err = settings.get("external_backup_error") or ""
    last_man = settings.get("last_manual_backup") or ""
    if folder:
        if err:
            when, _, msg = err.partition("|")
            if not last_ext or datetime.fromisoformat(last_ext) < now - timedelta(days=3):
                return dict(level="warn", folder=folder, last=last_ext, title="La copia de seguridad automática no se está haciendo",
                            detail=msg + (f" Última copia buena: {fdate(last_ext)}." if last_ext else ""))
        return dict(level="ok", folder=folder, last=last_ext,
                    title="Copia automática activada",
                    detail=f"Se copia sola todos los días en {folder}." + (f" Última copia: {datetime.fromisoformat(last_ext):%d/%m/%Y %H:%M}." if last_ext else " La primera copia se hace en unos minutos."))
    if not last_man or datetime.fromisoformat(last_man) < now - timedelta(days=30):
        return dict(level="warn", folder="", last=last_man, title="Tus datos están guardados solo en esta computadora",
                    detail="Si la compu se rompe, se pierden. Elegí una carpeta de Google Drive, OneDrive o un pendrive en "
                           "Configuración > Respaldo, y la aplicación hace una copia sola todos los días.")
    return dict(level="ok", folder="", last=last_man, title="Respaldo descargado",
                detail=f"Último respaldo descargado el {fdate(last_man)}. Conviene activar la copia automática.")
