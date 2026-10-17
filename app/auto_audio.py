"""Automatic, persistent chorus signatures. Read-only audio/NML; no Traktor writes."""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from . import audio_rhythm, chorus, config, db, learned_audio, phrase_match, sample_match, loop_audio
from .scan_control import check_stop, ScanCancelled

VERSION = 4
REVISION = 0
LOCK = threading.Lock()
SCANNING=threading.Event()
CONTROL = threading.RLock()
STOP = threading.Event()
NML_STAMP = None
CURRENT = None
THREAD = None
RESULT = None
STATE = {'phase':'Miruje', 'track_id':None, 'error':None}
DEFAULTS = dict(rhythm=True, tonal=True, rhythm_cutoff=97, tonal_cutoff=80, live_scan=False, auto_scan=False, whole_library=True, learned_model=True, neural_cutoff=75, periodic_cutoff=62)


def settings(body=None):
    result = {**DEFAULTS, **config.SETTINGS.get('chorus_audio', {})}
    result['rhythm_cutoff']=round(audio_rhythm.threshold()*100,6)
    if body is not None:
        for key in ('rhythm','tonal','auto_scan','live_scan','whole_library','learned_model'):
            if key in body:
                if not isinstance(body[key], bool): raise ValueError('Opcija mora biti ukljucena ili iskljucena.')
                result[key] = body[key]
        if 'auto_scan' in body and 'live_scan' not in body:result['live_scan']=body['auto_scan']
        for key in ('rhythm_cutoff','tonal_cutoff','neural_cutoff','periodic_cutoff'):
            if key in body:
                value = float(body[key])
                if not 50 <= value <= 100: raise ValueError('Prag mora biti od 50 do 100%.')
                result[key] = value
        if not result['rhythm'] and not result['tonal']: raise ValueError('Ukljuci bar jedan audio uslov.')
        result['auto_scan']=result['live_scan']
        if not result['live_scan'] and STATE.get('scanning'):STOP.set()
        config.SETTINGS['audio_similarity_threshold']=audio_rhythm.threshold(result['rhythm_cutoff']/100)
        config.SETTINGS['chorus_audio'] = result
        config.save(config.SETTINGS)
    result['auto_scan']=result['live_scan']
    return result


def init():
    db.conn().execute('''CREATE TABLE IF NOT EXISTS auto_chorus(
      track_id INTEGER PRIMARY KEY,path TEXT,size INTEGER,mtime INTEGER,version INTEGER,
      status TEXT,data TEXT,error TEXT,cue_key TEXT,model TEXT)''')
    if 'model' not in {r['name'] for r in db.conn().execute('PRAGMA table_info(auto_chorus)')}:
        db.conn().execute('ALTER TABLE auto_chorus ADD COLUMN model TEXT')
    db.conn().commit()


def cue_key(track):
    return json.dumps(db.rows('SELECT start,name,type FROM track_cues WHERE track_id=? ORDER BY start',(track['id'],)),sort_keys=True)


def valid(row, track):
    if not row or row['version'] != VERSION or row['path'] != track['path']: return False
    if learned_audio.available() and row.get('model')!=learned_audio.MODEL_ID: return False
    try: stat=os.stat(track['path'])
    except OSError: return False
    return row['size']==stat.st_size and row['mtime']==stat.st_mtime_ns and row['cue_key']==cue_key(track)


def select_sections(samples, cues=(), sr=11025, stop_event=None):
    """Repeated chroma + energy locate likely refrains; named cues are strong hints."""
    import numpy as np
    check_stop(stop_event)
    duration=len(samples)/sr
    if duration < 12: raise ValueError('Pesma je kraca od 12 sekundi.')
    length=min(24.,duration)
    named=[c for c in cues if c.get('type')!='4' and re.search(r'\b(refren\w*|chorus\w*|hook\w*)\b',c.get('name',''),re.I) and 0 <= c['start'] <= duration-12]
    sections=[]
    if named:
        start=min(float(named[0]['start']),duration-length)
        sections.append(dict(start=start,end=start+length,reason='Traktor cue: '+named[0]['name'],confidence='cue'))
    # One-second pitch/energy descriptors. No full-track chromagram retained in RAM.
    fft=4096;freq=np.fft.rfftfreq(fft,1/sr);mask=(freq>=100)&(freq<4000)
    notes=np.rint(69+12*np.log2(freq[mask]/440)).astype(int)%12
    desc=[];energy=[]
    for second in range(int(duration)):
        if second % 32==0: check_stop(stop_event)
        piece=np.asarray(samples[second*sr:second*sr+fft])
        if len(piece)<fft: break
        mag=np.sqrt(np.abs(np.fft.rfft(piece*np.hanning(fft))))[mask]
        chroma=np.array([mag[notes==n].sum() for n in range(12)])
        chroma=np.maximum(0,chroma-np.quantile(chroma,.2));chroma/=max(1e-8,float(np.linalg.norm(chroma)))
        desc.append(chroma);energy.append(float(np.mean(piece**2)))
    desc=np.array(desc);energy=np.array(energy)
    starts=list(range(0,max(1,int(duration-length)+1),6))
    width=min(int(length),len(desc));windows=[desc[x:x+width] for x in starts]
    scored=[]
    for i,(start,window) in enumerate(zip(starts,windows)):
        check_stop(stop_event)
        if len(window)!=width or not width: continue
        repeated=max([float((window*w).sum(axis=1).mean()) for j,w in enumerate(windows) if abs(starts[j]-start)>=length+6 and len(w)==width] or [0])
        power=float(energy[start:start+width].mean())
        active=min(1.,power/max(1e-9,float(np.quantile(energy,.65))))
        # Prefer repeated, audible material; generic hotcues only a weak tiebreaker.
        cue_bonus=.015 if any(c.get('type')!='4' and abs(c['start']-start)<=3 for c in cues) else 0
        score=repeated*.85+active*.15+cue_bonus
        scored.append((score,start,repeated))
    for score,start,repeat in sorted(scored,reverse=True):
        if any(abs(start-s['start'])<length for s in sections): continue
        sections.append(dict(start=float(start),end=float(start+length),reason='Ponavljajuci audio deo' if repeat>=.75 else 'Audio kandidat; refren nije pouzdano odredjen',confidence=round(repeat,3)))
        if len(sections)>=2: break
    return sections


def calculate(payload):
    """Spawn-safe CPU work. Return descriptors; only the parent writes SQLite."""
    import numpy as np
    from . import performance
    track,key,need_whole=payload
    stop_event=performance.STOP
    stat=os.stat(track['path'])
    with tempfile.TemporaryDirectory(prefix='dj-auto-chorus-') as folder:
        pcm=Path(folder)/'mono.f32'
        command=[audio_rhythm.decoder(),'-v','error','-nostdin','-threads','1','-i',track['path'],'-map','0:a:0','-vn','-ac','1','-ar','11025','-f','f32le',str(pcm)]
        with subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
            try:
                deadline=time.monotonic()+1800
                while True:
                    check_stop(stop_event)
                    if time.monotonic()>deadline: raise ValueError('Dekodiranje je predugo trajalo.')
                    try: _,error=process.communicate(timeout=.2);break
                    except subprocess.TimeoutExpired: pass
            except BaseException:
                process.kill();process.communicate();raise
        if process.returncode or not pcm.exists() or not pcm.stat().st_size: raise ValueError('Audio nije dekodiran: '+error.decode(errors='replace')[-180:])
        samples=np.memmap(pcm,dtype='<f4',mode='r')
        try:
            sections=select_sections(samples,json.loads(key),stop_event=stop_event)
            song_phrase=phrase_match.extract(samples,stop_event=stop_event)
            data=[]
            for section in sections:
                check_stop(stop_event)
                try: features=chorus.extract(samples[int(section['start']*11025):int(section['end']*11025)],stop_event=stop_event,require_rhythm=False)
                except ValueError: continue
                if learned_audio.available():
                    features['neural']=learned_audio.signatures(samples,section,stop_event=stop_event)
                data.append({**section,'features':features})
            if data:
                data[0]['song_phrase']=song_phrase
                if learned_audio.available():data[0]['song_loop']=loop_audio.extract(samples,stop_event=stop_event)
            whole=None
            if need_whole:
                try: whole=(audio_rhythm.analyze_pcm(samples,stop_event=stop_event),"ok",None)
                except ValueError as exc: whole=({},"error",str(exc))
            if not data: raise ValueError('U kandidatima za refren nema dovoljno audio detalja.')
        finally: del samples
    end=os.stat(track['path'])
    if (stat.st_size,stat.st_mtime_ns)!=(end.st_size,end.st_mtime_ns):
        raise ValueError('Pesma je promenjena tokom skena; ponovi sken.')
    return dict(data=data,whole=whole,size=stat.st_size,mtime=stat.st_mtime_ns,key=key,
                model=learned_audio.MODEL_ID if learned_audio.available() else None)


def store_calculated(track,result,stop_event=None):
    global REVISION
    check_stop(stop_event)
    stat=os.stat(track['path'])
    if (stat.st_size,stat.st_mtime_ns,cue_key(track))!=(result['size'],result['mtime'],result['key']):
        raise ValueError('Pesma ili cue su promenjeni tokom skena; ponovi sken.')
    if result['whole'] is not None:
        audio_rhythm.init();features,status,error=result['whole']
        db.conn().execute('INSERT OR REPLACE INTO audio_rhythm VALUES(?,?,?,?,?,?,?,?)',
            (track['id'],track['path'],stat.st_size,stat.st_mtime_ns,audio_rhythm.VERSION,status,json.dumps(features),error))
        audio_rhythm.SCAN_REVISION+=1
    db.conn().execute('INSERT OR REPLACE INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?,?)',
        (track['id'],track['path'],stat.st_size,stat.st_mtime_ns,VERSION,'ok',json.dumps(result['data']),None,result['key'],result['model']))
    db.conn().commit();REVISION+=1
    return db.one('SELECT * FROM auto_chorus WHERE track_id=?',(track['id'],))


def analyze(track, stop_event=None):
    init();audio_rhythm.init()
    cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(track['id'],))
    if valid(cached,track) and cached['status']=='ok': return cached
    from . import performance
    # The live worker is a separate process too; its stop event is supplied by initializer.
    payload=(track,cue_key(track),not audio_rhythm.valid(db.one('SELECT * FROM audio_rhythm WHERE track_id=?',(track['id'],)),track))
    from concurrent.futures import ProcessPoolExecutor, wait
    import multiprocessing
    ctx=multiprocessing.get_context('spawn');stop=ctx.Event()
    with ProcessPoolExecutor(1,mp_context=ctx,initializer=performance.initialize_worker,initargs=(stop,str(db.DB_FILE))) as pool:
        future=pool.submit(calculate,payload)
        while not future.done():
            if stop_event is not None and stop_event.is_set():stop.set()
            wait([future],timeout=.1)
        check_stop(stop_event)
        return store_calculated(track,future.result(),stop_event)


def _store_whole_rhythm(track,samples,stat,stop_event):
    """Maintain the second-level audio index using the already decoded PCM."""
    audio_rhythm.init()
    if audio_rhythm.valid(db.one('SELECT * FROM audio_rhythm WHERE track_id=?',(track['id'],)),track): return
    try:
        features=audio_rhythm.analyze_pcm(samples,stop_event=stop_event);status,error='ok',None
    except ValueError as exc:
        features,status,error={},'error',str(exc)
    check_stop(stop_event)
    db.conn().execute('INSERT OR REPLACE INTO audio_rhythm VALUES(?,?,?,?,?,?,?,?)',
        (track['id'],track['path'],stat.st_size,stat.st_mtime_ns,audio_rhythm.VERSION,status,json.dumps(features),error))
    db.conn().commit();audio_rhythm.SCAN_REVISION+=1


def refresh_cues():
    """Refresh cue hints without reimporting metadata or writing the collection."""
    global NML_STAMP
    from . import nml
    path=config.SETTINGS.get('nml_path')
    if not path or not os.path.isfile(path): return
    stat=os.stat(path);stamp=(path,stat.st_size,stat.st_mtime_ns)
    if stamp==NML_STAMP: return
    try: tracks,_=nml.parse_collection(path)
    except (OSError,nml.ET.ParseError):
        STATE['cue_warning']='Cue podaci trenutno nisu dostupni; koristim audio izbor dela.'
        return
    STATE.pop('cue_warning',None)
    ids={t['pkey']:t['id'] for t in db.rows('SELECT id,pkey FROM tracks')}
    for track in tracks:
        tid=ids.get(track['pkey'])
        if tid is None: continue
        db.conn().execute('DELETE FROM track_cues WHERE track_id=?',(tid,))
        db.conn().executemany('INSERT INTO track_cues VALUES(?,?,?,?)',[(tid,c['start'],c['name'],c['type']) for c in track.get('cue_positions',[])])
    db.conn().commit();NML_STAMP=stamp


def scan(progress=None,stop_event=None):
    SCANNING.set();STOP.set()
    try:
        while THREAD and THREAD.is_alive():
            check_stop(stop_event);THREAD.join(.1)
        return _scan(progress,stop_event)
    finally:SCANNING.clear()


def _record_error(track,exc):
    try:
        stat=os.stat(track['path'])
        db.conn().execute('INSERT OR REPLACE INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?,?)',
            (track['id'],track['path'],stat.st_size,stat.st_mtime_ns,VERSION,'error',None,str(exc),cue_key(track),learned_audio.MODEL_ID if learned_audio.available() else None))
        db.conn().commit()
    except OSError:pass


def _worker_init(stop):
    from . import performance
    performance.initialize_worker(stop)


def _worker(job):
    from . import performance
    track,key=job
    try:return 'ok',calculate((track,key,True))
    except ScanCancelled:return 'cancelled',None
    except (OSError,ValueError) as exc:return 'error',str(exc)


def _scan(progress=None,stop_event=None):
    from . import performance
    if performance.enabled():raise ValueError('Gig mod je ukljucen; skeniranje je pauzirano.')
    init();audio_rhythm.init();refresh_cues()
    tracks=db.rows('SELECT * FROM tracks WHERE on_disk=1 ORDER BY id')
    return _scan_parallel(tracks,performance.workers(),progress,stop_event)


def _scan_parallel(tracks, workers, progress, stop_event):
    """Same results as the serial scan; decoding and analysis run in worker processes, all writes stay here."""
    import multiprocessing
    from . import performance
    from concurrent import futures
    from concurrent.futures.process import BrokenProcessPool
    check_stop(stop_event)
    total=len(tracks);done=errors=finished=0;todo=[]
    for track in tracks:
        check_stop(stop_event)
        cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(track['id'],))
        if valid(cached,track) and cached['status']=='ok': done+=1;finished+=1
        else: todo.append((track,cue_key(track)))
    if not todo:return {'ok':done,'errors':0,'workers':0}
    workers=1 if len(todo)<20 else workers
    if progress:progress(finished,total,'Audio ritam / refren: pokrecem %d radnika'%workers)
    ctx=multiprocessing.get_context('spawn');mp_stop=ctx.Event()
    pool=futures.ProcessPoolExecutor(max_workers=workers,mp_context=ctx,initializer=_worker_init,initargs=(mp_stop,))
    pending={};queue=iter(todo);leftover=[];unsent=[];cancelled=False
    def fill():
        while len(pending)<workers*2:
            job=next(queue,None)
            if job is None: return
            try: pending[pool.submit(_worker,job)]=job
            except BrokenProcessPool:
                unsent.append(job);raise
    def handle(job,kind,payload):
        nonlocal done,errors,finished
        track,key=job
        if kind=='ok':
            try:
                with LOCK: store_calculated(track,payload)
                done+=1
            except (OSError,ValueError) as exc: errors+=1;_record_error(track,exc)
        elif kind=='error': errors+=1;_record_error(track,payload)
        if kind!='cancelled': finished+=1
        if progress: progress(finished,total,'Audio ritam / refren: '+(track['title'] or ''))
    try:
        try:
            fill()
            while pending:
                if performance.enabled():
                    cancelled=True;mp_stop.set()
                if stop_event is not None and stop_event.is_set() and not cancelled:
                    cancelled=True;mp_stop.set()
                    for fut in list(pending):
                        if fut.cancel(): pending.pop(fut)
                finished_now,_=futures.wait(list(pending),timeout=.3,return_when=futures.FIRST_COMPLETED)
                for fut in finished_now:
                    job=pending[fut]
                    kind,payload=fut.result()
                    pending.pop(fut)
                    handle(job,kind,payload)
                if not cancelled: fill()
        except BrokenProcessPool:
            leftover=unsent+list(pending.values())+list(queue);pending.clear()
    finally:
        mp_stop.set()
        pool.shutdown(wait=True,cancel_futures=True)
    if leftover and not cancelled:  # a worker died (e.g. out of memory): finish the rest the old, serial way
        for track,_ in leftover:
            check_stop(stop_event)
            try:
                with LOCK: analyze(track,stop_event)
                done+=1
            except (OSError,ValueError) as exc: errors+=1;_record_error(track,exc)
            finished+=1
            if progress: progress(finished,total,'Audio ritam / refren: '+(track['title'] or ''))
    if cancelled: raise ScanCancelled({'ok':done,'errors':errors})
    if progress: progress(total,total,'Audio ritam / refren zavrsen')
    return {'ok':done,'errors':errors,'workers':workers}


def rhythm(a,b):
    import numpy as np
    pa,pb=np.array(a['profile']),np.array(b['profile'])
    score=float(np.clip(np.fft.irfft((np.conj(np.fft.rfft(pa,axis=1))*np.fft.rfft(pb,axis=1)).sum(axis=0),n=64).max(),0,1))*100
    delta=min(abs(a['bpm']-b['bpm']*factor)/a['bpm'] for factor in (.5,1,2))
    reliable=min(a['periodicity'],b['periodicity'])>=.18 and min(a['active_bands'],b['active_bands'])>=2
    return score,reliable,delta


def tonal_hint(a,b):
    """Fast pitch/time-offset invariant ordered phrase screening; never a pass."""
    import numpy as np
    aa,bb=np.array(a['chroma']),np.array(b['chroma'])
    # Resample to 24 steps; retain note order rather than mean pitch-class/key.
    aa=aa[np.linspace(0,len(aa)-1,24).astype(int)];bb=bb[np.linspace(0,len(bb)-1,24).astype(int)]
    corr=np.fft.ifft2(np.conj(np.fft.fft2(aa,s=(48,12)))*np.fft.fft2(bb,s=(48,12))).real
    return max(float(corr[offset%48].max())/(24-abs(offset)) for offset in range(-8,9))


def passing(item, options):
    # An embedding score is retrieval evidence, never proof of a shared phrase.
    # The full-track rhythm index is the next level, not blended into this one.
    phrase=item.get('phrase_verification') or {}
    tp=options['tonal'] and (phrase_match.reliable(phrase,options['tonal_cutoff']) or (sample_match.reliable(item.get('sample_verification',dict(count=0,anchors=0,seconds=0,ratio=0))) and item['tonal_score']>=options['tonal_cutoff']))
    periodic=options['rhythm'] and options['learned_model'] and loop_audio.reliable(item.get('periodic_verification'),options.get('periodic_cutoff',62))
    return False,bool(tp or periodic)


def rank(track, source, stop_event, publish=None):
    init();sections=json.loads(source['data']);song=sections[0].get('song_phrase');all_hits=[]
    targets={t['id']:t for t in db.rows('SELECT * FROM tracks')}
    for row in db.rows('SELECT * FROM auto_chorus WHERE status="ok" AND version=?',(VERSION,)):
        check_stop(stop_event)
        target=targets.get(row['track_id'])
        if row['track_id']==track['id'] or not target or not valid(row,target): continue
        parts=json.loads(row['data']);other=parts[0].get('song_phrase')
        if not song or not other:continue
        best_r=(0.,False,None,None);nn=0.
        for a in sections:
            for b in parts:
                score,reliable,_=rhythm(a['features'],b['features'])
                if best_r[2] is None or score>best_r[0]:best_r=(score,reliable,a,b)
                nn=max(nn,learned_audio.similarity(a['features'].get('neural'),b['features'].get('neural'))[0])
        periodic=loop_audio.compare(sections[0].get('song_loop'),parts[0].get('song_loop')) if settings()['learned_model'] else {}
        hint=phrase_match.screen(song,other,stop_event)
        a,b=best_r[2:]
        clean=lambda part:{k:v for k,v in part.items() if k not in ('features','song_phrase','song_loop')}
        hit=dict(id=target['id'],artist=target['artist'],title=target['title'],score=0.,groove=best_r[0],
            rhythm_score=best_r[0],rhythm_reliable=best_r[1],tonal_score=0.,tonal_reliable=False,tonal_checked=False,
            periodic_verification=periodic,neural_score=round(nn,2),source_section=clean(a),target_section=clean(b),reasons=[],track=target,
            _hint=hint,_song=other,_sample_hint=sample_match.screen(song['sample'],other['sample']) if song.get('sample') and other.get('sample') else 0.)
        all_hits.append(hit)
    # Independent phrase retrieval ensures a genuine phrase is not eliminated
    # by the largest generic embedding cosine elsewhere in a large library.
    selected={h['id']:h for h in sorted(all_hits,key=lambda h:h['_hint']['score'],reverse=True)[:48]}
    if settings()['learned_model']:
        selected.update({h['id']:h for h in sorted(all_hits,key=lambda h:h['neural_score'],reverse=True)[:24]})
    selected.update({h['id']:h for h in sorted(all_hits,key=lambda h:h['_sample_hint'],reverse=True)[:24] if h['_sample_hint']>0})
    shortlist=list(selected.values()) if settings()['tonal'] else []
    for hit in all_hits:
        periodic=hit.get('periodic_verification') or {}
        if settings()['rhythm'] and loop_audio.reliable(periodic,settings()['periodic_cutoff']):
            hit.update(phrase_verification=periodic,tonal_score=periodic['score'],tonal_checked=True)
    for i,hit in enumerate(shortlist):
        check_stop(stop_event)
        STATE['phase']=f'Proveravam redosled fraze {i+1}/{len(shortlist)}'
        result=phrase_match.compare(song,hit['_song'],stop_event,hint=hit['_hint'])
        if song.get('sample') and hit['_song'].get('sample'):
            fragment=sample_match.compare(song['sample'],hit['_song']['sample'],stop_event)
            hit['sample_verification']=fragment
            if sample_match.reliable(fragment):
                sample_score=min(100,80+20*fragment['ratio'])
                if sample_score>result['score'] or not phrase_match.reliable(result,settings()['tonal_cutoff']):
                    result=dict(score=sample_score,strength=fragment['anchors'],seconds=fragment['seconds'],density=fragment['ratio'],
                        contrast=fragment['ratio'],a_start=fragment['a_start'],b_start=fragment['b_start'],kind='sample')
        periodic=hit.get('periodic_verification') or {}
        if settings()['rhythm'] and loop_audio.reliable(periodic,settings()['periodic_cutoff']) and not phrase_match.reliable(result,settings()['tonal_cutoff']) and not (result.get('kind')=='sample' and result['score']>=settings()['tonal_cutoff']):result=periodic
        hit.update(phrase_verification=result,tonal_checked=True,tonal_score=result['score'],
            tonal_reliable=phrase_match.reliable(result,50),
            tonal_source_section={'start':result['a_start'],'end':result['a_start']+result['seconds']},
            tonal_target_section={'start':result['b_start'],'end':result['b_start']+result['seconds']})
        if publish and (i==0 or i%4==3 or passing(hit,settings())[1]):
            publish([{k:v for k,v in h.items() if not k.startswith('_')} for h in all_hits])
    for hit in all_hits:
        hit.pop('_hint',None);hit.pop('_song',None);hit.pop('_sample_hint',None)
    return all_hits


def rank_worker(track,source,options):
    from . import performance
    config.SETTINGS['chorus_audio']=options
    config.SETTINGS['audio_similarity_threshold']=options['rhythm_cutoff']/100
    try:
        hits=rank(track,source,performance.STOP) if source else []
        check_stop(performance.STOP)
        targets=db.rows('SELECT * FROM tracks WHERE on_disk=1')
        measured,report=audio_rhythm.rank_audio(track,targets)
        return hits,(measured,report)
    finally:db.conn().close();db._local.c=None


def rank_background(track,source,event):
    from concurrent.futures import ProcessPoolExecutor,wait
    import multiprocessing
    from . import performance
    ctx=multiprocessing.get_context('spawn');stop=ctx.Event()
    with ProcessPoolExecutor(1,mp_context=ctx,initializer=performance.initialize_worker,initargs=(stop,str(db.DB_FILE))) as pool:
        future=pool.submit(rank_worker,track,source,settings())
        while not future.done():
            if event.is_set() or performance.enabled():stop.set()
            wait([future],timeout=.1)
        check_stop(event)
        if performance.enabled():raise ScanCancelled()
        return future.result()


def ensure(track_id):
    """Coalesce current changes; obsolete work never publishes over the current song."""
    global CURRENT,THREAD,STOP,RESULT
    from . import performance
    if not track_id or performance.enabled() or SCANNING.is_set(): return
    with CONTROL:
        CURRENT=int(track_id)
        if THREAD and THREAD.is_alive():
            if STATE.get('track_id') != CURRENT: STOP.set()
            return
        track=db.one('SELECT * FROM tracks WHERE id=?',(CURRENT,))
        def stamp(path):
            try:
                stat=os.stat(path);return (path,stat.st_size,stat.st_mtime_ns)
            except (OSError,TypeError): return (path,None,None)
        source_token=(stamp(track['path'] if track else None),stamp(config.SETTINGS.get('nml_path')))
        key=(CURRENT,REVISION,json.dumps(settings(),sort_keys=True),source_token)
        if STATE.get('key')==key: return
        if RESULT and RESULT.get('source_token')!=source_token: RESULT=None
        if not settings()['live_scan']:
            init();audio_rhythm.init();cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(CURRENT,))
            if not track or ((not valid(cached,track) or cached['status']!='ok') and not audio_rhythm.valid(db.one('SELECT * FROM audio_rhythm WHERE track_id=?',(CURRENT,)),track)):
                RESULT=None
                STATE.update(track_id=CURRENT,error=None,phase='Live scan OFF — koristim samo sacuvanu bazu',key=key)
                return
        STOP=threading.Event();event=STOP
        def worker():
            global RESULT,REVISION
            tid=key[0];STATE.update(track_id=tid,error=None,phase='Proveravam audio indeks',key=key)
            try:
                init();refresh_cues();track=db.one('SELECT * FROM tracks WHERE id=?',(tid,))
                if not track: raise ValueError('Trenutna pesma nije u biblioteci.')
                cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(tid,))
                if not valid(cached,track):
                    if settings()['live_scan']:
                        STATE['phase']='Automatski skeniram refren trenutne pesme'
                        STATE['scanning']=True
                        try:
                            with LOCK: cached=analyze(track,event)
                        finally:STATE['scanning']=False
                    else:cached=None
                if cached and cached['status']!='ok': raise ValueError(cached.get('error') or 'Ponovi skeniranje pesme.')
                rank_revision=REVISION
                def publish_partial(hits):
                    global RESULT
                    with CONTROL:
                        if CURRENT==tid and not event.is_set():
                            RESULT={'track_id':tid,'source_token':source_token,'hits':hits,'sections':[{k:v for k,v in s.items() if k not in ('features','song_phrase','song_loop')} for s in json.loads(cached['data'])],'complete':False}
                hits,whole=rank_background(track,cached,event);check_stop(event)
                with CONTROL:
                    if CURRENT==tid:
                        changed_during_rank=REVISION!=rank_revision
                        RESULT={'track_id':tid,'source_token':source_token,'hits':hits,'sections':[{k:v for k,v in part.items() if k not in ('features','song_phrase','song_loop')} for part in json.loads(cached['data'])] if cached else [],'complete':True}
                        audio_rhythm.PREPARED={'track_id':tid,'revision':audio_rhythm.SCAN_REVISION,'whole':whole,'source_stamp':source_token[0][1:]}
                        REVISION+=1
                        STATE.update(phase='Spremno',key=None if changed_during_rank else (tid,REVISION,json.dumps(settings(),sort_keys=True),source_token))
            except ScanCancelled:
                STATE.update(phase='Promenjena trenutna pesma',key=None)
            except Exception as exc:
                STATE.update(error=str(exc),phase='Audio nije spreman',key=(tid,REVISION,json.dumps(settings(),sort_keys=True),source_token))
            finally:
                db.conn().close();db._local.c=None
        THREAD=threading.Thread(target=worker,name='auto-chorus-live',daemon=True);THREAD.start()


def fifth(current, pool, excluded_ids, diagnostics):
    from .names import core_title
    ensure(current['id']);options=settings()
    report=dict(engine='chorus',current_id=current['id'],threshold=options['rhythm_cutoff']/100,options=options,
        pool=len(pool),compared=0,counts={},top=[],accepted_top=[],sections=[],current_error=None,
        shortlist_limit=96,learned_model=learned_audio.info(),selected_stage='normal',
        priority=['Potvrdjena fraza / refren','Ritam cele pesme iz baze','Izvorni predlog'])
    with CONTROL: result=RESULT
    ids={t['id'] for t in pool}-set(excluded_ids)
    hits=[]
    if result and result['track_id']==current['id']:
        for original in result['hits']:
            if original['id'] not in ids:continue
            hit={k:v for k,v in original.items() if k!='track'};rp,tp=passing(hit,options)
            hit.update(rhythm_pass=rp,tonal_pass=tp,accepted=tp,score=hit['tonal_score'],
                match_kind=('AI ritam fraze · TEST' if hit.get('phrase_verification',{}).get('kind')=='periodic' else 'Potvrdjena fraza') if tp else 'Nije potvrdjena fraza')
            if not tp:hit['reasons']=['AI skor i opsti beat nisu potvrda poklapanja. Fraza nije prosla redosled, trajanje i kontrolu slucajnog poklapanja.']
            hits.append(hit)
        report['sections']=result['sections'];report['complete']=result.get('complete',True)
    else:report['current_error']=STATE.get('error') or STATE['phase']
    hits.sort(key=lambda h:(h['accepted'],h['score'],h.get('phrase_verification',{}).get('strength',0),h['id']*-1),reverse=True)
    unique=[];seen=set()
    for hit in hits:
        identity=core_title(hit.get('artist') or '',hit.get('title') or '') or str(hit['id'])
        if hit['accepted'] and identity not in seen:unique.append(hit);seen.add(identity)
    report.update(compared=len(hits),top=hits[:20],accepted_top=unique[:10],counts={'potvrdjena_fraza':len(unique)})
    if unique:
        report['selected_stage']='chorus';diagnostics.update(report)
        hit=unique[0];track=next(t for t in pool if t['id']==hit['id']);proof=hit['phrase_verification']
        proof_note=(f"Eksperimentalni AI obrazac: skor {proof['score']:.1f}, kontrola {proof['control_score']:.1f}; razlika {proof['gap']:.1f}. Nije potvrda identicnog sempla." if proof.get('kind')=='periodic' else f"Zajednicki audio fragment: {proof['seconds']:.1f}s; {hit['sample_verification']['anchors']} vremenski uskladjenih spektralnih tacaka." if proof.get('kind')=='sample' else f"Potvrdjena fraza: {proof['seconds']:.1f}s; redosled {proof['density']:.0%}; kontrast prema kontrolama {proof['contrast']:.2f}x.")
        return {**track,'score':hit['score'],'label':'AI FRAZA · TEST' if proof.get('kind')=='periodic' else 'REFREN / FRAZA','audio_match':True,'audio_stage':'chorus',
            'why':[proof_note,
                   f"Izvor {proof['a_start']:.1f}s / cilj {proof['b_start']:.1f}s. AI sluzi za pronalazenje kandidata, ne za presudu."]},None
    # Level two uses the existing saved whole-track descriptors and its own
    # quality / tempo / threshold gates. No generic excerpt beat fills LIVE.
    fallback={}
    from . import performance
    if performance.enabled() and not (audio_rhythm.PREPARED and audio_rhythm.PREPARED['track_id']==current['id'] and audio_rhythm.PREPARED['revision']==audio_rhythm.SCAN_REVISION):
        report['fallback_reason']='Gig mod: teska audio poredjenja su pauzirana.'
        diagnostics.update(report)
        return None,report['fallback_reason']
    audio,message=audio_rhythm.fifth(current,pool,excluded_ids,fallback) if options['rhythm'] else (None,'Ritam cele pesme je iskljucen.')
    report['whole_rhythm']=fallback
    if audio:
        report['selected_stage']='whole_rhythm'
        report['accepted_top']=[dict(h,match_kind='Ritam cele pesme',rhythm_score=h['groove'],tonal_score=0.,tonal_pass=False,
             rhythm_pass=True,neural_score=0.,source_section={},target_section={}) for h in fallback['accepted_top']]
        audio.update(audio_stage='whole_rhythm');diagnostics.update(report)
        return audio,None
    report['fallback_reason']=message;diagnostics.update(report)
    return None,'Nema potvrdjene fraze ni prihvatljivog ritma cele pesme; koristi se izvorni peti predlog.'


def status():
    init()
    return {'settings':settings(),'counts':{r['status']:r['n'] for r in db.rows('SELECT status,COUNT(*) n FROM auto_chorus WHERE version=? GROUP BY status',(VERSION,))},'previous_index':db.one('SELECT COUNT(*) n FROM auto_chorus WHERE version!=?',(VERSION,))['n'],'live':dict(STATE),'revision':REVISION,'model':learned_audio.info(),'note':'AI pronalazi kandidate; poklapanje se potvrdjuje redosledom fraze u vremenu i kontrolom izmesanih delova. Peti i LIVE: potvrdjena fraza, zatim strogi ritam cele pesme, zatim samo levo izvorni predlog. LIVE se ne dopunjava nasumicno.'}
