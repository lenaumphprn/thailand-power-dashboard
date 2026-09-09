#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, sqlite3, sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from pipeline import DB, Http, ROOT, utcnow, parse_thai_page_date, thai_year_to_ad, THAI_MONTHS

WATCH_CONFIG = ROOT / 'watch_sources.json'
SEED_PATH = ROOT / 'seed_intelligence.json'

EN_MONTHS = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,'apr':4,'april':4,
    'may':5,'jun':6,'june':6,'jul':7,'july':7,'aug':8,'august':8,'sep':9,'sept':9,'september':9,
    'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12
}


def normalize_space(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def canonical_url(url: str) -> str:
    p=urlsplit(url)
    # Drop fragments and common analytics params; keep functional query params such as page/year/id/symbol.
    qs='&'.join(x for x in p.query.split('&') if x and not x.lower().startswith(('utm_','fbclid=','gclid=')))
    path=re.sub(r'/+$','',p.path) or '/'
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),path,qs,''))


def fingerprint(source_id: str, url: str, title: str='') -> str:
    base=f"{source_id}|{canonical_url(url)}|{normalize_space(title).lower()}"
    return hashlib.sha256(base.encode('utf-8')).hexdigest()


def parse_any_date(text: str) -> str|None:
    text=normalize_space(text)
    thai=parse_thai_page_date(text)
    if thai: return thai
    # 01 September 2026 / 01 Sep 2026
    m=re.search(r'\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\b',text,re.I)
    if m:
        return date(int(m.group(3)), EN_MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    # ISO-ish date
    m=re.search(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b',text)
    if m:
        return date(int(m.group(1)),int(m.group(2)),int(m.group(3))).isoformat()
    return None


def extract_capacities(text: str) -> list[float]:
    vals=[]
    patterns=[
        r'(?<![\d.])(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:MW|เมกะวัตต์)\b',
        r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:GW|กิกะวัตต์)\b'
    ]
    for i,pat in enumerate(patterns):
        for m in re.finditer(pat,text,re.I):
            v=float(m.group(1).replace(',',''))
            if i==1: v*=1000
            if 0 < v < 200000: vals.append(round(v,3))
    # preserve order, remove exact duplicates
    out=[]
    for v in vals:
        if v not in out: out.append(v)
    return out


def classify_event(title: str, body: str='') -> dict[str,Any]:
    text=(title+' '+body).lower()
    category='Other'; topic=None; status=None; technology=None
    if any(k in text for k in ['pdp2026','pdp 2026','power development plan','แผน pdp','ร่างแผนpdp','ร่างแผน pdp']):
        category='Policy & regulation'; topic='PDP2026'
    elif any(k in text for k in ['direct ppa','third party access','wheeling','tpa','utility green tariff','ugt']):
        category='Market design'; topic='Direct PPA / green procurement'
    elif any(k in text for k in ['ค่าไฟ','เอฟที','electricity tariff','ft ','fuel adjustment']):
        category='Tariff & economics'; topic='Tariff / Ft'
    elif any(k in text for k in ['transmission','grid','ระบบส่งไฟฟ้า','smart grid']):
        category='Grid & system'; topic='Grid / transmission'
    elif any(k in text for k in ['commercial operation','commencement of operation',' cod ','cod on','เปิดดำเนินการเชิงพาณิชย์','เริ่มเดินเครื่อง']):
        category='Project & investment'; topic='Project COD'; status='COD'
    elif any(k in text for k in ['power purchase agreement','execution of ppa','signed ppa','ลงนามสัญญาซื้อขายไฟฟ้า','สัญญาซื้อขายไฟฟ้า']):
        category='Project & investment'; topic='Project PPA'; status='PPA signed'
    elif any(k in text for k in ['project financing','financing for','เงินกู้โครงการ','construction','under construction','ก่อสร้าง']):
        category='Project & investment'; topic='Project execution'
    elif any(k in text for k in ['solar','wind','renewable','hydro','battery','bess','พลังงานแสงอาทิตย์','พลังงานลม','พลังงานหมุนเวียน','โรงไฟฟ้า']):
        category='Project & investment'; topic='Power / renewables project'

    if any(k in text for k in ['solar','แสงอาทิตย์']): technology='Solar'
    if any(k in text for k in ['wind','ลม']): technology='Wind' if not technology else technology+' / Wind'
    if any(k in text for k in ['bess','battery','storage','กักเก็บ']): technology='BESS' if not technology else technology+' / BESS'
    if any(k in text for k in ['hydro','พลังน้ำ']): technology='Hydro' if not technology else technology+' / Hydro'
    return {'category':category,'topic':topic,'status_after':status,'technology':technology}


def relevant(text: str, cfg: dict) -> bool:
    low=text.lower()
    pos=any(k.lower() in low for k in cfg['positive_keywords'])
    neg=any(k.lower() in low for k in cfg['negative_keywords'])
    # Strong energy/project terms override generic corporate negatives when both appear.
    strong=any(k in low for k in ['commercial operation','power purchase agreement','direct ppa','pdp2026','pdp 2026','solar','wind','renewable','bess','hydro','โรงไฟฟ้า','พลังงานหมุนเวียน'])
    return pos and (not neg or strong)


def materiality(title: str, body: str, info: dict, capacities: list[float], event_date: str|None, asof: date|None=None) -> int:
    text=(title+' '+body).lower(); score=2
    if info['topic']=='PDP2026': score=10
    elif info['category']=='Market design': score=9
    elif info['category']=='Tariff & economics': score=9
    elif info['category']=='Grid & system': score=7
    elif info['status_after']=='COD': score=6
    elif info['status_after']=='PPA signed': score=6
    elif info['category']=='Project & investment': score=4
    cap=max(capacities) if capacities else 0
    if cap>=1000: score+=3
    elif cap>=300: score+=2
    elif cap>=100: score+=1
    if any(k in text for k in ['public hearing','consultation','รับฟังความคิดเห็น','approved','เห็นชอบ','final rules','commercial operation','เปิดดำเนินการเชิงพาณิชย์']): score+=1
    if asof and event_date:
        try:
            age=(asof-date.fromisoformat(event_date)).days
            if age<=14: score+=1
            elif age>90: score-=1
        except: pass
    return max(0,min(12,score))


def compact_excerpt(text: str, max_chars=360) -> str:
    t=normalize_space(text)
    if len(t)<=max_chars: return t
    cut=t[:max_chars].rsplit(' ',1)[0]
    return cut+'…'


def extract_listing_items(html: str, base_url: str, cfg: dict) -> list[dict]:
    soup=BeautifulSoup(html,'html.parser'); items=[]; seen=set()
    for a in soup.find_all('a',href=True):
        title=normalize_space(a.get_text(' ',strip=True))
        if not title or len(title)<8: continue
        parent=a.find_parent(['article','li','div','tr']) or a.parent
        context=normalize_space(parent.get_text(' ',strip=True) if parent else title)
        if not relevant(title+' '+context,cfg): continue
        url=canonical_url(urljoin(base_url,a['href']))
        key=(url,title.lower())
        if key in seen: continue
        seen.add(key)
        event_date=parse_any_date(context) or parse_any_date(title)
        items.append({'title':title,'url':url,'event_date':event_date,'context':compact_excerpt(context,500)})
    return items


def find_project_alias(db: DB, company: str|None, text: str) -> str|None:
    if not company: return None
    rows=db.conn.execute("SELECT alias,project_id FROM project_alias WHERE company=? ORDER BY LENGTH(alias) DESC",(company,)).fetchall()
    low=text.lower()
    matches=[r['project_id'] for r in rows if r['alias'].lower() in low]
    return matches[0] if len(set(matches))==1 else None


def upsert_event(db: DB, event: dict) -> int:
    fp=event.get('fingerprint') or fingerprint(event['source_id'],event['source_url'],event['title'])
    eid=event.get('event_id') or ('EV-'+fp[:16])
    now=utcnow()
    existing=db.conn.execute("SELECT event_id FROM market_event WHERE fingerprint=?",(fp,)).fetchone()
    if existing:
        db.conn.execute("UPDATE market_event SET last_seen_at=?, body_excerpt=COALESCE(NULLIF(?,''),body_excerpt), materiality_score=MAX(materiality_score,?) WHERE fingerprint=?",
                        (now,event.get('body_excerpt',''),event.get('materiality_score',0),fp)); db.conn.commit(); return 0
    db.conn.execute("""INSERT INTO market_event(event_id,fingerprint,source_id,source_name,source_tier,source_url,title,body_excerpt,event_date,first_seen_at,last_seen_at,category,topic,company,technology,capacity_mw,capacity_mentions_json,status_after,matched_project_id,materiality_score,confidence,is_relevant,review_status,note)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (eid,fp,event.get('source_id'),event.get('source_name'),event.get('source_tier'),canonical_url(event['source_url']),event['title'],event.get('body_excerpt'),event.get('event_date'),now,now,event.get('category','Other'),event.get('topic'),event.get('company'),event.get('technology'),event.get('capacity_mw'),json.dumps(event.get('capacity_mentions',[])),event.get('status_after'),event.get('matched_project_id'),event.get('materiality_score',0),event.get('confidence',0.5),1 if event.get('is_relevant',True) else 0,event.get('review_status','auto'),event.get('note')))
    db.conn.commit(); return 1


def apply_project_status(db: DB, event: dict) -> bool:
    pid=event.get('matched_project_id'); status=event.get('status_after'); event_date=event.get('event_date')
    if not pid or not status or not event_date or event.get('confidence',0)<0.9: return False
    row=db.conn.execute("SELECT status,status_date FROM project_registry WHERE project_id=?",(pid,)).fetchone()
    if not row: return False
    # Never roll a project backwards; COD is terminal for this tracker.
    rank={'Announced':0,'Selected':1,'PPA signed':2,'Under construction':3,'Scheduled COD':4,'COD':5}
    if rank.get(status,0) < rank.get(row['status'],0): return False
    db.conn.execute("UPDATE project_registry SET status=?,status_date=?,source_url=COALESCE(?,source_url) WHERE project_id=?",(status,event_date,event.get('source_url'),pid))
    db.conn.commit(); return True


def seed_intelligence(db: DB, seed_path: Path=SEED_PATH) -> dict:
    data=json.loads(seed_path.read_text())
    for s in data.get('sources',[]): db.upsert_source(s)
    for a in data.get('aliases',[]):
        db.conn.execute("INSERT OR REPLACE INTO project_alias(alias,project_id,company,match_type) VALUES(?,?,?,?)",(a['alias'],a['project_id'],a.get('company'),a.get('match_type','contains')))
    added=0
    for e in data.get('events',[]):
        info=classify_event(e['title'],e.get('body_excerpt',''))
        caps=e.get('capacity_mentions') or extract_capacities(e.get('body_excerpt','')+' '+e['title'])
        x={**info,**e,'capacity_mentions':caps}
        if x.get('capacity_mw') is None and caps: x['capacity_mw']=max(caps)
        x['fingerprint']=fingerprint(x['source_id'],x['source_url'],x['title'])
        added+=upsert_event(db,x)
    db.conn.commit(); return {'events_added':added}


def refresh_watchers(db: DB, cfg: dict, http: Http|None=None, asof: date|None=None) -> dict:
    http=http or Http(); asof=asof or date.today(); checked=0; added=0; updated_projects=0; warnings=[]
    cutoff=asof-timedelta(days=int(cfg.get('lookback_days',120)))
    for source in cfg['sources']:
        checked+=1; sid=source['source_id']
        db.upsert_source(source)
        try:
            html=http.get(source['url']).text
            items=extract_listing_items(html,source['url'],cfg)
            # newest-looking items first; undated items last
            items.sort(key=lambda x:x.get('event_date') or '0000-00-00',reverse=True)
            detail_budget=int(cfg.get('max_detail_fetches_per_source',15))
            last_date=None; last_url=None
            for item in items:
                if item['event_date']:
                    try:
                        if date.fromisoformat(item['event_date'])<cutoff: continue
                    except: pass
                body=item['context']; detail_url=item['url']
                if detail_budget>0 and detail_url!=source['url']:
                    try:
                        r=http.get(detail_url); detail_budget-=1
                        body=compact_excerpt(BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True),2500)
                    except Exception as e:
                        warnings.append(f"{sid} detail {detail_url}: {e}")
                if not relevant(item['title']+' '+body,cfg): continue
                info=classify_event(item['title'],body); caps=extract_capacities(item['title']+' '+body)
                company=source.get('company'); matched=find_project_alias(db,company,item['title']+' '+body)
                score=materiality(item['title'],body,info,caps,item.get('event_date'),asof)
                event={**info,'source_id':sid,'source_name':source.get('source_name'),'source_tier':source.get('tier'),'source_url':detail_url,'title':item['title'],'body_excerpt':compact_excerpt(body),'event_date':item.get('event_date'),'company':company,'capacity_mw':max(caps) if caps else None,'capacity_mentions':caps,'matched_project_id':matched,'materiality_score':score,'confidence':0.95 if item.get('event_date') else 0.75,'review_status':'auto'}
                added+=upsert_event(db,event)
                if apply_project_status(db,event): updated_projects+=1
                if item.get('event_date') and (last_date is None or item['event_date']>last_date): last_date=item['event_date']; last_url=detail_url
            db.conn.execute("""INSERT INTO source_watch_state(source_id,last_success_at,last_item_date,last_item_url,last_hash,consecutive_failures,last_error)
              VALUES(?,?,?,?,?,0,NULL) ON CONFLICT(source_id) DO UPDATE SET last_success_at=excluded.last_success_at,last_item_date=excluded.last_item_date,last_item_url=excluded.last_item_url,last_hash=excluded.last_hash,consecutive_failures=0,last_error=NULL""",
              (sid,utcnow(),last_date,last_url,hashlib.sha256(html.encode('utf-8','ignore')).hexdigest()))
            db.conn.commit()
        except Exception as e:
            warnings.append(f"{sid}: {e}")
            db.conn.execute("""INSERT INTO source_watch_state(source_id,consecutive_failures,last_error)
              VALUES(?,1,?) ON CONFLICT(source_id) DO UPDATE SET consecutive_failures=consecutive_failures+1,last_error=excluded.last_error""",(sid,str(e)))
            db.conn.commit()
    return {'sources_checked':checked,'events_added':added,'project_updates':updated_projects,'warnings':warnings}


def event_fact(row: sqlite3.Row) -> str:
    body=normalize_space(row['body_excerpt'] or '')
    if body:
        # Use first sentence-like unit, keeping it compact.
        sent=re.split(r'(?<=[.!?])\s+',body)[0]
        if len(sent)>220: sent=sent[:217].rsplit(' ',1)[0]+'…'
        return sent
    cap=row['capacity_mw']
    if cap: return f"The development involves approximately {cap:,.0f} MW."
    return normalize_space(row['title'])


def why_it_matters(row: sqlite3.Row) -> str:
    topic=(row['topic'] or '').lower(); cat=row['category']; cap=row['capacity_mw'] or 0
    if 'pdp2026' in topic: return 'It resets the forward build envelope for generation, storage and grid investment, and will shape procurement opportunities through 2037 and beyond.'
    if 'direct ppa' in topic or cat=='Market design': return 'It can expand customer-led clean-power procurement and change the commercial roles of utilities, generators and large power users.'
    if cat=='Tariff & economics': return 'It changes the delivered-cost benchmark against which UGT, Direct PPA and behind-the-meter solutions are assessed.'
    if cat=='Grid & system': return 'Grid availability and connection timing can become the binding constraint on renewable build-out even when generation projects are ready.'
    if row['status_after']=='COD': return 'It is evidence that the contracted renewable pipeline is converting into operating capacity rather than remaining only an award or announcement.'
    if row['status_after']=='PPA signed': return 'It moves capacity from development ambition into a bankable contracted pipeline and improves visibility on future CODs.'
    if cat=='Project & investment':
        if cap>=300: return 'The scale is large enough to affect the near-term renewable pipeline and competitive positioning among major developers.'
        return 'It provides a concrete signal on how developer pipelines are progressing from award and financing toward construction and COD.'
    return 'It is a recent development with potential implications for Thailand’s power-market outlook.'


def generate_digest(db: DB, refresh_date: str|None=None, n: int=3, max_age_days: int=120) -> list[dict]:
    rd=date.fromisoformat(refresh_date) if refresh_date else date.today(); refresh_date=rd.isoformat(); cutoff=(rd-timedelta(days=max_age_days)).isoformat()
    rows=db.conn.execute("""SELECT * FROM market_event WHERE is_relevant=1 AND event_date IS NOT NULL AND event_date>=? AND event_date<=?
      ORDER BY materiality_score DESC,event_date DESC,confidence DESC""",(cutoff,refresh_date)).fetchall()
    # Add small deterministic recency boost for selection only.
    ranked=[]
    for r in rows:
        age=(rd-date.fromisoformat(r['event_date'])).days
        recency=2 if age<=14 else 1 if age<=45 else 0
        ranked.append((r['materiality_score']+recency,r))
    ranked.sort(key=lambda x:(x[0],x[1]['event_date']),reverse=True)
    chosen=[]; topics=set(); company_seen=set(); cat_counts={}
    for score,r in ranked:
        topic=(r['topic'] or r['title']).lower()
        if topic in topics: continue
        if r['company'] and r['company'] in company_seen: continue
        if cat_counts.get(r['category'],0)>=2: continue
        chosen.append((score,r)); topics.add(topic); cat_counts[r['category']]=cat_counts.get(r['category'],0)+1
        if r['company']: company_seen.add(r['company'])
        if len(chosen)>=n: break
    # Ensure a concrete project/investment signal is represented when one scores >=7.
    if chosen and not any(r['category']=='Project & investment' for _,r in chosen):
        candidate=next(((s,r) for s,r in ranked if r['category']=='Project & investment' and r['materiality_score']>=7),None)
        if candidate:
            chosen[-1]=candidate
    db.conn.execute("DELETE FROM weekly_digest WHERE refresh_date=?",(refresh_date,))
    out=[]
    for i,(sel_score,r) in enumerate(chosen[:n],1):
        item={'refresh_date':refresh_date,'rank':i,'category':r['category'],'headline':r['title'],'one_fact':event_fact(r),'why_it_matters':why_it_matters(r),'score':r['materiality_score'],'source_url':r['source_url'],'event_date':r['event_date']}
        db.conn.execute("""INSERT INTO weekly_digest(refresh_date,rank,category,headline,one_fact,why_it_matters,score,source_url,event_date)
          VALUES(?,?,?,?,?,?,?,?,?)""",tuple(item[k] for k in ['refresh_date','rank','category','headline','one_fact','why_it_matters','score','source_url','event_date']))
        out.append(item)
    db.conn.commit(); return out


def export_events(db: DB, out_dir: Path|None=None):
    import csv
    out_dir=out_dir or ROOT/'data'; out_dir.mkdir(parents=True,exist_ok=True)
    queries={
      'market_events.csv':"SELECT event_date,category,topic,company,title,capacity_mw,status_after,materiality_score,confidence,review_status,source_name,source_url FROM market_event ORDER BY event_date DESC,materiality_score DESC",
      'weekly_digest.csv':"SELECT * FROM weekly_digest ORDER BY refresh_date DESC,rank",
      'source_watch_state.csv':"SELECT * FROM source_watch_state ORDER BY source_id"
    }
    paths={}
    for fn,q in queries.items():
        cur=db.conn.execute(q); rows=cur.fetchall(); p=out_dir/fn
        fields=[d[0] for d in cur.description]
        with p.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
            if rows: w.writerows([dict(r) for r in rows])
        paths[fn]=str(p)
    return paths


def main():
    ap=argparse.ArgumentParser(description='Thailand power market intelligence watcher')
    ap.add_argument('command',choices=['seed','refresh','digest','export','all'])
    ap.add_argument('--db',default=str(ROOT/'data'/'thailand_power.db'))
    ap.add_argument('--offline',action='store_true')
    ap.add_argument('--asof',default=None,help='YYYY-MM-DD; defaults to today')
    args=ap.parse_args(); db=DB(Path(args.db)); db.init(); cfg=json.loads(WATCH_CONFIG.read_text()); out={}
    try:
        if args.command in {'seed','all'}: out['seed']=seed_intelligence(db)
        if args.command in {'refresh','all'} and not args.offline: out['refresh']=refresh_watchers(db,cfg,asof=date.fromisoformat(args.asof) if args.asof else None)
        if args.command in {'digest','all'}: out['digest']=generate_digest(db,args.asof)
        if args.command in {'export','all'}: out['exports']=export_events(db)
        print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
    finally: db.close()
    return 0

if __name__=='__main__': sys.exit(main())
