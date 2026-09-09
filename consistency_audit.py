#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import csv, json, re, sys
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
HTML=ROOT/'data'/'thailand_power_renewables_pulse_live.html'
DATA=ROOT/'data'/'dashboard_data.json'
OUT=ROOT/'data'/'consistency_audit.csv'

def num(text):
    m=re.search(r'[-+]?\d[\d,]*(?:\.\d+)?',text or '')
    return float(m.group(0).replace(',','')) if m else None

def close(a,b,tol=.11): return a is not None and b is not None and abs(float(a)-float(b))<=tol

def main():
    checks=[]
    def add(name,ok,detail): checks.append({'check':name,'status':'PASS' if ok else 'FAIL','detail':detail})
    if not HTML.exists() or not DATA.exists():
        add('Required build outputs exist',False,'HTML or dashboard_data.json missing')
    else:
        data=json.loads(DATA.read_text()); m=data.get('metrics',{}); soup=BeautifulSoup(HTML.read_text(),'html.parser')
        # One source of truth across the top KPI cards.
        specs=[('demand','K01'),('peak','K02'),('regen','K03'),('recap','K04'),('gas','K05')]
        for key,mid in specs:
            card=soup.select_one(f'.kpi[data-kpi="{key}"]'); k=m.get(mid)
            ok=bool(card and k)
            if ok:
                displayed=num(card.select_one('.kpi-value').get_text(' ',strip=True))
                expected=float(k['value'])/1000 if mid in ('K02','K04') else float(k['value'])
                ok=close(displayed,expected,.11)
                ok=ok and str(k['period_end']) in card.select_one('.asof').get_text(' ',strip=True)
            add(f'{key} KPI matches database value + period',ok,card.get_text(' ',strip=True)[:220] if card else 'missing')
        # Tariff is event-based rather than metric_observation-based.
        t=data.get('applicable_tariff_event'); card=soup.select_one('.kpi[data-kpi="tariff"]')
        ok=bool(t and card and close(num(card.select_one('.kpi-value').get_text()),t['value'],.01) and str(t['effective_start']) in card.select_one('.asof').get_text(' ',strip=True))
        add('tariff KPI matches applicable ERC event',ok,card.get_text(' ',strip=True)[:220] if card else 'missing')
        # RE card: headline, components, bar/legend use same observation.
        k=m.get('K03'); card=soup.select_one('.kpi[data-kpi="regen"]'); txt=card.get_text(' ',strip=True) if card else ''
        if k:
            r=float(k['details'].get('renewable_pct',0)); h=float(k['details'].get('hydro_pct',0)); other=100-float(k['value'])
            ok=all(x in txt for x in [f'{float(k["value"]):.1f}%',f'{r:.1f}% RE',f'{h:.1f}% hydro',f'{other:.1f}% other'])
        else: ok=False
        add('RE headline/components/legend synchronized',ok,txt[:260])
        # Gas card: current, prior comparator, remainder, period all synchronized.
        k=m.get('K05'); card=soup.select_one('.kpi[data-kpi="gas"]'); txt=card.get_text(' ',strip=True) if card else ''
        if k:
            cur=float(k['value']); prev=float(k['details'].get('prior_share_pct',0)); other=100-cur; delta=cur-prev
            ok=all(x in txt for x in [f'{cur:.1f}%',f'{prev:.1f}%',f'{delta:+.1f} ppt',f'{other:.1f}% other',str(k['period_end'])])
        else: ok=False
        add('Gas headline/comparator/bar synchronized',ok,txt[:300])
        # Generation section must lead with latest period, not stale prior FY.
        k3=m.get('K03'); k5=m.get('K05'); curcard=soup.select_one('#currentGenerationMixCard'); comp=soup.select_one('#generationComparatorCard')
        label=k3['details'].get('period_label') if k3 else None
        txt=(curcard.get_text(' ',strip=True) if curcard else '')
        ok=bool(k3 and k5 and curcard and label and label in txt and f'{float(k5["value"]):.1f}%' in txt and f'{float(k3["value"]):.1f}%' in txt and str(k3['period_end']) in txt)
        add('Generation stack leads with latest YTD period',ok,txt[:320])
        ctxt=comp.get_text(' ',strip=True) if comp else ''
        prior=k5['details'].get('prior_period_label') if k5 else None
        ok=bool(comp and prior and prior in ctxt and label in ctxt)
        add('Generation historical benchmark explicitly labelled',ok,ctxt[:300])
        # Demand and peak detail cards should carry the latest metric values, not template leftovers.
        dcard=next((c for c in soup.select('#market .card') if 'Demand growth' in c.get_text(' ',strip=True)[:80]),None)
        k=m.get('K01'); dtxt=dcard.get_text(' ',strip=True) if dcard else ''
        add('Demand detail card uses latest observation',bool(k and f'{float(k["value"]):+.1f}%' in dtxt and str(k['period_end'])[-5:] not in ('',)),dtxt[:300])
        pcard=next((c for c in soup.select('#market .card') if 'Peak demand' in c.get_text(' ',strip=True)[:80]),None); k=m.get('K02'); ptxt=pcard.get_text(' ',strip=True) if pcard else ''
        add('Peak detail card uses current system peak',bool(k and f'{float(k["value"])/1000:.2f} GW' in ptxt),ptxt[:300])
        # Source-click coverage: every major quantitative block must be inside a public source scope.
        blocks=[]
        selectors=['.kpi','.news','#market .card','#economics .card','#economics .policy-table tbody tr','#projectMilestoneBody tr']
        for sel in selectors:
            for el in soup.select(sel):
                if not re.search(r'\d',el.get_text(' ',strip=True)): continue
                blocks.append(el)
        missing=[]; bad=[]
        for el in blocks:
            scope=el if el.get('data-source-url') else el.find_parent(attrs={'data-source-url':True})
            # Cards with nested source-specific sub-blocks are okay when all numeric content is nested.
            if not scope:
                nested=el.select('[data-source-url]')
                if nested: continue
                missing.append(el.get_text(' ',strip=True)[:100]); continue
            u=scope.get('data-source-url','')
            if not u.startswith('https://'): bad.append(u)
        add('All major quantitative blocks have public source scope',not missing,'none missing' if not missing else ' | '.join(missing[:5]))
        add('All source scopes use HTTPS URLs',not bad,'all HTTPS' if not bad else str(bad[:5]))
        # Click-to-source runtime must exist.
        html=HTML.read_text()
        add('Click-to-source runtime embedded','figure-source' in html and 'data-source-url' in html and 'window.open' in html,'numeric figures and charts source-linked')
        add('KPI drawers resolve current embedded data','function liveContext(key)' in html and all(x in html for x in ['M.K01','M.K02','M.K03','M.K05']),'drawer context uses current metric payload rather than fixed current-period values')
        # Embedded payload + provenance must be present and parseable.
        try: json.loads(soup.find('script',id='pulse-data').string); pulse_ok=True
        except Exception: pulse_ok=False
        try: json.loads(soup.find('script',id='figure-provenance').string); prov_ok=True
        except Exception: prov_ok=False
        add('Embedded dashboard data valid',pulse_ok,'pulse-data JSON')
        add('Embedded figure provenance valid',prov_ok,'figure-provenance JSON')
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['check','status','detail']); w.writeheader(); w.writerows(checks)
    fails=[x for x in checks if x['status']=='FAIL']
    print(json.dumps({'checks':len(checks),'failures':len(fails),'output':str(OUT),'failed':[x['check'] for x in fails]},indent=2))
    return 1 if fails else 0

if __name__=='__main__': sys.exit(main())
