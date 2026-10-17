"""Desktop launcher: runs the server in-process and shows the UI in a native window (WebView2)."""
import os
import socket
import sys
import threading
import time

import uvicorn
import webview

from app.config import SETTINGS
from app.main import app


def _wait_port(port: int, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _window_bounds():
    """Keep the first window inside the desktop work area, including on 1366x768."""
    width, height = 1100, 720
    try:
        import ctypes
        from ctypes import wintypes
        area = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0):
            width = min(width, max(600, area.right-area.left-24))
            height = min(height, max(480, area.bottom-area.top-24))
    except (AttributeError, OSError):
        pass
    return width, height, (min(940, width), min(650, height))


def main() -> None:
    if '--window-restore-self-test' in sys.argv:
        from app.restore_smoke import run
        run(sys.argv[sys.argv.index('--window-restore-self-test') + 1])
        return
    if '--build-self-test' in sys.argv:
        import json
        from pathlib import Path
        from app import db, native_files
        from app.shortcuts import verify_hotkey_support
        from app.main import STATIC
        db.init()
        assert (Path(STATIC)/'index.html').is_file()
        assert verify_hotkey_support()
        assert native_files.verify_drag_payload(r'C:\Music\Niš, Žurka & song.flac')
        forbidden = ('mido','rtmidi','onnxruntime','librosa','numpy','scipy','app.auto_audio')
        assert not any(name in sys.modules for name in forbidden)
        Path(sys.argv[sys.argv.index('--build-self-test')+1]).write_text(json.dumps({
            'ok':True,'edition':'lite','frozen':bool(getattr(sys,'frozen',False)),
            'keyboard':True,'native_file_drop':True,'heavy_modules_loaded':False}),encoding='utf-8')
        return
    port = int(SETTINGS["port"])
    # Do not silently attach the new EXE to an older process on the same port.
    with socket.socket() as probe:
        probe.settimeout(.4)
        occupied = probe.connect_ex(("127.0.0.1", port)) == 0
    if occupied:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            f"Port {port} je vec zauzet. Zatvori prethodni DJ Miljan Assistant pa ponovo pokreni ovaj EXE. Baza i audio sken ostaju sacuvani.",
            "DJ Miljan Assistant — prethodna verzija je jos otvorena", 0x30)
        return
    server = uvicorn.Server(uvicorn.Config(app, host=SETTINGS["host"], port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    if not _wait_port(port):
        print(f"Server nije pokrenut (port {port} je mozda zauzet).")
        sys.exit(1)
    title = "DJ Miljan Assistant Lite"
    from app.shortcuts import manager, WindowsWindow
    width, height, minimum = _window_bounds()
    window = webview.create_window(title, f"http://127.0.0.1:{port}", width=width, height=height, min_size=minimum, background_color="#171918")
    from app.window_surface import refresh
    window.events.loaded += lambda: refresh(window)
    try:
        webview.start(lambda: manager.start(WindowsWindow(title, window)))
    finally:
        manager.stop()
        server.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        if '--build-self-test' in sys.argv:
            import traceback
            from pathlib import Path
            error=traceback.format_exc()
            Path(sys.argv[sys.argv.index('--build-self-test')+1]).write_text(error,encoding='utf-8')
            raise SystemExit(1)
        raise


