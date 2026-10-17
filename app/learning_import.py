"""Import ordered learning lists, without editing their files or importing audio."""
import hashlib,json,ntpath,os,xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import unquote,urlsplit
from . import db,nml,library


def track_path(value):
    value=(value or '').strip()
    if value.startswith('file:'):
        u=urlsplit(value);value=unquote(u.path)
        if u.netloc and u.netloc!='localhost':value='//'+u.netloc+value
        if len(value)>2 and value[0]=='/' and value[2]==':':value=value[1:]
    return nml.playlist_key_to_path(value)


def parse(name,text):
    ext=os.path.splitext(name)[1].lower()
    if ext in ('.m3u','.m3u8'):
        return [(os.path.splitext(name)[0],[track_path(v) for v in text.lstrip('\ufeff').splitlines() if v.strip() and not v.lstrip().startswith('#')])]
    if ext!='.nml':raise ValueError('Podrzani formati su NML, M3U i M3U8.')
    root=ET.fromstring(text.lstrip('\ufeff'));lists=[]
    def visit(node,parents):
        label=node.get('NAME') or 'Lista'
        if node.get('TYPE')=='PLAYLIST':
            lists.append((' / '.join(parents+[label]),[track_path(k.get('KEY')) for k in node.findall('./PLAYLIST/ENTRY/PRIMARYKEY') if k.get('KEY')]))
        for sub in node.findall('./SUBNODES/NODE'):visit(sub,parents+([label] if label!='$ROOT' else []))
    for node in root.findall('./PLAYLISTS/NODE'):visit(node,[])
    if not lists:
        paths=[track_path(k.get('KEY')) for k in root.findall('.//ENTRY/PRIMARYKEY') if k.get('KEY')]
        if not paths:paths=[nml.traktor_path(e.find('LOCATION').get('DIR'),e.find('LOCATION').get('FILE'),e.find('LOCATION').get('VOLUME')) for e in root.findall('./COLLECTION/ENTRY') if e.find('LOCATION') is not None]
        lists=[(os.path.splitext(name)[0],paths)]
    return lists


def import_files(files):
    if not isinstance(files,list) or not 1<=len(files)<=50:raise ValueError('Izaberi od 1 do 50 lista.')
    if sum(len(str(f.get('content','')).encode('utf-8')) for f in files)>50*1024*1024:raise ValueError('Uvoz je ogranicen na 50 MB po izboru.')
    c=db.conn();c.execute('CREATE TABLE IF NOT EXISTS learning_import_files(digest TEXT PRIMARY KEY,name TEXT,playlist_ids TEXT)')
    tracks=db.rows('SELECT id,path,pkey FROM tracks');exact={nml.pkey(t['path']):t['id'] for t in tracks};exact.update({t['pkey']:t['id'] for t in tracks})
    byname=defaultdict(set)
    for t in tracks:byname[ntpath.basename(t['path'] or '').casefold()].add(t['id'])
    result=[]
    for f in files:
        name=ntpath.basename(str(f.get('name','')));text=f.get('content','')
        item=dict(file=name,lists=0,matched=0,unmatched=0,ambiguous=0,filename_matches=0,examples=[])
        if not isinstance(text,str):raise ValueError('Neispravan sadrzaj liste.')
        digest=hashlib.sha256((os.path.splitext(name)[1].lower()+'\0'+text).encode('utf-8')).hexdigest()
        old=db.one('SELECT playlist_ids FROM learning_import_files WHERE digest=?',(digest,))
        previous=json.loads(old['playlist_ids']) if old else {}
        if not isinstance(previous,dict):previous={}
        unchanged=bool(old)
        try:lists=parse(name,text)
        except (ET.ParseError,ValueError) as exc:
            result.append(dict(item,error=str(exc)));continue
        pids={}
        for list_index,(label,paths) in enumerate(lists):
            entries=[]
            for pos,path in enumerate(paths):
                tid=exact.get(nml.pkey(path));candidates=byname.get(ntpath.basename(path).casefold(),set())
                if tid is None and len(candidates)==1:tid=next(iter(candidates));item['filename_matches']+=1
                if tid is None:
                    item['ambiguous' if len(candidates)>1 else 'unmatched']+=1
                    if len(item['examples'])<5:item['examples'].append(path)
                    continue
                entries.append((pos,tid));item['matched']+=1
            if not entries:continue
            pid=previous.get(str(list_index))
            if pid and db.one('SELECT id FROM src_playlists WHERE id=?',(pid,)):
                prior=[(r['pos'],r['track_id']) for r in db.rows('SELECT pos,track_id FROM src_entries WHERE playlist_id=? ORDER BY pos',(pid,))]
                unchanged=unchanged and prior==entries
                c.execute('DELETE FROM src_entries WHERE playlist_id=?',(pid,))
                c.executemany('INSERT INTO src_entries VALUES(?,?,?)',[(pid,pos,tid) for pos,tid in entries])
                c.execute('UPDATE src_playlists SET n=? WHERE id=?',(len(entries),pid))
                pids[str(list_index)]=pid;item['lists']+=1;continue
            unchanged=False
            base='Uvoz: '+name+' / '+label;display=base;suffix=2
            while db.one('SELECT id FROM src_playlists WHERE name=?',(display,)):
                display=base+' ('+str(suffix)+')';suffix+=1
            pid=c.execute('INSERT INTO src_playlists(name,source,use_learn,n) VALUES(?,?,1,?)',(display,'learning_import',len(entries))).lastrowid
            c.executemany('INSERT INTO src_entries VALUES(?,?,?)',[(pid,pos,tid) for pos,tid in entries]);pids[str(list_index)]=pid;item['lists']+=1
        # A wholly unmatched file is retryable after importing the collection.
        if pids:c.execute('INSERT OR REPLACE INTO learning_import_files VALUES(?,?,?)',(digest,name,json.dumps(pids)))
        if unchanged:item['already_imported']=True
        result.append(item)
    c.commit();library.rebuild_learning()
    return dict(results=result,lists=sum(r['lists'] for r in result),matched=sum(r['matched'] for r in result))
