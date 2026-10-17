import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave
import numpy as np

from app import db, audio_rhythm
from app.scan_control import ScanCancelled
from test_audio_rhythm import drum_audio


class AudioCacheTests(unittest.TestCase):
    def test_decode_cache_invalidation_and_selection(self):
        old_file = db.DB_FILE
        old_conn = getattr(db._local, 'c', None)
        with tempfile.TemporaryDirectory() as folder:
            try:
                db._local.c = None
                db.DB_FILE = Path(folder)/'test.db'
                db.init()
                paths = []
                for i, kind in enumerate(('dance', 'dance', 'broken'), 1):
                    path = str(Path(folder)/f'{i}.wav')
                    y = drum_audio(kind, gain=.6)
                    with wave.open(path, 'wb') as f:
                        f.setnchannels(1); f.setsampwidth(2); f.setframerate(11025)
                        f.writeframes((np.clip(y, -1, 1)*32767).astype('<i2').tobytes())
                    paths.append(path)
                    db.conn().execute('INSERT INTO tracks(id,path,pkey) VALUES(?,?,?)', (i,path,path))
                db.conn().commit()
                first = audio_rhythm.scan()
                self.assertEqual(first['scanned'], 3)
                self.assertEqual(first['failed'], 0)
                with patch.object(audio_rhythm, 'analyze_file', side_effect=AssertionError('cache missed')):
                    self.assertEqual(audio_rhythm.scan()['cached'], 3)
                current = {'id': 1, 'path': paths[0]}
                candidates = [{'id': i, 'path': paths[i-1]} for i in (2,3)]
                def prepare():
                    measured,report=audio_rhythm.rank_audio(current,candidates)
                    audio_rhythm.PREPARED={'track_id':1,'revision':audio_rhythm.SCAN_REVISION,'whole':(measured,report),'source_stamp':(os.stat(paths[0]).st_size,os.stat(paths[0]).st_mtime_ns)}
                prepare()
                selected, reason = audio_rhythm.fifth(current,candidates,set())
                self.assertEqual(selected['id'], 2)
                self.assertIsNone(reason)
                selected, reason = audio_rhythm.fifth(current,candidates,{2})
                self.assertIsNone(selected)
                # A measured match can be inspected even when the saved threshold rejects it.
                c2 = db.one('SELECT * FROM audio_rhythm WHERE track_id=2')
                feature = json.loads(c2['features'])
                profile = np.array(feature['profile'])
                profile += .4*np.roll(profile,7,axis=1)
                profile /= np.linalg.norm(profile)
                feature['profile'] = profile.tolist()
                db.conn().execute('UPDATE audio_rhythm SET features=? WHERE track_id=2', (json.dumps(feature),))
                db.conn().commit()
                from app.config import SETTINGS
                old_cutoff = SETTINGS.get('audio_similarity_threshold', audio_rhythm.MIN_SIMILARITY)
                try:
                    source = json.loads(db.one('SELECT features FROM audio_rhythm WHERE track_id=1')['features'])
                    score = audio_rhythm.similarity(source,feature)
                    self.assertGreater(score, .51); self.assertLess(score, .99)
                    SETTINGS['audio_similarity_threshold'] = score+.005
                    prepare()
                    diagnostic = {}
                    selected, _ = audio_rhythm.fifth(current,[candidates[0]],set(),diagnostic)
                    self.assertIsNone(selected)
                    self.assertEqual(diagnostic['compared'],1)
                    self.assertIn('ispod podesenog praga', diagnostic['top'][0]['reasons'])
                    SETTINGS['audio_similarity_threshold'] = score-.005
                    selected, _ = audio_rhythm.fifth(current,[candidates[0]],set())
                    self.assertEqual(selected['id'],2)
                    report = audio_rhythm.matches(1)
                    self.assertTrue(any(t['id']==2 for t in report['accepted_items']))
                    self.assertTrue(any(t['id']==2 for t in report['accepted_top']))
                    self.assertLessEqual(len(report['accepted_top']),10)
                finally:
                    SETTINGS['audio_similarity_threshold'] = old_cutoff
                # Stop leaves completed descriptors untouched and resume uses the cache.
                stop = threading.Event()
                def progress(done,total,msg):
                    if done == 0:
                        stop.set()
                before = db.rows('SELECT * FROM audio_rhythm ORDER BY track_id')
                with self.assertRaises(ScanCancelled):
                    audio_rhythm.scan(progress,stop)
                self.assertEqual(before,db.rows('SELECT * FROM audio_rhythm ORDER BY track_id'))
                self.assertEqual(audio_rhythm.scan()['cached'],3)
                row = db.one('SELECT * FROM audio_rhythm WHERE track_id=1')
                stat = os.stat(paths[0])
                os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns+1000000000))
                self.assertFalse(audio_rhythm.valid(row,current))
                selected, reason = audio_rhythm.fifth(current,candidates,set())
                self.assertIsNone(selected)
            finally:
                if getattr(db._local, 'c', None):
                    db._local.c.close()
                db._local.c = old_conn
                db.DB_FILE = old_file


if __name__ == '__main__':
    unittest.main()
