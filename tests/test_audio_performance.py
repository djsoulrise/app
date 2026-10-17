"""Resource policy, worker failure recovery, Gig gating and readonly polling."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from app import auto_audio as auto, audio_rhythm, config, performance


class PerformanceTests(unittest.TestCase):
    def test_worker_count_has_core_and_memory_caps_and_legacy_setting(self):
        with patch.dict(config.SETTINGS,{'scan_workers':8}),patch('os.cpu_count',return_value=16):
            config.SETTINGS.pop('audio_workers',None)
            self.assertEqual(performance.workers(),8)
        with patch.dict(config.SETTINGS,{'audio_workers':4}),patch('os.cpu_count',return_value=2):
            self.assertEqual(performance.workers(),2)

    def test_worker_crash_preserves_unsent_and_pending_tracks(self):
        tracks=[{'id':i,'title':str(i)} for i in range(25)]
        stored=[]
        class Pool:
            def __init__(self,**kw):self.n=0
            def submit(self,fn,job):
                self.n+=1
                if self.n==3:raise BrokenProcessPool('simulated worker death')
                result=Future();result.set_result(('ok',{}));return result
            def shutdown(self,**kw):pass
        with patch('concurrent.futures.ProcessPoolExecutor',Pool),patch.object(auto.db,'one',return_value=None),patch.object(auto,'cue_key',return_value='[]'),patch.object(auto,'analyze',side_effect=lambda t,e:stored.append(t['id'])),patch.dict(config.SETTINGS,{'gig_mode':False}):
            result=auto._scan_parallel(tracks,4,None,None)
        self.assertEqual(result['ok'],25)
        self.assertEqual(sorted(stored),list(range(25)))

    def test_threshold_uses_pending_songs_not_library_size(self):
        tracks=[{'id':i,'title':str(i)} for i in range(30)]
        counts=[]
        class Pool:
            def __init__(self,**kw):counts.append(kw['max_workers'])
            def submit(self,fn,job):
                result=Future();result.set_result(('ok',{}));return result
            def shutdown(self,**kw):pass
        def row(sql,args):return {'id':args[0],'status':'ok'} if args[0]<29 else None
        with patch('concurrent.futures.ProcessPoolExecutor',Pool),patch.object(auto.db,'one',side_effect=row),patch.object(auto,'valid',side_effect=lambda r,t:bool(r)),patch.object(auto,'cue_key',return_value='[]'),patch.object(auto,'store_calculated'),patch.dict(config.SETTINGS,{'gig_mode':False}):
            result=auto._scan_parallel(tracks,4,None,None)
        self.assertEqual(counts,[1]);self.assertEqual(result['ok'],30)

    def test_gig_prevents_live_work_and_scan(self):
        with patch.dict(config.SETTINGS,{'gig_mode':True}),patch.object(auto,'THREAD',None),patch.object(auto.db,'one',return_value=None),patch.object(auto,'init'),patch.object(audio_rhythm,'init'),patch.object(auto,'analyze',side_effect=AssertionError('Gig decoded audio')):
            auto.ensure(123)
            with self.assertRaises(ValueError):auto._scan()

    def test_status_does_not_schedule_audio(self):
        from app import main
        with patch.object(main.db,'one',return_value={}),patch.object(main.ai,'available',return_value=False),patch.object(main.duplicates,'_fpcalc',return_value=None),patch.object(main,'guest_url',return_value='local'),patch.object(auto,'ensure',side_effect=AssertionError('poll started audio')):
            main.status()

    def test_changed_file_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'song';path.write_bytes(b'changed')
            result={'size':2,'mtime':0,'key':'[]','whole':({},'ok',None)}
            with patch.object(auto,'cue_key',return_value='[]'),patch.object(auto.db,'conn',side_effect=AssertionError('write before validation')):
                with self.assertRaises(ValueError):auto.store_calculated({'id':1,'path':str(path)},result)

    def test_cached_fifth_does_not_compare_pairs_in_request(self):
        with patch.object(audio_rhythm,'PREPARED',None),patch.object(audio_rhythm,'rank_audio',side_effect=AssertionError('HTTP computed pairs')):
            selected,_=audio_rhythm.fifth({'id':1},[],set())
            self.assertIsNone(selected)

if __name__=='__main__':unittest.main()
