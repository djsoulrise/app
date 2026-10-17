"""Learnable window/UI shortcuts. No Traktor load/play commands or MIDI output."""
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
            'keyboard': {'toggle_window': {'vk': 88, 'modifiers': 2, 'label': 'Ctrl+X'}},
            'midi_enabled': False, 'midi_input': '', 'midi': {}}


def button_message(message):
    kind = getattr(message, 'type', '')
    if kind in ('note_on', 'note_off'):
        return ('note', message.channel, message.note), kind == 'note_on' and message.velocity > 0
    if kind == 'control_change':
        return ('cc', message.channel, message.control), message.value > 0
    return None, False


class MidiButtons:
    def __init__(self):
        self.pressed = set()

    def update(self, message, bindings):
        token, down = button_message(message)
        if token is None:
            return [], None, False
        edge = down and token not in self.pressed
        if down: self.pressed.add(token)
        else: self.pressed.discard(token)
        fired = []
        if edge:
            for action, binding in bindings.items():
                if list(token) == binding.get('button') and (not binding.get('modifier') or tuple(binding['modifier']) in self.pressed):
                    fired.append(action)
        # A shifted binding wins over the same button without a modifier.
        if any(bindings[a].get("modifier") for a in fired):
            fired = [a for a in fired if bindings[a].get("modifier")]
        return fired, token, edge


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.port_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(copy.deepcopy(config.SETTINGS.get('shortcuts') or {}))
        self.buttons = MidiButtons()
        self.input = None
        self.window = None
        self.thread = None
        self.learning = None
        self.learning_error = ''
        self.keyboard_learning_until = 0
        self.editing = False
        self.wait_keyboard_release = False
        self.keyboard_error = ''
        self.midi_error = ''
        self.last_midi = ''
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
                    'keyboard_error': self.keyboard_error, 'midi_error': self.midi_error,
                    'connected': self.input is not None, 'last_midi': self.last_midi,
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

    def ports(self):
        with self.port_lock:
            import mido
            mido.set_backend('mido.backends.rtmidi')
            return mido.get_input_names()

    def disconnect_midi(self):
        with self.port_lock:
            port = self.input
            self.input = None
            if port is not None: port.close()
            self.buttons = MidiButtons()

    def connect_midi(self, name, enabled):
        with self.port_lock:
            self.disconnect_midi()
            self.midi_error = ''
            if enabled:
                if not name: raise ValueError('Izaberi MIDI ulaz, najbolje virtuelni port iz Traktora.')
                try:
                    import mido
                    mido.set_backend('mido.backends.rtmidi')
                    self.input = mido.open_input(name, callback=self.on_midi)
                except Exception as exc:
                    self.midi_error = ('MIDI ulaz nije otvoren; moguce je da ga Traktor vec koristi. '
                                       'Koristi poseban virtuelni port iz Traktora. Detalji: '+str(exc))
                    raise ValueError(self.midi_error)
            with self.lock:
                self.settings.update(midi_input=name, midi_enabled=bool(enabled))
                self.persist()

    def learn_midi(self, action, combination):
        if action not in ACTIONS: raise ValueError('Nepoznata radnja.')
        if self.input is None: raise ValueError('Prvo povezi MIDI ulaz.')
        with self.lock:
            self.buttons = MidiButtons()
            self.learning_error = ''
            self.learning = {'action':action,'combination':bool(combination), 'modifier':None,
                             'until':time.monotonic()+30}

    def clear_midi(self, action):
        with self.lock:
            self.settings['midi'].pop(action,None)
            self.learning = None
            self.persist()

    def on_midi(self, message):
        fired=[]
        with self.lock:
            self.last_midi=str(message)[:180]
            fired,token,edge=self.buttons.update(message,self.settings['midi'])
            learning=self.learning
            if learning:
                if time.monotonic()>learning['until']:
                    self.learning=None
                    return
                if token is None or not edge: return
                if learning['combination'] and learning['modifier'] is None:
                    learning['modifier']=list(token)
                    return
                modifier=learning['modifier']
                if modifier and (tuple(modifier) not in self.buttons.pressed or list(token)==modifier): return
                binding={'modifier':modifier,'button':list(token)}
                for other,existing in self.settings['midi'].items():
                    if other!=learning['action'] and existing==binding:
                        self.learning_error='Kombinacija je vec dodeljena: '+ACTIONS[other]
                        self.learning=None
                        return
                self.settings['midi'][learning['action']]=binding
                self.learning=None
                self.persist()
                return  # Learning never executes the newly learned action.
        for action in fired: self.fire(action)

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
        if self.settings['midi_enabled']:
            try: self.connect_midi(self.settings['midi_input'],True)
            except Exception: pass

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(2)
        self.disconnect_midi()

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
                active=enabled and self.window.keyboard_scope(editing)
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
                    if msg.message==0x0312 and msg.wParam in registered and self.window.keyboard_scope(self.editing):
                        self.fire(registered[msg.wParam])
        finally:
            for ident in registered: user.UnregisterHotKey(None,ident)


class WindowsWindow:
    def __init__(self,title):
        import ctypes
        from ctypes import wintypes
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
        self.user.ShowWindow(own,9)
        self.user.SetForegroundWindow(own)

    def toggle(self):
        own=self.own()
        if not own: return
        if self.user.GetForegroundWindow()==own and not self.user.IsIconic(own):
            self.user.ShowWindow(own,6)
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
