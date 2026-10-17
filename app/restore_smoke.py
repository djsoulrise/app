"""Isolated GUI regression probe. Never connects to Traktor or opens music files."""
import json
import threading
import time
import traceback
from pathlib import Path


def run(report_path):
    import ctypes
    from ctypes import wintypes
    import webview
    from PIL import ImageGrab
    from .shortcuts import Manager, WindowsWindow
    from .window_surface import refresh

    result = {'ok':False,'cycles':0,'painted':False}
    report = Path(report_path)
    title = 'DJ Miljan Assistant restore regression probe'
    html = '<html><body style="margin:0;background:rgb(31,51,41);color:white;height:100vh"><h1>DJ Miljan Assistant</h1><input id="probe" value="keep this text"><div style="height:65vh;background:rgb(31,51,41)">RESTORE PROBE</div></body></html>'
    window = webview.create_window(title,html=html,width=700,height=480,on_top=True,background_color='#1f3329')

    def worker():
        try:
            assert window.events.loaded.wait(45), 'WebView did not load'
            from concurrent.futures import ProcessPoolExecutor
            from . import performance
            ctx=performance.spawn_context()
            with ProcessPoolExecutor(2,mp_context=ctx,initializer=performance.initialize_worker,initargs=(ctx.Event(),)) as pool:
                probes=[pool.submit(performance.worker_probe) for _ in range(2)]
                for future in probes:assert future.result(timeout=30)['worker']=='1'
            result['workers_after_gui']=True
            refresh(window)
            native = WindowsWindow(title,window)
            manager = Manager();manager.window = native
            user = native.user
            user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
            for cycle in range(3):
                # Start from a genuinely minimized native window on every cycle.
                window.minimize()
                time.sleep(.2)
                assert user.IsIconic(native.own()), 'Window did not minimize'
                manager.fire('toggle_window')
                time.sleep(.5)
                assert not user.IsIconic(native.own()), 'Window was not restored'
                assert window.evaluate_js('document.querySelector("#probe").value')=='keep this text', 'UI state was lost/reloaded'
                rect=wintypes.RECT();assert user.GetWindowRect(native.own(),ctypes.byref(rect))
                picture=ImageGrab.grab(bbox=(rect.left+20,rect.top+80,rect.right-20,rect.bottom-25)).convert('RGB')
                pixels=list(picture.resize((80,60)).getdata())
                colored=sum(1 for r,g,b in pixels if abs(r-31)<12 and abs(g-51)<12 and abs(b-41)<12)
                assert colored/len(pixels)>.6, 'Restored WebView is blank/unpainted'
                result['cycles']+=1
            result.update(ok=True,painted=True,state_preserved=True)
        except Exception:
            result['error']=traceback.format_exc()
        finally:
            report.write_text(json.dumps(result),encoding='utf-8')
            window.destroy()

    watchdog=threading.Timer(100,lambda: __import__('os')._exit(2));watchdog.daemon=True;watchdog.start()
    webview.start(worker,gui='edgechromium')
    watchdog.cancel()
    if not result['ok']:
        raise RuntimeError(result.get('error','GUI restore probe failed'))


if __name__=='__main__':
    import sys
    run(sys.argv[1])
