"""Desktop launcher: runs the server in-process and shows the UI in a native window (WebView2)."""
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


def main() -> None:
    if '--build-self-test' in sys.argv:
        import json
        import os
        from pathlib import Path
        from app import audio_rhythm, db
        import numpy as np
        from scipy.signal import find_peaks
        db.init()
        decoder = audio_rhythm.decoder()
        assert Path(decoder).is_file(), 'FFmpeg is missing from the EXE'
        assert find_peaks(np.array([0., 1., 0.]))[0].size == 1
        from app.main import STATIC
        assert (Path(STATIC) / 'index.html').is_file()
        audio_rhythm.init()
        report = sys.argv[sys.argv.index('--build-self-test') + 1]
        Path(report).write_text(json.dumps({'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)),
                                           'decoder_in_bundle': str(Path(decoder).resolve()).startswith(str(Path(sys._MEIPASS).resolve()))}), encoding='utf-8')
        return
    port = int(SETTINGS["port"])
    server = uvicorn.Server(uvicorn.Config(app, host=SETTINGS["host"], port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    if not _wait_port(port):
        print(f"Server nije pokrenut (port {port} je mozda zauzet).")
        sys.exit(1)
    webview.create_window("DJ asistent", f"http://127.0.0.1:{port}", width=1400, height=900, min_size=(900, 600))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()

