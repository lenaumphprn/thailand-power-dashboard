#!/usr/bin/env python3
from pathlib import Path
import csv, json, re, sqlite3, sys
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'thailand_power.db'
MANIFEST=ROOT/'public_figure_manifest.json'
HTML=ROOT/'data'/'thailand_power_renewables_pulse_live.html'
OUT=ROOT/'data'/'provenance_audit.csv'

def public_url(u): return bool(u and re.match(r'^https?://',str(u)))
def add(rows,severity,object_type,object_id,status,source_url,note): rows.append(dict(severity=severity,object_type=object_type,object_id=object_id,status=status,source_url=source_url or '',note=note))

def main():
    rows=[]; con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    # All displayed/latest metric observations must resolve to a public source URL.
    mids=['K01','K02','K03','K04','K05','K06','K07','K08','K09','K10','K11','K12']
    for mid in mids:
        r=con.execute("SELECT o.*,s.url registry_url FROM metric_observation o LEFT JOIN source_registry s ON o.source_id=s.source_id WHERE o.metric_id=? ORDER BY COALESCE(o.period_end,o.period_start) DESC,o.publication_date DESC,o.observation_id DESC LIMIT 1",(mid,)).fetchone()
        if not r: add(rows,'BLOCK','metric',mid,'FAIL','', 'Missing displayed metric observation'); continue
        u=r['raw_source_ref'] or r['registry_url']
        add(rows,'PASS' if public_url(u) else 'BLOCK','metric',mid,'PASS' if public_url(u) else 'FAIL',u,'Latest displayed observation has public lineage' if public_url(u) else 'Missing public source URL')
    # Renewable capacity / pipeline / project rows with quantitative values.
    checks=[
      ('renewable_capacity_snapshot','snapshot_id','source_url','capacity_mw'),
      ('procurement_cohort','cohort_id','source_url','target_mw'),
      ('project_registry','project_id','source_url','contracted_mw'),
      ('market_event','event_id','source_url','capacity_mw'),
      ('weekly_digest','rank','source_url','score'),
    ]
    for table,idcol,urlcol,valcol in checks:
        for r in con.execute(f"SELECT * FROM {table}").fetchall():
            u=r[urlcol] if urlcol in r.keys() else None
            # rows can have null value but if present/used as content, source must still be public
            if table in ('market_event',) and not r[valcol]: continue
            add(rows,'PASS' if public_url(u) else 'BLOCK',table,str(r[idcol]),'PASS' if public_url(u) else 'FAIL',u,'Public row-level source' if public_url(u) else 'Missing public row-level source')
    # Stage and SCOD rows resolve source_id -> public source registry.
    for table,idexpr in [('procurement_stage_snapshot',"cohort_id||':'||technology||':'||stage"),('procurement_scod_schedule',"cohort_id||':'||technology||':'||scod_year")]:
        for r in con.execute(f"SELECT {idexpr} rid,s.url source_url FROM {table} t LEFT JOIN source_registry s ON t.source_id=s.source_id").fetchall():
            u=r['source_url']; add(rows,'PASS' if public_url(u) else 'BLOCK',table,r['rid'],'PASS' if public_url(u) else 'FAIL',u,'Public source registry lineage' if public_url(u) else 'Missing public source')
    # Manifest governs static/hardcoded policy figures and all derived calculations.
    man=json.loads(MANIFEST.read_text()); ids={x['figure_id']:x for x in man['figures']}
    for fid,x in ids.items():
        urls=x.get('source_urls',[]); typ=x.get('provenance_type')
        ok=typ in ('DIRECT_PUBLIC','DERIVED_PUBLIC') and urls and all(public_url(u) for u in urls)
        if typ=='DERIVED_PUBLIC': ok=ok and bool(x.get('formula')) and bool(x.get('input_figure_ids')) and all(i in ids for i in x.get('input_figure_ids',[]))
        add(rows,'PASS' if ok else 'BLOCK','figure_manifest',fid,'PASS' if ok else 'FAIL',' | '.join(urls),'Direct public source' if typ=='DIRECT_PUBLIC' else 'Calculated only from public-source inputs')
    # Generated dashboard must carry the assurance + embedded provenance manifest and no prohibited placeholders.
    if not HTML.exists(): add(rows,'BLOCK','dashboard','html','FAIL','', 'Generated dashboard missing')
    else:
        h=HTML.read_text(); soup=BeautifulSoup(h,'html.parser')
        assurance=('Every quantitative figure is linked directly to its public source' in soup.get_text(' ',strip=True) or 'All quantitative figures use publicly available sources' in soup.get_text(' ',strip=True))
        embedded=soup.select_one('#figure-provenance') is not None
        add(rows,'PASS' if assurance else 'BLOCK','dashboard','source_assurance','PASS' if assurance else 'FAIL','', 'Visible public-source policy present')
        add(rows,'PASS' if embedded else 'BLOCK','dashboard','embedded_manifest','PASS' if embedded else 'FAIL','', 'Figure provenance manifest embedded in HTML')
        consistency=(ROOT/'data'/'consistency_audit.csv').exists()
        add(rows,'PASS' if consistency else 'BLOCK','dashboard','consistency_audit','PASS' if consistency else 'FAIL','', 'Consistency audit generated before publication')
        for tag in soup(['script','style']): tag.decompose()
        low=soup.get_text(' ',strip=True).lower()
        forbidden=[p for p in ['illustrative market value','model estimate:','assumed market value','placeholder market value'] if p in low]
        add(rows,'BLOCK' if forbidden else 'PASS','dashboard','forbidden_estimates','FAIL' if forbidden else 'PASS','',('Forbidden phrases: '+', '.join(forbidden)) if forbidden else 'No model-estimated / illustrative market values')
    con.close()
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['severity','object_type','object_id','status','source_url','note']); w.writeheader(); w.writerows(rows)
    blocks=[r for r in rows if r['severity']=='BLOCK']
    print(json.dumps({'rows':len(rows),'blocks':len(blocks),'output':str(OUT),'block_items':[f"{r['object_type']}:{r['object_id']}" for r in blocks]},indent=2))
    return 1 if blocks else 0
if __name__=='__main__': sys.exit(main())
