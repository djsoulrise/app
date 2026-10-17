"""On-demand comparison of two selected excerpts. Independent of recommendations."""
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from . import audio_rhythm, db
from .scan_control import check_stop

VERSION = 1
LAST = None


def bounds(start, end):
    start, end = float(start), float(end)
    if not math.isfinite(start+end) or start<0 or not 12<=end-start<=60:
        raise ValueError('Izaberi od 12 do 60 sekundi refrena; pocetak mora biti 0 ili veci.')
    return round(start,2),round(end,2)


def init():
    db.conn().execute('''CREATE TABLE IF NOT EXISTS chorus_cache(
        track_id INTEGER,start REAL,end REAL,path TEXT,size INTEGER,mtime INTEGER,
        version INTEGER,features TEXT,PRIMARY KEY(track_id,start,end))''')
    db.conn().commit()


def extract(samples, sr=11025, stop_event=None):
    import numpy as np
    from scipy.signal import correlate,find_peaks
    check_stop(stop_event)
    if len(samples)<sr*11.8: raise ValueError('Izabrani deo ima manje od 12 sekundi zvuka.')
    fft,hop=2048,256
    windows=np.lib.stride_tricks.sliding_window_view(np.asarray(samples),fft)[::hop]
    magnitude=np.abs(np.fft.rfft(windows*np.hanning(fft),axis=1)).astype('float32')
    frequencies=np.fft.rfftfreq(fft,1/sr)
    flux=np.maximum(0,np.diff(np.log1p(magnitude),axis=0,prepend=np.zeros((1,magnitude.shape[1]))))
    bands=np.stack([flux[:,(frequencies>=a)&(frequencies<b)].mean(axis=1)
                    for a,b in ((30,180),(180,1800),(1800,5500))],axis=1)
    bands=np.maximum(0,bands-np.median(bands,axis=0))
    energy=bands.sum(axis=1);centered=energy-energy.mean()
    ac=correlate(centered,centered,mode='full',method='fft')[len(energy)-1:]
    hz=sr/hop;lo,hi=int(hz*60/180),int(hz*60/70)
    peaks,_=find_peaks(ac[lo:hi+1])
    if float(ac[0])<1e-6 or not len(peaks): raise ValueError('U izabranom delu nema dovoljno jasnog ritma.')
    lag=int(max(peaks+lo,key=lambda p:ac[p]));period=float(lag)
    denom=ac[lag-1]-2*ac[lag]+ac[lag+1]
    if abs(denom)>1e-8: period+=float(.5*(ac[lag-1]-ac[lag+1])/denom)
    phase=((np.arange(len(bands))/period)%4*16).astype(int)%64
    counts=np.maximum(1,np.bincount(phase,minlength=64))
    profile=np.stack([np.bincount(phase,weights=bands[:,b],minlength=64)/counts for b in range(3)])
    profile-=profile.mean(axis=1,keepdims=True)
    norms=np.linalg.norm(profile,axis=1);active=int((norms>1e-5).sum())
    profile/=np.maximum(norms[:,None],1e-8);profile/=max(float(np.linalg.norm(profile)),1e-8)
    # Coarse chromagram for local alignment, allowing a different key and timing.
    chroma=np.zeros((len(magnitude),12),dtype='float32')
    valid=(frequencies>=100)&(frequencies<4000)
    notes=np.rint(69+12*np.log2(frequencies[valid]/440)).astype(int)%12
    spectral=np.sqrt(magnitude[:,valid])
    for note in range(12): chroma[:,note]=spectral[:,notes==note].sum(axis=1)
    chroma=np.maximum(0,chroma-np.quantile(chroma,.2,axis=1,keepdims=True))
    step=max(1,round(hz/5));chroma=np.array([chroma[i:i+step].mean(axis=0) for i in range(0,len(chroma),step)])
    chroma/=np.maximum(np.linalg.norm(chroma,axis=1,keepdims=True),1e-8)
    check_stop(stop_event)
    return {'bpm':round(60*hz/period,2),'periodicity':round(float(ac[lag]/ac[0]),3),
            'active_bands':active,'profile':profile.round(5).tolist(),'chroma':chroma.round(4).tolist(),
            'chroma_hz':hz/step,'tonal_variation':float(chroma.std(axis=0).mean()),'seconds':len(samples)/sr}


def features(track,start,end,stop_event=None):
    import numpy as np
    init();stat=os.stat(track['path'])
    cached=db.one('SELECT * FROM chorus_cache WHERE track_id=? AND start=? AND end=?',(track['id'],start,end))
    if cached and cached['version']==VERSION and cached['path']==track['path'] and cached['size']==stat.st_size and cached['mtime']==stat.st_mtime_ns:
        return json.loads(cached['features'])
    with tempfile.TemporaryDirectory(prefix='dj-chorus-') as folder:
        pcm=Path(folder)/'part.f32'
        command=[audio_rhythm.decoder(),'-v','error','-nostdin','-ss',str(start),'-i',track['path'],
                 '-t',str(end-start),'-map','0:a:0','-vn','-ac','1','-ar','11025','-f','f32le',str(pcm)]
        with subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
            try:
                deadline=time.monotonic()+180
                while True:
                    check_stop(stop_event)
                    if time.monotonic()>deadline: raise ValueError('Dekodiranje refrena je predugo trajalo.')
                    try: _,error=proc.communicate(timeout=.2);break
                    except subprocess.TimeoutExpired: pass
            except BaseException:
                proc.kill();proc.communicate();raise
        if proc.returncode or not pcm.is_file(): raise ValueError('Refren nije dekodiran: '+error.decode(errors='replace')[-150:])
        data=extract(np.fromfile(pcm,dtype='<f4'),stop_event=stop_event)
    check_stop(stop_event)
    db.conn().execute('INSERT OR REPLACE INTO chorus_cache VALUES(?,?,?,?,?,?,?,?)',
        (track['id'],start,end,track['path'],stat.st_size,stat.st_mtime_ns,VERSION,json.dumps(data)))
    db.conn().commit();return data


def align(a,b,stop_event=None):
    """Smith-Waterman local alignment over chroma; scores are heuristic, not probability."""
    import numpy as np
    aa,bb=np.array(a['chroma']),np.array(b['chroma']);best=None
    for shift in range(12):
        check_stop(stop_event)
        similarity=aa@np.roll(bb,shift,axis=1).T
        scores=np.zeros((len(aa)+1,len(bb)+1),dtype='float32');trace=np.zeros_like(scores,dtype='uint8')
        for i in range(1,len(aa)+1):
            if i % 16 == 0: check_stop(stop_event)
            for j in range(1,len(bb)+1):
                choices=(0,scores[i-1,j-1]+(similarity[i-1,j-1]-.68)*3,scores[i-1,j]-.35,scores[i,j-1]-.35)
                way=int(np.argmax(choices));scores[i,j]=choices[way];trace[i,j]=way
        i,j=np.unravel_index(np.argmax(scores),scores.shape);peak=float(scores[i,j])
        if best and peak<=best['alignment_strength']: continue
        end_i,end_j=i,j;matches=[]
        while i>0 and j>0 and trace[i,j]:
            way=trace[i,j]
            if way==1: matches.append(float(similarity[i-1,j-1]));i-=1;j-=1
            elif way==2: i-=1
            else: j-=1
        coverage=min((end_i-i)/max(1,len(aa)),(end_j-j)/max(1,len(bb)))
        best={'alignment_strength':peak,'tonal_score':round((sum(matches)/max(1,len(matches)))*100,1),
              'coverage':round(coverage*100,1),'pitch_shift':shift if shift<=6 else shift-12,
              'a_from':round(i/a['chroma_hz'],1),'a_to':round(end_i/a['chroma_hz'],1),
              'b_from':round(j/b['chroma_hz'],1),'b_to':round(end_j/b['chroma_hz'],1)}
    return best


def compare(a,b,stop_event=None,rhythm_cutoff=90,tonal_cutoff=85):
    import numpy as np
    pa,pb=np.array(a['profile']),np.array(b['profile'])
    correlation=np.fft.irfft((np.conj(np.fft.rfft(pa,axis=1))*np.fft.rfft(pb,axis=1)).sum(axis=0),n=64)
    rhythm=float(np.clip(correlation.max(),0,1))
    tonal=align(a,b,stop_event)
    notes=[]
    if min(a['periodicity'],b['periodicity'])<.18: notes.append('Ritam u jednom delu nije dovoljno pouzdan.')
    if min(a['active_bands'],b['active_bands'])<2: notes.append('Malo ritmickih detalja: zajednicki kick nije dokaz istog ritma.')
    if min(a['tonal_variation'],b['tonal_variation'])<.04: notes.append('Malo tonalnih promena; isti akord nije dokaz iste melodije.')
    rhythm_pass = rhythm*100 >= rhythm_cutoff and min(a['periodicity'],b['periodicity'])>=.18 and min(a['active_bands'],b['active_bands'])>=2
    tonal_seconds = min(tonal['a_to']-tonal['a_from'],tonal['b_to']-tonal['b_from'])
    tonal_pass = tonal['tonal_score']>=tonal_cutoff and tonal['coverage']>=60 and tonal_seconds>=8 and min(a['tonal_variation'],b['tonal_variation'])>=.04
    return {'rhythm_pass':rhythm_pass,'tonal_pass':tonal_pass,'either_pass':rhythm_pass or tonal_pass,
            'rhythm_cutoff':rhythm_cutoff,'tonal_cutoff':tonal_cutoff,'rhythm_score':round(rhythm*100,1),'bpm_a':a['bpm'],'bpm_b':b['bpm'],
            'tonal':tonal,'notes':notes,'disclaimer':'Poklapanje je heuristicko; nije potvrda identicnog snimka, sempla ili autorstva.'}


def compare_pair(body,progress=None,stop_event=None):
    global LAST
    LAST=None
    tracks=[];ranges=[];data=[]
    for side in ('a','b'):
        t=db.one('SELECT id,path,artist,title FROM tracks WHERE id=?',(int(body[side]),))
        if not t: raise ValueError('Pesma nije pronadjena u biblioteci.')
        tracks.append(dict(t));ranges.append(bounds(body[side+'_start'],body[side+'_end']))
    for i,(track,(start,end)) in enumerate(zip(tracks,ranges)):
        if progress: progress(i,2,'Citam samo odabrani refren: '+(track['title'] or ''))
        data.append(features(track,start,end,stop_event))
    result=compare(*data,stop_event=stop_event,rhythm_cutoff=float(body.get('rhythm_cutoff',90)),tonal_cutoff=float(body.get('tonal_cutoff',85)))
    for key,track,(start,end) in zip(('a','b'),tracks,ranges):
        result[key]={'id':track['id'],'artist':track['artist'],'title':track['title'],'start':start,'end':end}
    for side in ('a','b'):
        for edge in ('from','to'): result['tonal'][side+'_'+edge]=round(result['tonal'][side+'_'+edge]+result[side]['start'],1)
    check_stop(stop_event);LAST=result
    if progress: progress(2,2,'Refreni uporedjeni')
    return result
