"""Observed immediate neighbors of one track in the DJ's ordered lists."""
from collections import Counter,defaultdict
from . import db
from .names import core_title


def neighbors(track_id, excluded_ids=(), limit=3):
    tracks={t['id']:t for t in db.rows('SELECT * FROM tracks')}
    current=tracks.get(track_id)
    if not current:return {'before':[],'after':[],'track_id':track_id}
    identity=lambda t:core_title(t.get('artist') or '',t.get('title') or '') or str(t['id'])
    seen={identity(current)}|{identity(tracks[i]) for i in excluded_ids if i in tracks}
    result={'track_id':track_id,'before':[],'after':[],'scope':'Sve uvezene Traktor History liste i zabelezena pustanja u aplikaciji; samo neposredno pre / posle.'}
    for side,offset,window in (('before',-1,'LAG'),('after',1,'LEAD')):
        counts=Counter();sources=defaultdict(dict)
        def add(tid,key,name):
            if tid not in tracks or identity(tracks[tid])==identity(current):return
            counts[tid]+=1;sources[tid][key]=name
        # Real imported History lists count even when disabled for the
        # independent original scoring algorithm. Ordinary playlists do not
        # prove that their order was actually played.
        rows=db.rows('''SELECT n.track_id,p.id playlist_id,p.name FROM src_entries c
                       JOIN src_entries n ON n.playlist_id=c.playlist_id AND n.pos=c.pos+?
                       JOIN src_playlists p ON p.id=c.playlist_id WHERE c.track_id=?
                       AND (LOWER(p.source)='history' OR LOWER(p.name) LIKE 'history:%')''',(offset,track_id))
        for r in rows:add(r['track_id'],'src:'+str(r['playlist_id']),r['name'])
        rows=db.rows(f'''SELECT adjacent,event_id FROM
            (SELECT track_id,event_id,{window}(track_id) OVER(PARTITION BY event_id ORDER BY ts,id) adjacent FROM plays)
            WHERE track_id=? AND adjacent IS NOT NULL''',(track_id,))
        events={e['id']:e['name'] for e in db.rows('SELECT id,name FROM events')}
        for r in rows:add(r['adjacent'],'event:'+str(r['event_id']),events.get(r['event_id'],'Odsvirano'))
        # Merge duplicate file identities before sorting their real counts.
        grouped={}
        for tid,times in counts.items():
            t=tracks[tid];key=identity(t)
            if key in seen:continue
            if key not in grouped:grouped[key]=dict(t,times=0,source_names={},history_side=side)
            grouped[key]['times']+=times;grouped[key]['source_names'].update(sources[tid])
        for t in sorted(grouped.values(),key=lambda t:(-t['times'],-len(t['source_names']),t['id']))[:limit]:
            seen.add(identity(t));names=t.pop('source_names');t.update(lists=len(names),sources=list(names.values())[:4])
            result[side].append(t)
    return result
