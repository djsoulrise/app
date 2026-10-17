"""Learnable window/UI shortcuts. Keyboard only; no controller ports are opened."""
import copy
import ntpath
import threading
import time

from . import config

ACTIONS = {
    'toggle_window': 'Prikazi / minimizuj Assistant',
    'focus_search': 'Fokus na pretragu pesama',
    'toggle_view': 'Pun prikaz / Mini / Tiker',
    'next_suggestion': 'Izaberi sledeci predlog',
    'previous_suggestion': 'Izaberi prethodni predlog',
    'copy_song': 'Copy Song izabranog predloga',
    'drag_song': 'Prevuci izabrani predlog',
}
DEFAULTS = {'keyboard_enabled': True,
            'keyboard': {'toggle_window': {'vk': 88, 'modifiers': 2, 'label': 'Ctrl+X'}}}




def active_keyboard_bindings(bindings, in_scope, editing_own):
    """The window toggle must remain reachable after minimizing or switching apps."""
    if editing_own: return {}
    return {action: binding for action, binding in bindings.items()
            if action == 'toggle_window' or in_scope}




class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.settings = copy.deepcopy(DEFAULTS)
        saved = config.SETTINGS.get('shortcuts') or {}
        self.settings.update({key: copy.deepcopy(saved[key]) for key in DEFAULTS if key in saved})
        self.window = None
        self.thread = None
        self.learning = None
        self.learning_error = ''
        self.keyboard_learning_until = 0
        self.editing = False
        self.wait_keyboard_release = False
        self.keyboard_error = ''
        self.events = []
        self.sequence = 0

    def persist(self):
        config.SETTINGS['shortcuts'] = copy.deepcopy(self.settings)
        config.save(config.SETTINGS)

    def status(self):
        with self.lock:
            if self.learning and time.monotonic() > self.learning['until']:
                self.learning = None
                self.learning_error = 'Ucenje je isteklo. Pokreni ponovo.'
            return {'actions': ACTIONS, 'settings': copy.deepcopy(self.settings),
                    'keyboard_error': self.keyboard_error,
                    'learning': ({k:v for k,v in self.learning.items() if k!='until'} if self.learning else None),
                    'learning_error': self.learning_error, 'sequence': self.sequence}

    def bind_keyboard(self, action, binding):
        if action not in ACTIONS: raise ValueError('Nepoznata radnja.')
        if not binding:
            self.settings['keyboard'].pop(action, None)
        else:
            vk, mods = binding.get('vk'), binding.get('modifiers')
            if type(vk) is not int or type(mods) is not int or not 1<=vk<=254 or vk in (16,17,18,91,92,123) or not 0<=mods<=15:
                raise ValueError('Neispravna kombinacija. F12 i sami Ctrl/Shift/Alt/Win nisu podrzani.')
            for other, existing in self.settings['keyboard'].items():
                if other!=action and (existing['vk'],existing['modifiers'])==(vk,mods):
                    raise ValueError('Kombinacija je vec dodeljena: '+ACTIONS[other])
            self.settings['keyboard'][action]={'vk':vk,'modifiers':mods,'label':str(binding.get('label') or vk)[:80]}
        self.keyboard_learning_until = 0
        self.wait_keyboard_release = True
        self.persist()







    def fire(self, action):
        if self.window is None: return
        try:
            if action=='toggle_window': self.window.toggle(); return
            self.window.show()
            with self.lock:
                self.sequence+=1
                self.events.append({'sequence':self.sequence,'action':action})
                self.events=self.events[-100:]
        except Exception as exc:
            self.keyboard_error=str(exc)

    def queued(self, after):
        with self.lock:
            return [x for x in self.events if x['sequence']>after]

    def start(self, window):
        self.window=window
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.keyboard_loop,daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(2)

    def keyboard_loop(self):
        import ctypes
        from ctypes import wintypes
        user=ctypes.WinDLL('user32')
        user.RegisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
        user.UnregisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int]
        user.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        current=None;registered={};msg=wintypes.MSG()
        try:
            while not self.stop_event.wait(.04):
                with self.lock:
                    enabled=self.settings['keyboard_enabled'] and time.monotonic()>self.keyboard_learning_until
                    bindings=copy.deepcopy(self.settings['keyboard'])
                    editing=self.editing
                if self.wait_keyboard_release:
                    # Do not execute the combination that was just captured by Learn.
                    if any(user.GetAsyncKeyState(b["vk"]) & 0x8000 for b in bindings.values()):
                        enabled = False
                    else:
                        self.wait_keyboard_release = False
                bindings = active_keyboard_bindings(bindings, self.window.keyboard_scope(editing),
                                                    self.window.own_editing(editing)) if enabled else {}
                active = bool(bindings)
                desired=repr(bindings) if active else ''
                if desired!=current:
                    for ident in registered: user.UnregisterHotKey(None,ident)
                    registered={};self.keyboard_error=''
                    if active:
                        for i,(action,binding) in enumerate(bindings.items(),1):
                            if user.RegisterHotKey(None,i,binding['modifiers']|0x4000,binding['vk']): registered[i]=action
                            else: self.keyboard_error='Precica je zauzeta ili rezervisana: '+binding['label']
                    current=desired
                while user.PeekMessageW(ctypes.byref(msg),None,0,0,1):
                    if msg.message==0x0312 and msg.wParam in registered:
                        action = registered[msg.wParam]
                        if not self.window.own_editing(self.editing) and (action=='toggle_window' or self.window.keyboard_scope(self.editing)):
                            self.fire(action)
        finally:
            for ident in registered: user.UnregisterHotKey(None,ident)


class WindowsWindow:
    def __init__(self,title,desktop_window):
        import ctypes
        from ctypes import wintypes
        self.desktop=desktop_window
        self.wintypes=wintypes
        self.ctypes=ctypes;self.user=ctypes.WinDLL('user32');self.title=title;self.previous=None
        self.user.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR];self.user.FindWindowW.restype=wintypes.HWND
        self.user.GetForegroundWindow.restype=wintypes.HWND
        self.user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        self.user.IsIconic.argtypes=[wintypes.HWND]
        self.user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        self.user.SetForegroundWindow.argtypes=[wintypes.HWND]
        self.user.IsWindow.argtypes=[wintypes.HWND]
        self.user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        self.kernel=ctypes.WinDLL('kernel32')
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]

    def own(self): return self.user.FindWindowW(None,self.title)

    def own_editing(self,editing):
        return bool(editing and self.own() and self.user.GetForegroundWindow()==self.own())

    def keyboard_scope(self,editing):
        foreground=self.user.GetForegroundWindow();own=self.own()
        if foreground==own and own: return not editing
        # Check executable identity: a browser title containing "Traktor" is not Traktor.
        pid=self.wintypes.DWORD()
        self.user.GetWindowThreadProcessId(foreground,self.ctypes.byref(pid))
        handle=self.kernel.OpenProcess(0x1000,False,pid.value)
        if not handle: return False
        try:
            text=self.ctypes.create_unicode_buffer(32768);size=self.wintypes.DWORD(len(text))
            return bool(self.kernel.QueryFullProcessImageNameW(handle,0,text,self.ctypes.byref(size))) and ntpath.basename(text.value).lower()=='traktor.exe'
        finally:
            self.kernel.CloseHandle(handle)

    def show(self):
        own=self.own()
        if not own: return
        foreground=self.user.GetForegroundWindow()
        if foreground!=own: self.previous=foreground
        # WinForms must own state transitions so the WebView child is restored too.
        if self.user.IsIconic(own):
            self.desktop.restore()
        self.desktop.show()
        self.user.SetForegroundWindow(own)
        from .window_surface import refresh
        refresh(self.desktop)

    def toggle(self):
        own=self.own()
        if not own: return
        if self.user.GetForegroundWindow()==own and not self.user.IsIconic(own):
            self.desktop.minimize()
            if self.previous and self.user.IsWindow(self.previous): self.user.SetForegroundWindow(self.previous)
        else: self.show()


def verify_hotkey_support():
    """Build smoke test: register an unused key and immediately release it."""
    import ctypes
    user = ctypes.WinDLL('user32')
    ident = 0x3F11
    ok = bool(user.RegisterHotKey(None, ident, 0x4003, 0x86))  # Ctrl+Alt+F23
    if ok: user.UnregisterHotKey(None, ident)
    return ok


manager=Manager()

