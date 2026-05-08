"""
launcher.py — T850 VAD
Point d'entrée de l'application packagée (.exe).
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


if sys.platform.startswith("win"):
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("luka.t850_vad")


def _setup_logging() -> logging.Logger:
    log_path = Path(__file__).parent / "launcher.log"
    logger = logging.getLogger("t850.launcher")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = RotatingFileHandler(
            log_path, maxBytes=1_024 * 1_024, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


log = _setup_logging()


def ensure_persistent_webview_storage() -> None:
    if not sys.platform.startswith("win"):
        return
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    storage = Path(local) / "T850_VAD_LukaA" / "webview2"
    storage.mkdir(parents=True, exist_ok=True)
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(storage)
    log.debug("WEBVIEW2_USER_DATA_FOLDER=%s", storage)


def find_free_port(start: int = 7000, tries: int = 60) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Aucun port libre trouvé entre {start} et {start + tries - 1}.")


def wait_for_server(host: str, port: int, timeout_sec: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _run_flask(port: int) -> None:
    try:
        from app import app  # noqa: PLC0415
        log.info("Flask démarrage sur http://127.0.0.1:%d", port)
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except Exception:
        log.critical("FLASK_ERROR :\n%s", traceback.format_exc())


def _show_error_window(message: str) -> None:
    import webview  # noqa: PLC0415
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"/>
<style>body{{font-family:Segoe UI,Arial,sans-serif;padding:24px;
background:#fffde0;color:#1a1200}}pre{{white-space:pre-wrap;background:#fff9cc;
padding:14px;border-radius:8px;border:1px solid #e0c800;font-size:13px}}</style>
</head><body><h2>Impossible de démarrer T850</h2><pre>{message}</pre></body></html>"""
    webview.create_window("Erreur — T850 VAD", html=html, width=780, height=460)
    webview.start()


def main() -> None:
    log.info("=== T850 VAD — démarrage ===")
    try:
        ensure_persistent_webview_storage()
        port = find_free_port(7000)
        log.info("Port sélectionné : %d", port)

        flask_thread = threading.Thread(
            target=_run_flask, args=(port,), daemon=True, name="flask-worker"
        )
        flask_thread.start()

        url   = f"http://127.0.0.1:{port}"
        ready = wait_for_server("127.0.0.1", port, timeout_sec=15.0)

        if not ready:
            log.error("Serveur non prêt — affichage de la fenêtre d'erreur.")
            _show_error_window(
                f"Le serveur local n'a pas démarré dans le délai imparti.\n\n"
                f"Port essayé : {port}\nURL : {url}\n\n"
                "\U0001f449 Consulte launcher.log (à côté de l'exe) et copie la section FLASK_ERROR."
            )
            return

        log.info("Serveur prêt — ouverture WebView sur %s", url)
        import webview  # noqa: PLC0415
        webview.create_window(
            "T850 — Compteur VAD — Luka Augustin",
            url, width=1200, height=860, min_size=(900, 640),
        )
        webview.start()
        log.info("Fenêtre WebView fermée — arrêt propre.")

    except Exception:
        log.critical("LAUNCHER_ERROR :\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
