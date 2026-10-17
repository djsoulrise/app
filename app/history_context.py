"""Immediate neighbors in imported Traktor playlists and History archives only."""
from collections import Counter,defaultdict
from . import db
from .names import core_title


def neighbors(track_id, excluded_ids=(), limit=3):
    tracks={t['id']:t for t in db.rows('SELECT * FROM tracks')}
    current=tracks.get(track_id)
    if not current:return {'before':[],'after':[],'track_id':track_id}
    identity=lambda t:core_title(t.get('artist') or '',t.get('title') or '') or str(t['id'])
    seen={identity(current)}|{identity(tracks[i]) for i in excluded_ids if i in tracks}
    result={'track_id':track_id,'before':[],'after':[],'scope':'Sve uvezene Traktor plejliste i History arhive; samo neposredno pre / posle. Ukljucene su i oznacene dodatno uvezene liste. Assistant Vece se ne koristi.'}
    for side,offset in (('before',-1),('after',1)):
        counts=Counter();sources=defaultdict(dict)
        def add(tid,key,name):
            if tid not in tracks or identity(tracks[tid])==identity(current):return
            counts[tid]+=1;sources[tid][key]=name
        # All imported Traktor lists count, independently of use_learn (which
        # still controls the untouched original recommendation algorithm).
        # Context describes playlist order, not a claim of actual playback.
        rows=db.rows('''SELECT n.track_id,p.id playlist_id,p.name FROM src_entries c
                       JOIN src_entries n ON n.playlist_id=c.playlist_id AND n.pos=c.pos+?
                       JOIN src_playlists p ON p.id=c.playlist_id WHERE c.track_id=?
                       AND (LOWER(p.source) IN ('history','traktor') OR (p.source='learning_import' AND p.use_learn=1))''',(offset,track_id))
        for r in rows:add(r['track_id'],'src:'+str(r['playlist_id']),r['name'])
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
