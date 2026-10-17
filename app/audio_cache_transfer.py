"""Portable audio signatures. Exact file SHA256 identity, never filename guesses."""
import gzip,hashlib,json,os,tempfile,uuid
from pathlib import Path
from collections import defaultdict
from . import config,db,nml,audio_rhythm,auto_audio,learned_audio,library
from .scan_control import check_stop

FORMAT='DJ-Miljan-Audio-Cache';VERSION=2
LAST={};EXPORTS={}


def init():
    auto_audio.init();audio_rhythm.init()
    db.conn().execute('CREATE TABLE IF NOT EXISTS audio_file_hashes(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,digest TEXT)');db.conn().commit()


def digest(path,stop_event=None):
    stat=os.stat(path)
    # Always re-read content for a transfer: Windows can preserve size/mtime
    # across rapid rewrites. Metadata alone is not a content identity proof.
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while chunk:=f.read(1024*1024):check_stop(stop_event);h.update(chunk)
    end=os.stat(path)
    if (stat.st_size,stat.st_mtime_ns)!=(end.st_size,end.st_mtime_ns):raise ValueError('Fajl je promenjen tokom provere: '+str(path))
    value=h.hexdigest();db.conn().execute('INSERT OR REPLACE INTO audio_file_hashes VALUES(?,?,?,?)',(str(path),stat.st_size,stat.st_mtime_ns,value));db.conn().commit();return value


def status():
    return dict(database_path=str(db.DB_FILE.resolve()),data_folder=str(config.DATA.resolve()),transfer_folder=str((config.DATA/'audio_transfers').resolve()),model=learned_audio.MODEL_ID,last=dict(LAST))


def check(query='',offset=0):
    # No decoder, model inference, hashing, ensure(), or scan is invoked.
    auto_audio.init();audio_rhythm.init();rows=[];counts=defaultdict(int)
    for t in db.rows('SELECT * FROM tracks ORDER BY artist,title,id'):
        row=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(t['id'],));rh=db.one('SELECT * FROM audio_rhythm WHERE track_id=?',(t['id'],))
        state='nije skenirano'
        if row:
            if row['version']!=auto_audio.VERSION or (learned_audio.available() and row['model']!=learned_audio.MODEL_ID):state='nekompatibilna verzija'
            elif row['status']=='error':state='greska skena'
            elif not os.path.isfile(t['path'] or ''):state='nedostaje fajl / Relocate'
            elif not auto_audio.valid(row,t):state='promenjen fajl ili cue'
            else:state='skenirano'
        if state=='nije skenirano' and rh and audio_rhythm.valid(rh,t):state='skeniran samo ritam cele pesme'
        counts[state]+=1
        if query.casefold() not in (' '.join((t.get('artist') or '',t.get('title') or '',t.get('path') or '',state))).casefold():continue
        rows.append(dict(id=t['id'],artist=t['artist'],title=t['title'],path=t['path'],state=state,rhythm_ready=bool(rh and audio_rhythm.valid(rh,t))))
    offset=max(0,int(offset));return dict(status(),counts=dict(counts),total=len(rows),offset=offset,rows=rows[offset:offset+200])


def export(progress=None,stop_event=None):
    init();tracks=db.rows('SELECT * FROM tracks ORDER BY id');items=[];skipped=0
    for i,t in enumerate(tracks):
        check_stop(stop_event)
        if progress:progress(i,len(tracks),'Pakujem audio bazu: '+(t['title'] or ''))
        a=db.one('SELECT * FROM auto_chorus WHERE track_id=?',(t['id'],));r=db.one('SELECT * FROM audio_rhythm WHERE track_id=?',(t['id'],))
        a=a if a and a['status']=='ok' and auto_audio.valid(a,t) else None
        r=r if r and audio_rhythm.valid(r,t) else None
        if not a and not r:skipped+=1;continue
        try:
            sha=digest(t['path'],stop_event);current=os.stat(t['path']);size=current.st_size
            verified=db.one('SELECT size,mtime,digest FROM audio_file_hashes WHERE path=?',(t['path'],))
            if not verified or verified['digest']!=sha or (verified['size'],verified['mtime'])!=(current.st_size,current.st_mtime_ns):skipped+=1;continue
        except OSError:skipped+=1;continue
        item=dict(sha256=sha,size=size,name=Path(t['path']).name,artist=t['artist'],title=t['title'])
        if a:item['chorus']={k:a[k] for k in ('version','data','cue_key','model')}
        if r:item['rhythm']={k:r[k] for k in ('version','features')}
        items.append(item)
    check_stop(stop_event);folder=config.DATA/'audio_transfers';folder.mkdir(exist_ok=True);token=uuid.uuid4().hex;path=folder/('DJ-Miljan-audio-'+token[:8]+'.djaudio')
    try:
        with gzip.open(str(path)+'.partial','wt',encoding='utf-8') as f:
            f.write(json.dumps(dict(format=FORMAT,version=VERSION,model=learned_audio.MODEL_ID))+'\n')
            for item in items:
                check_stop(stop_event);f.write(json.dumps(item,separators=(',',':'))+'\n')
        check_stop(stop_event);os.replace(str(path)+'.partial',path)
    finally:Path(str(path)+'.partial').unlink(missing_ok=True)
    EXPORTS[token]=path;result=dict(kind='export',exported=len(items),skipped=skipped,path=str(path),download='/api/audio/cache/download/'+token)
    LAST.clear();LAST.update(result)
    if progress:progress(len(tracks),len(tracks),'Audio baza izvezena')
    return result


def read_package(path):
    # Line-oriented gzip avoids materializing a second full uncompressed JSON
    # document when transferring several thousand scanned tracks.
    with gzip.open(path,'rb') as f:
        first=f.readline(65537)
        if len(first)>65536:raise ValueError('Neispravno zaglavlje audio baze.')
        data=json.loads(first)
        if data.get('format')!=FORMAT or data.get('version')!=VERSION:raise ValueError('Nepodrzan format audio baze.')
        items=[];total=len(first)
        while True:
            line=f.readline(16*1024*1024+1)
            if not line:break
            total+=len(line)
            if len(line)>16*1024*1024 or total>2*1024*1024*1024:raise ValueError('Audio baza je prevelika.')
            item=json.loads(line)
            if not isinstance(item,dict) or not isinstance(item.get('size'),int) or item['size']<0:raise ValueError('Neispravan zapis u audio bazi.')
            items.append(item)
    data['items']=items;return data


def apply_package(package,roots=(),progress=None,stop_event=None):
    init();roots=[os.path.expandvars(os.path.expanduser(r.strip().strip(chr(34)))) for r in roots];items=package['items'];wanted={i['sha256']:i for i in items if isinstance(i.get('sha256'),str) and len(i['sha256'])==64}
    sizes={i['size'] for i in wanted.values()};matches=defaultdict(list);tracks=db.rows('SELECT * FROM tracks ORDER BY id');stats=dict(kind='import',matched=0,chorus=0,rhythm=0,incompatible=0,not_found=0,relocated=0,cue_hints_adopted=0)
    paths={}
    for t in tracks:
        check_stop(stop_event)
        path=t.get('path') or ''
        try:
            if os.path.getsize(path) not in sizes:continue
            paths[os.path.realpath(path)]=t
        except OSError:continue
    for root in roots:
        if not os.path.isdir(root):raise ValueError('Relocate folder ne postoji: '+root)
        for folder,dirs,files in os.walk(root):
            check_stop(stop_event)
            for filename in files:
                path=os.path.join(folder,filename)
                if Path(path).suffix.lower() not in library.AUDIO_EXT:continue
                try:
                    if os.path.getsize(path) in sizes:paths.setdefault(os.path.realpath(path),None)
                except OSError:pass
    for i,(path,t) in enumerate(paths.items()):
        check_stop(stop_event)
        if progress:progress(i,len(paths),'Proveravam sadrzaj fajla: '+Path(path).name)
        try:sha=digest(path,stop_event)
        except OSError:continue
        if sha in wanted:
            cached_hash=db.one('SELECT size,mtime,digest FROM audio_file_hashes WHERE path=?',(str(path),))
            if cached_hash and cached_hash['digest']==sha:matches[sha].append((path,t,(cached_hash['size'],cached_hash['mtime'])))
    missing_tracks=defaultdict(list)
    for t in tracks:
        if not os.path.isfile(t.get('path') or ''):missing_tracks[Path((t.get('path') or '').replace('\\','/')).name.casefold()].append(t)
    for sha,item in wanted.items():
        check_stop(stop_event)
        targets=matches.get(sha,[])
        if not targets:stats['not_found']+=1;continue
        applied=False
        for path,t,verified_stat in targets:
            try:current=os.stat(path)
            except OSError:continue
            if (current.st_size,current.st_mtime_ns)!=verified_stat:continue
            if t is None:
                # Manual Relocate: only repair an unambiguous existing missing
                # library entry, additionally verified against exported SHA256.
                candidates=missing_tracks.get(item.get('name','').casefold(),[])
                if len(candidates)!=1:continue
                t=candidates[0];existing=db.one('SELECT * FROM tracks WHERE pkey=?',(nml.pkey(path),))
                if existing:t=existing
                else:
                    db.conn().execute('UPDATE tracks SET path=?,pkey=?,folder=?,on_disk=1 WHERE id=?',(path,nml.pkey(path),os.path.dirname(path),t['id']))
                    t=dict(t,path=path,pkey=nml.pkey(path),on_disk=1);stats['relocated']+=1
            stat=os.stat(path);a=item.get('chorus');r=item.get('rhythm');wrote=False
            if a:
                if a.get('version')!=auto_audio.VERSION or a.get('model')!=learned_audio.MODEL_ID:stats['incompatible']+=1
                else:
                    sections=json.loads(a['data'])
                    if not isinstance(sections,list) or not sections or not sections[0].get('song_phrase'):raise ValueError('Neispravni audio potpisi u bazi.')
                    # Features and absolute audio window offsets remain valid for
                    # identical file content. Source cue hints are provenance,
                    # not a requirement that both Traktor setups share cue tags.
                    sections[0]['imported_cue_key']=a.get('cue_key','[]')
                    local_cues=auto_audio.cue_key(t)
                    if local_cues!=a.get('cue_key'):stats['cue_hints_adopted']+=1
                    db.conn().execute('INSERT OR REPLACE INTO auto_chorus VALUES(?,?,?,?,?,?,?,?,?,?)',(t['id'],t['path'],stat.st_size,stat.st_mtime_ns,auto_audio.VERSION,'ok',json.dumps(sections),None,local_cues,learned_audio.MODEL_ID));stats['chorus']+=1;wrote=True
            if r:
                if r.get('version')!=audio_rhythm.VERSION:stats['incompatible']+=1
                else:
                    json.loads(r['features']);db.conn().execute('INSERT OR REPLACE INTO audio_rhythm VALUES(?,?,?,?,?,?,?,?)',(t['id'],t['path'],stat.st_size,stat.st_mtime_ns,audio_rhythm.VERSION,'ok',r['features'],None));stats['rhythm']+=1;wrote=True
            db.conn().commit();applied=applied or wrote
        if applied:stats['matched']+=1
        else:stats['not_found']+=1
    auto_audio.REVISION+=1;audio_rhythm.SCAN_REVISION+=1;LAST.clear();LAST.update(stats)
    if progress:progress(len(paths),len(paths),'Uvoz audio baze zavrsen')
    return stats


def import_path(path,roots=(),progress=None,stop_event=None):
    try:return apply_package(read_package(path),roots,progress,stop_event)
    finally:Path(path).unlink(missing_ok=True)
