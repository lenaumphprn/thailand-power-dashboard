#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, subprocess, sys, uuid
from datetime import date
from pathlib import Path
from pipeline import DB, ROOT, seed, refresh_live, export_dashboard, qa, utcnow
from intelligence import seed_intelligence, refresh_watchers, generate_digest, export_events, WATCH_CONFIG


def run_build():
    subprocess.run([sys.executable,str(ROOT/'build_dashboard.py')],check=True,cwd=ROOT)

def run_consistency_audit():
    p=subprocess.run([sys.executable,str(ROOT/'consistency_audit.py')],cwd=ROOT,capture_output=True,text=True)
    return p.returncode,p.stdout.strip(),p.stderr.strip()

def run_provenance_audit():
    p=subprocess.run([sys.executable,str(ROOT/'provenance_audit.py')],cwd=ROOT,capture_output=True,text=True)
    return p.returncode,p.stdout.strip(),p.stderr.strip()


def intelligence_qa(db: DB):
    issues=[]
    d=db.conn.execute("SELECT MAX(refresh_date) d FROM weekly_digest").fetchone()['d']
    if not d: issues.append(('BLOCK','DIGEST','no weekly digest generated'))
    elif db.conn.execute("SELECT COUNT(*) n FROM weekly_digest WHERE refresh_date=?",(d,)).fetchone()['n']<3:
        issues.append(('WARN','DIGEST','fewer than 3 material developments available'))
    fail=db.conn.execute("SELECT COUNT(*) n FROM source_watch_state WHERE consecutive_failures>=3").fetchone()['n']
    if fail: issues.append(('WARN','WATCHERS',f'{fail} source watcher(s) have failed 3+ consecutive runs'))
    return issues


def main():
    ap=argparse.ArgumentParser(description='Run the full Thailand Power Pulse weekly refresh')
    ap.add_argument('--offline',action='store_true',help='Use verified seeds/fixtures; skip outbound HTTP')
    ap.add_argument('--asof',default=None,help='YYYY-MM-DD, defaults to today')
    args=ap.parse_args(); asof=args.asof or date.today().isoformat(); db=DB(ROOT/'data'/'thailand_power.db'); db.init()
    run_id=str(uuid.uuid4()); start=utcnow(); result={'asof':asof,'mode':'offline' if args.offline else 'live'}
    try:
        result['seed']=seed(db); result['intelligence_seed']=seed_intelligence(db)
        config=json.loads((ROOT/'config.json').read_text()); watch=json.loads(WATCH_CONFIG.read_text())
        if not args.offline:
            result['data_refresh']=refresh_live(db,config)
            result['intelligence_refresh']=refresh_watchers(db,watch,asof=date.fromisoformat(asof))
        result['digest']=generate_digest(db,asof)
        result['qa']=qa(db)+intelligence_qa(db)
        result['dashboard_export']=str(ROOT/'data'/'dashboard_data.json'); export_dashboard(db)
        result['event_exports']=export_events(db)
        blocks=[x for x in result['qa'] if x[0]=='BLOCK']; warns=[x for x in result['qa'] if x[0]=='WARN']
        # Source fetch failures are warnings, not blocks: keep last verified data.
        if not blocks:
            run_build(); result['dashboard']=str(ROOT/'data'/'thailand_power_renewables_pulse_live.html')
            crc,cout,cerr=run_consistency_audit(); result['consistency_audit']={'returncode':crc,'stdout':cout,'stderr':cerr}
            if crc!=0:
                blocks.append(('BLOCK','CONSISTENCY','dashboard single-source-of-truth consistency audit failed')); result['qa']=result['qa']+[blocks[-1]]
            prc,pout,perr=run_provenance_audit(); result['provenance_audit']={'returncode':prc,'stdout':pout,'stderr':perr}
            if prc!=0:
                blocks.append(('BLOCK','PROVENANCE','public-source provenance audit failed')); result['qa']=result['qa']+[blocks[-1]]
        db.conn.execute("INSERT OR REPLACE INTO refresh_log(run_id,started_at,completed_at,mode,sources_checked,new_observations,new_events,qa_warnings,qa_blocks,published,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
          (run_id,start,utcnow(),result['mode'],result.get('data_refresh',{}).get('sources_checked',0)+result.get('intelligence_refresh',{}).get('sources_checked',0),result.get('data_refresh',{}).get('new_observations',0),result.get('intelligence_refresh',{}).get('events_added',0),len(warns)+len(result.get('intelligence_refresh',{}).get('warnings',[])),len(blocks),0 if blocks else 1,json.dumps(result,ensure_ascii=False,default=str)))
        db.conn.commit(); print(json.dumps(result,ensure_ascii=False,indent=2,default=str)); return 1 if blocks else 0
    finally: db.close()

if __name__=='__main__': sys.exit(main())
