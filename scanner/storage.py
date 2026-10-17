import json,os,sqlite3,tempfile,zlib,zipfile,shutil
from pathlib import Path
from . import FORMAT,ENGINE
DATA=Path(os.environ.get('DJ_SCANNER_DATA') or Path(os.environ.get('APPDATA',str(Path.home())))/'DJ Miljan Audio Scanner')
DATA.mkdir(parents=True,exist_ok=True)
DATABASE=DATA/'scanner.db'
SCHEMA='''
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,hash TEXT,size INTEGER,mtime INTEGER,status TEXT,error TEXT,seconds REAL);
CREATE TABLE IF NOT EXISTS features(hash TEXT PRIMARY KEY,engine TEXT,data BLOB,seconds REAL);
CREATE TABLE IF NOT EXISTS pairs(a TEXT,b TEXT,engine TEXT,metrics TEXT,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS feedback(a TEXT,b TEXT,value INTEGER,PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
'''

from contextlib import contextmanager

@contextmanager
def connect():
    c=sqlite3.connect(DATABASE,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL')
    try:
        with c:yield c
    finally:c.close()

def init():
    with connect() as c:c.executescript(SCHEMA);c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('schema',str(FORMAT)))

def rows(sql,args=()):
    with connect() as c:return [dict(r) for r in c.execute(sql,args)]

def save(result):
    path=Path(result['path']);now=path.stat()
    if (now.st_size,now.st_mtime_ns)!=(result['size'],result['mtime']):raise ValueError('Fajl se promenio pre upisa; ponovi sken.')
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO features VALUES(?,?,?,?)',(result['hash'],ENGINE,zlib.compress(json.dumps(result['features'],separators=(',',':')).encode(),3),result['seconds']))
        c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)',(result['path'],result['hash'],result['size'],result['mtime'],'ok',None,result['seconds']))

def load_records():
    from collections.abc import Mapping
    from functools import lru_cache
    from .matcher import record
    keys=[r['hash'] for r in rows("SELECT hash FROM features WHERE engine=? AND EXISTS(SELECT 1 FROM files p WHERE p.hash=features.hash AND p.status='ok')",(ENGINE,))]
    @lru_cache(maxsize=12)
    def fetch(identity):
        found=rows('SELECT data,seconds FROM features WHERE hash=? AND engine=?',(identity,ENGINE))
        if not found:raise KeyError(identity)
        r=found[0];return record(json.loads(zlib.decompress(r['data'])),r['seconds'])
    class Records(Mapping):
        def __len__(self):return len(keys)
        def __iter__(self):return iter(keys)
        def __getitem__(self,identity):return fetch(identity)
    return Records()


def catalogue():
    out={}
    for row in rows("SELECT * FROM files WHERE status='ok' ORDER BY path"):
        path=Path(row['path'])
        try:
            st=path.stat()
            if (st.st_size,st.st_mtime_ns)!=(row['size'],row['mtime']):continue
        except OSError:continue
        out.setdefault(row['hash'],row)
    return out

def export(path,full=False,thresholds=None):
    from .matcher import DEFAULTS,CONTRAST,MIN_SECONDS
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    cat=catalogue();pairs=[r for r in rows('SELECT * FROM pairs WHERE engine=?',(ENGINE,)) if r['a'] in cat and r['b'] in cat]
    rejected={(r['a'],r['b']) for r in rows('SELECT * FROM feedback WHERE value<0')}
    pairs=[r for r in pairs if (r['a'],r['b']) not in rejected]
    payload={'format':'dj-miljan-audio-results','schema':FORMAT,'engine':ENGINE,'thresholds':thresholds or DEFAULTS,'contrast':CONTRAST,'minimum_seconds':MIN_SECONDS,
        'tracks':[{'hash':key,'path':r['path'],'name':Path(r['path']).name,'size':r['size'],'seconds':r['seconds']} for key,r in cat.items()],
        'pairs':[{'a':r['a'],'b':r['b'],'metrics':json.loads(r['metrics'])} for r in pairs]}
    temp=path.with_name(path.name+'.partial')
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps(payload,ensure_ascii=False))
            if full:
                with tempfile.TemporaryDirectory() as folder:
                    backup=Path(folder)/'scanner.db'
                    with connect() as source,sqlite3.connect(backup) as destination:source.backup(destination)
                    z.write(backup,'scanner.db')
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    return {'path':str(path),'tracks':len(cat),'pairs':len(pairs),'full':full}

def read_manifest(path):
    with zipfile.ZipFile(path) as z:
        info=z.getinfo('manifest.json')
        if info.file_size>100*1024**2:raise ValueError('Prevelik manifest.')
        data=json.loads(z.read(info))
    if data.get('format')!='dj-miljan-audio-results' or data.get('schema')!=FORMAT or data.get('engine')!=ENGINE:raise ValueError('Nekompatibilna verzija audio baze.')
    return data

def import_full(path,roots=(),stop=None):
    from .features import file_id
    manifest=read_manifest(path);count=0;relocated=0;unresolved=0
    with tempfile.TemporaryDirectory(dir=DATA) as folder:
        backup=Path(folder)/'source.db'
        with zipfile.ZipFile(path) as z:
            info=z.getinfo('scanner.db')
            if info.file_size>4*1024**3:raise ValueError('Baza je veca od 4 GB.')
            with z.open(info) as src,backup.open('wb') as dst:shutil.copyfileobj(src,dst)
        source=sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
        try:
            if source.execute('SELECT value FROM meta WHERE key="schema"').fetchone()[0]!=str(FORMAT):raise ValueError('Nekompatibilna sema.')
            index={}
            for root in roots:
                for p in Path(root).rglob('*'):
                    if p.is_file():index.setdefault((p.name.casefold(),p.stat().st_size),[]).append(p)
            with connect() as c:
                for item in source.execute('SELECT * FROM features WHERE engine=?',(ENGINE,)):
                    if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
                    json.loads(zlib.decompress(item['data']))
                    c.execute('INSERT OR IGNORE INTO features VALUES(?,?,?,?)',tuple(item));count+=1
                for item in source.execute("SELECT * FROM files WHERE status='ok'"):
                    old=Path(item['path']);options=[old] if old.is_file() else index.get((old.name.casefold(),item['size']),[])
                    good=[]
                    for candidate in options:
                        if stop is not None and stop.is_set():raise InterruptedError('Zaustavljeno')
                        if candidate.stat().st_size==item['size'] and file_id(candidate,stop)==item['hash']:good.append(candidate)
                    if len(good)!=1:unresolved+=1;continue
                    p=good[0];st=p.stat();relocated+=str(p)!=item['path']
                    c.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)',(str(p.resolve()),item['hash'],st.st_size,st.st_mtime_ns,'ok',None,item['seconds']))
                for item in source.execute('SELECT * FROM pairs WHERE engine=?',(ENGINE,)):
                    json.loads(item['metrics']);c.execute('INSERT OR IGNORE INTO pairs VALUES(?,?,?,?)',tuple(item))
                for item in source.execute('SELECT * FROM feedback'):
                    c.execute('INSERT OR IGNORE INTO feedback VALUES(?,?,?)',tuple(item))
        finally:source.close()
    return {'features':count,'relocated':relocated,'unresolved':unresolved}
