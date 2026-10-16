"""Offline audio-only rhythmic descriptors. Never uses metadata to score rhythm."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import db

VERSION = 1
MIN_SIMILARITY = 0.97  # heuristic similarity, never a probability


def decoder():
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise RuntimeError('Nedostaje audio dekoder. Pokreni INSTALL-AUDIO.bat.')


def init():
    db.conn().execute('''CREATE TABLE IF NOT EXISTS audio_rhythm(
        track_id INTEGER PRIMARY KEY, path TEXT, size INTEGER, mtime INTEGER,
        version INTEGER, status TEXT, features TEXT, error TEXT)''')
    db.conn().commit()


def analyze_pcm(samples, sr=11025):
    """Full-track multiband onset envelope, tempo and beat-relative groove.

    PCM can be memory mapped. STFT blocks bound RAM even for long tracks.
    Beat phase is aligned by circular correlation at comparison time.
    """
    import numpy as np
    from scipy.signal import find_peaks
    fft, hop = 1024, 256
    hz = sr / hop
    frames = (len(samples) - fft) // hop + 1
    if frames < hz * 20:
        raise ValueError('Premalo zvuka: potrebno je najmanje 20 sekundi.')
    frequencies = np.fft.rfftfreq(fft, 1 / sr)
    masks = [(frequencies >= a) & (frequencies < b)
             for a, b in ((30, 180), (180, 1800), (1800, 5500))]
    env = np.empty((frames, 3), dtype=np.float32)
    prev = np.zeros((fft // 2 + 1,), dtype=np.float32)
    window = np.hanning(fft)
    for start in range(0, frames, 1024):
        count = min(1024, frames - start)
        chunk = np.asarray(samples[start * hop:(start + count - 1) * hop + fft])
        windows = np.lib.stride_tricks.sliding_window_view(chunk, fft)[::hop]
        spectrum = np.log1p(np.abs(np.fft.rfft(windows * window, axis=1)))
        flux = np.maximum(0, np.diff(spectrum, axis=0, prepend=prev[None, :]))
        for band, mask in enumerate(masks):
            env[start:start + count, band] = flux[:, mask].mean(axis=1)
        prev = spectrum[-1]
    env = np.maximum(0, env - np.median(env, axis=0))
    combined = env.sum(axis=1)
    if float(combined.max()) < 1e-5:
        raise ValueError('Nema dovoljno ritmickog signala.')
    # Analyze 24-second blocks. Discard silence and report disagreement.
    length = int(hz * 24)
    tempos, strengths, grooves = [], [], []
    for start in range(0, frames - length + 1, length):
        band = env[start:start + length]
        e = band.sum(axis=1)
        if e.mean() < combined.mean() * .20:
            continue
        e = e - e.mean()
        ac = np.correlate(e, e, mode='full')[len(e)-1:]
        lo, hi = int(hz * 60 / 180), int(hz * 60 / 70)
        peaks, _ = find_peaks(ac[lo:hi+1])
        if not len(peaks):
            continue
        lag = int(max(peaks + lo, key=lambda x: ac[x]))
        strength = float(ac[lag] / max(ac[0], 1e-8))
        if strength < .12:
            continue
        # Sub-frame peak interpolation avoids smearing the groove over time.
        if 0 < lag < len(ac)-1:
            denominator = ac[lag-1] - 2*ac[lag] + ac[lag+1]
            period = lag + float(.5*(ac[lag-1]-ac[lag+1])/denominator) if abs(denominator)>1e-8 else float(lag)
        else:
            period = float(lag)
        tempos.append(60 * hz / period)
        strengths.append(strength)
        # Four beats x 16 subdivisions, separately for bass/mid/high transients.
        phase = ((np.arange(length) / period) % 4 * 16).astype(int) % 64
        groove = np.zeros((3, 64))
        counts = np.bincount(phase, minlength=64)
        for b in range(3):
            groove[b] = np.bincount(phase, weights=band[:, b], minlength=64) / np.maximum(1, counts)
        # Equal band weighting prevents a shared kick from hiding different hats/snares.
        for b in range(3):
            groove[b] /= max(float(np.linalg.norm(groove[b])), 1e-8)
        groove /= max(float(np.linalg.norm(groove)), 1e-8)
        grooves.append(groove)
    if len(grooves) < 2:
        raise ValueError('Ritam nije dovoljno pouzdan za audio preporuku.')
    # Align blocks before aggregation; preserve relative bass/snare/hat phase.
    anchor = grooves[0]
    aligned = [max((np.roll(g, s, axis=1) for s in range(64)),
                   key=lambda g: float((g * anchor).sum())) for g in grooves]
    profile = np.median(aligned, axis=0)
    profile /= max(float(np.linalg.norm(profile)), 1e-8)
    bpm = float(np.median(tempos))
    adjusted = [min((t * .5, t, t * 2), key=lambda v: abs(v-bpm)) for t in tempos]
    spread = float((np.percentile(adjusted, 90)-np.percentile(adjusted, 10)) / bpm)
    consistency = float(np.median([float((g * profile).sum()) for g in aligned]))
    return dict(bpm=round(bpm, 2), profile=profile.round(5).tolist(),
                periodicity=round(float(np.median(strengths)), 3),
                consistency=round(consistency, 3), tempo_spread=round(spread, 3),
                variable_tempo_suspected=spread > .06, blocks=len(grooves),
                seconds=round(len(samples)/sr, 1))


def analyze_file(path):
    import numpy as np
    with tempfile.TemporaryDirectory(prefix='dj-rhythm-') as folder:
        pcm = str(Path(folder) / 'mono.f32')
        result = subprocess.run([decoder(), '-v', 'error', '-nostdin', '-i', path,
                                 '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '11025',
                                 '-f', 'f32le', pcm], capture_output=True, timeout=1800,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not os.path.isfile(pcm) or not os.path.getsize(pcm):
            raise ValueError('Audio nije dekodiran: ' + result.stderr.decode(errors='replace')[-200:])
        audio = np.memmap(pcm, dtype='<f4', mode='r')
        try:
            return analyze_pcm(audio)
        finally:
            del audio


def valid(row, track):
    if not row or row['status'] != 'ok' or row['version'] != VERSION or row['path'] != track['path']:
        return False
    try:
        s = os.stat(track['path'])
        return s.st_size == row['size'] and s.st_mtime_ns == row['mtime']
    except OSError:
        return False


def scan(progress=None):
    init()
    decoder()  # actionable error before starting thousands of tracks
    rows = db.rows('SELECT id,path FROM tracks ORDER BY id')
    result = dict(scanned=0, cached=0, missing=0, failed=0)
    for i, t in enumerate(rows):
        if progress:
            progress(i, len(rows), 'Audio deep scan: ' + os.path.basename(t['path'] or ''))
        cached = db.one('SELECT * FROM audio_rhythm WHERE track_id=?', (t['id'],))
        if valid(cached, t):
            result['cached'] += 1
            continue
        try:
            stat = os.stat(t['path'])
        except OSError:
            result['missing'] += 1
            continue
        try:
            features = analyze_file(t['path'])
            status, error = 'ok', None
            result['scanned'] += 1
        except Exception as exc:
            features, status, error = {}, 'error', str(exc)[:400]
            result['failed'] += 1
        db.conn().execute('INSERT OR REPLACE INTO audio_rhythm VALUES(?,?,?,?,?,?,?,?)',
                          (t['id'], t['path'], stat.st_size, stat.st_mtime_ns, VERSION,
                           status, json.dumps(features), error))
        db.conn().commit()  # resumable after closing app
    if progress:
        progress(len(rows), len(rows), 'Audio skeniranje zavrseno')
    return result


def similarity(a, b):
    import numpy as np
    if min(a['periodicity'], b['periodicity']) < .18 or min(a['consistency'], b['consistency']) < .65:
        return None
    if a['variable_tempo_suspected'] or b['variable_tempo_suspected']:
        return None
    tempo_delta = min(abs(a['bpm'] - b['bpm'] * scale) / a['bpm'] for scale in (.5, 1, 2))
    if tempo_delta > .04:
        return None
    pa, pb = np.array(a['profile']), np.array(b['profile'])
    groove = max(float((pa * np.roll(pb, s, axis=1)).sum()) for s in range(64))
    return .9 * groove + .1 * max(0, 1-tempo_delta/.04)


def status():
    init()
    counts = db.rows('SELECT status,COUNT(*) n FROM audio_rhythm GROUP BY status')
    return {'counts': {r['status']: r['n'] for r in counts}, 'version': VERSION}


def fifth(current, candidates, excluded):
    init()
    c = db.one('SELECT * FROM audio_rhythm WHERE track_id=?', (current['id'],))
    if not valid(c, current):
        return None, 'Peti audio predlog: trenutna pesma nema vazeci audio sken.'
    features = json.loads(c['features'])
    cached = {r['track_id']: r for r in db.rows("SELECT * FROM audio_rhythm WHERE status='ok'")}
    best, best_score = None, MIN_SIMILARITY
    for t in candidates:
        if t['id'] in excluded or not valid(cached.get(t['id']), t):
            continue
        score = similarity(features, json.loads(cached[t['id']]['features']))
        if score is not None and score >= best_score:
            best, best_score = t, score
    if best is None:
        return None, 'Peti audio predlog: nema dovoljno pouzdanog ritmickog poklapanja.'
    return dict(best, score=round(best_score*100, 1), label='AUDIO RITAM',
                audio_match=True, why=[f'Audio ritam: slicnost {best_score:.1%} (nije verovatnoca niti dokaz identicnog ritma).',
                'Poredjenje bas/srednji/visoki udarci kroz celu pesmu; bez tagova u audio oceni.']), None
