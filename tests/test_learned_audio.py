import json
import threading
import unittest
import numpy as np
from app import learned_audio as learned,auto_audio as auto
from app.scan_control import ScanCancelled


class LearnedAudioTests(unittest.TestCase):
 def test_model_is_bundled_and_cpu_only(self):
  self.assertTrue(learned.available(),'Export the pinned model before running model tests.')
  self.assertEqual(learned.session().get_providers(),['CPUExecutionProvider'])
  meta=json.loads((learned.ASSETS/'discogs-vinet.json').read_text())
  self.assertEqual(meta['checkpoint_sha256'],'9d0b1d5cdd459a22b1062f69f0ef12ff59d0681a5d90d338827598dc55e8bf13')
  self.assertLess(meta['onnx_parity_max_error'],1e-4)
 def test_real_feature_shape_finiteness_and_self_match(self):
  sr=11025;t=np.arange(sr*13)/sr;notes=np.array([0,4,7,2,5,9,11,4]);freq=220*2**(notes[(t*2).astype(int)%len(notes)]/12)
  sound=(.4*np.sin(2*np.pi*freq*t)+.1*np.sin(2*np.pi*80*t)*np.exp(-(t%.5)*40)).astype('float32')
  x=learned.tensor(sound);self.assertEqual(x.shape,(1,1,84,400));self.assertTrue(np.isfinite(x).all());self.assertGreater(x.max(),.99)
  signature=learned.signatures(sound,{'start':0,'end':13});vectors=learned.vectors(signature['vectors'])
  self.assertEqual(vectors.shape,(1,512));self.assertGreater(learned.similarity(signature,signature)[0],99.9)
 def test_neural_score_needs_ordered_phrase_evidence(self):
  hit=dict(rhythm_reliable=False,rhythm_score=0,tonal_reliable=True,tonal_score=75,neural_score=90)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,True))
  self.assertEqual(auto.passing(hit,{**auto.DEFAULTS,'learned_model':False}),(False,False))
  self.assertEqual(auto.passing(hit,{**auto.DEFAULTS,'tonal':False}),(False,False))
  hit['tonal_reliable']=False;self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
  hit.update(tonal_reliable=True,tonal_score=60);self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
 def test_borderline_models_need_near_best_and_generic_chords_cannot_override_model(self):
  hit=dict(rhythm_reliable=False,rhythm_score=0,tonal_reliable=True,tonal_score=72,neural_score=78,neural_near_best=True)
  self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,True))
  hit['neural_near_best']=False;self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
  hit.update(tonal_score=82,neural_score=65);self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,False))
  hit['tonal_score']=95;self.assertEqual(auto.passing(hit,auto.DEFAULTS),(False,True))
 def test_stop_and_unknown_model(self):
  stop=threading.Event();stop.set()
  with self.assertRaises(ScanCancelled):learned.tensor(np.ones(11025*13,dtype='float32'),stop_event=stop)
  self.assertEqual(learned.similarity({'model':'unknown'},None),(0.,None,None))

if __name__=='__main__':unittest.main()
