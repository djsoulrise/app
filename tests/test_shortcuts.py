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
    def test_combo_requires_hold_and_edge(self):
        bindings={'toggle_window':{'modifier':['cc',15,110],'button':['cc',15,111]}}
        b=sc.MidiButtons();self.assertEqual(b.update(cc(111),bindings)[0],[])
        b.update(cc(111,0),bindings);b.update(cc(110),bindings)
        self.assertEqual(b.update(cc(111),bindings)[0],['toggle_window'])
        self.assertEqual(b.update(cc(111,64),bindings)[0],[])
        b.update(cc(111,0),bindings);b.update(cc(110,0),bindings)
        self.assertEqual(b.update(cc(111),bindings)[0],[])
    def test_shifted_binding_wins(self):
        b=sc.MidiButtons();bindings={'copy_song':{'button':['cc',15,111],'modifier':None},'drag_song':{'button':['cc',15,111],'modifier':['cc',15,110]}}
        self.assertEqual(b.update(cc(111),bindings)[0],['copy_song'])
        b.update(cc(111,0),bindings);b.update(cc(110),bindings)
        self.assertEqual(b.update(cc(111),bindings)[0],['drag_song'])
    def test_note_zero_velocity_releases(self):
        b=sc.MidiButtons();bindings={'focus_search':{'button':['note',0,60]}}
        for velocity in (127,0,127):
            result=b.update(Msg(type='note_on',channel=0,note=60,velocity=velocity),bindings)[0]
            self.assertEqual(result,['focus_search'] if velocity else [])
    def test_learning_saves_without_firing(self):
        self.m.input=object()
        with patch.object(self.m,'fire') as fire:
            self.m.learn_midi('toggle_window',True);self.m.on_midi(cc(110));self.m.on_midi(cc(111))
            self.assertIsNone(self.m.learning);fire.assert_not_called()
            self.assertEqual(self.m.settings['midi']['toggle_window']['modifier'],['cc',15,110])
            self.m.on_midi(cc(111,0));self.m.on_midi(cc(111));self.m.on_midi(cc(111,64))
            fire.assert_called_once_with('toggle_window')
    def test_duplicate_midi_learning_suppresses_actions(self):
        self.m.input=object();self.m.settings['midi']={'copy_song':{'modifier':None,'button':['cc',15,111]}}
        with patch.object(self.m,'fire') as fire:
            self.m.learn_midi('drag_song',False);self.m.on_midi(cc(111))
            fire.assert_not_called();self.assertNotIn('drag_song',self.m.settings['midi'])
            self.assertIn('vec dodeljena',self.m.learning_error)
    def test_expired_learning_does_not_fire(self):
        self.m.input=object();self.m.learn_midi('copy_song',False);self.m.learning['until']=0
        with patch.object(self.m,'fire') as fire: self.m.on_midi(cc(111));fire.assert_not_called()
        self.assertIsNone(self.m.learning)
    def test_event_order_and_native_toggle(self):
        with patch.object(self.m,'window') as window:
            self.m.fire('toggle_window');window.toggle.assert_called_once();self.assertEqual(self.m.queued(0),[])
            self.m.fire('focus_search');self.m.fire('copy_song')
            self.assertEqual([e['action'] for e in self.m.queued(1)],['copy_song'])
            self.assertEqual(window.show.call_count,2)
    def test_remote_clients_blocked(self):
        from app.main import _shortcut_local
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised: _shortcut_local(Msg(client=Msg(host='192.168.1.12')))
        self.assertEqual(raised.exception.status_code,403)
        _shortcut_local(Msg(client=Msg(host='127.0.0.1')))


if __name__=='__main__': unittest.main()
