"""Inicia la aplicación: levanta el servidor local y abre el navegador."""
import json
import logging
import os
import socket
import sys
import threading
import urllib.request
import webbrowser

HOST = "127.0.0.1"
PORT = 8765


def _port_file():
    from app.db import DATA_DIR
    return os.path.join(DATA_DIR, "puerto.txt")


def last_port():
    """Puerto donde quedó abierta la última vez (por si no era el habitual)."""
    try:
        with open(_port_file()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def already_running(port):
    """True si en ese puerto ya está corriendo esta misma aplicación."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/meta", timeout=2) as r:
            return "services" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def free_port():
    for port in [PORT] + list(range(PORT + 1, PORT + 40)):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No hay puertos libres")


def main():
    no_browser = "--no-browser" in sys.argv
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("Consumos Hoteles")
    except Exception:
        pass
    for p in {PORT, last_port() or PORT}:
        if already_running(p):
            print("La aplicación ya estaba abierta: se abre en el navegador.")
            if not no_browser:
                webbrowser.open(f"http://{HOST}:{p}/")
            return
    try:
        from app.server import create_app
        app = create_app()
        port = free_port()
    except Exception as e:  # noqa: BLE001
        logging.exception("No se pudo iniciar")
        print("\n No se pudo iniciar la aplicación:\n ", e)
        input("\n Presioná Enter para cerrar...")
        return
    url = f"http://{HOST}:{port}/"
    try:
        with open(_port_file(), "w") as f:
            f.write(str(port))
    except OSError:
        pass
    from app.db import DATA_DIR
    print("=" * 62)
    print("  Consumos Hoteles")
    print(f"  Abierta en: {url}")
    print(f"  Datos en:   {DATA_DIR}")
    print("  No cierres esta ventana mientras uses la aplicación.")
    print("  Para cerrar la aplicación, cerrá esta ventana.")
    print("=" * 62)
    if not no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    from waitress import serve
    serve(app, host=HOST, port=port, threads=6)


if __name__ == "__main__":
    main()
