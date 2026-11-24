"""Base de datos local (SQLite) y carpeta de datos."""
import glob
import logging
import os
import shutil
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime


def base_dir():
    # Junto al .exe cuando está empaquetado; si no, la raíz del proyecto.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        test = os.path.join(path, ".prueba_escritura")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return True
    except OSError:
        return False


def _data_dir():
    if os.environ.get("CONSUMOS_DATA"):
        return os.environ["CONSUMOS_DATA"]
    local = os.path.join(base_dir(), "datos")
    if _writable(local):
        return local
    # p. ej. si el programa quedó en "Archivos de programa": usar la carpeta del usuario
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ConsumosHoteles", "datos")


DATA_DIR = _data_dir()
BACKUP_DIR = os.path.join(DATA_DIR, "respaldos")
LOG_PATH = os.path.join(DATA_DIR, "errores.log")
DB_PATH = os.path.join(DATA_DIR, "consumos.db")
FILES_DIR = os.path.join(DATA_DIR, "facturas")
PENDING_DIR = os.path.join(DATA_DIR, "pendientes")
EXTERNAL_NAME = "Consumos Hoteles - respaldo"

SERVICES = {
    "agua": {"label": "Agua", "unit": "m³", "provider": "AySA"},
    "electricidad": {"label": "Electricidad", "unit": "kWh", "provider": "Edesur"},
    "gas": {"label": "Gas", "unit": "m³", "provider": "MetroGAS"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotels (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  legal_name TEXT,
  address TEXT,
  sort INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  service TEXT NOT NULL,
  number TEXT NOT NULL,
  hotel_id INTEGER NOT NULL REFERENCES hotels(id),
  label TEXT,
  UNIQUE(service, number)
);
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  hotel_id INTEGER NOT NULL REFERENCES hotels(id),
  service TEXT NOT NULL,
  provider TEXT,
  account_number TEXT,
  invoice_number TEXT,
  part INTEGER DEFAULT 1,
  split INTEGER DEFAULT 1,
  issue_date TEXT,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  consumption REAL NOT NULL,
  unit TEXT,
  amount REAL,
  holder TEXT,
  file_name TEXT,
  file_stored TEXT,
  source TEXT DEFAULT 'pdf',
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(service, account_number, invoice_number, part)
);
CREATE TABLE IF NOT EXISTS guests (
  hotel_id INTEGER NOT NULL REFERENCES hotels(id),
  year INTEGER NOT NULL,
  month INTEGER NOT NULL,
  count INTEGER,
  PRIMARY KEY (hotel_id, year, month)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  hotel_id INTEGER REFERENCES hotels(id),   -- NULL = general (todos los hoteles)
  due_date TEXT NOT NULL,                    -- fecha límite
  notify_days INTEGER DEFAULT 3,             -- empieza a avisar estos días antes (2 a 7)
  notes TEXT,                                -- seguimiento: qué pasó, con quién habló, etc.
  done_at TEXT,                              -- cuándo la marcó como resuelta
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS pending (
  token TEXT PRIMARY KEY,
  filename TEXT,
  path TEXT,
  sha1 TEXT,
  data TEXT,
  kind TEXT,
  message TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

# Columnas agregadas después de la primera versión (se suman solas a bases viejas)
MIGRATIONS = [
    ("invoices", "deleted_at", "TEXT"),   # papelera: fecha en que se borró
    ("invoices", "review", "TEXT"),       # aviso de "número raro" a revisar
    ("invoices", "due_date", "TEXT"),     # fecha de vencimiento
    ("invoices", "paid_at", "TEXT"),      # cuándo se marcó como pagada
]

DEFAULT_HOTELS = [
    # id, nombre, razón social, dirección
    (1, "Hotel Centro", "HOTEL CENTRO S.R.L.", ""),
    (2, "Hotel Norte", "HOTEL NORTE S.R.L.", ""),
    (3, "Hotel Sur", "HOTEL SUR S.R.L.", ""),
    (4, "Hotel Parque", "HOTEL PARQUE S.A.", ""),
]

# Cuentas de servicio por hotel (service, número, hotel_id); se completan desde la app
DEFAULT_ACCOUNTS = []

DEFAULT_SETTINGS = {
    "ref_agua": "0.23",          # m³ por huésped (persona-noche) – UTGRA 2022
    "ref_electricidad": "11.65", # kWh por huésped – UTGRA 2022
    "ref_gas": "",               # sin referencia para Argentina
    "company": "Hoteles Demo",
    "user_name": "Administración",
    "backup_folder": "",          # carpeta externa (Drive, OneDrive, pendrive) para la copia automática
    "last_external_backup": "",
    "external_backup_error": "",
    "last_manual_backup": "",
    "todo_hidden": "",            # avisos de "Para hacer" que ella decidió ocultar
}


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 20000")
    return con


@contextmanager
def db():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s", force=True)


def auto_backup(keep=20):
    """Copia diaria de la base de datos (se guardan las últimas `keep`)."""
    if not os.path.exists(DB_PATH):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"consumos-{date.today().isoformat()}.db")
    if not os.path.exists(target):
        try:
            src = sqlite3.connect(DB_PATH, timeout=20)
            dst = sqlite3.connect(target)
            src.backup(dst)
            dst.close(); src.close()
        except Exception:
            logging.exception("No se pudo hacer el respaldo automático")
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "consumos-*.db")))[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass


def check_db(path):
    """True si el archivo es una base de esta aplicación en buen estado."""
    con = None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return ok and {"hotels", "invoices", "guests", "settings", "accounts"} <= tables
    except Exception:
        return False
    finally:
        if con is not None:
            con.close()


def init():
    setup_logging()
    if os.path.exists(DB_PATH) and not check_db(DB_PATH):
        # base dañada: se aparta y se recupera el último respaldo bueno
        bad = DB_PATH + f".danada-{date.today().isoformat()}"
        shutil.move(DB_PATH, bad)
        logging.error("Base de datos dañada, movida a %s", bad)
        for b in sorted(glob.glob(os.path.join(BACKUP_DIR, "consumos-*.db")), reverse=True):
            if check_db(b):
                shutil.copy(b, DB_PATH)
                logging.warning("Recuperada desde %s", b)
                break
    auto_backup()
    tmp = os.path.join(DATA_DIR, "tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with db() as con:
        con.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        if not con.execute("SELECT 1 FROM hotels LIMIT 1").fetchone():
            for i, (hid, name, legal, addr) in enumerate(DEFAULT_HOTELS):
                con.execute("INSERT INTO hotels(id,name,legal_name,address,sort) VALUES(?,?,?,?,?)",
                            (hid, name, legal, addr, i))
            for svc, num, hid in DEFAULT_ACCOUNTS:
                con.execute("INSERT OR IGNORE INTO accounts(service,number,hotel_id) VALUES(?,?,?)", (svc, num, hid))
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        # la marca pasó a ser Hoteles Demo: si nunca cambió el nombre de ejemplo, se actualiza
        con.execute("UPDATE settings SET value='Hoteles Demo' WHERE key='company' AND value='Gestora Hotelera'")
        # pendientes cuyo PDF ya no está (p. ej. borrado a mano)
        for r in con.execute("SELECT token, path FROM pending").fetchall():
            if not r["path"] or not os.path.exists(os.path.join(DATA_DIR, r["path"])):
                con.execute("DELETE FROM pending WHERE token=?", (r["token"],))
        # la papelera se vacía sola después de 90 días
        for r in con.execute("SELECT id FROM invoices WHERE deleted_at IS NOT NULL "
                             "AND deleted_at < datetime('now','localtime','-90 days')").fetchall():
            purge_invoice(con, r["id"])


def purge_invoice(con, iid):
    """Borra definitivamente una factura (y su PDF si ninguna otra lo usa)."""
    r = con.execute("SELECT file_stored FROM invoices WHERE id=?", (iid,)).fetchone()
    con.execute("DELETE FROM invoices WHERE id=?", (iid,))
    if r and r["file_stored"]:
        if not con.execute("SELECT 1 FROM invoices WHERE file_stored=?", (r["file_stored"],)).fetchone():
            path = os.path.join(FILES_DIR, r["file_stored"])
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                logging.exception("No se pudo borrar %s", path)


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))


# ------------------------------------------------------------------ copia externa
_ext_lock = threading.Lock()

EXTERNAL_README = """COPIA DE SEGURIDAD - Consumos Hoteles
=====================================

Esta carpeta la llena sola la aplicación Consumos Hoteles una vez por día.
No hace falta tocar nada.

- "base": la base de datos de cada día (se guardan los últimos 30 días).
- "facturas": copia de todos los PDF cargados.

Si la computadora se rompe o se pierde:
  1. Instalar la aplicación en la computadora nueva.
  2. Abrirla e ir a Configuración > Respaldo > "Restaurar respaldo…".
  3. Elegir el archivo más nuevo de la carpeta "base" (consumos-AAAA-MM-DD.db).
  4. Para recuperar también los PDF, copiar la carpeta "facturas" dentro de
     la carpeta "datos" de la aplicación.
"""


def external_backup(force=False):
    """Copia la base y los PDF a la carpeta externa elegida (una vez por día).
    Devuelve (ok, mensaje). No lanza errores: si falla, queda anotado."""
    if not _ext_lock.acquire(blocking=False):
        return False, "Ya se está haciendo una copia"
    try:
        with db() as con:
            s = get_settings(con)
        folder = (s.get("backup_folder") or "").strip()
        if not folder:
            return False, "No hay carpeta de copia elegida"
        now = datetime.now()
        if not force and (s.get("last_external_backup") or "")[:10] == now.date().isoformat():
            return True, "La copia de hoy ya está hecha"
        try:
            if not os.path.isdir(folder):
                raise OSError(f"No se encuentra la carpeta {folder}. ¿Está conectado el pendrive o abierto Google Drive/OneDrive?")
            dest = os.path.join(folder, EXTERNAL_NAME)
            base = os.path.join(dest, "base")
            os.makedirs(base, exist_ok=True)
            target = os.path.join(base, f"consumos-{now.date().isoformat()}.db")
            tmp = target + ".tmp"
            src = sqlite3.connect(DB_PATH, timeout=20)
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close(); src.close()
            os.replace(tmp, target)
            for old in sorted(glob.glob(os.path.join(base, "consumos-*.db")))[:-30]:
                try:
                    os.remove(old)
                except OSError:
                    pass
            # PDF: solo se copian los nuevos o cambiados
            for root, _, files in os.walk(FILES_DIR):
                for fn in files:
                    p = os.path.join(root, fn)
                    q = os.path.join(dest, "facturas", os.path.relpath(p, FILES_DIR))
                    if not os.path.exists(q) or os.path.getsize(q) != os.path.getsize(p):
                        os.makedirs(os.path.dirname(q), exist_ok=True)
                        shutil.copy2(p, q)
            with open(os.path.join(dest, "LEEME.txt"), "w", encoding="utf-8") as f:
                f.write(EXTERNAL_README)
        except Exception as e:  # noqa: BLE001
            logging.exception("No se pudo hacer la copia externa")
            msg = str(e) if isinstance(e, OSError) and "No se encuentra" in str(e) else f"No se pudo copiar a {folder}: {e}"
            with db() as con:
                set_setting(con, "external_backup_error", f"{now:%Y-%m-%d %H:%M}|{msg[:300]}")
            return False, msg
        with db() as con:
            set_setting(con, "last_external_backup", now.isoformat(timespec="seconds"))
            set_setting(con, "external_backup_error", "")
        return True, "Copia hecha"
    finally:
        _ext_lock.release()


def backfill_due_dates():
    """Una sola vez: lee el vencimiento de las facturas que ya estaban cargadas.
    Las que ya vencieron se dan por pagadas (son historia)."""
    from .parsers import due_date, pdf_text
    with db() as con:
        if get_settings(con).get("due_backfill") == "ok":
            return
        rows = con.execute("SELECT id, file_stored FROM invoices WHERE due_date IS NULL AND file_stored IS NOT NULL").fetchall()
    today = date.today().isoformat()
    done = 0
    for r in rows:
        path = os.path.join(FILES_DIR, r["file_stored"])
        try:
            d = due_date(pdf_text(path)) if os.path.exists(path) else None
        except Exception:
            d = None
        if d:
            with db() as con:
                # las ya vencidas se dan por pagadas en su fecha de vencimiento
                con.execute("UPDATE invoices SET due_date=?, paid_at=CASE WHEN ? < ? THEN ? ELSE paid_at END WHERE id=?",
                            (d, d, today, d, r["id"]))
            done += 1
    with db() as con:
        set_setting(con, "due_backfill", "ok")
    logging.info("Vencimientos cargados en %s facturas viejas", done)


def backup_places():
    """Carpetas sugeridas para la copia: Google Drive, OneDrive, Dropbox y pendrives."""
    out, seen = [], set()

    def add(label, path):
        if path and os.path.isdir(path) and os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            out.append(dict(label=label, path=path))

    home = os.path.expanduser("~")
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        for sub in ("Mi unidad", "My Drive"):
            add(f"Google Drive ({letter}:)", f"{letter}:\\{sub}")
    add("Google Drive", os.path.join(home, "Google Drive"))
    add("Google Drive", os.path.join(home, "Mi unidad"))
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        add("OneDrive", os.environ.get(var))
    add("Dropbox", os.path.join(home, "Dropbox"))
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:  # extraíble
                    add(f"Pendrive ({chr(65 + i)}:)", root)
    except Exception:
        pass
    return out


def get_settings(con):
    return {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}


def reference(settings, service):
    v = (settings.get(f"ref_{service}") or "").strip().replace(",", ".")
    try:
        return float(v) if v else None
    except ValueError:
        return None
