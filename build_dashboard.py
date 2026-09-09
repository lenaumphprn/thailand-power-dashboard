#!/usr/bin/env python3
from __future__ import annotations
import json, re
from datetime import date, datetime
from pathlib import Path
from bs4 import BeautifulSoup
from pipeline import DB, export_dashboard, ROOT

DB_PATH=ROOT/'data'/'thailand_power.db'
TEMPLATE=ROOT/'dashboard_template.html'
OUT=ROOT/'data'/'thailand_power_renewables_pulse_live.html'

COLORS={
 'gas':'#4d6f8e','coal_lignite':'#7e8791','renewable':'#5aa982','hydro':'#7bb7d7',
 'oil':'#c7a979','imports':'#b7c2cd','other':'#d6dce2','residual':'#c4ccd4'
}
LABELS={'gas':'Gas','coal_lignite':'Coal / lignite','renewable':'Other RE','hydro':'Hydro','oil':'Oil','imports':'Imports','other':'Other','residual':'Other fuels / imports'}

def fmt_pct(x): return f'{float(x):.1f}%'
def set_source(el,url):
    if not el or not url: return
    cls=list(el.get('class',[]))
    if 'source-scope' not in cls: cls.append('source-scope')
    el['class']=cls; el['data-source-url']=url; el['data-source-title']='Open public source'

def set_html(el,html):
    if not el: return
    frag=BeautifulSoup(html,'html.parser'); el.clear()
    for ch in list(frag.contents): el.append(ch)

def set_asof(card,text):
    el=card.select_one('.asof') if card else None
    if not el: return
    el.clear(); el.append(text)
    hint=card.new_tag('span',attrs={'class':'source-hint'}) if hasattr(card,'new_tag') else None
    # BeautifulSoup tags don't expose new_tag reliably; use fragment instead.
    frag=BeautifulSoup('<span class="source-hint">↗ click figures for source</span>','html.parser')
    el.append(frag.span)

def set_card(soup,key,value_html=None,delta=None,asof=None,source_url=None):
    card=soup.select_one(f'[data-kpi="{key}"]')
    if not card: return
    if value_html is not None: set_html(card.select_one('.kpi-value'),value_html)
    if delta is not None:
        d=card.select_one('.kpi-delta')
        if d: d.string=delta
    if asof is not None:
        el=card.select_one('.asof'); el.clear(); el.append(asof)
        hint=soup.new_tag('span',attrs={'class':'source-hint'}); hint.string='↗ click figures for source'; el.append(hint)
    if source_url: set_source(card,source_url)

def text_id(soup,eid,text):
    x=soup.select_one(f'#{eid}')
    if x: x.string=str(text)

def style_width(soup,eid,pct):
    x=soup.select_one(f'#{eid}')
    if not x: return
    rest=';'.join(v for v in (x.get('style') or '').split(';') if v.strip() and not v.strip().startswith('width:'))
    x['style']=f'width:{max(0,min(100,float(pct))):.1f}%;'+rest

def period_label(period_end):
    d=date.fromisoformat(period_end); y=d.year; m=d.month
    if m==12: return f'FY{y}'
    if m==6: return f'H1 {y}'
    return f"Jan–{d.strftime('%b')} {y}"

def render_developments(soup,db):
    d=db.conn.execute('SELECT MAX(refresh_date) d FROM weekly_digest').fetchone()['d']
    if not d: return
    items=db.conn.execute('SELECT * FROM weekly_digest WHERE refresh_date=? ORDER BY rank',(d,)).fetchall()
    grid=soup.select_one('#pulse .news-grid')
    if not grid or not items: return
    grid.clear()
    for item in items:
        art=soup.new_tag('article',attrs={'class':'news source-scope','data-source-url':item['source_url'],'data-source-title':'Open public source'})
        num=soup.new_tag('div',attrs={'class':'news-num'}); num.string=str(item['rank']); art.append(num)
        tag=soup.new_tag('div',attrs={'class':'tagline'})
        impact=soup.new_tag('span',attrs={'class':'pill high' if item['score']>=9 else 'pill med'}); impact.string='High impact' if item['score']>=9 else 'Market signal'; tag.append(impact)
        cat=soup.new_tag('span',attrs={'class':'pill policy'}); cat.string=item['category']; tag.append(cat); art.append(tag)
        try: _d=date.fromisoformat(item['event_date']); _label=f"{_d.day} {_d.strftime('%b %Y')}"
        except Exception: _label=item['event_date']
        dt=soup.new_tag('div',attrs={'class':'smallnote'}); dt.string=_label; art.append(dt)
        h=soup.new_tag('h3'); h.string=item['headline']; art.append(h)
        p=soup.new_tag('p'); p.string=item['one_fact']; art.append(p)
        sw=soup.new_tag('p',attrs={'class':'so-what'}); b=soup.new_tag('b'); b.string='Why it matters: '; sw.append(b); sw.append(item['why_it_matters']); art.append(sw)
        grid.append(art)

def render_demand_history(soup,db,latest):
    card=next((c for c in soup.select('#market .card') if 'Demand' in c.get_text(' ',strip=True)[:80]),None)
    if not card or not latest: return
    set_source(card,latest.get('source_url'))
    rows=db.conn.execute("SELECT * FROM metric_observation WHERE metric_id='K01' ORDER BY COALESCE(period_end,period_start), publication_date, observation_id").fetchall()
    unique={}
    for r in rows: unique[r['period_end'] or r['period_start']]=r
    cur=[r for k,r in sorted(unique.items()) if (r['period_end'] or '').startswith(str(date.fromisoformat(latest['period_end']).year))]
    cur=cur[-6:]
    card.clear()
    title=soup.new_tag('div',attrs={'class':'card-title'}); left=soup.new_tag('div'); h=soup.new_tag('h3'); h.string='Demand growth'; left.append(h); sn=soup.new_tag('div',attrs={'class':'smallnote'}); sn.string='YTD electricity consumption growth, YoY'; left.append(sn); title.append(left); meta=soup.new_tag('div',attrs={'class':'meta'}); meta.string='EPPO • latest available period first'; title.append(meta); card.append(title)
    # Prior FY comparator from latest details.
    prior=latest['details'].get('fy2025_growth_pct')
    if prior is not None:
        row=soup.new_tag('div',attrs={'class':'bar-row'}); row.append(BeautifulSoup(f'<span>FY2025</span><div class="bar-track"><div class="bar-fill" style="width:{min(100,abs(float(prior))*8):.1f}%;background:#a8b8c8"></div></div><span class="bar-num">{float(prior):+.1f}%</span>','html.parser')); card.append(row)
    for r in cur:
        det=db.details(r['observation_id']); lab=period_label(r['period_end']); val=float(r['value'])
        row=BeautifulSoup(f'<div class="bar-row"><span>{lab}</span><div class="bar-track"><div class="bar-fill" style="width:{min(100,abs(val)*8):.1f}%;"></div></div><span class="bar-num">{val:+.1f}%</span></div>','html.parser').div; card.append(row)
    call=soup.new_tag('div',attrs={'class':'callout'}); call.append(BeautifulSoup(f'<b>Latest:</b> {period_label(latest["period_end"])} demand is {float(latest["value"]):+.1f}% YoY at {float(latest["details"].get("ytd_gwh",0)):,.0f} GWh.','html.parser')); card.append(call)

def render_peak_card(soup,k):
    if not k: return
    card=next((c for c in soup.select('#market .card') if 'Peak demand' in c.get_text(' ',strip=True)[:100]),None)
    if not card: return
    set_source(card,k.get('source_url'))
    det=k['details']; prior=float(det.get('prior_year_peak_mw',0)); cur=float(k['value']); growth=float(det.get('growth_pct',(cur/prior-1)*100 if prior else 0))
    card.clear(); title=soup.new_tag('div',attrs={'class':'card-title'}); left=soup.new_tag('div'); h=soup.new_tag('h3'); h.string='Peak demand'; left.append(h); sn=soup.new_tag('div',attrs={'class':'smallnote'}); sn.string='EGAT system peak'; left.append(sn); title.append(left); meta=soup.new_tag('div',attrs={'class':'meta'}); meta.string=f"Current-year peak: {k['period_end']}"; title.append(meta); card.append(title)
    card.append(BeautifulSoup(f'<div class="compare-band"><div class="compare-cell"><span>{int(date.fromisoformat(k["period_end"]).year)-1} peak</span><b>{prior/1000:.2f} GW</b></div><div class="compare-arrow">→</div><div class="compare-cell current"><span>{date.fromisoformat(k["period_end"]).year} YTD peak</span><b>{cur/1000:.2f} GW</b></div></div>','html.parser').div)
    stats=soup.new_tag('div',attrs={'class':'stat-row'}); stats.append(BeautifulSoup(f'<div class="stat-chip"><b>{growth:+.1f}%</b><br/>vs prior-year peak</div>','html.parser').div)
    if det.get('latest_monthly_peak_mw') is not None:
        md=date.fromisoformat(det['latest_monthly_peak_date']); stats.append(BeautifulSoup(f'<div class="stat-chip"><b>{float(det["latest_monthly_peak_mw"])/1000:.2f} GW</b><br/>{md.strftime("%b")} monthly peak • {md.day} {md.strftime("%b")}</div>','html.parser').div)
        if det.get('latest_monthly_change_pct') is not None: stats.append(BeautifulSoup(f'<div class="stat-chip"><b>{float(det["latest_monthly_change_pct"]):+.2f}%</b><br/>latest monthly peak vs prior month</div>','html.parser').div)
    card.append(stats)
    note=soup.new_tag('div',attrs={'class':'footer-note'}); note.string='Do not mix EGAT-system peak with EPPO’s “3 utility” system peak without labeling the definition.'; card.append(note)

def render_generation(soup,k03,k05):
    if not k03 or not k05: return
    url=k03.get('source_url') or k05.get('source_url'); label=k03['details'].get('period_label') or period_label(k03['period_end'])
    card=soup.select_one('#currentGenerationMixCard'); set_source(card,url)
    text_id(soup,'currentGenerationMixTitle',f'{label} generation mix'); text_id(soup,'currentGenerationMixPeriod',f'Latest available EPPO YTD mix • as of {k03["period_end"]}')
    d=k03['details']; gas=float(k05['value']); re=float(d.get('renewable_pct',0)); hydro=float(d.get('hydro_pct',0)); coal=float(d.get('coal_lignite_pct',0)); oil=float(d.get('oil_pct',0)); imports=float(d.get('imports_pct',0)); other=float(d.get('other_fuels_pct',0))
    known=gas+re+hydro+coal+oil+imports+other; residual=max(0,100-known)
    vals=[('gas',gas),('coal_lignite',coal),('renewable',re),('hydro',hydro),('oil',oil),('imports',imports),('other',other)]
    if residual>0.05: vals.append(('residual',residual))
    vals=[x for x in vals if x[1]>=0.05]
    bar=soup.select_one('#currentGenerationMixBar'); bar.clear(); leg=soup.select_one('#currentGenerationMixLegend'); leg.clear()
    for key,val in vals:
        sp=soup.new_tag('span'); sp['style']=f'width:{val:.2f}%;background:{COLORS[key]}'; bar.append(sp)
        item=soup.new_tag('span'); i=soup.new_tag('i'); i['style']=f'background:{COLORS[key]}'; item.append(i); item.append(f'{LABELS[key]} '); b=soup.new_tag('b'); b.string=f'{val:.1f}%'; item.append(b); leg.append(item)
    text_id(soup,'currentGasShare',fmt_pct(gas)); text_id(soup,'currentReHydroShare',fmt_pct(float(k03['value'])))
    other_share=100-gas-float(k03['value']); text_id(soup,'currentOtherShare',fmt_pct(other_share))
    stats=soup.select_one('#currentGenerationStats')
    if stats and float(d.get('ytd_generation_gwh',0) or 0)>0:
        extra=soup.new_tag('div',attrs={'class':'stat-chip'}); b=soup.new_tag('b'); b.string=f"{float(d['ytd_generation_gwh']):,.0f} GWh"; extra.append(b); extra.append(soup.new_tag('br')); extra.append('YTD generation'); stats.append(extra)
    # Comparator
    prior_gas=float(k05['details'].get('prior_share_pct',d.get('prior_fy_gas_pct',0))); prior_re=float(d.get('prior_fy_re_hydro_pct',13.8)); gd=gas-prior_gas; rd=float(k03['value'])-prior_re; prior_label=k05['details'].get('prior_period_label',f'FY{date.fromisoformat(k03["period_end"]).year-1}')
    text_id(soup,'generationComparatorTitle',f'Change vs {prior_label}')
    for eid,val in [('compareGasCurrent',fmt_pct(gas)),('compareGasDelta',f'{gd:+.1f} ppt'),('compareGasPrior',fmt_pct(prior_gas)),('compareReCurrent',fmt_pct(k03['value'])),('compareReDelta',f'{rd:+.1f} ppt'),('compareRePrior',fmt_pct(prior_re)),('compareGasPriorBarNum',fmt_pct(prior_gas)),('compareGasCurrentBarNum',fmt_pct(gas)),('compareRePriorBarNum',fmt_pct(prior_re)),('compareReCurrentBarNum',fmt_pct(k03['value']))]: text_id(soup,eid,val)
    text_id(soup,'compareGasCurrentLabel',f'gas • {label}'); text_id(soup,'compareReCurrentLabel',f'RE + hydro • {label}'); text_id(soup,'compareGasCurrentBarLabel',f'Gas {label}'); text_id(soup,'compareReCurrentBarLabel',f'RE + hydro {label}')
    style_width(soup,'compareGasPriorBar',prior_gas); style_width(soup,'compareGasCurrentBar',gas); style_width(soup,'compareRePriorBar',prior_re); style_width(soup,'compareReCurrentBar',k03['value'])
    sig=soup.select_one('#generationSignal'); sig.clear(); b=soup.new_tag('b'); b.string='Signal:'; sig.append(b); sig.append(f' {label} gas share is {gd:+.1f} ppt vs {prior_label}, while RE + hydro is {rd:+.1f} ppt.')
    set_source(soup.select_one('#generationComparatorCard'),url)

def render_projects(soup,data):
    byid={x['project_id']:x for x in data.get('renewables',{}).get('projects',[])}; body=soup.select_one('#projectMilestoneBody')
    if not body: return
    body.clear(); ids=['GULF-LNE-2026','GULF-YE2026-SOLAR-TRANCHE','GULF-WIND-286','GUNKUL-SOLAR-4608','GUNKUL-WIND-180']
    # LNE + SSE combined row.
    lne=byid.get('GULF-LNE-2026'); sse=byid.get('GULF-SSE-2026')
    rows=[]
    if lne and sse:
        status='COD '+(lne.get('status_date') or sse.get('status_date') or '') if (lne.get('status')=='COD' or sse.get('status')=='COD') else (lne.get('status') or 'Current status')
        rows.append(('GULF • LNE + SSE, Suphan Buri',f"{float(lne.get('contracted_mw') or 0)+float(sse.get('contracted_mw') or 0):.0f} MW contracted",status,f"Two FiT projects; installed vs contracted MW are kept as separate capacity bases.",lne.get('source_url') or sse.get('source_url')))
    for pid in ids[1:]:
        p=byid.get(pid)
        if not p: continue
        timing=p.get('status') or 'Current status'
        if p.get('status_date'): timing += f" • {p['status_date']}"
        elif p.get('expected_cod_start'):
            timing += f" • expected {p['expected_cod_start']}" + (f" to {p['expected_cod_end']}" if p.get('expected_cod_end') and p.get('expected_cod_end')!=p.get('expected_cod_start') else '')
        cap=p.get('contracted_mw'); capstr=f'{float(cap):.1f} MW contracted' if cap is not None else 'See source'
        rows.append((f"{p.get('company','')} • {p.get('project_name','')}",capstr,timing,'Developer-level milestone; overlaps with national procurement totals and is not added again.',p.get('source_url')))
    for name,cap,status,signal,url in rows:
        tr=soup.new_tag('tr'); set_source(tr,url)
        for i,v in enumerate([name,cap,status,signal]):
            td=soup.new_tag('td');
            if i==0: b=soup.new_tag('b'); b.string=v; td.append(b)
            else: td.string=v
            tr.append(td)
        body.append(tr)

def render_tariff_card(soup,db,t):
    if not t: return
    card=next((c for c in soup.select('#economics .card') if 'Average billed tariff' in c.get_text(' ',strip=True)[:120]),None)
    if not card: return
    set_source(card,t['source_url'])
    big=card.find('div',style=lambda s:s and 'font-size:48px' in s)
    if big: big.string=f"{float(t['value']):.2f}"
    prev=db.conn.execute("SELECT * FROM tariff_event WHERE event_id<>? AND publication_date<=? ORDER BY publication_date DESC LIMIT 1",(t['event_id'],t['publication_date'])).fetchone()
    band=card.select_one('.compare-band')
    if band and prev:
        cells=band.select('.compare-cell'); cells[0].select_one('span').string='Prior decision'; cells[0].select_one('b').string=f"{float(prev['value']):.2f}"; cells[1].select_one('span').string=f"From {date.fromisoformat(t['effective_start']).strftime('%b-%y')}"; cells[1].select_one('b').string=f"{float(t['value']):.2f}"
        delta=(float(t['value'])/float(prev['value'])-1)*100 if prev['value'] else 0
        note=card.select_one('.smallnote[style*="margin-top:8px"]')
        if note: note.string=f"{delta:+.1f}% vs the prior headline decision."

def main():
    db=DB(DB_PATH); db.init(); data=export_dashboard(db)
    soup=BeautifulSoup(TEMPLATE.read_text(),'html.parser')
    render_developments(soup,db); m=data['metrics']
    # Dynamic refresh timestamp.
    ref=soup.select_one('.refresh b'); ref.string=f"Refreshed {date.today().strftime('%-d %b %Y')}" if ref else None
    # KPI cards.
    k=m.get('K01')
    if k:
        set_card(soup,'demand',f"{float(k['value']):+.1f}%",f"{period_label(k['period_end'])} vs prior year",f"As of {k['period_end']} - EPPO - {float(k['details'].get('ytd_gwh',0)):,.0f} GWh",k.get('source_url'))
        # comparator labels
        card=soup.select_one('[data-kpi="demand"]'); vals=card.select('.compare-cell b'); labs=card.select('.compare-cell span')
        if vals and k['details'].get('fy2025_growth_pct') is not None: vals[0].string=f"{float(k['details']['fy2025_growth_pct']):+.1f}%"; vals[1].string=f"{float(k['value']):+.1f}%"
        if len(labs)>1: labs[0].string='FY2025'; labs[1].string=period_label(k['period_end'])
    k=m.get('K02')
    if k:
        set_card(soup,'peak',f"{float(k['value'])/1000:.1f} <span class='kpi-unit'>GW</span>",f"{float(k['details'].get('growth_pct',0)):+.1f}% vs prior-year peak",f"Peak {k['period_end']} - EGAT - {float(k['value']):,.1f} MW",k.get('source_url'))
    k03=m.get('K03')
    if k03:
        set_card(soup,'regen',f"{float(k03['value']):.1f}%",f"{float(k03['details'].get('renewable_pct',0)):.1f}% RE + {float(k03['details'].get('hydro_pct',0)):.1f}% hydro",f"As of {k03['period_end']} - EPPO - incl. hydro",k03.get('source_url'))
        card=soup.select_one('[data-kpi="regen"]'); bars=card.select('.segment-track span'); vals=[float(k03['details'].get('renewable_pct',0)),float(k03['details'].get('hydro_pct',0)),100-float(k03['value'])]
        for sp,v in zip(bars,vals):
            st=sp.get('style',''); sp['style']=re.sub(r'width:[^;]+',f'width:{v:.1f}%',st)
        legs=card.select('.segment-legend span'); set_html(legs[0],f'<b>{float(k03["value"]):.1f}%</b> RE + hydro'); legs[1].string=f'{100-float(k03["value"]):.1f}% other'
    k=m.get('K04')
    if k: set_card(soup,'recap',f"{float(k['value'])/1000:.1f} <span class='kpi-unit'>GW</span>",f"{float(k['value']):,.0f} MW contracted-sale capacity",f"As of {k['period_end']} - ERC - COD, contracted-sale MW",k.get('source_url'))
    k05=m.get('K05')
    if k05:
        prior=float(k05['details'].get('prior_share_pct',0)); delta=float(k05['value'])-prior
        set_card(soup,'gas',f"{float(k05['value']):.1f}%",f"{delta:+.1f} ppt vs {k05['details'].get('prior_period_label','prior period')}",f"As of {k05['period_end']} - EPPO",k05.get('source_url'))
        card=soup.select_one('[data-kpi="gas"]'); vals=card.select('.compare-cell b'); labs=card.select('.compare-cell span'); vals[0].string=fmt_pct(prior); vals[1].string=fmt_pct(k05['value']); labs[0].string=k05['details'].get('prior_period_label','Prior'); labs[1].string=k05['details'].get('period_label',period_label(k05['period_end']))
        bars=card.select('.segment-track span');
        if len(bars)>=2:
            for sp,v in zip(bars,[float(k05['value']),100-float(k05['value'])]): sp['style']=re.sub(r'width:[^;]+',f'width:{v:.1f}%',sp.get('style',''))
        legs=card.select('.segment-legend span'); set_html(legs[0],f'<b>{float(k05["value"]):.1f}%</b> gas'); legs[1].string=f'{100-float(k05["value"]):.1f}% other'
    t=data.get('applicable_tariff_event')
    if t: set_card(soup,'tariff',f"{float(t['value']):.2f} <span class='kpi-unit'>THB/kWh</span>",'Latest applicable ERC-stated average',f"Effective {t['effective_start']} - ERC - {t['customer_scope']}",t['source_url'])
    # Hero demand wording.
    if m.get('K01'):
        hp=soup.select_one('.hero-main p'); hp.clear(); hp.append(f"Electricity consumption was up "); a=soup.new_tag('a',href=m['K01'].get('source_url'),target='_blank',rel='noopener',attrs={'class':'figure-source','title':'Open public source'}); a.string=f"{float(m['K01']['value']):.1f}% YoY in {period_label(m['K01']['period_end'])}"; hp.append(a); hp.append(' after a '); a=soup.new_tag('a',href=m['K01'].get('source_url'),target='_blank',rel='noopener',attrs={'class':'figure-source','title':'Open public source'}); a.string=f"{abs(float(m['K01']['details'].get('fy2025_growth_pct',0))):.1f}% decline in 2025"; hp.append(a); hp.append(', while the EGAT-system peak remains a key capacity signal. Policy direction is simultaneously shifting through PDP2026, Direct PPA, UGT and grid-flexibility measures.')
    # Main sections.
    render_demand_history(soup,db,m.get('K01')); render_peak_card(soup,m.get('K02')); render_generation(soup,k03,k05)
    # Price/cost context.
    pgh=data.get('pool_gas_history',[])
    if pgh:
        cur=pgh[-1]; prev=pgh[-2] if len(pgh)>1 else None; jan=next((x for x in pgh if x['period_start'].startswith(str(date.fromisoformat(cur['period_start']).year)+'-01')),None)
        text_id(soup,'poolGasValue',f"{float(cur['value']):.2f}"); text_id(soup,'poolGasPeriod',f"THB/MMBtu • {date.fromisoformat(cur['period_start']).strftime('%b-%y')}")
        if prev and prev['value']: text_id(soup,'poolGasMom',f"{('▲' if cur['value']>=prev['value'] else '▼')} {abs((cur['value']/prev['value']-1)*100):.1f}% vs prior month")
        if jan and jan['value']: text_id(soup,'poolGasVsJan',f"{(cur['value']/jan['value']-1)*100:+.1f}% vs Jan-{str(date.fromisoformat(cur['period_start']).year)[2:]}")
        set_source(soup.select_one('#poolGasValue').parent,cur.get('source_url'))
    fth=data.get('ft_history',[])
    if fth:
        cur=fth[-1]; text_id(soup,'ftCurrent',f"{float(cur['value']):.2f}"); set_source(soup.select_one('#ftCurrent').parent,cur.get('source_url'))
        early=next((x for x in fth if x['period_start'][:4]==cur['period_start'][:4] and x['period_start'][5:7]=='01'),None); prev=fth[-2] if len(fth)>1 else None
        if early and early['value']: text_id(soup,'ftVsEarly',f"{(cur['value']/early['value']-1)*100:+.1f}% vs first period")
        if prev: text_id(soup,'ftVsPrev','flat vs prior period' if abs(cur['value']-prev['value'])<1e-9 else f"{(cur['value']/prev['value']-1)*100:+.1f}% vs prior period")
    # Renewable pipeline.
    f=data.get('renewables',{}).get('funnel',{}); cod=f.get('cod',{}).get('value',0); ppa=f.get('ppa_not_cod',{}).get('value',0); acc=f.get('accepted_pre_ppa',{}).get('value',0)
    all_status=db.conn.execute("SELECT capacity_mw FROM renewable_capacity_snapshot WHERE snapshot_id='ERC-2025-06-30-UTILITY-ALL_STATUSES'").fetchone(); total=float(all_status['capacity_mw']) if all_status else (cod+ppa+acc)
    if total:
        text_id(soup,'utilityCommittedTotal',f'{total/1000:.2f}'); text_id(soup,'funnelCod',f'{cod/1000:.2f} GW'); text_id(soup,'funnelPpa',f'{ppa/1000:.2f} GW'); text_id(soup,'funnelAccepted',f'{acc/1000:.2f} GW'); style_width(soup,'funnelCodBar',cod/total*100); style_width(soup,'funnelPpaBar',ppa/total*100); style_width(soup,'funnelAcceptedBar',acc/total*100)
    su=f.get('self_use_direct',{})
    if su:
        suv=su.get('value',0); det=su.get('details',{}); solar=float(det.get('solar_mw',0)); biomass=float(det.get('biomass_mw',0)); text_id(soup,'selfUseTotal',f'{suv/1000:.2f}'); text_id(soup,'selfUseSolar',f'{solar/1000:.2f} GW'); text_id(soup,'selfUseBiomass',f'{biomass/1000:.2f} GW');
        if suv: style_width(soup,'selfUseSolarBar',solar/suv*100); style_width(soup,'selfUseBiomassBar',biomass/suv*100)
    stages=[x for x in data.get('renewables',{}).get('stages',[]) if x['cohort_id']=='FIT2022_NO_FUEL' and x['technology']=='Total']; st={x['stage']:x['capacity_mw'] for x in stages}; sel=st.get('selected',0)
    for key,eid in {'selected':'fitSelected','ppa':'fitPpa','cop':'fitCop','licensed':'fitLicensed','cod':'fitCod'}.items():
        v=st.get(key)
        if v is not None: text_id(soup,eid,f'{v/1000:.2f}'); style_width(soup,eid+'Bar',v/sel*100 if sel else 0)
    for x in [x for x in data.get('renewables',{}).get('scod_schedule',[]) if x['cohort_id']=='FIT2024_ADDITIONAL' and x['technology']=='Total']: text_id(soup,f"scod{x['scod_year']}",f"{x['capacity_mw']:.0f}")
    render_projects(soup,data); render_tariff_card(soup,db,t)
    # Embed fresh data/provenance in existing placeholder locations so runtime source-link/sync scripts execute after data exists.
    tag=soup.find('script',id='pulse-data')
    if not tag:
        tag=soup.new_tag('script',type='application/json',id='pulse-data'); soup.body.append(tag)
    tag.string=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    manifest_path=ROOT/'public_figure_manifest.json'; ptag=soup.find('script',id='figure-provenance')
    if not ptag:
        ptag=soup.new_tag('script',type='application/json',id='figure-provenance'); soup.body.append(ptag)
    if manifest_path.exists(): ptag.string=manifest_path.read_text()
    OUT.write_text(str(soup)); print(OUT); db.close()

if __name__=='__main__': main()
