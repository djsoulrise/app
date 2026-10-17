"""Desktop launcher: runs the server in-process and shows the UI in a native window (WebView2)."""
import os
for _thread_key in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMBA_NUM_THREADS"):
    os.environ[_thread_key]="1"
import multiprocessing
if __name__ == "__main__":
    multiprocessing.freeze_support()

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
        import os
        from pathlib import Path
        from app import audio_rhythm, db
        import numpy as np
        from scipy.signal import find_peaks
        db.init()
        from concurrent.futures import ProcessPoolExecutor
        from app import performance
        ctx=multiprocessing.get_context('spawn')
        with ProcessPoolExecutor(2,mp_context=ctx,initializer=performance.initialize_worker,initargs=(ctx.Event(),str(db.DB_FILE))) as pool:
            probes=[pool.submit(performance.worker_probe) for _ in range(2)]
            for future in probes:
                result=future.result(timeout=45)
                assert result['pid']!=os.getpid() and result['threads']=='1' and result['worker']=='1'
        decoder = audio_rhythm.decoder()
        assert Path(decoder).is_file(), 'FFmpeg is missing from the EXE'
        assert find_peaks(np.array([0., 1., 0.]))[0].size == 1
        from app.main import STATIC
        assert (Path(STATIC) / 'index.html').is_file()
        audio_rhythm.init()
        from app import native_files
        import mido.backends.rtmidi, rtmidi
        from app.shortcuts import verify_hotkey_support
        assert verify_hotkey_support(), "Windows hotkey API failed"
        assert native_files.verify_drag_payload(r'C:\Music\Niš, Žurka & song.flac')
        from app import learned_audio
        assert learned_audio.available(), 'Trained model is missing from EXE'
        sr=11025;t=np.arange(sr*13)/sr
        signal=(.4*np.sin(2*np.pi*(220+40*np.sin(t))*t)+.1*np.sin(2*np.pi*80*t)*np.exp(-(t%.5)*40)).astype('float32')
        signature=learned_audio.signatures(signal,{'start':0,'end':13})
        assert learned_audio.similarity(signature,signature)[0]>99.9
        from app import phrase_match
        phrase=phrase_match.extract(signal)
        assert phrase_match.unpack(phrase['chroma']).shape[1]==12
        assert phrase_match.compare(phrase,phrase)['seconds']>=8
        from app import loop_audio
        periodic=loop_audio.extract(signal)
        assert periodic and loop_audio.compare(periodic,periodic)['score']>99
        import tempfile,wave
        from app import auto_audio
        with tempfile.TemporaryDirectory() as tmp:
            audio=Path(tmp)/'worker.wav'
            with wave.open(str(audio),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(sr)
                wav.writeframes((signal*30000).astype('<i2').tobytes())
            payload=({'id':1,'path':str(audio)},'[]',True)
            with ProcessPoolExecutor(2,mp_context=ctx,initializer=performance.initialize_worker,initargs=(ctx.Event(),str(db.DB_FILE))) as pool:
                jobs=[pool.submit(auto_audio.calculate,payload) for _ in range(2)]
                for job in jobs:
                    calculated=job.result(timeout=90)
                    assert calculated['data'][0]['features'].get('neural')
                    assert calculated['data'][0]['song_phrase']['sample']['data']
                    assert calculated['model']==learned_audio.MODEL_ID
        report = sys.argv[sys.argv.index('--build-self-test') + 1]
        Path(report).write_text(json.dumps({'ok': True, 'parallel_audio': True, 'native_file_drop': True, 'shortcuts_backend': True, 'learned_model': True, 'phrase_features': True, 'periodic_features': True, 'frozen': bool(getattr(sys, 'frozen', False)),
                                           'decoder_in_bundle': str(Path(decoder).resolve()).startswith(str(Path(sys._MEIPASS).resolve()))}), encoding='utf-8')
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
    title = "DJ Miljan Assistant"
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


