"""Servidor local: API + interfaz web (se abre en el navegador de la compu)."""
import hashlib
import io
import json
import logging
import os
import sqlite3
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from datetime import date, datetime, timedelta

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from . import checks, core, reports
from .db import (DATA_DIR, DB_PATH, FILES_DIR, PENDING_DIR, SERVICES, auto_backup, backfill_due_dates, backup_places, check_db, db,
                 external_backup, get_settings, init, purge_invoice, set_setting)
from .parsers import InformativeNote, ParseError, detect_and_parse

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024   # respaldos grandes (con todos los PDF)
MAX_PDF = 60 * 1024 * 1024
TMP_DIR = os.path.join(DATA_DIR, "tmp")



def need_confirm(msg):
    """Corta el pedido y le pide a la interfaz que pregunte "¿Seguro?" antes de guardar."""
    resp = jsonify(confirm=msg)
    resp.status_code = 422
    abort(resp)


def safe_name(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\-. ]+", "_", s).strip() or "archivo"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


@app.errorhandler(Exception)
def handle_err(e):
    if isinstance(e, HTTPException):
        if e.response is not None:
            return e.response
        if e.code == 413:
            return jsonify(error="El archivo es demasiado grande (máximo 60 MB)."), 413
        return jsonify(error=e.description or str(e)), e.code
    if isinstance(e, sqlite3.IntegrityError):
        return jsonify(error="Ya existe una factura con esos mismos datos (misma cuenta, número y tramo)."), 409
    if isinstance(e, sqlite3.OperationalError) and "locked" in str(e):
        return jsonify(error="La base de datos está ocupada; probá de nuevo en unos segundos."), 503
    logging.exception("Error no controlado en %s %s", request.method, request.path)
    return jsonify(error="Ocurrió un error inesperado. Quedó registrado en datos/errores.log. Detalle: " + str(e)[:200]), 500


@app.after_request
def no_cache(resp):
    # que el navegador siempre tome la versión nueva de la interfaz
    if request.path == "/" or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/clientlog")
def client_log():
    d = request.get_json(silent=True) or {}
    logging.error("Error en la interfaz: %s", str(d)[:2000])
    return jsonify(ok=True)


# ------------------------------------------------------------------ validación
def _num(v, field, minimum=None, maximum=1e12, allow_none=False):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if allow_none:
            return None
        abort(400, f"Falta el dato: {field}")
    try:
        x = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        abort(400, f"El valor de {field} no es un número válido: {v}")
    if x != x or (minimum is not None and x < minimum) or abs(x) > maximum:
        abort(400, f"El valor de {field} está fuera de rango: {v}")
    return x


def _date(v, field):
    try:
        d = date.fromisoformat(str(v).strip()[:10])
    except (TypeError, ValueError):
        abort(400, f"La fecha '{field}' no es válida: {v}")
    if not 2000 <= d.year <= 2100:
        abort(400, f"La fecha '{field}' está fuera de rango: {v}")
    return d


def _hotel_exists(con, hid):
    try:
        hid = int(hid)
    except (TypeError, ValueError):
        abort(400, "Hotel inválido")
    if not con.execute("SELECT 1 FROM hotels WHERE id=?", (hid,)).fetchone():
        abort(400, "El hotel elegido no existe")
    return hid


def _arg_int(name):
    try:
        return int(request.args.get(name, ""))
    except ValueError:
        abort(400, f"Parámetro inválido: {name}")


def clean_invoice(d):
    """Normaliza y valida los datos de una factura. Corta con un mensaje claro si algo está mal."""
    d = dict(d)
    if d.get("service") not in SERVICES:
        abort(400, "Servicio inválido (tiene que ser agua, electricidad o gas)")
    a, b = _date(d.get("period_start"), "desde"), _date(d.get("period_end"), "hasta")
    if b < a:
        a, b = b, a
    if (b - a).days > 400:
        abort(400, "El período de la factura es demasiado largo (más de 400 días). Revisá las fechas.")
    d["period_start"], d["period_end"] = a.isoformat(), b.isoformat()
    d["consumption"] = _num(d.get("consumption"), "consumo", minimum=0, maximum=1e9)
    d["amount"] = _num(d.get("amount"), "importe", allow_none=True)
    d["part"] = int(_num(d.get("part") or 1, "tramo", minimum=1, maximum=12))
    d["split"] = int(_num(d.get("split") or 1, "cantidad de tramos", minimum=1, maximum=12))
    if d["part"] > d["split"]:
        abort(400, "El número de tramo no puede ser mayor que la cantidad de tramos")
    for k in ("issue_date", "due_date"):
        if d.get(k):
            try:
                d[k] = date.fromisoformat(str(d[k])[:10]).isoformat()
            except ValueError:
                d[k] = None
        else:
            d[k] = None
    for k in ("account", "account_number", "invoice_number", "notes", "holder"):
        if d.get(k) is not None:
            d[k] = str(d[k]).strip()[:200] or None
    return d


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


# ------------------------------------------------------------------ meta
@app.get("/api/meta")
def meta():
    with db() as con:
        hotels = [dict(r) for r in con.execute("SELECT * FROM hotels ORDER BY sort,id")]
        accounts = [dict(r) for r in con.execute(
            "SELECT a.*, h.name AS hotel FROM accounts a JOIN hotels h ON h.id=a.hotel_id ORDER BY a.service, h.sort")]
        return jsonify(hotels=hotels, accounts=accounts, services=SERVICES,
                       settings=get_settings(con), years=core.years_available(con),
                       months=core.MONTHS, months_short=core.MONTHS_SHORT,
                       counts=dict(invoices=con.execute("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL").fetchone()[0],
                                   trash=con.execute("SELECT COUNT(*) FROM invoices WHERE deleted_at IS NOT NULL").fetchone()[0]),
                       data_dir=DATA_DIR)


SETTING_KEYS = {"ref_agua", "ref_electricidad", "ref_gas", "company", "user_name", "backup_folder"}


@app.put("/api/settings")
def put_settings():
    data = request.get_json(silent=True) or {}
    data = {k: str(v if v is not None else "").strip()[:400] for k, v in data.items() if k in SETTING_KEYS}
    for k in ("ref_agua", "ref_electricidad", "ref_gas"):
        if data.get(k):
            data[k] = str(_num(data[k], "referencia", minimum=0, maximum=10000))
            if float(data[k]) == 0:
                abort(400, "La referencia no puede ser 0 (dejala vacía si no querés usarla)")
    if data.get("backup_folder"):
        folder = os.path.abspath(data["backup_folder"])
        if not os.path.isdir(folder):
            abort(400, f"No existe la carpeta {folder}")
        if os.path.normcase(folder).startswith(os.path.normcase(os.path.abspath(DATA_DIR))):
            abort(400, "La copia tiene que ir a otra carpeta (Drive, OneDrive o pendrive), no dentro de los datos de la aplicación")
        try:
            test = os.path.join(folder, ".prueba_consumos")
            with open(test, "w") as fh:
                fh.write("ok")
            os.remove(test)
        except OSError:
            abort(400, f"No se puede escribir en {folder}. Elegí otra carpeta.")
        data["backup_folder"] = folder
    with db() as con:
        for k, v in data.items():
            set_setting(con, k, v)
        if "backup_folder" in data:
            set_setting(con, "external_backup_error", "")
            set_setting(con, "last_external_backup", "")
    if data.get("backup_folder"):
        threading.Thread(target=external_backup, kwargs=dict(force=True), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/hotels")
def add_hotel():
    d = request.get_json(silent=True) or {}
    d["name"] = str(d.get("name") or "").strip()[:80]
    if not d["name"]:
        abort(400, "Escribí el nombre del hotel")
    with db() as con:
        cur = con.execute("INSERT INTO hotels(name,legal_name,address,sort) VALUES(?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM hotels))",
                          (d["name"], d.get("legal_name", ""), d.get("address", "")))
        return jsonify(id=cur.lastrowid)


@app.put("/api/hotels/<int:hid>")
def edit_hotel(hid):
    d = request.get_json(silent=True) or {}
    d["name"] = str(d.get("name") or "").strip()[:80]
    if not d["name"]:
        abort(400, "El nombre del hotel no puede quedar vacío")
    with db() as con:
        _hotel_exists(con, hid)
        con.execute("UPDATE hotels SET name=?, legal_name=?, address=? WHERE id=?",
                    (d["name"], d.get("legal_name", ""), d.get("address", ""), hid))
    return jsonify(ok=True)


@app.post("/api/accounts")
def add_account():
    d = request.get_json(silent=True) or {}
    if d.get("service") not in SERVICES or not str(d.get("number") or "").strip():
        abort(400, "Completá servicio y número de cuenta")
    with db() as con:
        _hotel_exists(con, d.get("hotel_id"))
        con.execute("INSERT INTO accounts(service,number,hotel_id,label) VALUES(?,?,?,?) "
                    "ON CONFLICT(service,number) DO UPDATE SET hotel_id=excluded.hotel_id, label=excluded.label",
                    (d["service"], str(d["number"]).strip(), int(d["hotel_id"]), d.get("label", "")))
    return jsonify(ok=True)


@app.delete("/api/accounts/<int:aid>")
def del_account(aid):
    with db() as con:
        con.execute("DELETE FROM accounts WHERE id=?", (aid,))
    return jsonify(ok=True)


# ------------------------------------------------------------------ facturas
def _store_file(con, hotel_id, service, filename, content):
    hotel = con.execute("SELECT name FROM hotels WHERE id=?", (hotel_id,)).fetchone()["name"]
    folder = os.path.join(FILES_DIR, safe_name(hotel), SERVICES[service]["label"])
    os.makedirs(folder, exist_ok=True)
    name = safe_name(filename)
    path = os.path.join(folder, name)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if hashlib.sha1(f.read()).digest() != hashlib.sha1(content).digest():
                base, ext = os.path.splitext(name)
                path = os.path.join(folder, f"{base}_{uuid.uuid4().hex[:6]}{ext}")
    with open(path, "wb") as f:
        f.write(content)
    return os.path.relpath(path, FILES_DIR)


def _find_duplicate(con, d):
    if d.get("invoice_number"):
        r = con.execute("SELECT id FROM invoices WHERE deleted_at IS NULL AND service=? AND account_number=? AND invoice_number=? AND part=?",
                        (d["service"], d.get("account"), d["invoice_number"], d.get("part", 1))).fetchone()
        if r:
            return r["id"]
    r = con.execute("SELECT id FROM invoices WHERE deleted_at IS NULL AND service=? AND account_number IS ? AND period_start=? AND period_end=? AND part=? AND split=?",
                    (d["service"], d.get("account"), d["period_start"], d["period_end"], d.get("part", 1), d.get("split", 1))).fetchone()
    return r["id"] if r else None


def _insert(con, d, hotel_id, filename, content, source="pdf", review=None):
    d = clean_invoice(d)
    hotel_id = _hotel_exists(con, hotel_id)
    # si la misma factura está en la papelera, se descarta la vieja para guardar esta
    if d.get("invoice_number"):
        for r in con.execute("SELECT id FROM invoices WHERE deleted_at IS NOT NULL AND service=? AND account_number IS ? "
                             "AND invoice_number=? AND part=?", (d["service"], d.get("account"), d["invoice_number"],
                                                                 int(d.get("part") or 1))).fetchall():
            purge_invoice(con, r["id"])
    stored = _store_file(con, hotel_id, d["service"], filename, content) if content else None
    cur = con.execute(
        """INSERT INTO invoices(hotel_id,service,provider,account_number,invoice_number,part,split,issue_date,
           period_start,period_end,consumption,unit,amount,holder,file_name,file_stored,source,notes,review,due_date,paid_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (hotel_id, d["service"], d.get("provider") or SERVICES[d["service"]]["provider"], d.get("account"),
         d.get("invoice_number"), int(d.get("part") or 1), int(d.get("split") or 1), d.get("issue_date"),
         d["period_start"], d["period_end"], float(d["consumption"]), d.get("unit") or SERVICES[d["service"]]["unit"],
         float(d["amount"]) if d.get("amount") not in (None, "") else None, d.get("holder"),
         filename, stored, source, d.get("notes"), review, d.get("due_date"),
         d["due_date"] if d.get("due_date") and d["due_date"] < (date.today() - timedelta(days=45)).isoformat() else None))
    return cur.lastrowid


def _describe(d):
    unit = SERVICES[d["service"]]["unit"]
    cons = d["consumption"] / (d.get("split") or 1)
    tramo = f" (tramo {d['part']} de {d['split']})" if (d.get("split") or 1) > 1 else ""
    return f"{SERVICES[d['service']]['label']} · {d['period_start']} → {d['period_end']} · {cons:,.0f} {unit}{tramo}"


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f:
        abort(400, "No se recibió ningún archivo")
    content = f.read(MAX_PDF + 1)
    if len(content) > MAX_PDF:
        abort(413)
    filename = (f.filename or "factura.pdf").split("/")[-1].split("\\")[-1]
    if not filename.lower().endswith(".pdf"):
        return jsonify(status="ignorado", file=filename, message="No es un PDF")
    try:
        d = detect_and_parse(content)
    except InformativeNote as e:
        return jsonify(status="nota", file=filename, message=str(e))
    except ParseError as e:
        token = _hold(filename, content, None, "error", str(e))
        return jsonify(status="error", file=filename, token=token, message=str(e))
    except Exception as e:  # PDF dañado, etc.
        msg = f"No se pudo leer el PDF: {e}"
        token = _hold(filename, content, None, "error", msg)
        return jsonify(status="error", file=filename, token=token, message=msg)

    with db() as con:
        dup = _find_duplicate(con, d)
        if dup:
            return jsonify(status="duplicada", file=filename, data=d, message="Ya estaba cargada: " + _describe(d))
        acc = con.execute("SELECT a.hotel_id, h.name FROM accounts a JOIN hotels h ON h.id=a.hotel_id WHERE a.service=? AND a.number=?",
                          (d["service"], d.get("account"))).fetchone()
        if not acc:
            msg = f"Cuenta {d.get('account')} ({d.get('holder') or 'sin titular'}) no está asignada a ningún hotel"
            token = _hold(filename, content, d, "cuenta_desconocida", msg)
            return jsonify(status="cuenta_desconocida", file=filename, token=token, data=d, message=msg)
        review = checks.check_invoice(con, clean_invoice(d), acc["hotel_id"])
        iid = _insert(con, d, acc["hotel_id"], filename, content, review=review)
        return jsonify(status="revisar" if review else "guardada", file=filename, id=iid, hotel=acc["name"], data=d,
                       message=_describe(d) + (" — " + review if review else ""))


def _hold(filename, content, data, kind, message):
    """Guarda la factura en la bandeja de pendientes (sobrevive si se cierra la app)."""
    os.makedirs(PENDING_DIR, exist_ok=True)
    sha = hashlib.sha1(content).hexdigest()
    with db() as con:
        old = con.execute("SELECT token FROM pending WHERE sha1=?", (sha,)).fetchone()
        if old:
            return old["token"]
        token = uuid.uuid4().hex
        path = os.path.join(PENDING_DIR, token + ".pdf")
        with open(path, "wb") as fh:
            fh.write(content)
        con.execute("INSERT INTO pending(token,filename,path,sha1,data,kind,message) VALUES(?,?,?,?,?,?,?)",
                    (token, filename, os.path.relpath(path, DATA_DIR), sha, json.dumps(data) if data else None, kind, message))
    return token


def _pending(con, token):
    p = con.execute("SELECT * FROM pending WHERE token=?", (token,)).fetchone()
    if not p:
        abort(404, "Esa factura pendiente ya no está (puede que ya se haya guardado o descartado).")
    p = dict(p)
    p["abs"] = os.path.join(DATA_DIR, p["path"])
    p["data"] = json.loads(p["data"]) if p["data"] else None
    return p


def _drop_pending(con, p):
    con.execute("DELETE FROM pending WHERE token=?", (p["token"],))
    try:
        os.remove(p["abs"])
    except OSError:
        pass


@app.get("/api/pending/<token>/pdf")
def pending_pdf(token):
    with db() as con:
        p = _pending(con, token)
    if not os.path.exists(p["abs"]):
        abort(404, "No se encuentra el PDF")
    return send_file(p["abs"], mimetype="application/pdf")


@app.post("/api/pending/<token>")
def commit_pending(token):
    body = request.get_json(silent=True) or {}
    with db() as con:
        p = _pending(con, token)
        d = dict(p["data"] or {})
        d.update({k: v for k, v in (body.get("data") or {}).items() if v not in (None, "")})
        d = clean_invoice(d)
        hotel_id = _hotel_exists(con, body.get("hotel_id"))
        if _find_duplicate(con, d):
            _drop_pending(con, p)
            con.commit()
            abort(409, "Esa factura ya estaba cargada. La saqué de pendientes.")
        if not body.get("confirm"):
            warn = checks.check_invoice(con, d, hotel_id)
            if warn:
                need_confirm(warn)
        if body.get("remember") and d.get("account"):
            con.execute("INSERT INTO accounts(service,number,hotel_id) VALUES(?,?,?) "
                        "ON CONFLICT(service,number) DO UPDATE SET hotel_id=excluded.hotel_id",
                        (d["service"], d["account"], hotel_id))
        with open(p["abs"], "rb") as fh:
            content = fh.read()
        iid = _insert(con, d, hotel_id, p["filename"], content, source="pdf" if p["data"] else "manual")
        _drop_pending(con, p)
    return jsonify(ok=True, id=iid)


@app.delete("/api/pending/<token>")
def discard_pending(token):
    with db() as con:
        _drop_pending(con, _pending(con, token))
    return jsonify(ok=True)


@app.get("/api/invoices")
def list_invoices():
    q = "SELECT i.*, h.name AS hotel FROM invoices i JOIN hotels h ON h.id=i.hotel_id WHERE i.deleted_at IS NULL"
    args = []
    if request.args.get("hotel"):
        q += " AND i.hotel_id=?"; args.append(int(request.args["hotel"]))
    if request.args.get("service"):
        q += " AND i.service=?"; args.append(request.args["service"])
    if request.args.get("year"):
        y = request.args["year"]
        q += " AND (substr(i.period_start,1,4)=? OR substr(i.period_end,1,4)=?)"; args += [y, y]
    q += " ORDER BY i.period_end DESC, h.sort, i.service"
    with db() as con:
        return jsonify([dict(r) for r in con.execute(q, args)])


@app.post("/api/invoices")
def create_invoice():
    d = request.get_json(silent=True) or {}
    with db() as con:
        c = clean_invoice(d)
        hid = _hotel_exists(con, d.get("hotel_id"))
        if _find_duplicate(con, c):
            abort(409, "Esa factura ya está cargada (mismo período y cuenta).")
        if not d.get("confirm"):
            warn = checks.check_invoice(con, c, hid)
            if warn:
                need_confirm(warn)
        iid = _insert(con, d, hid, None, None, source="manual")
    return jsonify(id=iid)


@app.put("/api/invoices/<int:iid>")
def edit_invoice(iid):
    body = request.get_json(silent=True) or {}
    fields = ["hotel_id", "service", "account_number", "invoice_number", "part", "split", "issue_date",
              "period_start", "period_end", "consumption", "amount", "notes", "due_date"]
    with db() as con:
        cur = con.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        if not cur:
            abort(404, "La factura ya no existe")
        d = dict(cur)
        d.update({f: body[f] for f in fields if f in body})
        d = clean_invoice(d)
        d["hotel_id"] = _hotel_exists(con, d["hotel_id"])
        d["unit"] = SERVICES[d["service"]]["unit"] if d["service"] != cur["service"] else cur["unit"]
        if not body.get("confirm"):
            warn = checks.check_invoice(con, d, d["hotel_id"], exclude_id=iid)
            if warn:
                need_confirm(warn)
        d["review"] = None   # la revisó y la guardó: ya no queda marcada
        sets = fields + ["unit", "review"]
        con.execute(f"UPDATE invoices SET {', '.join(f + '=?' for f in sets)} WHERE id=?",
                    [d.get(f) for f in sets] + [iid])
    return jsonify(ok=True)


@app.delete("/api/invoices/<int:iid>")
def delete_invoice(iid):
    """No se borra: va a la papelera (se puede recuperar durante 90 días)."""
    with db() as con:
        con.execute("UPDATE invoices SET deleted_at=datetime('now','localtime') WHERE id=?", (iid,))
    return jsonify(ok=True)


@app.post("/api/invoices/<int:iid>/paid")
def invoice_paid(iid):
    paid = bool((request.get_json(silent=True) or {}).get("paid", True))
    with db() as con:
        con.execute("UPDATE invoices SET paid_at=? WHERE id=?",
                    (datetime.now().isoformat(timespec="minutes") if paid else None, iid))
    return jsonify(ok=True)


@app.get("/api/due")
def due():
    """Facturas sin pagar (con vencimiento) y las pagadas en los últimos 45 días."""
    since = (date.today() - timedelta(days=45)).isoformat()
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT i.*, h.name AS hotel FROM invoices i JOIN hotels h ON h.id=i.hotel_id "
            "WHERE i.deleted_at IS NULL AND i.due_date IS NOT NULL AND (i.paid_at IS NULL OR i.paid_at >= ?) "
            "ORDER BY (i.paid_at IS NOT NULL), i.due_date", (since,))]
    unpaid = [r for r in rows if not r["paid_at"]]
    return jsonify(unpaid=unpaid, paid=[r for r in rows if r["paid_at"]],
                   total=round(sum(r["amount"] or 0 for r in unpaid), 2),
                   overdue=sum(1 for r in unpaid if r["due_date"] < date.today().isoformat()))


# ------------------------------------------------------------------ alertas (las crea ella)
def _clean_reminder(con, d):
    title = str(d.get("title") or "").strip()[:200]
    if not title:
        abort(400, "Poné un título para la alerta")
    hid = d.get("hotel_id")
    hid = _hotel_exists(con, hid) if hid not in (None, "", 0, "0") else None
    due_ = _date(d.get("due_date"), "fecha límite").isoformat()
    days = int(_num(d.get("notify_days", 3), "días de aviso", minimum=2, maximum=7))
    notes = str(d.get("notes") or "").strip()[:4000] or None
    return title, hid, due_, days, notes


@app.get("/api/reminders")
def reminders():
    """Alertas pendientes (por fecha) y las resueltas en los últimos 60 días."""
    since = (date.today() - timedelta(days=60)).isoformat()
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT r.*, h.name AS hotel FROM reminders r LEFT JOIN hotels h ON h.id=r.hotel_id "
            "WHERE r.done_at IS NULL OR r.done_at >= ? ORDER BY (r.done_at IS NOT NULL), r.due_date, r.id", (since,))]
    today = date.today()
    for r in rows:
        r["days_left"] = (date.fromisoformat(r["due_date"]) - today).days
        r["active"] = not r["done_at"] and r["days_left"] <= (r["notify_days"] or 0)
    return jsonify(open=[r for r in rows if not r["done_at"]], done=[r for r in rows if r["done_at"]],
                   active=sum(1 for r in rows if r["active"]),
                   overdue=sum(1 for r in rows if not r["done_at"] and r["days_left"] < 0))


@app.post("/api/reminders")
def create_reminder():
    with db() as con:
        cur = con.execute("INSERT INTO reminders(title,hotel_id,due_date,notify_days,notes) VALUES(?,?,?,?,?)",
                          _clean_reminder(con, request.get_json(silent=True) or {}))
        return jsonify(ok=True, id=cur.lastrowid)


@app.put("/api/reminders/<int:rid>")
def edit_reminder(rid):
    with db() as con:
        con.execute("UPDATE reminders SET title=?,hotel_id=?,due_date=?,notify_days=?,notes=? WHERE id=?",
                    (*_clean_reminder(con, request.get_json(silent=True) or {}), rid))
    return jsonify(ok=True)


@app.post("/api/reminders/<int:rid>/done")
def reminder_done(rid):
    done = bool((request.get_json(silent=True) or {}).get("done", True))
    with db() as con:
        con.execute("UPDATE reminders SET done_at=? WHERE id=?",
                    (datetime.now().isoformat(timespec="minutes") if done else None, rid))
    return jsonify(ok=True)


@app.delete("/api/reminders/<int:rid>")
def delete_reminder(rid):
    with db() as con:
        r = con.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
        con.execute("DELETE FROM reminders WHERE id=?", (rid,))
    return jsonify(ok=True, deleted=dict(r) if r else None)


@app.post("/api/invoices/<int:iid>/ok")
def invoice_ok(iid):
    """Ella revisó el aviso de "número raro" y está bien."""
    with db() as con:
        con.execute("UPDATE invoices SET review=NULL WHERE id=?", (iid,))
    return jsonify(ok=True)


@app.get("/api/trash")
def trash():
    with db() as con:
        return jsonify([dict(r) for r in con.execute(
            "SELECT i.*, h.name AS hotel FROM invoices i JOIN hotels h ON h.id=i.hotel_id "
            "WHERE i.deleted_at IS NOT NULL ORDER BY i.deleted_at DESC")])


@app.post("/api/trash/<int:iid>/restore")
def trash_restore(iid):
    with db() as con:
        r = con.execute("SELECT * FROM invoices WHERE id=? AND deleted_at IS NOT NULL", (iid,)).fetchone()
        if not r:
            abort(404, "Esa factura ya no está en la papelera")
        d = dict(r)
        d["account"] = d["account_number"]
        if _find_duplicate(con, d):
            abort(409, "No se puede recuperar: ya hay otra factura igual cargada.")
        con.execute("UPDATE invoices SET deleted_at=NULL WHERE id=?", (iid,))
    return jsonify(ok=True)


@app.delete("/api/trash/<int:iid>")
def trash_purge(iid):
    with db() as con:
        if con.execute("SELECT 1 FROM invoices WHERE id=? AND deleted_at IS NOT NULL", (iid,)).fetchone():
            purge_invoice(con, iid)
    return jsonify(ok=True)


@app.delete("/api/trash")
def trash_empty():
    with db() as con:
        for r in con.execute("SELECT id FROM invoices WHERE deleted_at IS NOT NULL").fetchall():
            purge_invoice(con, r["id"])
    return jsonify(ok=True)


@app.get("/api/invoices/<int:iid>/pdf")
def invoice_pdf(iid):
    with db() as con:
        r = con.execute("SELECT file_stored FROM invoices WHERE id=?", (iid,)).fetchone()
    if not r or not r["file_stored"]:
        abort(404, "Esta factura no tiene PDF (fue cargada a mano)")
    path = os.path.join(FILES_DIR, r["file_stored"])
    if not os.path.exists(path):
        abort(404, "No se encuentra el archivo PDF de esta factura en la carpeta de datos")
    return send_file(path, mimetype="application/pdf")


# ------------------------------------------------------------------ huéspedes
@app.get("/api/guests")
def get_guests():
    year = _arg_int("year")
    with db() as con:
        out = {}
        for r in con.execute("SELECT hotel_id,month,count FROM guests WHERE year=?", (year,)):
            out.setdefault(str(r["hotel_id"]), {})[str(r["month"])] = r["count"]
    return jsonify(out)


@app.put("/api/guests")
def put_guests():
    d = request.get_json(silent=True)
    confirm = False
    if isinstance(d, dict) and "items" in d:
        confirm, d = bool(d.get("confirm")), d["items"]
    items = d if isinstance(d, list) else [d or {}]
    with db() as con:
        for it in items:
            it["hotel_id"] = _hotel_exists(con, it.get("hotel_id"))
            it["year"] = int(_num(it.get("year"), "año", minimum=2000, maximum=2100))
            it["month"] = int(_num(it.get("month"), "mes", minimum=1, maximum=12))
            if it.get("count") not in (None, ""):
                it["count"] = _num(it["count"], "huéspedes", minimum=0, maximum=1e6)
        if not confirm:
            warns = checks.check_guests(con, items)
            if warns:
                need_confirm("\n".join(warns[:8]) + (f"\n…y {len(warns) - 8} más." if len(warns) > 8 else ""))
        for it in items:
            c = it.get("count")
            if c not in (None, ""):
                c = _num(c, "huéspedes", minimum=0, maximum=1e6)
            if c in (None, ""):
                con.execute("DELETE FROM guests WHERE hotel_id=? AND year=? AND month=?",
                            (int(it["hotel_id"]), int(it["year"]), int(it["month"])))
            else:
                con.execute("INSERT INTO guests(hotel_id,year,month,count) VALUES(?,?,?,?) "
                            "ON CONFLICT(hotel_id,year,month) DO UPDATE SET count=excluded.count",
                            (int(it["hotel_id"]), int(it["year"]), int(it["month"]), int(round(float(c)))))
    return jsonify(ok=True)


MONTH_WORDS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _parse_month(v):
    if isinstance(v, (datetime, date)):
        return v.year, v.month
    s = norm(v)
    y = re.search(r"(20\d\d)", s)
    for i, w in enumerate(MONTH_WORDS):
        if re.search(r"\b" + w, s) and y:
            return int(y.group(1)), i + 1
    m = re.match(r"\s*(\d{1,2})\D+(20\d\d)", s)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None


@app.post("/api/guests/import")
def import_guests():
    """Importa huéspedes desde un Excel con una columna 'Mes' y una columna por hotel."""
    import openpyxl
    f = request.files.get("file")
    if not f:
        abort(400, "No se recibió ningún archivo")
    dry = request.args.get("preview") == "1"
    changes = dict(new=0, changed=0, same=0)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), data_only=True)
    except Exception:
        abort(400, "No se pudo abrir el archivo. Tiene que ser un Excel .xlsx")
    with db() as con:
        hotels = con.execute("SELECT id,name FROM hotels").fetchall()
        keys = {}
        for h in hotels:
            words = [w for w in norm(h["name"]).split() if len(w) > 3 and w not in ("hotel",)]
            keys[h["id"]] = words or norm(h["name"]).split()
        keys_extra = {}
        n = 0
        for ws in wb.worksheets:
            header_row, cols = None, {}
            for row in ws.iter_rows(min_row=1, max_row=30):
                if any(norm(c.value).strip() == "mes" for c in row):
                    header_row = row[0].row
                    for c in row:
                        txt = norm(c.value)
                        txt = keys_extra.get(txt.strip(), txt)
                        for hid, words in keys.items():
                            if any(w in txt for w in words) and "m3" not in txt and "kw" not in txt:
                                cols[c.column] = hid
                    break
            if not header_row or not cols:
                continue
            mes_col = next(c.column for c in ws[header_row] if norm(c.value).strip() == "mes")
            for row in ws.iter_rows(min_row=header_row + 1):
                ym = _parse_month(row[mes_col - 1].value)
                if not ym:
                    continue
                for c in row:
                    if c.column in cols and isinstance(c.value, (int, float)) and c.value > 0:
                        old = con.execute("SELECT count FROM guests WHERE hotel_id=? AND year=? AND month=?",
                                          (cols[c.column], ym[0], ym[1])).fetchone()
                        key = "new" if not old or old["count"] is None else ("same" if old["count"] == int(round(c.value)) else "changed")
                        changes[key] += 1
                        n += 1
                        if dry:
                            continue
                        con.execute("INSERT INTO guests(hotel_id,year,month,count) VALUES(?,?,?,?) "
                                    "ON CONFLICT(hotel_id,year,month) DO UPDATE SET count=excluded.count",
                                    (cols[c.column], ym[0], ym[1], int(round(c.value))))
            break
    if not n:
        abort(400, "No encontré datos. El Excel debe tener una columna 'Mes' y una columna por hotel.")
    return jsonify(ok=True, imported=n, **changes)


# ------------------------------------------------------------------ estadísticas
@app.get("/api/stats")
def stats():
    hid, svc, year = _arg_int("hotel"), request.args.get("service"), _arg_int("year")
    if svc not in SERVICES:
        abort(400, "Servicio inválido")
    with db() as con:
        rows, ref = core.monthly(con, hid, svc, year)
        cmp_, prev = core.compare_prev(con, hid, svc, year, rows)
        return jsonify(rows=rows, prev_rows=prev, summary=core.summarize(rows, ref), compare=cmp_, reference=ref)


@app.get("/api/compare")
def compare():
    svc, year = request.args.get("service"), _arg_int("year")
    if svc not in SERVICES:
        abort(400, "Servicio inválido")
    with db() as con:
        out = []
        for h in con.execute("SELECT id,name FROM hotels ORDER BY sort,id").fetchall():
            rows, ref = core.monthly(con, h["id"], svc, year)
            out.append(dict(hotel_id=h["id"], hotel=h["name"], rows=rows, summary=core.summarize(rows, ref)))
        return jsonify(hotels=out, reference=core.reference(get_settings(con), svc))


@app.get("/api/dashboard")
def dashboard():
    year = _arg_int("year")
    with db() as con:
        cards = []
        alerts = []
        for h in con.execute("SELECT id,name FROM hotels ORDER BY sort,id").fetchall():
            card = dict(hotel_id=h["id"], hotel=h["name"], services={})
            for svc in SERVICES:
                rows, ref = core.monthly(con, h["id"], svc, year)
                s = core.summarize(rows, ref)
                cmp_, _ = core.compare_prev(con, h["id"], svc, year, rows)
                card["services"][svc] = dict(summary=s, compare=cmp_,
                                             spark=[r["per_guest"] if r["complete"] else None for r in rows])
                for r in rows:
                    if r["status"] == "alto" and r["complete"]:
                        alerts.append(dict(hotel=h["name"], service=svc, month=r["label"], per_guest=r["per_guest"], reference=ref))
            card["guests"] = sum(r["count"] or 0 for r in con.execute(
                "SELECT count FROM guests WHERE hotel_id=? AND year=?", (h["id"], year)))
            cards.append(card)
        return jsonify(cards=cards, alerts=alerts, pending=core.pending(con, year))


# ------------------------------------------------------------------ informes y respaldo
def _pdf_pages(data, scale=1.15):
    """Páginas del PDF como imágenes (para la vista previa en miniatura)."""
    import base64
    import pypdfium2 as pdfium
    out = []
    doc = pdfium.PdfDocument(data)
    try:
        for i in range(len(doc)):
            img = doc[i].render(scale=scale).to_pil().convert("RGB")
            b = io.BytesIO()
            img.save(b, "JPEG", quality=82)
            out.append("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode())
    finally:
        doc.close()
    return out


@app.route("/api/report", methods=["GET", "POST"])
def report():
    """Informes. Por POST llegan las opciones elegidas (qué secciones, gráficos, números, texto…).
    Con preview=1 devuelve el PDF para mostrarlo dentro de la aplicación."""
    body = request.get_json(silent=True) or {} if request.method == "POST" else {}
    arg = lambda k, d=None: body.get(k, request.args.get(k, d))  # noqa: E731
    try:
        year = int(arg("year"))
    except (TypeError, ValueError):
        abort(400, "Año inválido")
    fmt = arg("format", "pdf")
    if fmt not in ("pdf", "xlsx", "ambos"):
        abort(400, "Formato inválido")
    hotel = str(arg("hotel", "all") or "all")
    opts = reports.report_options(body.get("opts"))
    preview = bool(body.get("preview"))
    with db() as con:
        hotels = con.execute("SELECT * FROM hotels ORDER BY sort,id").fetchall()
        if hotel != "all":
            hotels = [h for h in hotels if str(h["id"]) == hotel]
            if not hotels:
                abort(404, "El hotel no existe")
        files = []

        def make(name, fn, *args):
            try:
                files.append((name, fn(*args)))
            except ValueError as e:
                abort(400, str(e))
            except Exception:
                logging.exception("Error generando %s", name)
                abort(500, f"No se pudo generar el archivo {name}. El error quedó registrado en datos/errores.log.")

        if preview:
            make("vista.pdf", reports.pdf_report, con, dict(hotels[0]), year, opts)
            return jsonify(pages=_pdf_pages(files[0][1]), gaps=reports.report_gaps(con, hotels[0]["id"], year, opts))
        per = ""
        if (opts["desde"], opts["hasta"]) != (1, 12):
            mm = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
            per = f" ({mm[opts['desde'] - 1]}" + (f"-{mm[opts['hasta'] - 1]})" if opts["hasta"] != opts["desde"] else ")")
        for h in hotels:
            base = f"Informe consumos {safe_name(h['name'])} {year}"
            if fmt in ("pdf", "ambos"):
                make(base + per + ".pdf", reports.pdf_report, con, dict(h), year, opts)
            if fmt in ("xlsx", "ambos"):
                make(base + ".xlsx", reports.excel_report, con, dict(h), year)
        if hotel == "all" and fmt in ("pdf", "ambos") and body.get("comparativo", True):
            make(f"Comparativo hoteles {year}.pdf", reports.pdf_comparison, con, year)
    if len(files) == 1:
        name, data = files[0]
        return send_file(io.BytesIO(data), download_name=name, as_attachment=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    buf.seek(0)
    return send_file(buf, download_name=f"Informes consumos {year}.zip", as_attachment=True)


@app.get("/api/todo")
def todo():
    with db() as con:
        return jsonify(checks.todo(con))


@app.post("/api/todo/hide")
def todo_hide():
    item = str((request.get_json(silent=True) or {}).get("id") or "")[:200]
    with db() as con:
        ids = checks.hidden_ids(get_settings(con))
        if item:
            ids.add(item)
        set_setting(con, "todo_hidden", json.dumps(sorted(ids)))
    return jsonify(ok=True)


@app.post("/api/todo/unhide")
def todo_unhide():
    with db() as con:
        set_setting(con, "todo_hidden", "[]")
    return jsonify(ok=True)


@app.get("/api/backup/status")
def backup_status():
    with db() as con:
        s = get_settings(con)
    return jsonify(status=checks.backup_status(s), places=backup_places())


@app.post("/api/backup/now")
def backup_now():
    ok, msg = external_backup(force=True)
    if not ok:
        abort(400, msg)
    return jsonify(ok=True, message=msg)


@app.post("/api/backup/pick")
def backup_pick():
    """Abre la ventana de Windows para elegir una carpeta (la app corre en esta misma compu)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$f.Description='Elegí la carpeta donde guardar la copia de seguridad';"
          "$f.ShowNewFolderButton=$true;"
          "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
          "if($f.ShowDialog($o) -eq 'OK'){Write-Output $f.SelectedPath}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, timeout=900,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        path = r.stdout.decode("utf-8", "ignore").strip()
    except Exception:
        logging.exception("No se pudo abrir el selector de carpetas")
        abort(500, "No se pudo abrir la ventana para elegir carpeta. Escribí la ruta a mano.")
    return jsonify(path=path or None)


@app.get("/api/backup")
def backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # copia consistente de la base
        with db() as con:
            tmp = os.path.join(tempfile.gettempdir(), f"consumos_{uuid.uuid4().hex}.db")
            bk = __import__("sqlite3").connect(tmp)
            con.backup(bk)
            bk.close()
        z.write(tmp, "consumos.db")
        os.remove(tmp)
        for root, _, fs in os.walk(FILES_DIR):
            for fn in fs:
                p = os.path.join(root, fn)
                z.write(p, os.path.join("facturas", os.path.relpath(p, FILES_DIR)))
    buf.seek(0)
    with db() as con:
        set_setting(con, "last_manual_backup", datetime.now().isoformat(timespec="seconds"))
    return send_file(buf, download_name=f"Respaldo consumos {date.today().isoformat()}.zip", as_attachment=True)


@app.post("/api/restore")
def restore():
    f = request.files.get("file")
    if not f:
        abort(400, "No se recibió ningún archivo")
    if (f.filename or "").lower().endswith(".db"):
        return _restore_db(f)
    tmpzip = os.path.join(tempfile.gettempdir(), f"restore_{uuid.uuid4().hex}.zip")
    f.save(tmpzip)
    try:
        z = zipfile.ZipFile(tmpzip)
    except zipfile.BadZipFile:
        os.remove(tmpzip)
        abort(400, "El archivo no es un respaldo válido (.zip)")
    try:
        with z:
            if "consumos.db" not in z.namelist():
                abort(400, "El archivo no es un respaldo válido")
            probe = os.path.join(tempfile.gettempdir(), f"probe_{uuid.uuid4().hex}.db")
            with open(probe, "wb") as out:
                out.write(z.read("consumos.db"))
            ok = check_db(probe)
            os.remove(probe)
            if not ok:
                abort(400, "La base de datos del respaldo está dañada o no es de esta aplicación. No se cambió nada.")
            # guardo la base actual por las dudas
            if os.path.exists(DB_PATH):
                shutil.copy(DB_PATH, DB_PATH + f".antes-de-restaurar-{datetime.now():%Y%m%d-%H%M%S}")
            for name in z.namelist():
                if name.startswith("/") or ".." in name:
                    continue
                target = DB_PATH if name == "consumos.db" else os.path.join(DATA_DIR, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(z.read(name))
    finally:
        z.close()
        try:
            os.remove(tmpzip)
        except OSError:
            pass
    init()
    return jsonify(ok=True)


def _restore_db(f):
    """Restaurar desde una base suelta (.db), p. ej. la de la copia en Drive/OneDrive."""
    probe = os.path.join(tempfile.gettempdir(), f"probe_{uuid.uuid4().hex}.db")
    f.save(probe)
    try:
        if not check_db(probe):
            abort(400, "Ese archivo no es una base de esta aplicación o está dañado. No se cambió nada.")
        if os.path.exists(DB_PATH):
            shutil.copy(DB_PATH, DB_PATH + f".antes-de-restaurar-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy(probe, DB_PATH)
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass
    init()
    return jsonify(ok=True)


def _background():
    """Cada hora: respaldo diario local y copia externa (si hay carpeta elegida)."""
    try:
        backfill_due_dates()
    except Exception:
        logging.exception("No se pudieron leer los vencimientos de las facturas viejas")
    time.sleep(20)
    while True:
        try:
            auto_backup()
            external_backup()
        except Exception:
            logging.exception("Error en la tarea de respaldo")
        time.sleep(3600)


def create_app():
    init()
    threading.Thread(target=_background, daemon=True).start()
    return app
