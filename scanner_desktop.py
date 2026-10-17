"""Standalone launcher. Scanner subprocesses are bundled Node, never Python spawn."""
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
import sys,json,socket,threading,time
from pathlib import Path

def main():
    if '--self-test' in sys.argv:
        from scanner.self_test import run
        report=run();Path(sys.argv[sys.argv.index('--self-test')+1]).write_text(json.dumps(report),encoding='utf-8');return
    import uvicorn,webview
    from scanner import server,jobs
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    http=uvicorn.Server(uvicorn.Config(server.app,host='127.0.0.1',port=port,log_level='warning'))
    threading.Thread(target=http.run,daemon=True).start()
    for _ in range(100):
        if http.started:break
        time.sleep(.05)
    if not http.started:raise RuntimeError('Skener nije uspeo da pokrene lokalni interfejs.')
    width,height=1150,820
    try:
        import ctypes
        from ctypes import wintypes
        area=wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30,0,ctypes.byref(area),0):width=min(width,area.right-area.left-24);height=min(height,area.bottom-area.top-24)
    except (AttributeError,OSError):pass
    window=webview.create_window('DJ Miljan Audio Scanner',f'http://127.0.0.1:{port}',width=width,height=height,min_size=(min(850,width),min(600,height)),background_color='#181d18');server.WINDOW=window
    def closing():
        if jobs.STATE['running']:
            if not window.create_confirmation_dialog('Sken je u toku','Zaustavi posao i zatvori skener? Zavrseni rezultati ostaju sacuvani.'):return False
            jobs.STOP.set();jobs.PAUSE.clear()
            end=time.monotonic()+8
            while jobs.STATE['running'] and time.monotonic()<end:time.sleep(.1)
        http.should_exit=True
    window.events.closing+=closing
    webview.start(gui='edgechromium')

if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        text=traceback.format_exc()
        if '--self-test' in sys.argv:Path(sys.argv[sys.argv.index('--self-test')+1]).write_text(text,encoding='utf-8')
        else:
            from scanner.storage import DATA
            (DATA/'startup-error.txt').write_text(text,encoding='utf-8')
        raise
