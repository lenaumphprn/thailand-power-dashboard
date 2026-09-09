#!/usr/bin/env python3
import json, re
from pathlib import Path
from bs4 import BeautifulSoup
from pipeline import DB, export_dashboard, ROOT

DB_PATH=ROOT/'data'/'thailand_power.db'
TEMPLATE=ROOT/'dashboard_template.html'
OUT=ROOT/'data'/'thailand_power_renewables_pulse_live.html'

def set_card(soup,key,value_html=None,delta=None,asof=None):
    card=soup.select_one(f'[data-kpi="{key}"]')
    if not card: return
    if value_html is not None:
        card.select_one('.kpi-value').clear(); card.select_one('.kpi-value').append(BeautifulSoup(value_html,'html.parser'))
    if delta is not None: card.select_one('.kpi-delta').string=delta
    if asof is not None: card.select_one('.asof').string=asof

def text_id(soup,element_id,text):
    x=soup.select_one(f'#{element_id}')
    if x: x.string=str(text)

def style_width(soup,element_id,pct):
    x=soup.select_one(f'#{element_id}')
    if x:
        rest=';'.join(v for v in (x.get('style') or '').split(';') if v.strip() and not v.strip().startswith('width:'))
        x['style']=f'width:{max(0,min(100,pct)):.1f}%;'+rest

def render_developments(soup, db):
    d=db.conn.execute("SELECT MAX(refresh_date) d FROM weekly_digest").fetchone()['d']
    if not d: return
    items=db.conn.execute("SELECT * FROM weekly_digest WHERE refresh_date=? ORDER BY rank",(d,)).fetchall()
    grid=soup.select_one('#pulse .news-grid')
    if not grid or not items: return
    grid.clear()
    for item in items:
        art=soup.new_tag('article',attrs={'class':'news'})
        num=soup.new_tag('div',attrs={'class':'news-num'}); num.string=str(item['rank']); art.append(num)
        tag=soup.new_tag('div',attrs={'class':'tagline'})
        impact=soup.new_tag('span',attrs={'class':'pill high' if item['score']>=9 else 'pill med'}); impact.string='High impact' if item['score']>=9 else 'Market signal'; tag.append(impact)
        cat=soup.new_tag('span',attrs={'class':'pill policy'}); cat.string=item['category']; tag.append(cat); art.append(tag)
        try:
            from datetime import date as _date
            _d=_date.fromisoformat(item['event_date']); _label=f"{_d.day} {_d.strftime('%b %Y')}"
        except Exception:
            _label=item['event_date']
        dt=soup.new_tag('div',attrs={'class':'smallnote'}); dt.string=_label; art.append(dt)
        h=soup.new_tag('h3'); h.string=item['headline']; art.append(h)
        p=soup.new_tag('p'); p.string=item['one_fact']; art.append(p)
        sw=soup.new_tag('p',attrs={'class':'so-what'}); b=soup.new_tag('b'); b.string='Why it matters: '; sw.append(b); sw.append(item['why_it_matters']); art.append(sw)
        grid.append(art)

def main():
    db=DB(DB_PATH); db.init(); data=export_dashboard(db)
    soup=BeautifulSoup(TEMPLATE.read_text(),'html.parser')
    render_developments(soup,db)
    m=data['metrics']
    k=m.get('K01')
    if k: set_card(soup,'demand',f"{k['value']:+.1f}%",f"{k['period_start'][:4]} YTD through {k['period_end'][:7]}",f"As of {k['period_end']} - EPPO - {k['details'].get('ytd_gwh',0):,.0f} GWh")
    k=m.get('K02')
    if k: set_card(soup,'peak',f"{k['value']/1000:.1f} <span class='kpi-unit'>GW</span>",f"+{k['details'].get('growth_pct',0):.1f}% vs 2025 peak",f"Peak {k['period_end']} - EGAT - {k['value']:,.1f} MW")
    k=m.get('K03')
    if k: set_card(soup,'regen',f"{k['value']:.1f}%",f"{k['details'].get('renewable_pct',0):.1f}% RE + {k['details'].get('hydro_pct',0):.1f}% hydro",f"As of {k['period_end']} - EPPO - incl. hydro")
    k=m.get('K04')
    if k: set_card(soup,'recap',f"{k['value']/1000:.1f} <span class='kpi-unit'>GW</span>",f"{k['value']:,.0f} MW contracted-sale capacity",f"As of {k['period_end']} - ERC - COD, contracted-sale MW")
    k=m.get('K05')
    if k: set_card(soup,'gas',f"{k['value']:.1f}%",f"Gas generation {k['details'].get('gas_generation_yoy_pct',0):+.1f}% YoY",f"As of {k['period_end']} - EPPO")
    t=data.get('applicable_tariff_event')
    if t: set_card(soup,'tariff',f"{t['value']:.2f} <span class='kpi-unit'>THB/kWh</span>","Latest applicable ERC-stated average",f"Effective {t['effective_start']} - ERC - {t['customer_scope']}")

    # Price / cost metrics should carry context, not standalone levels.
    pgh=data.get('pool_gas_history',[])
    if pgh:
        cur=pgh[-1]; prev=pgh[-2] if len(pgh)>1 else None; jan=next((x for x in pgh if x['period_start'].startswith('2026-01')),None)
        text_id(soup,'poolGasValue',f"{cur['value']:.2f}")
        from datetime import date as _date
        _pd=_date.fromisoformat(cur['period_start'])
        text_id(soup,'poolGasPeriod',f"THB/MMBtu • {_pd.strftime('%b-%y')}")
        if prev and prev['value']:
            pct=(cur['value']/prev['value']-1)*100
            text_id(soup,'poolGasMom',f"{'▲' if pct>=0 else '▼'} {abs(pct):.1f}% vs prior month")
        if jan and jan['value']:
            pct=(cur['value']/jan['value']-1)*100
            text_id(soup,'poolGasVsJan',f"{pct:+.1f}% vs Jan-26")
        d=m.get('K09',{}).get('details',{})
        if d.get('lng_mom_pct_change') is not None:
            text_id(soup,'poolGasDriver',f"Main driver: imported LNG {float(d['lng_mom_pct_change']):+.1f}% MoM.")
    fth=data.get('ft_history',[])
    if fth:
        cur=fth[-1]; text_id(soup,'ftCurrent',f"{cur['value']:.2f}")
        early=next((x for x in fth if x['period_start']=='2026-01-01'),None)
        prev=fth[-2] if len(fth)>1 else None
        if early and early['value']:
            text_id(soup,'ftVsEarly',f"{(cur['value']/early['value']-1)*100:+.1f}% vs Jan–Apr")
        if prev:
            diff=cur['value']-prev['value']
            text_id(soup,'ftVsPrev','flat vs May–Aug' if abs(diff)<1e-9 else f"{(cur['value']/prev['value']-1)*100:+.1f}% vs prior period")

    # Renewable utility funnel and separate self-use basis.
    f=data.get('renewables',{}).get('funnel',{})
    cod=f.get('cod',{}).get('value',0); ppa=f.get('ppa_not_cod',{}).get('value',0); acc=f.get('accepted_pre_ppa',{}).get('value',0)
    all_status=db.conn.execute("SELECT capacity_mw FROM renewable_capacity_snapshot WHERE snapshot_id='ERC-2025-06-30-UTILITY-ALL_STATUSES'").fetchone()
    total=float(all_status['capacity_mw']) if all_status else (cod+ppa+acc)
    if total:
        text_id(soup,'utilityCommittedTotal',f'{total/1000:.2f}')
        text_id(soup,'funnelCod',f'{cod/1000:.2f} GW'); text_id(soup,'funnelPpa',f'{ppa/1000:.2f} GW'); text_id(soup,'funnelAccepted',f'{acc/1000:.2f} GW')
        style_width(soup,'funnelCodBar',cod/total*100); style_width(soup,'funnelPpaBar',ppa/total*100); style_width(soup,'funnelAcceptedBar',acc/total*100)
    su=f.get('self_use_direct',{})
    if su:
        suv=su.get('value',0); det=su.get('details',{}); solar=float(det.get('solar_mw',0)); biomass=float(det.get('biomass_mw',0))
        text_id(soup,'selfUseTotal',f'{suv/1000:.2f}'); text_id(soup,'selfUseSolar',f'{solar/1000:.2f} GW'); text_id(soup,'selfUseBiomass',f'{biomass/1000:.2f} GW')
        if suv: style_width(soup,'selfUseSolarBar',solar/suv*100); style_width(soup,'selfUseBiomassBar',biomass/suv*100)

    # First FiT cohort stage funnel.
    stages=[x for x in data.get('renewables',{}).get('stages',[]) if x['cohort_id']=='FIT2022_NO_FUEL' and x['technology']=='Total']
    st={x['stage']:x['capacity_mw'] for x in stages}; sel=st.get('selected',0)
    idmap={'selected':'fitSelected','ppa':'fitPpa','cop':'fitCop','licensed':'fitLicensed','cod':'fitCod'}
    for key,eid in idmap.items():
        v=st.get(key)
        if v is not None:
            text_id(soup,eid,f'{v/1000:.2f}')
            if sel: style_width(soup,eid+'Bar',v/sel*100)

    # Additional round SCOD totals.
    sched=[x for x in data.get('renewables',{}).get('scod_schedule',[]) if x['cohort_id']=='FIT2024_ADDITIONAL' and x['technology']=='Total']
    for x in sched: text_id(soup,f"scod{x['scod_year']}",f"{x['capacity_mw']:.0f}")

    # Project milestone table - curated from registry, with overlap guardrails.
    byid={x['project_id']:x for x in data.get('renewables',{}).get('projects',[])}
    body=soup.select_one('#projectMilestoneBody')
    if body:
        body.clear(); rows=[]
        lne=byid.get('GULF-LNE-2026'); sse=byid.get('GULF-SSE-2026')
        if lne and sse:
            rows.append(('GULF • LNE + SSE, Suphan Buri',f"{lne['contracted_mw']+sse['contracted_mw']:.0f} MW contracted",'COD 1 Sep 2026',f"Two FiT projects entered operation; {lne['installed_mw']+sse['installed_mw']:.1f} MW installed vs {lne['contracted_mw']+sse['contracted_mw']:.0f} MW contracted highlights the capacity-basis distinction."))
        p=byid.get('GULF-YE2026-SOLAR-TRANCHE')
        if p: rows.append(('GULF • 4 solar / solar+BESS projects',f"{p['contracted_mw']:.1f} MW contracted",'Scheduled Nov–Dec 2026','Near-term test of whether the signed pipeline continues converting to COD on schedule.'))
        p=byid.get('GULF-WIND-286')
        if p: rows.append(('GULF • 4 wind farms',f"{p['contracted_mw']:.0f} MW contracted",'Expected COD 2027','Material wind tranche with turbine-supply agreements secured.'))
        p=byid.get('GUNKUL-SOLAR-4608')
        if p: rows.append(('GUNKUL • 9 solar projects',f"{p['contracted_mw']:.1f} MW contracted",'SCOD 2026–2030','Developer-level view of capacity already under PPA within the wider national pipeline.'))
        p=byid.get('GUNKUL-WIND-180')
        if p: rows.append(('GUNKUL • 2 wind projects',f"{p['contracted_mw']:.0f} MW contracted",'SCOD 2029–2030','Longer-dated wind capacity already under PPA.'))
        for name,cap,status,signal in rows:
            tr=soup.new_tag('tr')
            vals=[name,cap,status,signal]
            for i,v in enumerate(vals):
                td=soup.new_tag('td')
                if i==0:
                    b=soup.new_tag('b'); b.string=v; td.append(b)
                else: td.string=v
                tr.append(td)
            body.append(tr)

    # Render current tariff on Economics card.
    eco=soup.find('h3',string=re.compile('Average billed tariff'))
    if eco and t:
        card=eco.find_parent('div',class_='card'); big=card.find('div',style=lambda s:s and 'font-size:48px' in s)
        if big: big.string=f"{t['value']:.2f}"

    tag=soup.new_tag('script',type='application/json',id='pulse-data'); tag.string=json.dumps(data,ensure_ascii=False)
    soup.body.append(tag)
    manifest_path=ROOT/'public_figure_manifest.json'
    if manifest_path.exists():
        ptag=soup.new_tag('script',type='application/json',id='figure-provenance'); ptag.string=manifest_path.read_text()
        soup.body.append(ptag)
    soup.append(soup.new_string('\n<!-- Generated from thailand_power.db; values are database-backed and capacity bases are governed separately. -->\n'))
    OUT.write_text(str(soup)); print(OUT)
    db.close()

if __name__=='__main__': main()
