"""Automatic, persistent chorus signatures. Read-only audio/NML; no Traktor writes."""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from . import audio_rhythm, chorus, config, db
from .scan_control import check_stop, ScanCancelled

VERSION = 1
REVISION = 0
LOCK = threading.Lock()
CONTROL = threading.RLock()
STOP = threading.Event()
NML_STAMP = None
CURRENT = None
THREAD = None
RESULT = None
STATE = {'phase':'Miruje', 'track_id':None, 'error':None}
DEFAULTS = dict(rhythm=True, tonal=True, rhythm_cutoff=90, tonal_cutoff=85, auto_scan=True)


def settings(body=None):
    result = {**DEFAULTS, **config.SETTINGS.get('chorus_audio', {})}
    if body is not None:
        for key in ('rhythm','tonal','auto_scan'):
            if key in body:
                if not isinstance(body[key], bool): raise ValueError('Opcija mora biti ukljucena ili iskljucena.')
                result[key] = body[key]
        for key in ('rhythm_cutoff','tonal_cutoff'):
            if key in body:
                value = float(body[key])
                if not 50 <= value <= 100: raise ValueError('Prag mora biti od 50 do 100%.')
                result[key] = value
        if not result['rhythm'] and not result['tonal']: raise ValueError('Ukljuci bar jedan audio uslov.')
        config.SETTINGS['chorus_audio'] = result
        config.save(config.SETTINGS)
    return result


def init():
    db.conn().execute('''CREATE TABLE IF NOT EXISTS auto_chorus(
      track_id INTEGER PRIMARY KEY,path TEXT,size INTEGER,mtime INTEGER,version INTEGER,
      status TEXT,data TEXT,error TEXT,cue_key TEXT)''')
    db.conn().commit()


def cue_key(track):
    return json.dumps(db.rows('SELECT start,name,type FROM track_cues WHERE track_id=? ORDER BY start',(track['id'],)),sort_keys=True)


def valid(row, track):
    if not row or row['version'] != VERSION or row['path'] != track['path']: return False
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


def analyze(track, stop_event=None):
    import numpy as np
    init();cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(track['id'],))
    if valid(cached,track) and cached['status']=='ok': return cached
    stat=os.stat(track['path']);key=cue_key(track)
    with tempfile.TemporaryDirectory(prefix='dj-auto-chorus-') as folder:
        pcm=Path(folder)/'mono.f32'
        command=[audio_rhythm.decoder(),'-v','error','-nostdin','-i',track['path'],'-map','0:a:0','-vn','-ac','1','-ar','11025','-f','f32le',str(pcm)]
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
            data=[]
            for section in sections:
                check_stop(stop_event)
                try: features=chorus.extract(samples[int(section['start']*11025):int(section['end']*11025)],stop_event=stop_event,require_rhythm=False)
                except ValueError: continue
                data.append({**section,'features':features})
            if not data: raise ValueError('U kandidatima za refren nema dovoljno audio detalja.')
        finally: del samples
    # Do not cache a signature against a file/cue revision that changed mid-read.
    end=os.stat(track['path'])
    if (stat.st_size,stat.st_mtime_ns,key)!=(end.st_size,end.st_mtime_ns,cue_key(track)): raise ValueError('Pesma ili cue su promenjeni tokom skena; ponovi sken.')
    check_stop(stop_event)
    db.conn().execute('INSERT OR REPLACE INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?)',(track['id'],track['path'],stat.st_size,stat.st_mtime_ns,VERSION,'ok',json.dumps(data),None,key))
    db.conn().commit()
    global REVISION
    REVISION+=1
    return db.one('SELECT * FROM auto_chorus WHERE track_id=?',(track['id'],))


def refresh_cues():
    """Refresh cue hints without reimporting metadata or writing the collection."""
    global NML_STAMP
    from . import nml
    path=config.SETTINGS.get('nml_path')
    if not path or not os.path.isfile(path): return
    stat=os.stat(path);stamp=(path,stat.st_size,stat.st_mtime_ns)
    if stamp==NML_STAMP: return
    tracks,_=nml.parse_collection(path)
    ids={t['pkey']:t['id'] for t in db.rows('SELECT id,pkey FROM tracks')}
    for track in tracks:
        tid=ids.get(track['pkey'])
        if tid is None: continue
        db.conn().execute('DELETE FROM track_cues WHERE track_id=?',(tid,))
        db.conn().executemany('INSERT INTO track_cues VALUES(?,?,?,?)',[(tid,c['start'],c['name'],c['type']) for c in track.get('cue_positions',[])])
    db.conn().commit();NML_STAMP=stamp


def scan(progress=None, stop_event=None):
    init();refresh_cues();tracks=db.rows('SELECT * FROM tracks WHERE on_disk=1 ORDER BY id');done=errors=0
    for i,track in enumerate(tracks):
        check_stop(stop_event)
        if progress: progress(i,len(tracks),'Audio ritam / refren: '+(track['title'] or ''))
        try:
            with LOCK: analyze(track,stop_event)
            done+=1
        except (OSError,ValueError) as exc:
            errors+=1
            # Errors are visible and retryable by the next explicit full scan.
            try:
                stat=os.stat(track['path'])
                db.conn().execute('INSERT OR REPLACE INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?)',(track['id'],track['path'],stat.st_size,stat.st_mtime_ns,VERSION,'error',None,str(exc),cue_key(track)))
                db.conn().commit()
            except OSError: pass
    if progress: progress(len(tracks),len(tracks),'Audio ritam / refren zavrsen')
    return {'ok':done,'errors':errors}


def rhythm(a,b):
    import numpy as np
    pa,pb=np.array(a['profile']),np.array(b['profile'])
    score=float(np.clip(np.fft.irfft((np.conj(np.fft.rfft(pa,axis=1))*np.fft.rfft(pb,axis=1)).sum(axis=0),n=64).max(),0,1))*100
    delta=min(abs(a['bpm']-b['bpm']*factor)/a['bpm'] for factor in (.5,1,2))
    reliable=min(a['periodicity'],b['periodicity'])>=.18 and min(a['active_bands'],b['active_bands'])>=2 and delta<=.04
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
    rp=options['rhythm'] and item['rhythm_reliable'] and item['rhythm_score']>=options['rhythm_cutoff']
    tp=options['tonal'] and item['tonal_reliable'] and item['tonal_score']>=options['tonal_cutoff']
    return bool(rp),bool(tp)


def rank(track, source, stop_event):
    init();sections=json.loads(source['data']);all_hits=[]
    rows=db.rows('SELECT * FROM auto_chorus WHERE status="ok" AND version=?',(VERSION,))
    for row in rows:
        check_stop(stop_event)
        if row['track_id']==track['id']: continue
        target=db.one('SELECT * FROM tracks WHERE id=?',(row['track_id'],))
        if not target or not valid(row,target): continue
        best=None;tonal_pairs=[]
        for a in sections:
            for b in json.loads(row['data']):
                score,reliable,delta=rhythm(a['features'],b['features'])
                entry=dict(id=target['id'],artist=target['artist'],title=target['title'],score=score,groove=score,rhythm_score=score,rhythm_reliable=reliable,tempo_delta=delta*100,audio={'bpm':b['features']['bpm']},tonal_score=0.,tonal_reliable=False,source_section={k:v for k,v in a.items() if k!='features'},target_section={k:v for k,v in b.items() if k!='features'},reasons=[],track=target)
                if best is None or score>best['rhythm_score']: best=entry
                tonal_pairs.append((tonal_hint(a['features'],b['features']),a,b))
        hint,a,b=max(tonal_pairs,key=lambda p:p[0]);best['_tonal_pair']=(a,b);best['_hint']=hint
        all_hits.append(best)
    # Verify a broad ordered-chroma shortlist using local alignment. Screening is
    # disclosed; unverified candidates can pass rhythm, never tonal.
    shortlist=sorted(all_hits,key=lambda h:h['_hint'],reverse=True)[:120]
    for i,hit in enumerate(shortlist):
        check_stop(stop_event)
        STATE['phase']=f'Proveravam tonalne delove {i+1}/{len(shortlist)}'
        a,b=hit['_tonal_pair'];aa,bb=a['features'],b['features'];aligned=chorus.align(aa,bb,stop_event)
        seconds=min(aligned['a_to']-aligned['a_from'],aligned['b_to']-aligned['b_from'])
        hit['tonal_score']=aligned['tonal_score'];hit['tonal_reliable']=aligned['coverage']>=60 and seconds>=8 and min(aa['tonal_variation'],bb['tonal_variation'])>=.04
        hit['tonal_alignment']=aligned;hit['tonal_source_section']={k:v for k,v in a.items() if k!='features'};hit['tonal_target_section']={k:v for k,v in b.items() if k!='features'}
    for hit in all_hits: hit.pop('_tonal_pair',None);hit.pop('_hint',None)
    return all_hits


def ensure(track_id):
    """Coalesce current changes; obsolete work never publishes over the current song."""
    global CURRENT,THREAD,STOP,RESULT
    if not track_id: return
    with CONTROL:
        CURRENT=int(track_id)
        if THREAD and THREAD.is_alive():
            if STATE.get('track_id') != CURRENT: STOP.set()
            return
        track=db.one('SELECT path FROM tracks WHERE id=?',(CURRENT,))
        def stamp(path):
            try:
                stat=os.stat(path);return (path,stat.st_size,stat.st_mtime_ns)
            except (OSError,TypeError): return (path,None,None)
        source_token=(stamp(track['path'] if track else None),stamp(config.SETTINGS.get('nml_path')))
        key=(CURRENT,REVISION,json.dumps(settings(),sort_keys=True),source_token)
        if STATE.get('key')==key: return
        if RESULT and RESULT.get('source_token')!=source_token: RESULT=None
        STOP=threading.Event();event=STOP
        def worker():
            global RESULT,REVISION
            tid=key[0];STATE.update(track_id=tid,error=None,phase='Proveravam audio indeks',key=key)
            try:
                init();refresh_cues();track=db.one('SELECT * FROM tracks WHERE id=?',(tid,))
                if not track: raise ValueError('Trenutna pesma nije u biblioteci.')
                cached=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(tid,))
                if not valid(cached,track):
                    if not settings()['auto_scan']: raise ValueError('Trenutna pesma nije skenirana. Ukljuci automatski sken ili skeniraj biblioteku.')
                    STATE['phase']='Automatski skeniram refren trenutne pesme'
                    with LOCK: cached=analyze(track,event)
                if cached['status']!='ok': raise ValueError(cached.get('error') or 'Ponovi skeniranje pesme.')
                rank_revision=REVISION
                hits=rank(track,cached,event);check_stop(event)
                with CONTROL:
                    if CURRENT==tid:
                        RESULT={'track_id':tid,'source_token':source_token,'hits':hits,'sections':[{k:v for k,v in s.items() if k!='features'} for s in json.loads(cached['data'])]}
                        STATE.update(phase='Spremno',key=(tid,REVISION,json.dumps(settings(),sort_keys=True),source_token))
                        REVISION+=1
                        STATE['key']=(tid,rank_revision+1,key[2],source_token)
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
    report=dict(engine='chorus',current_id=current['id'],threshold=options['rhythm_cutoff']/100,options=options,pool=len(pool),compared=0,counts={},top=[],accepted_top=[],sections=[],current_error=None,shortlist_limit=120)
    with CONTROL: result=RESULT
    if not result or result['track_id']!=current['id']:
        report['current_error']=STATE.get('error') or STATE['phase'];diagnostics.update(report)
        return None,report['current_error']
    ids={t['id'] for t in pool}-set(excluded_ids);hits=[]
    for original in result['hits']:
        if original['id'] not in ids: continue
        hit={k:v for k,v in original.items() if k!='track'};rp,tp=passing(hit,options)
        hit.update(rhythm_pass=rp,tonal_pass=tp,accepted=rp or tp,score=max(hit['rhythm_score'] if rp else 0,hit['tonal_score'] if tp else 0),match_kind='R + T' if rp and tp else 'Ritam' if rp else 'Tonalno' if tp else 'Ne prolazi')
        if not hit['accepted']: hit['reasons']=['Ne prolazi aktivne audio uslove (tonalno se detaljno proverava do 120 najblizih).']
        hits.append(hit)
    hits.sort(key=lambda h:(h['accepted'],h['rhythm_pass'] and h['tonal_pass'],h['score'],h['rhythm_score']),reverse=True)
    unique=[];seen=set()
    for hit in hits:
        identity=core_title(hit['artist'] or '',hit['title'] or '')
        if hit['accepted'] and identity not in seen: unique.append(hit);seen.add(identity)
    report.update(compared=len(hits),top=hits[:20],accepted_top=unique[:10],sections=result['sections'],counts={'prolazi':len(unique),'ne_prolazi':len(hits)-sum(h['accepted'] for h in hits)})
    diagnostics.update(report)
    if not unique: return None,'Nema refrena koji prolaze aktivne audio uslove.'
    hit=unique[0];track=next(t for t in pool if t['id']==hit['id'])
    return {**track,'score':hit['score'],'label':'AUDIO RITAM','audio_match':True,'why':[f"Refren: {hit['match_kind']} · R {hit['rhythm_score']:.1f}% / T {hit['tonal_score']:.1f}%"]},None


def status():
    init()
    return {'settings':settings(),'counts':{r['status']:r['n'] for r in db.rows('SELECT status,COUNT(*) n FROM auto_chorus GROUP BY status')},'live':dict(STATE),'revision':REVISION,'note':'Tonalna detaljna provera: do 120 najblizih po redosledu hroma. Skor je heuristika; ne identitet glasa.'}
