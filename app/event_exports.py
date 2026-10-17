"""Day-scoped exports of played music. Originals are only read and copied."""
from datetime import date
from pathlib import Path
import os
import re
import shutil
import tempfile
from . import config, db
from .scan_control import check_stop, ScanCancelled


def day_value(day):
    value = date.fromisoformat(day)
    if value.isoformat() != day: raise ValueError('Datum mora biti YYYY-MM-DD.')
    return day


def event(eid):
    item = db.one('SELECT * FROM events WHERE id=?', (eid,))
    if not item: raise ValueError('Dogadjaj nije pronadjen.')
    return item


def days(eid):
    event(eid)
    return db.rows('SELECT substr(ts,1,10) day,COUNT(*) plays FROM plays WHERE event_id=? GROUP BY substr(ts,1,10) ORDER BY day DESC', (eid,))


def tracks(eid, day):
    event(eid);day_value(day)
    # The timestamp's local calendar date is also the date displayed in Vece.
    return db.rows('''SELECT t.*,p.id play_id,p.ts FROM plays p LEFT JOIN tracks t ON t.id=p.track_id
                      WHERE p.event_id=? AND substr(p.ts,1,10)=? ORDER BY p.ts,p.id''', (eid,day))


def clean(value):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(value or '')).strip(' .')[:100] or 'pesma'


def lines(items, relative=False):
    yield '#EXTM3U'
    for t in items:
        path=t.get('export_path') if relative else t.get('path')
        if not path or '\n' in path or '\r' in path: continue
        label=' - '.join(str(t.get(k) or '').replace('\n',' ').replace('\r',' ') for k in ('artist','title'))
        yield f"#EXTINF:{int(t.get('duration') or 0)},{label}"
        yield path


def export(eid, day, copy_songs=False, progress=None, stop_event=None):
    selected=tracks(eid,day)
    if not selected: raise ValueError('Nema odsviranih pesama za taj dan.')
    root=Path(config.SETTINGS.get('export_dir') or '')
    if not config.SETTINGS.get('export_dir'): raise ValueError('Izaberi folder za izvoz u Podesavanjima.')
    root.mkdir(parents=True,exist_ok=True)
    name=f"{day}_{clean(event(eid)['name'])}_{eid}"
    folder=Path(tempfile.mkdtemp(prefix=name+('_pesme_' if copy_songs else '_lista_'),dir=root))
    result={'path':str(folder),'day':day,'plays':len(selected),'copied':0,'missing':[],'playlist':None}
    copied={};playlist=[]
    try:
        for i,t in enumerate(selected):
            check_stop(stop_event)
            if progress: progress(i,len(selected),'Izvozim '+day+': '+str(t.get('title') or 'Nedostaje pesma'))
            source=Path(t['path']) if t.get('path') else None
            if source is None or not source.is_file():
                result['missing'].append(str(source or ('Obrisana pesma / pustanje '+str(t['play_id']))));continue
            if copy_songs:
                identity=os.path.normcase(str(source.resolve()))
                if identity not in copied:
                    target=folder/f'{len(copied)+1:03d}_{clean(source.stem)}{source.suffix.lower()}'
                    # Exclusive creation and a new destination prevent overwriting.
                    partial=target.with_suffix(target.suffix+'.partial')
                    try:
                        with source.open('rb') as src,partial.open('xb') as dst:
                            while block:=src.read(1024*1024):
                                check_stop(stop_event);dst.write(block)
                        partial.rename(target)
                    except BaseException:
                        partial.unlink(missing_ok=True)
                        raise
                    shutil.copystat(source,target);copied[identity]=target.name;result['copied']+=1
                t={**t,'export_path':copied[identity]}
            playlist.append(t)
    except ScanCancelled:
        result['cancelled']=True
        raise ScanCancelled(result)
    finally:
        target=folder/(name+'.m3u8')
        target.write_text('\n'.join(lines(playlist,relative=copy_songs))+'\n',encoding='utf-8-sig')
        result['playlist']=str(target)
        if result['missing']:(folder/'Nedostaju-pesme.txt').write_text('\n'.join(result['missing'])+'\n',encoding='utf-8-sig')
    if progress:progress(len(selected),len(selected),'Izvoz zavrsen: '+str(folder))
    return result
