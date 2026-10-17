import threading
import unittest
import numpy as np
from app import chorus
from app.scan_control import ScanCancelled

class ChorusTests(unittest.TestCase):
 def test_range_validation(self):
  self.assertEqual(chorus.bounds(30,60),(30.,60.))
  for a,b in ((0,5),(-1,30),(0,61),(float('nan'),30)):
   with self.assertRaises(ValueError): chorus.bounds(a,b)
 def test_local_alignment_transposition_and_offset(self):
  sequence=np.repeat([0,4,7,2,5,9,11,4,2,7,0,5],5)
  a={'chroma':np.eye(12)[sequence].tolist(),'chroma_hz':5}
  shifted=np.roll(np.eye(12)[sequence],3,axis=1)
  b={'chroma':np.vstack([np.eye(12)[[1]*5],shifted]).tolist(),'chroma_hz':5}
  hit=chorus.align(a,b)
  self.assertEqual(hit['pitch_shift'],-3);self.assertGreater(hit['coverage'],90)
  self.assertGreater(hit['tonal_score'],99);self.assertAlmostEqual(hit['b_from'],1,delta=.3)
 def test_unrelated_note_order_is_weaker(self):
  sequence=np.repeat([0,4,7,2,5,9,11,4,2,7,0,5],5)
  a={'chroma':np.eye(12)[sequence].tolist(),'chroma_hz':5}
  b={'chroma':np.eye(12)[sequence[::-1]].tolist(),'chroma_hz':5}
  self.assertLess(chorus.align(a,b)['alignment_strength'],chorus.align(a,a)['alignment_strength']*.8)
 def test_gain_stability_and_silence(self):
  sr=11025;t=np.arange(sr*13)/sr;sound=np.zeros_like(t)
  for at in np.arange(0,13,.5):
   u=t-at;mask=(u>=0)&(u<.08);sound[mask]+=np.sin(2*np.pi*80*u[mask])*np.exp(-u[mask]*50)
  for at in np.arange(.25,13,.5):
   u=t-at;mask=(u>=0)&(u<.025);sound[mask]+=.25*np.sin(2*np.pi*3000*u[mask])*np.exp(-u[mask]*90)
  a=chorus.extract(sound);b=chorus.extract(sound*.3)
  self.assertGreater(chorus.compare(a,b)['rhythm_score'],95);self.assertAlmostEqual(a['bpm'],120,delta=3)
  with self.assertRaises(ValueError): chorus.extract(np.zeros(sr*13))
 def test_same_key_or_static_chord_does_not_pass_tonal_condition(self):
  profile=np.zeros((3,64));profile[0,0]=profile[1,8]=profile[2,16]=1/np.sqrt(3)
  a={'chroma':np.tile(np.eye(12)[0],(80,1)).tolist(),'chroma_hz':5,'profile':profile.tolist(),'bpm':120,'periodicity':.9,'active_bands':3,'tonal_variation':0}
  hit=chorus.compare(a,a)
  self.assertTrue(hit['rhythm_pass']);self.assertFalse(hit['tonal_pass']);self.assertTrue(hit['either_pass'])
 def test_stop(self):
  stop=threading.Event();stop.set();a={'chroma':np.eye(12).tolist(),'chroma_hz':5}
  with self.assertRaises(ScanCancelled): chorus.align(a,a,stop)
if __name__=='__main__': unittest.main()
