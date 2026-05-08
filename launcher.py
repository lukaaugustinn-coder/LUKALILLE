import sys
import os
import threading
import time
import traceback
import socket

if sys.platform.startswith("win"):
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("luka.t850_vad")

LOG_FILE = os.path.join(os.path.dirname(__file__), "launcher.log")


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def ensure_persistent_webview_storage():
    if not sys.platform.startswith("win"):
        return
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    base = os.path.join(local, "T850_VAD_LukaA", "webview2")
    os.makedirs(base, exist_ok=True)
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = base
    log(f"WEBVIEW2_USER_DATA_FOLDER={base}")


def find_free_port(start=5003, tries=60):
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("Impossible de trouver un port libre.")


def wait_for_server(host, port, timeout_sec=15):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def run_flask(port):
    try:
        from app import app
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except Exception:
        log("FLASK_ERROR:\n" + traceback.format_exc())


def main():
    try:
        ensure_persistent_webview_storage()
        port = find_free_port(7000)
        log(f"Using port: {port}")

        t = threading.Thread(target=run_flask, args=(port,), daemon=True)
        t.start()

        url = f"http://127.0.0.1:{port}"
        ok = wait_for_server("127.0.0.1", port, timeout_sec=15)

        import webview

        if not ok:
            msg = (
                "Le serveur local n'a pas démarré.\n\n"
                "👉 Ouvre le fichier 'launcher.log' (à côté de l'exe)\n"
                "et copie-colle la partie FLASK_ERROR ici.\n\n"
                f"Port essayé : {port}\n"
                f"URL : {url}"
            )
            log("Server not ready. Showing error window.")
            webview.create_window(
                "Erreur — T850 VAD",
                html=f"""<html><body style="font-family:Segoe UI,Arial;padding:18px;background:#fffde0">
                <h2 style="color:#1a1200">Impossible de démarrer T850</h2>
                <pre style="white-space:pre-wrap;background:#fff9cc;padding:12px;border-radius:10px;border:1px solid #e0c800">{msg}</pre>
                </body></html>""",
                width=760, height=440
            )
            webview.start()
            return

        webview.create_window(
            "T850 — Compteur VAD — Luka Augustin",
            url,
            width=1200,
            height=860
        )
        webview.start()

    except Exception:
        log("LAUNCHER_ERROR:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
