"""Automatic integration: cues, independent gates, cache, live acquisition, exclusions."""
import json
import tempfile
import threading
import time
import unittest
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import numpy as np
from app import auto_audio as auto, chorus, config, db, nml
from app.scan_control import ScanCancelled


class AutoChorusTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
  self.old=getattr(db._local,'c',None);db._local.c=None
  self.patches=[patch.object(db,'DB_FILE',self.folder/'library.db'),patch.object(config,'SETTINGS_FILE',self.folder/'settings.json'),patch.dict(config.SETTINGS,{'nml_path':'','chorus_audio':dict(auto.DEFAULTS),'audio_similarity_threshold':.97})]
  for p in self.patches:p.start()
  db.init();auto.init();auto.THREAD=None;auto.CURRENT=None;auto.RESULT=None;auto.STATE.clear();auto.STATE.update(phase='Miruje',track_id=None,error=None);auto.REVISION=0
 def tearDown(self):
  auto.STOP.set()
  if auto.THREAD:auto.THREAD.join(10)
  db.conn().close();db._local.c=self.old
  for p in self.patches[::-1]:p.stop()
  self.temp.cleanup()
 def track(self,tid):
  path=self.folder/f'{tid}.wav';sr=11025;t=np.arange(sr*26)/sr
  # Audible note sequence over drums, enough to test decode/cache end-to-end.
  notes=np.array([0,4,7,2,5,9,11,4,2,7,0,5]);pitch=220*2**(notes[(t//2).astype(int)%len(notes)]/12)
  sound=.15*np.sin(2*np.pi*pitch*t)
  u=t%.5;sound+=np.sin(2*np.pi*80*u)*np.exp(-u*65)
  u=(t+.25)%.5;sound+=.25*np.sin(2*np.pi*3000*u)*np.exp(-u*90)
  with wave.open(str(path),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(sound,-1,1)*32000).astype('<i2').tobytes())
  db.conn().execute('INSERT INTO tracks(id,pkey,path,artist,title,on_disk) VALUES(?,?,?,?,?,1)',(tid,str(path),str(path),'Test',f'Pesma {tid}'));db.conn().commit()
  return db.one('SELECT * FROM tracks WHERE id=?',(tid,))
 def test_nml_cue_time_and_named_preference(self):
  entry=ET.fromstring('<ENTRY TITLE="Song"><LOCATION DIR="/:Music/:" FILE="a.wav" VOLUME="C:"/><CUE_V2 NAME="Refren" START="32000.25" TYPE="0" HOTCUE="2"/><CUE_V2 NAME="Grid" START="0" TYPE="4"/></ENTRY>')
  cues=nml.parse_entry(entry)['cue_positions'];self.assertEqual(cues[0]['start'],32.)
  samples=np.random.default_rng(1).normal(size=11025*70).astype('float32')
  sections=auto.select_sections(samples,cues)
  self.assertEqual(sections[0]['start'],32.);self.assertIn('Refren',sections[0]['reason']);self.assertEqual(sections[0]['end'],56.)
 def test_refren_is_verified_and_rhythm_is_a_separate_fallback(self):
  item=dict(rhythm_reliable=True,rhythm_score=99,tonal_reliable=True,tonal_score=99,neural_score=99)
  self.assertEqual(auto.passing(item,auto.DEFAULTS),(False,False))
  item['phrase_verification']=dict(score=95,seconds=16,density=.95,strength=56,contrast=3.,a_start=0.,b_start=2.)
  self.assertEqual(auto.passing(item,auto.DEFAULTS),(False,True))
  self.assertEqual(auto.passing(item,{**auto.DEFAULTS,'tonal':False}),(False,False))
 def test_rhythm_uses_pattern_not_metadata_tempo(self):
  profile=np.random.default_rng(3).normal(size=(3,64));profile-=profile.mean(axis=1,keepdims=True);profile/=np.linalg.norm(profile)
  a={'profile':profile.tolist(),'bpm':100.,'periodicity':.6,'active_bands':3}
  b={**a,'bpm':125.}
  score,reliable,delta=auto.rhythm(a,b)
  self.assertGreater(score,99);self.assertTrue(reliable);self.assertGreater(delta,.2)
  b['profile']=np.random.default_rng(4).normal(size=(3,64)).tolist()
  # Normalize the deliberately unrelated signature as the extractor does.
  p=np.array(b['profile']);p-=p.mean(axis=1,keepdims=True);p/=np.linalg.norm(p);b['profile']=p.tolist()
  self.assertLess(auto.rhythm(a,b)[0],80)
 def test_threshold_validation_and_saved_settings(self):
  self.assertEqual(auto.settings({'rhythm_cutoff':93,'tonal':False})['rhythm_cutoff'],93)
  self.assertFalse(json.loads(config.SETTINGS_FILE.read_text())['chorus_audio']['tonal'])
  for body in ({'rhythm_cutoff':float('nan')},{'tonal':False,'rhythm':False},{'auto_scan':'yes'}):
   with self.assertRaises(ValueError):auto.settings(body)
 def test_full_scan_cache_and_cue_invalidation(self):
  t=self.track(1);self.assertEqual(auto.scan()['ok'],1)
  row=db.one('SELECT * FROM auto_chorus WHERE track_id=1');self.assertTrue(auto.valid(row,t))
  with patch.object(auto.subprocess,'Popen',side_effect=AssertionError('decode repeated')):self.assertEqual(auto.scan()['ok'],1)
  db.conn().execute('INSERT INTO track_cues VALUES(1,0,"Hook","0")');db.conn().commit();self.assertFalse(auto.valid(row,t))
 def test_live_scans_missing_source_and_persists(self):
  self.track(1);auto.settings({'live_scan':True});auto.ensure(1);auto.THREAD.join(20)
  self.assertFalse(auto.THREAD.is_alive());self.assertIsNone(auto.STATE.get('error'),auto.STATE)
  self.assertEqual(auto.RESULT['track_id'],1);self.assertEqual(db.one('SELECT status FROM auto_chorus')['status'],'ok')
  old=auto.THREAD
  with patch.object(auto.subprocess,'Popen',side_effect=AssertionError('decode repeated')):auto.ensure(1)
  self.assertIs(auto.THREAD,old)
 def test_disabled_live_scan_does_not_decode(self):
  self.track(1);auto.settings({'auto_scan':False})
  with patch.object(auto.subprocess,'Popen',side_effect=AssertionError('disabled scanner ran')):auto.ensure(1)
  self.assertIsNone(auto.THREAD);self.assertIsNone(auto.RESULT);self.assertIn('OFF',auto.STATE['phase'])
 def test_live_scan_defaults_off_and_switching_off_cancels_only_live_work(self):
  self.track(1);self.assertFalse(auto.settings()['live_scan'])
  entered=threading.Event()
  def analyze(track,event):
   entered.set();event.wait(5);raise ScanCancelled()
  auto.settings({'live_scan':True})
  with patch.object(auto,'analyze',side_effect=analyze):
   auto.ensure(1);self.assertTrue(entered.wait(2));auto.settings({'live_scan':False});auto.THREAD.join(5)
  self.assertFalse(auto.THREAD.is_alive());self.assertFalse(auto.settings()['live_scan'])
 def test_fifth_and_live_share_gates_and_exclusions(self):
  current={'id':1};pool=[{'id':i,'artist':'Test','title':f'Track {i}'} for i in range(2,15)]
  auto.RESULT={'track_id':1,'sections':[],'hits':[{**t,'rhythm_reliable':True,'rhythm_score':96.,'tonal_reliable':False,'tonal_score':96.,'score':96.,'phrase_verification':dict(score=96,seconds=16,density=.95,strength=56,contrast=3.,a_start=0.,b_start=2.),'reasons':[]} for t in pool]}
  d={}
  with patch.object(auto,'ensure'):selected,_=auto.fifth(current,pool,{2,3,4,5},d)
  self.assertEqual(selected['id'],6);self.assertEqual(d['accepted_top'][0]['id'],6);self.assertTrue(all(t['id'] not in {1,2,3,4,5} for t in d['accepted_top']))
  auto.settings({'tonal_cutoff':99})
  with patch.object(auto,'ensure'):selected,_=auto.fifth(current,pool,set(),{})
  self.assertIsNone(selected)
 def test_discovery_bypasses_key_tempo_gates_without_changing_first_four(self):
  from app import suggest
  for tid in range(1,11):
   db.conn().execute('INSERT INTO tracks(id,pkey,path,artist,title,on_disk,bpm,camelot,energy) VALUES(?,?,?,?,?,1,?,?,5)',(tid,str(tid),'unused','Artist '+chr(64+tid)*3,'Anthem '+chr(64+tid)*3,200 if tid==10 else 120,'1A' if tid==10 else '8A'))
  db.conn().commit()
  with patch.object(auto,'fifth',return_value=(None,None)): baseline=suggest.suggest(1)['items']
  auto.RESULT={'track_id':1,'sections':[],'hits':[{'id':10,'artist':'Artist JJJ','title':'Anthem JJJ','rhythm_reliable':True,'rhythm_score':95.,'tonal_reliable':False,'tonal_score':95.,'score':95.,'phrase_verification':dict(score=95,seconds=16,density=.95,strength=56,contrast=3.,a_start=0.,b_start=2.),'reasons':[]}]}
  with patch.object(auto,'ensure'): result=suggest.suggest(1)
  self.assertEqual([t['id'] for t in result['items'][:4]],[t['id'] for t in baseline[:4]])
  self.assertEqual(result['items'][4]['id'],10)
  self.assertEqual(result['audio_diagnostics']['accepted_top'][0]['id'],10)
  self.assertEqual(len({t['id'] for t in result['items']}),5)
 def test_unreadable_cue_collection_keeps_audio_fallback(self):
  broken=self.folder/'collection.nml';broken.write_text('<NML>unfinished')
  with patch.dict(config.SETTINGS,{'nml_path':str(broken)}):auto.refresh_cues()
  self.assertIn('audio',auto.STATE['cue_warning'])
 def test_fallback_order_and_live_do_not_mix_unrelated_levels(self):
  current={'id':1};pool=[{'id':2,'artist':'A','title':'Hook'},{'id':3,'artist':'B','title':'Full rhythm'}]
  proof=dict(score=90,seconds=16,density=.95,strength=56,contrast=3.,a_start=0.,b_start=8.)
  hit=dict(id=2,artist='A',title='Hook',rhythm_score=40.,tonal_score=90.,phrase_verification=proof)
  deep={**pool[1],'score':99,'why':[]}
  deep_report={'accepted_top':[dict(id=3,artist='B',title='Full rhythm',score=99,groove=99)]}
  auto.RESULT={'track_id':1,'sections':[],'hits':[hit]}
  with patch.object(auto,'ensure'),patch.object(auto.audio_rhythm,'fifth',return_value=(deep,None)) as fallback:
   report={};winner,_=auto.fifth(current,pool,set(),report)
   self.assertEqual(winner['id'],2);self.assertEqual(report['selected_stage'],'chorus');fallback.assert_not_called()
   self.assertEqual([x['id'] for x in report['accepted_top']],[2])
  auto.RESULT={'track_id':1,'sections':[],'hits':[]}
  def rhythm(*args):args[-1].update(deep_report);return deep,None
  with patch.object(auto,'ensure'),patch.object(auto.audio_rhythm,'fifth',side_effect=rhythm):
   report={};winner,_=auto.fifth(current,pool,set(),report)
   self.assertEqual(winner['id'],3);self.assertEqual(report['selected_stage'],'whole_rhythm')
  with patch.object(auto,'ensure'),patch.object(auto.audio_rhythm,'fifth',return_value=(None,'Nema')):
   report={};winner,_=auto.fifth(current,pool,set(),report)
   self.assertIsNone(winner);self.assertEqual(report['selected_stage'],'normal');self.assertEqual(report['accepted_top'],[])
 def test_stop_before_decode(self):
  self.track(1);stop=threading.Event();stop.set()
  with self.assertRaises(ScanCancelled):auto.scan(stop_event=stop)
  self.assertIsNone(db.one('SELECT * FROM auto_chorus'))

if __name__=='__main__':unittest.main()
