"""Run with python -m unittest discover -s tests -p test_audio_rhythm.py."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from app.audio_rhythm import analyze_pcm, similarity, comparison, threshold, MIN_SIMILARITY
from app import config


def drum_audio(kind='dance', gain=1):
    sr = 11025
    y = np.zeros(sr*96, dtype=np.float32)
    for beat in np.arange(0, 96, .5):
        events = [(beat, 70, .8)]
        if kind == 'dance':
            if round(beat*2) % 4 in (1, 3):
                events.append((beat, 1300, .4))
            events.append((beat+.25, 3500, .3))
        else:
            if round(beat*2) % 4 in (0, 3):
                events.append((beat+.125, 1300, .9))
            events.extend([(beat+.125, 3500, .3), (beat+.375, 3500, .3)])
        for at, frequency, amplitude in events:
            n, i = int(.10*sr), int(at*sr)
            if i+n > len(y):
                continue
            t = np.arange(n)/sr
            y[i:i+n] += gain*amplitude*np.sin(2*np.pi*frequency*t)*np.exp(-t*60)
    return y


class RhythmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = analyze_pcm(drum_audio())

    def test_same_pattern_different_gain(self):
        score = similarity(self.a, analyze_pcm(drum_audio(gain=.6)))
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, MIN_SIMILARITY)

    def test_same_bpm_different_pattern(self):
        score = similarity(self.a, analyze_pcm(drum_audio('broken')))
        self.assertLess(score or 0, MIN_SIMILARITY)

    def test_silence_is_not_a_match(self):
        with self.assertRaises(ValueError):
            analyze_pcm(np.zeros(11025*60, dtype=np.float32))

    def test_variable_tempo_excluded(self):
        other = {**self.a, 'variable_tempo_suspected': True}
        self.assertIsNone(similarity(self.a, other))

    def test_large_tempo_difference_excluded(self):
        self.assertIsNone(similarity(self.a, {**self.a, 'bpm': 155}))

    def test_threshold_validation(self):
        for value in (True, False, 'nan', 'inf', .49, 1.01):
            with self.subTest(value=value), self.assertRaises(ValueError):
                threshold(value)
        self.assertEqual(threshold(.50), .50)
        self.assertEqual(threshold(1), 1)

    def test_threshold_persists_without_rescan(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'settings.json'
            with patch.object(config, 'SETTINGS_FILE', target):
                config.save({**config.DEFAULTS, 'audio_similarity_threshold': .85})
                self.assertEqual(config.load()['audio_similarity_threshold'], .85)

    def test_fft_score_matches_original_phase_search(self):
        rng = np.random.default_rng(30)
        a, b = rng.random((3,64)), rng.random((3,64))
        a /= np.linalg.norm(a); b /= np.linalg.norm(b)
        original = max(float((a*np.roll(b,s,axis=1)).sum()) for s in range(64))
        measured = comparison({**self.a, 'profile': a.tolist()}, {**self.a, 'profile': b.tolist()})
        self.assertAlmostEqual(measured['groove'], original, places=10)


if __name__ == '__main__':
    unittest.main()
