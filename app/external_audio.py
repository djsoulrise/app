"""Read precomputed audio results only. No decoder, model, NumPy or MIDI."""
import hashlib,json,math,ntpath,os,tempfile,zipfile
from pathlib import Path
from . import db,config,suggest,library,styles
from .names import core_title
from .scan_control import ScanCancelled
ENGINE='essentia-wasm-hpcp-local-v1'
THRESHOLDS={'tonal':55.,'rhythm':65.,'sample':70.}
CONTRAST={'tonal':1.6,'rhythm':1.8,'sample':1.7}
SECONDS={'tonal':8.,'rhythm':8.,'sample':6.}
LABELS={'tonal':'zajednicka fraza','rhythm':'ritmicki obrazac','sample':'zvucni fragment'}


def init():
    db.conn().executescript('''CREATE TABLE IF NOT EXISTS external_audio_map(track_id INTEGER PRIMARY KEY,hash TEXT,size INTEGER,mtime INTEGER);
    CREATE INDEX IF NOT EXISTS ix_external_hash ON external_audio_map(hash);
    CREATE TABLE IF NOT EXISTS external_audio_pairs(a TEXT,b TEXT,metrics TEXT,score REAL,PRIMARY KEY(a,b));
    CREATE INDEX IF NOT EXISTS ix_external_b ON external_audio_pairs(b);
    ''')


def status():
    init();return {'mapped':db.one('SELECT COUNT(*) n FROM external_audio_map')['n'],'pairs':db.one('SELECT COUNT(*) n FROM external_audio_pairs')['n'],
        'source':config.SETTINGS.get('additional_source','original'),'thresholds':config.SETTINGS.get('external_audio_thresholds',THRESHOLDS),
        'last_import':config.SETTINGS.get('external_audio_import'),'database':str(db.DB_FILE),'engine':ENGINE}


def digest(path,stop=None):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        while block:=stream.read(1024*1024):
            if stop is not None and stop.is_set():raise ScanCancelled({'phase':'Provera fajlova zaustavljena; prethodna baza je sacuvana.'})
            h.update(block)
    return h.hexdigest()


def validate(data):
    if data.get('format')!='dj-miljan-audio-results' or data.get('schema')!=1 or data.get('engine')!=ENGINE:raise ValueError('Nekompatibilna verzija audio baze.')
    tracks=data.get('tracks');pairs=data.get('pairs')
    if not isinstance(tracks,list) or not isinstance(pairs,list) or len(tracks)>100000 or len(pairs)>1000000:raise ValueError('Neispravna ili prevelika baza.')
    known=set()
    for t in tracks:
        h=t.get('hash','')
        if len(h)!=64 or any(c not in '0123456789abcdef' for c in h):raise ValueError('Neispravan identitet audio fajla.')
        if not isinstance(t.get('size'),int) or t['size']<0:raise ValueError('Neispravna velicina fajla.')
        known.add(h)
    for p in pairs:
        if p.get('a') not in known or p.get('b') not in known or p['a']==p['b']:raise ValueError('Neispravna veza izmedju pesama.')
        if not isinstance(p.get('metrics'),dict):raise ValueError('Nedostaju audio rezultati.')
        for kind,m in p['metrics'].items():
            if kind not in THRESHOLDS:raise ValueError('Nepoznata vrsta audio rezultata.')
            for field in ('score','contrast','seconds'):
                value=m.get(field)
                if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Neispravan audio skor.')
            if m['score']>100:raise ValueError('Skor je izvan opsega.')
            for field in ('a_section','b_section'):
                section=m.get(field)
                if not isinstance(section,list) or len(section)!=2 or not all(isinstance(x,(float,int)) and math.isfinite(x) and x>=0 for x in section) or section[1]<section[0]:raise ValueError('Neispravan vremenski interval.')
    return tracks,pairs


def import_file(path,progress=None,stop=None):
    init()
    with zipfile.ZipFile(path) as archive:
        info=archive.getinfo('manifest.json')
        if info.file_size>100*1024**2:raise ValueError('Manifest je veci od 100 MB.')
        data=json.loads(archive.read(info))
    tracks,pairs=validate(data);existing=db.rows('SELECT id,path,title,artist FROM tracks');by_size={};by_path={}
    for item in existing:
        try:
            stat=Path(item['path'] or '').stat()
            if not Path(item['path']).is_file():continue
        except (OSError,TypeError):continue
        value={**item,'size':stat.st_size,'mtime':stat.st_mtime_ns};by_size.setdefault(stat.st_size,[]).append(value);by_path[os.path.normcase(os.path.abspath(item['path']))]=value
    mapped={};missing=[];hashes={};mappings=[]
    for i,t in enumerate(tracks):
        if stop is not None and stop.is_set():raise ScanCancelled({'mapped':len(mappings),'not_imported':True})
        if progress:progress(i,len(tracks),'Povezujem fajlove po sadrzaju: '+str(t.get('name') or 'pesma'))
        candidates=by_size.get(t['size'],[])
        preferred=by_path.get(os.path.normcase(os.path.abspath(t.get('path') or '')))
        if preferred:candidates=[preferred]+[x for x in candidates if x['id']!=preferred['id']]
        name=ntpath.basename(t.get('name') or t.get('path') or '').casefold()
        candidates=sorted(candidates,key=lambda x:ntpath.basename(x['path']).casefold()!=name)
        matches=[]
        for candidate in candidates:
            key=(os.path.normcase(candidate['path']),candidate['size'],candidate['mtime'])
            if key not in hashes:
                try:hashes[key]=digest(candidate['path'],stop)
                except OSError:hashes[key]=None
            if hashes[key]==t['hash']:
                st=Path(candidate['path']).stat()
                if (st.st_size,st.st_mtime_ns)!=(candidate['size'],candidate['mtime']):continue
                matches.append(candidate)
        if not matches:missing.append(t.get('name') or t.get('path'));continue
        mapped[t['hash']]=True
        mappings.extend((x['id'],t['hash'],x['size'],x['mtime']) for x in matches)
    # All-or-nothing replacement: a stopped/invalid import preserves the old base.
    if stop is not None and stop.is_set():raise ScanCancelled({'not_imported':True})
    c=db.conn()
    with c:
        c.execute('DELETE FROM external_audio_map');c.execute('DELETE FROM external_audio_pairs')
        c.executemany('INSERT OR REPLACE INTO external_audio_map VALUES(?,?,?,?)',mappings)
        for pair in pairs:
            if pair['a'] not in mapped or pair['b'] not in mapped:continue
            a,b=pair['a'],pair['b'];metrics=pair['metrics']
            c.execute('INSERT OR REPLACE INTO external_audio_pairs VALUES(?,?,?,?)',(a,b,json.dumps(metrics),max((x['score'] for x in metrics.values()),default=0)))
    imported_thresholds=data.get('thresholds')
    if isinstance(imported_thresholds,dict) and all(isinstance(imported_thresholds.get(k),(int,float)) and math.isfinite(imported_thresholds[k]) and 0<=imported_thresholds[k]<=100 for k in THRESHOLDS):
        config.SETTINGS['external_audio_thresholds']={k:float(imported_thresholds[k]) for k in THRESHOLDS}
    result={'mapped':len(mappings),'unresolved':len(missing),'examples':missing[:20],'pairs':db.one('SELECT COUNT(*) n FROM external_audio_pairs')['n']}
    config.SETTINGS['external_audio_import']=result;config.save(config.SETTINGS)
    if progress:progress(len(tracks),len(tracks),'Uvoz audio rezultata zavrsen')
    return result


def valid_map(mapping,track):
    try:
        stat=Path(track['path']).stat()
        return stat.st_size==mapping['size'] and stat.st_mtime_ns==mapping['mtime']
    except (OSError,TypeError):return False


def recommend(track_id,excluded=()):
    init();current=db.one('SELECT * FROM tracks WHERE id=?',(track_id,));mapping=db.one('SELECT * FROM external_audio_map WHERE track_id=?',(track_id,))
    if not current or not mapping or not valid_map(mapping,current):return []
    event=suggest.active_event_id();bans=suggest.banned(event)
    played={r['track_id'] for r in db.rows('SELECT track_id FROM plays WHERE event_id=?',(event,))} if event else set()
    reserved=set(excluded)|played|{track_id};identities={core_title(t['artist'] or '',t['title'] or '') for t in db.rows('SELECT id,artist,title FROM tracks') if t['id'] in reserved}
    thresholds=config.SETTINGS.get('external_audio_thresholds',THRESHOLDS);found=[]
    eligible=[]
    for pair in db.rows('SELECT * FROM external_audio_pairs WHERE a=? OR b=? ORDER BY score DESC LIMIT 1000',(mapping['hash'],mapping['hash'])):
        reverse=pair['b']==mapping['hash'];other=pair['a'] if reverse else pair['b'];metrics=json.loads(pair['metrics'])
        accepted=[k for k,m in metrics.items() if m['score']>=thresholds.get(k,THRESHOLDS[k]) and m['contrast']>=CONTRAST[k] and m['seconds']>=SECONDS[k]]
        if not accepted:continue
        eligible.append((max(metrics[k]['score'] for k in accepted),other,metrics,accepted,reverse))
    for score,other,metrics,accepted,reverse in sorted(eligible,key=lambda row:-row[0]):
        for track in db.rows('SELECT t.*,m.hash,m.size audio_size,m.mtime audio_mtime FROM external_audio_map m JOIN tracks t ON t.id=m.track_id WHERE m.hash=? ORDER BY t.on_disk DESC,t.in_traktor DESC,t.id',(other,)):
            identity=core_title(track['artist'] or '',track['title'] or '')
            if track['id'] in reserved or identity in identities or suggest.is_banned(track,bans):continue
            if not valid_map({'size':track['audio_size'],'mtime':track['audio_mtime']},track):continue
            best=max(accepted,key=lambda k:metrics[k]['score']);m=metrics[best]
            why=[LABELS[k]+': skor '+str(round(metrics[k]['score'],1))+', kontrola redosleda '+str(round(metrics[k]['contrast'],2))+'x' for k in accepted]
            track.update(score=round(m['score'],1),label='Audio: '+LABELS[best],why=why,external_audio=True,
                match_kind=best,source_section=m['b_section'] if reverse else m['a_section'],target_section=m['a_section'] if reverse else m['b_section'],style_label='Audio baza · '+LABELS[best])
            found.append(track);identities.add(identity);reserved.add(track['id']);break
        if len(found)>=3:break
    found.sort(key=lambda x:-x['score']);library.attach_tags(found[:3]);return found[:3]
