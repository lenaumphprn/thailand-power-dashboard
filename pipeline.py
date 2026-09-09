#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, io, json, os, re, sqlite3, sys, uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "thailand_power.db"
DEFAULT_EXPORT = ROOT / "data" / "dashboard_data.json"

THAI_MONTHS = {
    "มกราคม":1,"กุมภาพันธ์":2,"มีนาคม":3,"เมษายน":4,"พฤษภาคม":5,"มิถุนายน":6,
    "กรกฎาคม":7,"สิงหาคม":8,"กันยายน":9,"ตุลาคม":10,"พฤศจิกายน":11,"ธันวาคม":12,
    "ม.ค.":1,"ก.พ.":2,"มี.ค.":3,"เม.ย.":4,"พ.ค.":5,"มิ.ย.":6,
    "ก.ค.":7,"ส.ค.":8,"ก.ย.":9,"ต.ค.":10,"พ.ย.":11,"ธ.ค.":12,
}
EN_MONTHS = {m.lower(): i for i,m in enumerate(["", "Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]) if m}
EN_MONTHS.update({m.lower(): i for i,m in enumerate(["", "January","February","March","April","May","June","July","August","September","October","November","December"]) if m})


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def thai_year_to_ad(y: int) -> int:
    return y - 543 if y > 2400 else y

def month_number(text: Any) -> int:
    s = str(text).strip()
    if not s: raise ValueError("blank month")
    if s.isdigit(): return int(s)
    low = s.lower().replace('.', '')
    for k,v in EN_MONTHS.items():
        if low.startswith(k.replace('.','')): return v
    for k,v in THAI_MONTHS.items():
        if k in s: return v
    raise ValueError(f"unrecognized month: {text!r}")

def month_end(y: int, m: int) -> date:
    import calendar
    return date(y,m,calendar.monthrange(y,m)[1])

def normalize_key(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()

def parse_float(v: Any) -> float:
    if isinstance(v,(int,float)): return float(v)
    s=str(v).replace(',','').strip()
    s=re.sub(r"[^0-9.\-+]", "", s)
    return float(s)

class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
    def init(self):
        self.conn.executescript((ROOT/'schema.sql').read_text())
        self.conn.commit()
    def upsert_source(self, d):
        self.conn.execute("""INSERT INTO source_registry(source_id,organization,source_name,url,tier,cadence,method,last_checked_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET organization=excluded.organization,source_name=excluded.source_name,url=excluded.url,tier=excluded.tier,cadence=excluded.cadence,method=excluded.method""",
        (d['source_id'],d.get('organization'),d.get('source_name'),d.get('url'),d.get('tier'),d.get('cadence'),d.get('method'),d.get('last_checked_at')))
    def upsert_metric(self, d):
        self.conn.execute("""INSERT INTO metric_definition(metric_id,metric_name,definition,unit,scope_version,source_id_primary,active)
        VALUES(?,?,?,?,?,?,1) ON CONFLICT(metric_id) DO UPDATE SET metric_name=excluded.metric_name,definition=excluded.definition,unit=excluded.unit,scope_version=excluded.scope_version,source_id_primary=excluded.source_id_primary""",
        (d['metric_id'],d['metric_name'],d['definition'],d.get('unit'),d.get('scope_version','v1.0'),d.get('source_id_primary')))
    def add_observation(self, d):
        ing=d.get('ingested_at',utcnow())
        scope=d.get('scope_version','v1.0')
        existing=self.conn.execute("""SELECT observation_id FROM metric_observation
          WHERE metric_id=? AND COALESCE(period_start,'')=COALESCE(?, '') AND COALESCE(period_end,'')=COALESCE(?, '')
            AND COALESCE(source_id,'')=COALESCE(?, '') AND COALESCE(publication_date,'')=COALESCE(?, '') AND scope_version=?
          LIMIT 1""",(d['metric_id'],d.get('period_start'),d.get('period_end'),d.get('source_id'),d.get('publication_date'),scope)).fetchone()
        if existing: return 0
        cur=self.conn.execute("""INSERT OR IGNORE INTO metric_observation(metric_id,period_start,period_end,value,unit,source_id,publication_date,effective_start,effective_end,ingested_at,scope_version,qa_status,raw_source_ref,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['metric_id'],d.get('period_start'),d.get('period_end'),d['value'],d.get('unit'),d.get('source_id'),d.get('publication_date'),d.get('effective_start'),d.get('effective_end'),ing,scope,d.get('qa_status','pass'),d.get('raw_source_ref'),d.get('note')))
        if cur.rowcount:
            oid=cur.lastrowid
            for k,v in (d.get('details') or {}).items():
                if isinstance(v,(int,float)):
                    self.conn.execute("INSERT OR REPLACE INTO observation_detail(observation_id,detail_key,detail_value) VALUES(?,?,?)",(oid,k,float(v)))
                else:
                    self.conn.execute("INSERT OR REPLACE INTO observation_detail(observation_id,detail_key,detail_text) VALUES(?,?,?)",(oid,k,str(v)))
        self.conn.commit()
        return cur.rowcount
    def add_tariff_event(self, d):
        self.conn.execute("""INSERT OR REPLACE INTO tariff_event(event_id,metric_id,publication_date,effective_start,effective_end,value,unit,customer_scope,source_id,source_url,title,supersedes_event_id,ingested_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['event_id'],d['metric_id'],d.get('publication_date'),d.get('effective_start'),d.get('effective_end'),d['value'],d['unit'],d.get('customer_scope'),d.get('source_id'),d.get('source_url'),d.get('title'),d.get('supersedes_event_id'),d.get('ingested_at',utcnow())))
        self.conn.commit()
    def latest(self, metric_id: str):
        return self.conn.execute("""SELECT * FROM metric_observation WHERE metric_id=? ORDER BY COALESCE(effective_start,period_end,period_start) DESC, COALESCE(publication_date,'') DESC, observation_id DESC LIMIT 1""",(metric_id,)).fetchone()
    def details(self, observation_id: int):
        rows=self.conn.execute("SELECT * FROM observation_detail WHERE observation_id=?",(observation_id,)).fetchall()
        return {r['detail_key']: (r['detail_value'] if r['detail_value'] is not None else r['detail_text']) for r in rows}
    def close(self): self.conn.close()

class Http:
    def __init__(self, timeout=30):
        if requests is None: raise RuntimeError("requests/bs4 are required for live mode")
        self.s=requests.Session(); self.s.headers.update({'User-Agent':'ThailandPowerPulse/1.0'}); self.timeout=timeout
    def get(self,url):
        r=self.s.get(url,timeout=self.timeout); r.raise_for_status(); return r


def seed(db: DB, seed_path=ROOT/'seed_snapshot.json'):
    p=json.loads(Path(seed_path).read_text())
    for s in p['sources']: db.upsert_source(s)
    for m in p['metrics']: db.upsert_metric(m)
    n=0
    for o in p['observations']: n += db.add_observation(o)
    # Seed Ft history as metric observations; current row may duplicate K07 and will be ignored.
    for f in p.get('ft_history',[]):
        n += db.add_observation({'metric_id':'K07','period_start':f['period_start'],'period_end':f['period_end'],'effective_start':f['period_start'],'effective_end':f['period_end'],'value':f['value'],'unit':'satang/kWh','source_id':'S08','publication_date':f['period_start'],'scope_version':'v1.0','qa_status':'seeded_verified','raw_source_ref':'https://erc.or.th/th/automatic/','note':'Seeded from ERC Ft history table.'})
    # Applicable headline tariff events from official ERC releases.
    db.add_tariff_event({'event_id':'ERC-2026-07-23-FT','metric_id':'K06','publication_date':'2026-07-23','effective_start':'2026-09-01','effective_end':'2026-12-31','value':3.95,'unit':'THB/kWh','customer_scope':'average headline at Ft decision','source_id':'S09','source_url':'https://www.erc.or.th/th/news-release/3458','title':'Sep-Dec 2026 Ft decision'})
    db.add_tariff_event({'event_id':'ERC-2026-08-13-RATE','metric_id':'K06','publication_date':'2026-08-13','effective_start':'2026-09-01','effective_end':None,'value':3.86,'unit':'THB/kWh','customer_scope':'average billed rate across all categories','source_id':'S09','source_url':'https://www.erc.or.th/th/news-release/3472','title':'Residential progressive-rate structure; average billed rate across all categories','supersedes_event_id':'ERC-2026-07-23-FT'})
    renewable_result=seed_renewables(db)
    db.conn.commit(); return {'metric_observations':n,'renewables':renewable_result}



def seed_renewables(db: DB, seed_path=ROOT/'seed_renewables.json'):
    """Seed governed renewable capacity, procurement cohorts, and project milestones."""
    p=json.loads(Path(seed_path).read_text())
    for s in p.get('sources',[]): db.upsert_source(s)
    for m in p.get('metrics',[]): db.upsert_metric(m)
    n_obs=0
    for o in p.get('observations',[]): n_obs += db.add_observation(o)
    for d in p.get('capacity_snapshots',[]):
        db.conn.execute("""INSERT OR REPLACE INTO renewable_capacity_snapshot
        (snapshot_id,as_of_date,scope,status_stage,technology,project_count,capacity_mw,capacity_basis,source_id,source_url,publication_date,qa_status,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['snapshot_id'],d['as_of_date'],d['scope'],d.get('status_stage'),d['technology'],d.get('project_count'),d['capacity_mw'],d['capacity_basis'],d.get('source_id'),d.get('source_url'),d.get('publication_date'),d.get('qa_status','pass'),d.get('note')))
    for d in p.get('cohorts',[]):
        db.conn.execute("""INSERT OR REPLACE INTO procurement_cohort
        (cohort_id,cohort_name,scheme,as_of_date,target_mw,selected_projects,selected_mw,capacity_basis,status,scod_start_year,scod_end_year,source_id,source_url,qa_status,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['cohort_id'],d['cohort_name'],d.get('scheme'),d.get('as_of_date'),d.get('target_mw'),d.get('selected_projects'),d.get('selected_mw'),d.get('capacity_basis','contracted_sale_mw'),d.get('status'),d.get('scod_start_year'),d.get('scod_end_year'),d.get('source_id'),d.get('source_url'),d.get('qa_status','pass'),d.get('note')))
    for d in p.get('stages',[]):
        db.conn.execute("""INSERT OR REPLACE INTO procurement_stage_snapshot
        (cohort_id,as_of_date,technology,stage,project_count,capacity_mw,capacity_basis,source_id,qa_status,note)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (d['cohort_id'],d['as_of_date'],d.get('technology','Total'),d['stage'],d.get('project_count'),d['capacity_mw'],d.get('capacity_basis','contracted_sale_mw'),d.get('source_id'),d.get('qa_status','pass'),d.get('note')))
    for d in p.get('scod',[]):
        db.conn.execute("""INSERT OR REPLACE INTO procurement_scod_schedule
        (cohort_id,technology,scod_year,capacity_mw,source_id,qa_status,note)
        VALUES(?,?,?,?,?,?,?)""",
        (d['cohort_id'],d['technology'],d['scod_year'],d['capacity_mw'],d.get('source_id'),d.get('qa_status','pass'),d.get('note')))
    for d in p.get('projects',[]):
        db.conn.execute("""INSERT OR REPLACE INTO project_registry
        (project_id,company,project_name,is_aggregate,project_count,technology,province,contracted_mw,installed_mw,status,status_date,expected_cod_start,expected_cod_end,cohort_id,overlap_group,source_id,source_url,qa_status,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d['project_id'],d.get('company'),d['project_name'],d.get('is_aggregate',0),d.get('project_count',1),d.get('technology'),d.get('province'),d.get('contracted_mw'),d.get('installed_mw'),d.get('status'),d.get('status_date'),d.get('expected_cod_start'),d.get('expected_cod_end'),d.get('cohort_id'),d.get('overlap_group'),d.get('source_id'),d.get('source_url'),d.get('qa_status','pass'),d.get('note')))
    db.conn.commit()
    return {'observations':n_obs,'capacity_snapshots':len(p.get('capacity_snapshots',[])),'cohorts':len(p.get('cohorts',[])),'stages':len(p.get('stages',[])),'scod_rows':len(p.get('scod',[])),'projects':len(p.get('projects',[]))}

def _package_show(http: Http, base: str, package_id: str) -> dict:
    url=f"{base.rstrip('/')}/api/3/action/package_show"
    r=http.s.get(url,params={'id':package_id},timeout=http.timeout); r.raise_for_status()
    j=r.json()
    if not j.get('success'): raise RuntimeError(f"CKAN package_show failed: {j}")
    return j['result']

def resolve_resource(http: Http, config: dict, key: str) -> tuple[str,str]:
    spec=config['eppo']['packages'][key]
    errors=[]
    for base in config['eppo']['base_urls']:
        try:
            pkg=_package_show(http,base,spec['package_id'])
            resources=pkg.get('resources',[])
            ranked=[]
            for r in resources:
                fmt=(r.get('format') or '').lower()
                score=(3 if fmt=='csv' else 0)+(2 if r.get('datastore_active') else 0)+(1 if r.get('last_modified') else 0)
                ranked.append((score,r))
            if ranked:
                r=max(ranked,key=lambda x:x[0])[1]
                return r.get('id') or spec['fallback_resource_id'], r.get('url') or ''
        except Exception as e: errors.append(str(e))
    return spec['fallback_resource_id'], ''

def fetch_ckan_records(http: Http, config: dict, key: str) -> list[dict]:
    resource_id, direct_url=resolve_resource(http,config,key)
    errors=[]
    for base in config['eppo']['base_urls']:
        url=f"{base.rstrip('/')}/api/3/action/datastore_search"
        try:
            offset=0; rows=[]
            while True:
                r=http.s.get(url,params={'resource_id':resource_id,'limit':10000,'offset':offset},timeout=http.timeout); r.raise_for_status()
                j=r.json(); result=j['result']; batch=result.get('records',[]); rows.extend(batch)
                if len(batch)<10000: return rows
                offset += len(batch)
        except Exception as e: errors.append(str(e))
    if direct_url:
        try:
            r=http.get(direct_url)
            return list(csv.DictReader(io.StringIO(r.text.lstrip('\ufeff'))))
        except Exception as e: errors.append(str(e))
    raise RuntimeError("EPPO resource fetch failed: " + " | ".join(errors))


def _find_col(row: dict, candidates: Iterable[str]):
    normalized={normalize_key(k):k for k in row}
    for c in candidates:
        if normalize_key(c) in normalized: return normalized[normalize_key(c)]
    return None

def _period_rows(rows: list[dict]):
    for r in rows:
        ycol=_find_col(r,['Year','ปี']); mcol=_find_col(r,['Month','เดือน']); qcol=_find_col(r,['Quantity','ปริมาณ','Value'])
        if not all([ycol,mcol,qcol]): raise ValueError(f"required columns not found in {list(r)}")
        y=thai_year_to_ad(int(float(str(r[ycol]).strip()))); m=month_number(r[mcol]); q=parse_float(r[qcol])
        yield r,y,m,q

def transform_demand(rows: list[dict]):
    buckets={}
    for r,y,m,q in _period_rows(rows):
        scol=_find_col(r,['Sector','สาขา']); sec=normalize_key(r.get(scol,'')) if scol else ''
        buckets.setdefault((y,m),[]).append((sec,q))
    monthly={}
    total_alias={'total','รวม','ทั้งประเทศ','grand total'}
    for k,vals in buckets.items():
        totals=[q for sec,q in vals if sec in total_alias or 'total'==sec]
        monthly[k]=totals[0] if len(totals)==1 else sum(q for sec,q in vals if sec not in total_alias)
    latest_y=max(y for y,m in monthly); latest_m=max(m for y,m in monthly if y==latest_y)
    cur=sum(v for (y,m),v in monthly.items() if y==latest_y and m<=latest_m)
    prev=sum(v for (y,m),v in monthly.items() if y==latest_y-1 and m<=latest_m)
    if prev<=0: raise ValueError("prior-year comparison missing")
    growth=(cur/prev-1)*100
    fy_prev=sum(v for (y,m),v in monthly.items() if y==latest_y-1)
    fy_prev2=sum(v for (y,m),v in monthly.items() if y==latest_y-2)
    fy_prev_growth=((fy_prev/fy_prev2-1)*100) if fy_prev>0 and fy_prev2>0 else None
    if abs(growth)>15: qa='warn' 
    else: qa='pass'
    return {'year':latest_y,'month':latest_m,'ytd':cur,'prior_ytd':prev,'growth_pct':growth,'qa':qa,'monthly':monthly,
            'prior_fy_total':fy_prev,'prior_prior_fy_total':fy_prev2,'prior_fy_growth_pct':fy_prev_growth}

def classify_fuel(name: str, config: dict):
    n=normalize_key(name)
    for cls,aliases in config['fuel_aliases'].items():
        if any(normalize_key(a) in n or n in normalize_key(a) for a in aliases): return cls
    return 'other'

def transform_generation(rows: list[dict], config: dict):
    """Return latest-year YTD generation mix plus prior-FY benchmark.

    EPPO publishes monthly fuel volumes. The dashboard's current generation mix is YTD
    through the latest available month, not the latest month alone. Prior full-year
    shares are retained only as an explicitly labelled comparator.
    """
    buckets={}
    for r,y,m,q in _period_rows(rows):
        fcol=_find_col(r,['Fuel Type','Fuel','ชนิดเชื้อเพลิง','เชื้อเพลิง'])
        fuel=str(r.get(fcol,'')) if fcol else ''
        buckets.setdefault((y,m),[]).append((fuel,q))
    if not buckets: raise ValueError('no generation rows')
    latest_y=max(y for y,m in buckets)
    latest_m=max(m for y,m in buckets if y==latest_y)
    total_alias={'total','grand total','รวม','รวมทั้งสิ้น','total generation'}

    def aggregate(year:int, max_month:int|None=None):
        classes={}; explicit_total=0.0; has_total=False
        for (y,m),vals in buckets.items():
            if y!=year or (max_month is not None and m>max_month): continue
            for fuel,q in vals:
                n=normalize_key(fuel)
                if n in total_alias or n.startswith('total '):
                    explicit_total += q; has_total=True; continue
                cls=classify_fuel(fuel,config)
                classes[cls]=classes.get(cls,0.0)+q
        category_total=sum(classes.values())
        total=explicit_total if has_total and explicit_total>0 else category_total
        # Guard against a malformed total row. Shares must reconcile to category sum.
        if has_total and category_total>0 and not (0.98 <= category_total/total <= 1.02):
            total=category_total
        shares={k:(v/total*100 if total else 0.0) for k,v in classes.items()}
        if total and abs(sum(shares.values())-100)>1.0:
            raise ValueError(f'generation shares do not reconcile: {sum(shares.values()):.2f}%')
        return {'total':total,'classes':classes,'shares':shares}

    cur=aggregate(latest_y,latest_m)
    prior_same=aggregate(latest_y-1,latest_m)
    prior_fy=aggregate(latest_y-1,None)
    shares=cur['shares']; pf=prior_fy['shares']
    re_share=shares.get('renewable',0)+shares.get('hydro',0)
    prior_re=pf.get('renewable',0)+pf.get('hydro',0)
    gas_share=shares.get('gas',0); prior_gas=pf.get('gas',0)
    cur_gas=cur['classes'].get('gas',0); prev_gas=prior_same['classes'].get('gas',0)
    gas_yoy=((cur_gas/prev_gas-1)*100) if prev_gas>0 else None
    qa='pass' if cur['total']>0 and 99.0 <= sum(shares.values()) <= 101.0 else 'block'
    return {
      'year':latest_y,'month':latest_m,'total':cur['total'],'classes':cur['classes'],'shares':shares,
      're_share':re_share,'gas_share':gas_share,'import_share':shares.get('imports',0),'qa':qa,
      'prior_fy_shares':pf,'prior_fy_re_share':prior_re,'prior_fy_gas_share':prior_gas,
      'gas_generation_yoy_pct':gas_yoy
    }


def parse_egat_peak_html(html: str):
    """Parse current-year EGAT system peak and latest monthly peak from the public page."""
    if BeautifulSoup is None: raise RuntimeError('bs4 unavailable')
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    # Latest monthly sentence: month, date, BE year, time, MW, absolute move, percent move.
    mm=re.search(
      r'เดือน(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม).*?'
      r'วันที่\s*(\d{1,2})\s*\1?[^\d]{0,20}(?:พ\.ศ\.)?\s*(\d{4}).*?เวลา\s*(\d{1,2})[.:](\d{2}).*?'
      r'([\d,]+\.\d+)\s*เมกะวัตต์.*?(เพิ่มขึ้น|ลดลง).*?([\d,]+\.\d+)\s*เมกะวัตต์.*?ร้อยละ\s*([\d.]+)',
      text)
    # The date portion on the page does not repeat the month name after "วันที่"; use a looser fallback.
    if not mm:
        mm=re.search(
          r'เดือน(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม).*?'
          r'วันที่\s*(\d{1,2})\s+\S+\s+(?:พ\.ศ\.)?\s*(\d{4}).*?เวลา\s*(\d{1,2})[.:](\d{2}).*?'
          r'([\d,]+\.\d+)\s*เมกะวัตต์.*?(เพิ่มขึ้น|ลดลง).*?([\d,]+\.\d+)\s*เมกะวัตต์.*?ร้อยละ\s*([\d.]+)',
          text)
    # Annual current-year peak sentence.
    am=re.search(
      r'ส่วนความต้องการพลังไฟฟ้าสูงสุดของระบบ\s*กฟผ\..*?วันที่\s*(\d{1,2})\s+' 
      r'(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+' 
      r'(?:พ\.ศ\.)?\s*(\d{4}).*?เวลา\s*(\d{1,2})[.:](\d{2}).*?([\d,]+\.\d+)\s*เมกะวัตต์', text)
    if not am: raise ValueError('EGAT annual system peak not found')
    d,mon,by,hh,mi,val=am.groups(); y=thai_year_to_ad(int(by)); peak_date=date(y,THAI_MONTHS[mon],int(d)).isoformat(); peak=float(val.replace(',',''))
    out={'peak_mw':peak,'peak_date':peak_date,'peak_time':f'{int(hh):02d}:{mi}'}
    # Match the immediately prior calendar year's stated peak, not the current-year 'ปี' token.
    prior_be=str((y-1)+543)
    prior=re.search(r'ปี\s*'+re.escape(prior_be)+r'.*?วันที่\s*(\d{1,2})\s+\S+.*?([\d,]+\.\d+)\s*เมกะวัตต์',text)
    if prior:
        _,pv=prior.groups(); out['prior_year_peak_mw']=float(pv.replace(',','')); out['prior_year']=y-1
        if out['prior_year_peak_mw']>0: out['growth_pct']=(peak/out['prior_year_peak_mw']-1)*100
    if mm:
        mon,d,by,hh,mi,val,direction,abs_move,pct=mm.groups(); ly=thai_year_to_ad(int(by)); move=float(pct)
        if direction=='ลดลง': move=-move
        out.update({'latest_month':THAI_MONTHS[mon],'latest_monthly_peak_mw':float(val.replace(',','')),
                    'latest_monthly_peak_date':date(ly,THAI_MONTHS[mon],int(d)).isoformat(),
                    'latest_monthly_peak_time':f'{int(hh):02d}:{mi}','latest_monthly_change_pct':move})
    return out


def parse_erc_ft_html(html: str):
    if BeautifulSoup is None: raise RuntimeError("bs4 unavailable")
    soup=BeautifulSoup(html,'html.parser'); out=[]
    for tr in soup.find_all('tr'):
        cells=[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'])]
        if len(cells)<2: continue
        period=cells[0]; val=cells[1]
        ym=re.findall(r'(มกราคม|พฤษภาคม|กันยายน)\s*[–\-]\s*(เมษายน|สิงหาคม|ธันวาคม)\s*(\d{4})',period)
        if not ym: continue
        start_name,end_name,by=ym[0]; y=thai_year_to_ad(int(by)); sm=THAI_MONTHS[start_name]; em=THAI_MONTHS[end_name]
        try: ft=parse_float(val)
        except: continue
        out.append({'period_start':date(y,sm,1).isoformat(),'period_end':month_end(y,em).isoformat(),'value':ft,'unit':'satang/kWh','label':period})
    return out

def parse_thai_page_date(text: str):
    m=re.search(r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{4})',text)
    if not m: return None
    return date(thai_year_to_ad(int(m.group(3))),THAI_MONTHS[m.group(2)],int(m.group(1))).isoformat()

def parse_erc_tariff_decision(html: str, url: str):
    if BeautifulSoup is None: raise RuntimeError("bs4 unavailable")
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    pub=parse_thai_page_date(text)
    values=[float(x) for x in re.findall(r'(?:เหลือ|อัตรา[^.]{0,80}?)(\d+\.\d+)\s*บาทต่อหน่วย',text)]
    if not values:
        values=[float(x) for x in re.findall(r'(\d+\.\d+)\s*บาทต่อหน่วย',text)]
    if not values: return None
    value=values[-1]
    # Specific current rule: releases that say billing starts Sep 69 become effective 2026-09-01.
    eff=None
    if re.search(r'(?:เริ่มบิล|ตั้งแต่).*ก\.ย\.\s*69|กันยายน\s*2569',text): eff='2026-09-01'
    return {'event_id':'ERC-'+hashlib.sha1(url.encode()).hexdigest()[:12], 'metric_id':'K06','publication_date':pub,'effective_start':eff,'effective_end':None,'value':value,'unit':'THB/kWh','customer_scope':'as stated by ERC','source_id':'S09','source_url':url,'title':text[:180]}


def refresh_live(db: DB, config: dict):
    http=Http(); new_obs=0; new_events=0; warnings=[]; blocks=[]; checked=0
    # EPPO demand
    checked+=1
    try:
        rows=fetch_ckan_records(http,config,'demand'); t=transform_demand(rows)
        ps=date(t['year'],1,1).isoformat(); pe=month_end(t['year'],t['month']).isoformat()
        new_obs += db.add_observation({'metric_id':'K01','period_start':ps,'period_end':pe,'value':t['growth_pct'],'unit':'% YoY','source_id':'S01','publication_date':date.today().isoformat(),'scope_version':'v1.0','qa_status':t['qa'],'note':f"YTD demand {t['ytd']:.2f}; prior {t['prior_ytd']:.2f}", 'details':{'ytd_gwh':t['ytd'],'prior_ytd_gwh':t['prior_ytd'],'fy2025_growth_pct':t.get('prior_fy_growth_pct')}})
        if t['qa']=='warn': warnings.append('K01 demand growth >15%')
    except Exception as e: warnings.append('S01 demand: '+str(e))
    # EPPO generation - latest-year YTD mix, not latest month alone.
    checked+=1
    try:
        rows=fetch_ckan_records(http,config,'generation'); t=transform_generation(rows,config)
        ps=date(t['year'],1,1).isoformat(); pe=month_end(t['year'],t['month']).isoformat()
        if t['qa']=='block': blocks.append('K03/K05 generation shares failed reconciliation')
        else:
            sh=t['shares']; pf=t['prior_fy_shares']
            current_label=('H1 '+str(t['year'])) if t['month']==6 else (('FY'+str(t['year'])) if t['month']==12 else f"Jan-{month_end(t['year'],t['month']).strftime('%b')} {t['year']}")
            re_details={'renewable_pct':sh.get('renewable',0),'hydro_pct':sh.get('hydro',0),'gas_pct':sh.get('gas',0),
                        'coal_lignite_pct':sh.get('coal_lignite',0),'oil_pct':sh.get('oil',0),'imports_pct':sh.get('imports',0),
                        'other_fuels_pct':sh.get('other',0),'other_pct':100-t['re_share'],'ytd_generation_gwh':t['total'],
                        'prior_fy_re_hydro_pct':t['prior_fy_re_share'],'prior_fy_gas_pct':t['prior_fy_gas_share'],
                        'prior_period_label':f"FY{t['year']-1}",'period_label':current_label}
            gas_details={'prior_share_pct':t['prior_fy_gas_share'],'prior_period_label':f"FY{t['year']-1}",
                         'share_change_ppt':t['gas_share']-t['prior_fy_gas_share'],'renewable_pct':sh.get('renewable',0),
                         'hydro_pct':sh.get('hydro',0),'re_hydro_pct':t['re_share'],'period_label':current_label}
            if t['gas_generation_yoy_pct'] is not None: gas_details['gas_generation_yoy_pct']=t['gas_generation_yoy_pct']
            src='https://catalog.eppo.go.th/dataset/dataset_11_27'
            new_obs += db.add_observation({'metric_id':'K03','period_start':ps,'period_end':pe,'value':t['re_share'],'unit':'% of generation','source_id':'S02','publication_date':date.today().isoformat(),'scope_version':'v1.0','qa_status':'pass','raw_source_ref':src,'details':re_details})
            new_obs += db.add_observation({'metric_id':'K05','period_start':ps,'period_end':pe,'value':t['gas_share'],'unit':'% of generation','source_id':'S02','publication_date':date.today().isoformat(),'scope_version':'v1.0','qa_status':'pass','raw_source_ref':src,'details':gas_details})
    except Exception as e: warnings.append('S02 generation: '+str(e))
    # EGAT current-year system peak + latest monthly peak context.
    checked+=1
    try:
        u=config.get('egat',{}).get('peak_url','https://www.egat.co.th/home/statistics-demand-latest/')
        t=parse_egat_peak_html(http.get(u).text)
        det={k:v for k,v in t.items() if k not in ('peak_mw','peak_date')}
        new_obs += db.add_observation({'metric_id':'K02','period_start':t['peak_date'][:4]+'-01-01','period_end':t['peak_date'],'value':t['peak_mw'],'unit':'MW','source_id':'S07','publication_date':date.today().isoformat(),'scope_version':'v1.0','qa_status':'pass','raw_source_ref':u,'details':det})
    except Exception as e: warnings.append('S07 EGAT peak: '+str(e))
    # ERC Ft
    checked+=1
    try:
        html=http.get(config['erc']['ft_url']).text; rows=parse_erc_ft_html(html)
        for f in rows:
            new_obs += db.add_observation({'metric_id':'K07',**f,'effective_start':f['period_start'],'effective_end':f['period_end'],'source_id':'S08','publication_date':date.today().isoformat(),'scope_version':'v1.0','qa_status':'pass'})
    except Exception as e: warnings.append('S08 Ft: '+str(e))
    # ERC tariff decisions
    for u in config['erc']['known_tariff_decisions']:
        checked+=1
        try:
            d=parse_erc_tariff_decision(http.get(u).text,u)
            if d: db.add_tariff_event(d); new_events+=1
        except Exception as e: warnings.append('S09 tariff '+u+': '+str(e))
    return {'sources_checked':checked,'new_observations':new_obs,'new_events':new_events,'warnings':warnings,'blocks':blocks}


def latest_applicable_tariff(db: DB, on_date: str|None=None):
    d=on_date or date.today().isoformat()
    return db.conn.execute("""SELECT * FROM tariff_event WHERE effective_start IS NOT NULL AND effective_start<=? AND (effective_end IS NULL OR effective_end>=?) ORDER BY publication_date DESC,effective_start DESC LIMIT 1""",(d,d)).fetchone()

def export_renewable_csvs(db: DB, out_dir: Path | None=None):
    out_dir=out_dir or (ROOT/'data')
    out_dir.mkdir(parents=True,exist_ok=True)
    exports={
      'renewable_capacity_snapshot.csv':"SELECT * FROM renewable_capacity_snapshot ORDER BY as_of_date,scope,status_stage,technology",
      'procurement_pipeline.csv':"""SELECT c.cohort_id,c.cohort_name,c.scheme,c.as_of_date,c.target_mw,c.selected_projects,c.selected_mw,c.status,c.scod_start_year,c.scod_end_year,
        s.technology,s.stage,s.project_count AS stage_projects,s.capacity_mw AS stage_mw,s.qa_status AS stage_qa
        FROM procurement_cohort c LEFT JOIN procurement_stage_snapshot s ON c.cohort_id=s.cohort_id
        ORDER BY c.cohort_id,s.technology,CASE s.stage WHEN 'selected' THEN 1 WHEN 'ppa' THEN 2 WHEN 'cop' THEN 3 WHEN 'licensed' THEN 4 WHEN 'cod' THEN 5 ELSE 9 END""",
      'project_registry.csv':"SELECT * FROM project_registry ORDER BY COALESCE(status_date,expected_cod_start) DESC,company,project_name",
      'procurement_scod_schedule.csv':"SELECT * FROM procurement_scod_schedule ORDER BY cohort_id,scod_year,technology",
    }
    paths={}
    for fn,sql in exports.items():
        rows=db.conn.execute(sql).fetchall(); path=out_dir/fn
        with path.open('w',newline='',encoding='utf-8-sig') as f:
            if rows:
                w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        paths[fn]=str(path)
    return paths

def export_dashboard(db: DB, out_path: Path=DEFAULT_EXPORT):
    obj={'generated_at':utcnow(),'metrics':{},'ft_history':[],'pool_gas_history':[],'renewables':{},'weekly_digest':[],'market_events':[]}
    for mid in ['K01','K02','K03','K04','K05','K06','K07','K08','K09','K10','K11','K12','K13']:
        r=db.latest(mid)
        if r:
            obj['metrics'][mid]={k:r[k] for k in r.keys()}
            obj['metrics'][mid]['details']=db.details(r['observation_id'])
            sr=db.conn.execute("SELECT organization,source_name,url,tier FROM source_registry WHERE source_id=?",(r['source_id'],)).fetchone() if r['source_id'] else None
            obj['metrics'][mid]['source_url']=r['raw_source_ref'] or (sr['url'] if sr else None)
            obj['metrics'][mid]['source_name']=(sr['source_name'] if sr else None)
            obj['metrics'][mid]['source_tier']=(sr['tier'] if sr else None)
            obj['metrics'][mid]['provenance_type']='DIRECT_PUBLIC'
    t=latest_applicable_tariff(db)
    if t: obj['applicable_tariff_event']={k:t[k] for k in t.keys()}
    rows=db.conn.execute("SELECT o.period_start,o.period_end,o.value,COALESCE(o.raw_source_ref,s.url) source_url FROM metric_observation o LEFT JOIN source_registry s ON o.source_id=s.source_id WHERE o.metric_id='K07' ORDER BY o.period_start").fetchall()
    obj['ft_history']=[dict(r) for r in rows]
    rows=db.conn.execute("SELECT o.period_start,o.period_end,o.value,COALESCE(o.raw_source_ref,s.url) source_url FROM metric_observation o LEFT JOIN source_registry s ON o.source_id=s.source_id WHERE o.metric_id='K09' ORDER BY o.period_start").fetchall()
    obj['pool_gas_history']=[dict(r) for r in rows]
    obj['renewables']['capacity_snapshots']=[dict(r) for r in db.conn.execute("SELECT * FROM renewable_capacity_snapshot ORDER BY as_of_date,scope,status_stage,technology").fetchall()]
    funnel={}
    for mid,label in [('K04','cod'),('K10','ppa_not_cod'),('K11','accepted_pre_ppa'),('K12','self_use_direct')]:
        r=db.latest(mid)
        if r:
            sr=db.conn.execute("SELECT url FROM source_registry WHERE source_id=?",(r['source_id'],)).fetchone() if r['source_id'] else None
            funnel[label]={'value':r['value'],'unit':r['unit'],'period_end':r['period_end'],'details':db.details(r['observation_id']),'scope_version':r['scope_version'],'source_url':r['raw_source_ref'] or (sr['url'] if sr else None),'provenance_type':'DIRECT_PUBLIC'}
    obj['renewables']['funnel']=funnel
    obj['renewables']['cohorts']=[dict(r) for r in db.conn.execute("SELECT * FROM procurement_cohort ORDER BY cohort_id").fetchall()]
    obj['renewables']['stages']=[dict(r) for r in db.conn.execute("SELECT * FROM procurement_stage_snapshot ORDER BY cohort_id,technology,CASE stage WHEN 'selected' THEN 1 WHEN 'ppa' THEN 2 WHEN 'cop' THEN 3 WHEN 'licensed' THEN 4 WHEN 'cod' THEN 5 ELSE 9 END").fetchall()]
    obj['renewables']['scod_schedule']=[dict(r) for r in db.conn.execute("SELECT * FROM procurement_scod_schedule ORDER BY cohort_id,scod_year,technology").fetchall()]
    obj['renewables']['projects']=[dict(r) for r in db.conn.execute("SELECT * FROM project_registry ORDER BY CASE status WHEN 'COD' THEN 0 WHEN 'Scheduled COD' THEN 1 WHEN 'Procurement open' THEN 2 ELSE 3 END,COALESCE(status_date,expected_cod_start) DESC,company").fetchall()]
    latest_digest_date=db.conn.execute("SELECT MAX(refresh_date) d FROM weekly_digest").fetchone()['d']
    if latest_digest_date:
        obj['weekly_digest']=[dict(r) for r in db.conn.execute("SELECT * FROM weekly_digest WHERE refresh_date=? ORDER BY rank",(latest_digest_date,)).fetchall()]
    obj['market_events']=[dict(r) for r in db.conn.execute("SELECT event_date,category,topic,company,title,capacity_mw,status_after,materiality_score,confidence,source_name,source_url FROM market_event WHERE is_relevant=1 ORDER BY event_date DESC,materiality_score DESC LIMIT 50").fetchall()]

    obj['renewables']['aggregation_rules']=[
      'Do not sum contracted-sale MW and installed MW.',
      'Utility funnel stages COD, signed PPA not COD, and accepted pre-PPA are mutually exclusive.',
      'Procurement stages are nested/progressive and are not additive.',
      'Project/company rows with an overlap_group are milestones/context and are excluded from national market sums.'
    ]
    obj['renewables']['csv_exports']=export_renewable_csvs(db)
    out_path.parent.mkdir(parents=True,exist_ok=True); out_path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)); return obj

def qa(db: DB):
    issues=[]
    for mid in ['K01','K02','K03','K04','K05','K06','K07','K10','K11','K12']:
        if not db.latest(mid): issues.append(('BLOCK',mid,'missing current/seed observation'))
    k4=db.latest('K04')
    if k4 and (k4['period_end'] or '')<'2025-06-30': issues.append(('WARN','K04','grid-connected RE COD snapshot is older than Jun-2025'))
    if k4 and k4['scope_version']!='v2.0': issues.append(('BLOCK','K04','headline renewable capacity is not on governed v2.0 scope'))
    tariff=latest_applicable_tariff(db,'2026-09-08')
    if tariff and abs(tariff['value']-3.86)>1e-9: issues.append(('BLOCK','K06','latest applicable tariff is not 3.86 for Sep-26 seed date'))
    ft=db.conn.execute("SELECT COUNT(*) n FROM metric_observation WHERE metric_id='K07'").fetchone()['n']
    if ft<3: issues.append(('WARN','K07','Ft history too short'))
    pg=db.conn.execute("SELECT COUNT(*) n FROM metric_observation WHERE metric_id='K09'").fetchone()['n']
    if pg<4: issues.append(('WARN','K09','Pool Gas history too short for period comparison'))

    vals={mid:(db.latest(mid)['value'] if db.latest(mid) else None) for mid in ['K04','K10','K11']}
    if all(v is not None for v in vals.values()):
        total=sum(vals.values())
        if abs(total-17375.38)>0.02: issues.append(('BLOCK','RE_FUNNEL',f'COD + PPA-not-COD + accepted = {total:.2f}, expected 17,375.38 MW'))
    tech=db.conn.execute("""SELECT SUM(capacity_mw) s FROM renewable_capacity_snapshot
      WHERE scope='utility_sold' AND status_stage='ALL_STATUSES' AND technology<>'Total' AND capacity_basis='contracted_sale_mw'""").fetchone()['s']
    if tech is not None and abs(tech-17375.38)>0.02: issues.append(('BLOCK','RE_TECH',f'utility technology total {tech:.2f} != 17,375.38 MW'))
    selftech=db.conn.execute("""SELECT SUM(capacity_mw) s FROM renewable_capacity_snapshot
      WHERE scope='self_use_direct' AND technology<>'Total' AND capacity_basis='installed_mw'""").fetchone()['s']
    if selftech is not None and abs(selftech-5978.90)>0.02: issues.append(('BLOCK','RE_SELFUSE',f'self-use technology total {selftech:.2f} != 5,978.90 MW'))

    rows=db.conn.execute("SELECT stage,project_count,capacity_mw FROM procurement_stage_snapshot WHERE cohort_id='FIT2022_NO_FUEL' AND technology='Total'").fetchall()
    st={r['stage']:r for r in rows}; order=['selected','ppa','cop','licensed','cod']
    for a,b in zip(order,order[1:]):
        if a in st and b in st:
            if st[b]['capacity_mw']-st[a]['capacity_mw']>0.02: issues.append(('BLOCK','FIT2022',f'{b} MW exceeds {a} MW'))
            if st[b]['project_count'] is not None and st[a]['project_count'] is not None and st[b]['project_count']>st[a]['project_count']: issues.append(('BLOCK','FIT2022',f'{b} project count exceeds {a}'))

    total_sched=db.conn.execute("SELECT SUM(capacity_mw) s FROM procurement_scod_schedule WHERE cohort_id='FIT2024_ADDITIONAL' AND technology='Total'").fetchone()['s']
    if total_sched is not None:
        sched_diff=abs(total_sched-2148.70)
        if abs(sched_diff-1.0)<0.02: issues.append(('WARN','FIT2024_SCOD',f'official source table has a 1.00 MW inconsistency: 2027 total 155.40 vs technology rows 154.40, so annual rows sum to {total_sched:.2f} MW vs stated 2,148.70 MW; source values retained'))
        elif sched_diff>0.02: issues.append(('BLOCK','FIT2024_SCOD',f'total SCOD schedule {total_sched:.2f} != 2,148.70 MW'))
    years=db.conn.execute("SELECT DISTINCT scod_year FROM procurement_scod_schedule WHERE cohort_id='FIT2024_ADDITIONAL'").fetchall()
    for yr in [r['scod_year'] for r in years]:
        total=db.conn.execute("SELECT capacity_mw FROM procurement_scod_schedule WHERE cohort_id='FIT2024_ADDITIONAL' AND technology='Total' AND scod_year=?",(yr,)).fetchone()
        parts=db.conn.execute("SELECT SUM(capacity_mw) s FROM procurement_scod_schedule WHERE cohort_id='FIT2024_ADDITIONAL' AND technology<>'Total' AND scod_year=?",(yr,)).fetchone()['s'] or 0
        if total:
            diff=abs(total['capacity_mw']-parts)
            if yr==2027 and abs(diff-1.0)<0.02: pass  # already surfaced in the single source-table warning above
            elif diff>0.02: issues.append(('BLOCK','FIT2024_SCOD',f'{yr} total-vs-tech mismatch {diff:.2f} MW'))
    mixed=db.conn.execute("SELECT COUNT(*) n FROM renewable_capacity_snapshot WHERE capacity_basis NOT IN ('contracted_sale_mw','installed_mw','target_mw')").fetchone()['n']
    if mixed: issues.append(('BLOCK','CAPACITY_BASIS','unrecognized capacity basis'))
    return issues

def main():
    ap=argparse.ArgumentParser(description='Thailand Power & Renewables Pulse data pipeline')
    ap.add_argument('command',choices=['init','seed','refresh','export','qa','all'])
    ap.add_argument('--db',default=str(DEFAULT_DB)); ap.add_argument('--offline',action='store_true',help='Skip live HTTP refresh')
    args=ap.parse_args(); db=DB(Path(args.db)); db.init(); config=json.loads((ROOT/'config.json').read_text())
    run_id=str(uuid.uuid4()); start=utcnow(); result={}
    try:
        if args.command in {'seed','all'}: result['seeded']=seed(db)
        if args.command=='init': result['initialized']=True
        if args.command in {'refresh','all'} and not args.offline: result['refresh']=refresh_live(db,config)
        if args.command in {'export','all'}: export_dashboard(db); result['export']=str(DEFAULT_EXPORT)
        if args.command in {'qa','all'}: result['qa']=qa(db)
        warns=len([i for i in result.get('qa',[]) if i[0]=='WARN'])+len(result.get('refresh',{}).get('warnings',[])); blocks=len([i for i in result.get('qa',[]) if i[0]=='BLOCK'])+len(result.get('refresh',{}).get('blocks',[]))
        db.conn.execute("INSERT OR REPLACE INTO refresh_log(run_id,started_at,completed_at,mode,sources_checked,new_observations,new_events,qa_warnings,qa_blocks,published,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id,start,utcnow(),'offline' if args.offline else 'live',result.get('refresh',{}).get('sources_checked',0),result.get('refresh',{}).get('new_observations',0),result.get('refresh',{}).get('new_events',0),warns,blocks,1 if blocks==0 else 0,json.dumps(result,ensure_ascii=False)))
        db.conn.commit(); print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        return 1 if blocks else 0
    finally: db.close()
if __name__=='__main__': sys.exit(main())
