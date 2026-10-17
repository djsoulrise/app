"""Offline audio-only rhythmic descriptors. Never uses metadata to score rhythm."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import db
from .scan_control import ScanCancelled, check_stop

SCAN_REVISION = 0
VERSION = 1
MIN_SIMILARITY = 0.97  # heuristic similarity, never a probability


def decoder():
    if getattr(sys, 'frozen', False):
        import imageio_ffmpeg
        binaries = Path(imageio_ffmpeg.__file__).resolve().parent / 'binaries'
        matches = sorted(binaries.glob('ffmpeg*.exe'))
        if not matches:
            raise RuntimeError('U EXE paketu nedostaje ugradjeni FFmpeg dekoder.')
        return str(matches[0])
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


def analyze_pcm(samples, sr=11025, stop_event=None):
    """Full-track multiband onset envelope, tempo and beat-relative groove.

    PCM can be memory mapped. STFT blocks bound RAM even for long tracks.
    Beat phase is aligned by circular correlation at comparison time.
    """
    check_stop(stop_event)
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
        check_stop(stop_event)
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
        check_stop(stop_event)
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


def analyze_file(path, stop_event=None):
    import numpy as np
    check_stop(stop_event)
    with tempfile.TemporaryDirectory(prefix='dj-rhythm-') as folder:
        pcm = str(Path(folder) / 'mono.f32')
        command = [decoder(), '-v', 'error', '-nostdin', '-i', path,
                   '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '11025', '-f', 'f32le', pcm]
        import time
        deadline = time.monotonic() + 1800
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                while True:
                    check_stop(stop_event)
                    if time.monotonic() > deadline:
                        raise TimeoutError('Audio dekodiranje je predugo trajalo.')
                    try:
                        _, error = process.communicate(timeout=.25)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                process.communicate()
                raise
            if process.returncode or not os.path.isfile(pcm) or not os.path.getsize(pcm):
                raise ValueError('Audio nije dekodiran: ' + error.decode(errors='replace')[-200:])
        check_stop(stop_event)
        audio = np.memmap(pcm, dtype='<f4', mode='r')
        try:
            return analyze_pcm(audio, stop_event=stop_event)
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


def scan(progress=None, stop_event=None):
    global SCAN_REVISION
    check_stop(stop_event)
    init()
    decoder()  # actionable error before starting thousands of tracks
    rows = db.rows('SELECT id,path FROM tracks ORDER BY id')
    result = dict(scanned=0, cached=0, missing=0, failed=0)
    for i, t in enumerate(rows):
        if stop_event is not None and stop_event.is_set():
            raise ScanCancelled(result)
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
            features = analyze_file(t['path'], stop_event=stop_event)
            status, error = 'ok', None
            result['scanned'] += 1
        except ScanCancelled:
            raise ScanCancelled(result)
        except Exception as exc:
            features, status, error = {}, 'error', str(exc)[:400]
            result['failed'] += 1
        db.conn().execute('INSERT OR REPLACE INTO audio_rhythm VALUES(?,?,?,?,?,?,?,?)',
                          (t['id'], t['path'], stat.st_size, stat.st_mtime_ns, VERSION,
                           status, json.dumps(features), error))
        db.conn().commit()  # resumable after closing app
        SCAN_REVISION += 1
    if progress:
        progress(len(rows), len(rows), 'Audio skeniranje zavrseno')
    return result


def threshold(value=None):
    import math
    from .config import SETTINGS
    if value is None:
        value = SETTINGS.get('audio_similarity_threshold', MIN_SIMILARITY)
    if isinstance(value, bool):
        raise ValueError('Prag mora biti broj od 50 do 100%.')
    value = float(value)
    if not math.isfinite(value) or not .5 <= value <= 1:
        raise ValueError('Prag mora biti od 50 do 100%.')
    return value


def comparison(a, b):
    """Return a measured score even when a quality gate rejects the pair."""
    import numpy as np
    reasons = []
    if min(a['periodicity'], b['periodicity']) < .18:
        reasons.append('slaba periodicnost ritma (<0.18)')
    if min(a['consistency'], b['consistency']) < .65:
        reasons.append('nestabilan ritmicki obrazac (<0.65)')
    if a['variable_tempo_suspected'] or b['variable_tempo_suspected']:
        reasons.append('audio sumnja na promenljiv tempo')
    tempo_delta = min(abs(a['bpm'] - b['bpm'] * scale) / a['bpm'] for scale in (.5, 1, 2))
    if tempo_delta > .04:
        reasons.append('razlika audio tempa preko 4%')
    pa, pb = np.array(a['profile']), np.array(b['profile'])
    # Exact circular correlation, equivalent to testing all 64 phase shifts.
    correlation = np.fft.irfft((np.conj(np.fft.rfft(pa, axis=1)) *
                               np.fft.rfft(pb, axis=1)).sum(axis=0), n=64)
    groove = float(np.clip(correlation.max(), 0, 1))
    score = .9 * groove + .1 * max(0, 1-tempo_delta/.04)
    return {'score': score, 'groove': groove, 'tempo_delta': tempo_delta, 'reasons': reasons}


def similarity(a, b):
    result = comparison(a, b)
    return None if result['reasons'] else result['score']


def cache_reason(row, track):
    if row is None:
        return 'pesma nije audio skenirana'
    if row['status'] != 'ok':
        return 'greska skena: ' + (row['error'] or 'nepoznata greska')
    if row['version'] != VERSION:
        return 'sken je iz druge verzije analizatora'
    if row['path'] != track['path']:
        return 'putanja pesme se promenila'
    try:
        stat = os.stat(track['path'])
    except OSError:
        return 'audio fajl trenutno nije dostupan'
    if stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime']:
        return 'fajl se promenio posle skeniranja (velicina ili datum izmene)'
    return None


def source_metrics(features):
    return {k: features.get(k) for k in ('bpm', 'periodicity', 'consistency',
                                       'tempo_spread', 'variable_tempo_suspected', 'blocks')}


def rank_audio(current, candidates, excluded=(), cutoff=None):
    from collections import Counter
    init()
    cutoff = threshold(cutoff)
    cached = {r['track_id']: r for r in db.rows('SELECT * FROM audio_rhythm')}
    reason = cache_reason(cached.get(current['id']), current)
    report = {'threshold': cutoff, 'current_id': current['id'], 'current':
              {'id': current['id'], 'artist': current.get('artist'), 'title': current.get('title')},
              'current_error': reason, 'current_audio': None, 'pool': len(candidates),
              'counts': {}, 'compared': 0, 'top': [], 'accepted_top': []}
    if reason:
        return [], report
    features = json.loads(cached[current['id']]['features'])
    report['current_audio'] = source_metrics(features)
    results, counts = [], Counter()
    for t in candidates:
        if t['id'] == current['id'] or t['id'] in excluded:
            counts['vec izabrana'] += 1
            continue
        reason = cache_reason(cached.get(t['id']), t)
        if reason:
            counts[reason] += 1
            continue
        other = json.loads(cached[t['id']]['features'])
        measured = comparison(features, other)
        reasons = list(measured['reasons'])
        if measured['score'] < cutoff:
            reasons.append('ispod podesenog praga')
        for reason in reasons:
            counts[reason] += 1
        if not reasons:
            counts['prolazi audio uslove'] += 1
        results.append({'track': t, 'audio': source_metrics(other), **measured,
                        'reasons': reasons, 'accepted': not reasons})
    results.sort(key=lambda x: (-x['score'], x['track']['id']))
    report.update(counts=dict(counts), compared=len(results))
    report['top'] = [public_match(r) for r in results[:5]]
    report['accepted_top'] = unique_accepted(results, 10)
    return results, report


def unique_accepted(results, limit):
    from .names import core_title
    seen, out = set(), []
    for result in results:
        if not result['accepted']:
            continue
        t = result['track']
        identity = core_title(t.get('artist') or '', t.get('title') or '') or str(t['id'])
        if identity in seen:
            continue
        seen.add(identity)
        out.append(public_match(result))
        if len(out) >= limit:
            break
    return out


def public_match(result):
    t = result['track']
    return {'id': t['id'], 'artist': t.get('artist') or '', 'title': t.get('title') or '',
            'score': round(result['score']*100, 2), 'groove': round(result['groove']*100, 2),
            'tempo_delta': round(result['tempo_delta']*100, 2), 'audio': result['audio'],
            'reasons': result['reasons'], 'accepted': result['accepted']}


def status():
    init()
    counts = db.rows('SELECT status,COUNT(*) n FROM audio_rhythm GROUP BY status')
    errors = db.rows("SELECT t.id,t.artist,t.title,a.error FROM audio_rhythm a JOIN tracks t ON t.id=a.track_id WHERE a.status='error' LIMIT 10")
    return {'counts': {r['status']: r['n'] for r in counts}, 'version': VERSION,
            'threshold': threshold(), 'errors': errors}


def scanned_tracks(q=''):
    init()
    return db.rows("""SELECT t.id,t.artist,t.title FROM tracks t JOIN audio_rhythm a ON a.track_id=t.id
                      WHERE a.status='ok' AND (LOWER(COALESCE(t.artist,'') || ' ' || COALESCE(t.title,'')) LIKE ?)
                      ORDER BY t.artist,t.title LIMIT 200""", ('%'+q.lower()+'%',))


def matches(track_id, cutoff=None, limit=50):
    current = db.one('SELECT * FROM tracks WHERE id=?', (track_id,))
    if not current:
        raise ValueError('Pesma nije pronadjena.')
    # Explorer covers the whole scanned library, separately from recommendation gates.
    candidates = db.rows("SELECT t.* FROM tracks t JOIN audio_rhythm a ON a.track_id=t.id WHERE a.status='ok' AND t.id!=?", (track_id,))
    results, report = rank_audio(current, candidates, cutoff=cutoff)
    report['accepted_total'] = sum(r['accepted'] for r in results)
    report['items'] = [public_match(r) for r in results[:max(1, min(limit, 100))]]
    report['accepted_items'] = unique_accepted(results, 50)
    report['scope'] = 'Cela audio skenirana biblioteka; bez izvornih stilskih/key/istorijskih filtera. Lista nije automatska preporuka.'
    return report


PREPARED=None


def fifth(current, candidates, excluded, diagnostics=None):
    # Pair measurements are computed in the isolated AI worker, never in the
    # HTTP request that handles energy-mode changes or redraws the console.
    from . import performance
    saved=PREPARED
    if not saved or saved['track_id']!=current['id'] or saved['revision']!=SCAN_REVISION:
        if diagnostics is not None:diagnostics.update(current_error='Gig mod' if performance.enabled() else 'Audio poredjenje radi u pozadini',accepted_top=[],top=[])
        return None,'Gig mod: audio poredjenje je pauzirano.' if performance.enabled() else 'Audio poredjenje se priprema u pozadini.'
    try:stamp=os.stat(current['path']);source_stamp=(stamp.st_size,stamp.st_mtime_ns)
    except (OSError,KeyError):source_stamp=None
    original=saved.get('source_stamp')
    if original is not None and original!=source_stamp:
        if diagnostics is not None:diagnostics.update(current_error='Pesma je promenjena; ponovi skeniranje.',accepted_top=[],top=[])
        return None,'Pesma je promenjena; ponovi skeniranje.'
    allowed={t['id'] for t in candidates}-set(excluded)
    results=[r for r in saved['whole'][0] if r['track']['id'] in allowed]
    cutoff=threshold()
    results=[dict(r,reasons=[x for x in r['reasons'] if x!='ispod podesenog praga']+(['ispod podesenog praga'] if r['score']<cutoff else [])) for r in results]
    for r in results:r['accepted']=not r['reasons']
    report=dict(saved['whole'][1],pool=len(candidates),threshold=cutoff,compared=len(results),
                top=[public_match(r) for r in results[:5]],accepted_top=unique_accepted(results,10))
    if diagnostics is not None:
        diagnostics.update(report)
    if report['current_error']:
        return None, 'Peti audio predlog: ' + report['current_error'] + '.'
    accepted = [r for r in results if r['accepted']]
    if not accepted:
        best = f" Najblizi audio skor: {report['top'][0]['score']:.2f}%." if report['top'] else ''
        return None, (f"Audio: uporedjeno {report['compared']}/{report['pool']} kandidata; prag {report['threshold']:.0%}." + best + ' Nema kandidata koji prolazi sve audio uslove.')
    winner = accepted[0]
    return dict(winner['track'], score=round(winner['score']*100, 1), label='AUDIO RITAM',
                audio_match=True, why=[f"Audio ritam: slicnost {winner['score']:.1%}; sacuvani prag {report['threshold']:.0%}.",
                'Audio skor nije verovatnoca niti dokaz identicnog ritma.']), None


