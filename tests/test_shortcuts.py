import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Msg
from unittest.mock import patch
from app import shortcuts as sc


def cc(n,value=127): return Msg(type='control_change',channel=15,control=n,value=value)


class ShortcutsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.file=patch.object(sc.config,'SETTINGS_FILE',Path(self.temp.name)/'settings.json')
        self.cfg=patch.object(sc.config,'SETTINGS',{})
        self.file.start();self.cfg.start();self.m=sc.Manager()
    def tearDown(self):
        self.file.stop();self.cfg.stop();self.temp.cleanup()
    def test_keyboard_saved_and_duplicate_rejected(self):
        self.m.bind_keyboard('focus_search',{'vk':70,'modifiers':3,'label':'Ctrl+Alt+F'})
        self.assertEqual(sc.Manager().settings['keyboard']['focus_search']['vk'],70)
        with self.assertRaises(ValueError): self.m.bind_keyboard('copy_song',{'vk':70,'modifiers':3})
        self.m.bind_keyboard('focus_search',None)
        self.assertNotIn('focus_search',self.m.settings['keyboard'])
        self.assertTrue((Path(self.temp.name)/'settings.json').is_file())
    def test_reserved_keys_rejected(self):
        for vk in (0,16,17,18,91,92,123,255):
            with self.assertRaises(ValueError): self.m.bind_keyboard('focus_search',{'vk':vk,'modifiers':2})
    def test_toggle_stays_registered_when_focus_moves_outside_traktor(self):
        bindings={'toggle_window':{'vk':88,'modifiers':2},'copy_song':{'vk':67,'modifiers':3}}
        # This is the state after minimizing into an unrecognized/fullscreen foreground app.
        self.assertEqual(set(sc.active_keyboard_bindings(bindings,False,False)),{'toggle_window'})
        self.assertEqual(set(sc.active_keyboard_bindings(bindings,True,False)),set(bindings))
        self.assertEqual(sc.active_keyboard_bindings(bindings,True,True),{})
    def test_window_toggle_restores_minimized_native_window(self):
        # Exercise both Win32 command paths without interacting with real Traktor.
        w=sc.WindowsWindow.__new__(sc.WindowsWindow)
        state={'foreground':100,'iconic':False};calls=[]
        w.own=lambda:100;w.previous=200
        def show(hwnd,command):
            calls.append(command);state['iconic']=command==6
            if command==6: state['foreground']=200
        def foreground(hwnd): state['foreground']=hwnd;return 1
        w.desktop=Msg(minimize=lambda:show(100,6),restore=lambda:show(100,9),show=lambda:None)
        w.user=Msg(GetForegroundWindow=lambda:state['foreground'],IsIconic=lambda h:state['iconic'],SetForegroundWindow=foreground,IsWindow=lambda h:True)
        with patch('app.window_surface.refresh') as repaint:
            w.toggle();self.assertTrue(state['iconic'])
            w.toggle();self.assertFalse(state['iconic']);self.assertEqual(state['foreground'],100)
            repaint.assert_called_once_with(w.desktop)
        self.assertEqual(calls,[6,9])
    def test_event_order_and_native_toggle(self):
        with patch.object(self.m,'window') as window:
            self.m.fire('toggle_window');window.toggle.assert_called_once();self.assertEqual(self.m.queued(0),[])
            self.m.fire('focus_search');self.m.fire('copy_song')
            self.assertEqual([e['action'] for e in self.m.queued(1)],['copy_song'])
            self.assertEqual(window.show.call_count,2)
    def test_keyboard_scope_checks_executable_and_editing(self):
        import ctypes
        from ctypes import wintypes
        w=sc.WindowsWindow.__new__(sc.WindowsWindow)
        w.ctypes=ctypes;w.wintypes=wintypes;w.own=lambda:100
        foreground=[200];image=[r'C:\Program Files\Traktor Pro 4\Traktor.exe']
        def query(handle,flags,text,size): text.value=image[0];return True
        w.user=Msg(GetForegroundWindow=lambda:foreground[0],GetWindowThreadProcessId=lambda h,p:None)
        w.kernel=Msg(OpenProcess=lambda *args:1,QueryFullProcessImageNameW=query,CloseHandle=lambda h:None)
        self.assertTrue(w.keyboard_scope(True))  # Traktor remains usable when Assistant had a search field focused.
        image[0]=r'C:\Browser\msedge.exe'
        self.assertFalse(w.keyboard_scope(False))
        foreground[0]=100
        self.assertFalse(w.keyboard_scope(True));self.assertTrue(w.keyboard_scope(False))
    def test_ui_font_preferences_persist_and_validate(self):
        from fastapi.testclient import TestClient
        from app import main
        import json
        with patch.object(main,'SETTINGS',{'dj_pin':'000000'}):
            client=TestClient(main.app,client=('127.0.0.1',12345))
            self.assertEqual(client.get('/api/ui-preferences').json()['font_scale'],115)
            self.assertEqual(client.post('/api/ui-preferences',json={'font_scale':140,'ctrl_wheel_text':False}).status_code,200)
            self.assertEqual(client.get('/api/ui-preferences').json(),{'font_scale':140,'ctrl_wheel_text':False})
            saved=json.loads(sc.config.SETTINGS_FILE.read_text())
            self.assertEqual(saved['ui_preferences']['font_scale'],140)
            self.assertEqual(client.post('/api/ui-preferences',json={'font_scale':999}).status_code,400)
    def test_remote_clients_blocked(self):
        from app.main import _shortcut_local
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised: _shortcut_local(Msg(client=Msg(host='192.168.1.12')))
        self.assertEqual(raised.exception.status_code,403)
        _shortcut_local(Msg(client=Msg(host='127.0.0.1')))


if __name__=='__main__': unittest.main()

