import csv, json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline import thai_year_to_ad, parse_erc_ft_html, parse_egat_peak_html, transform_demand, transform_generation, DB, seed, qa, export_dashboard, ROOT

class PipelineTests(unittest.TestCase):
    def test_thai_year(self):
        self.assertEqual(thai_year_to_ad(2569),2026)

    def test_ft_parser(self):
        rows=parse_erc_ft_html((ROOT/'fixtures'/'erc_ft_sample.html').read_text())
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['period_start'],'2026-09-01')
        self.assertEqual(rows[0]['value'],16.23)

    def test_demand_transform(self):
        with open(ROOT/'fixtures'/'eppo_demand_sample.csv',newline='') as f:
            rows=list(csv.DictReader(f))
        d=transform_demand(rows)
        self.assertAlmostEqual(d['growth_pct'],5.7894736842,places=5)
        self.assertEqual(d['month'],2)

    def test_generation_transform(self):
        cfg=json.loads((ROOT/'config.json').read_text())
        with open(ROOT/'fixtures'/'eppo_generation_sample.csv',newline='') as f:
            rows=list(csv.DictReader(f))
        g=transform_generation(rows,cfg)
        # Latest year is aggregated YTD through Feb, not Feb alone.
        self.assertEqual(g['month'],2)
        self.assertAlmostEqual(g['gas_share'],60.0)
        self.assertAlmostEqual(g['re_share'],13.0)
        self.assertAlmostEqual(g['prior_fy_gas_share'],50.0)
        self.assertEqual(g['qa'],'pass')

    def test_egat_peak_parser(self):
        out=parse_egat_peak_html((ROOT/'fixtures'/'egat_peak_sample.html').read_text())
        self.assertAlmostEqual(out['peak_mw'],35991.6)
        self.assertEqual(out['peak_date'],'2026-04-22')
        self.assertAlmostEqual(out['prior_year_peak_mw'],34568.3)
        self.assertAlmostEqual(out['latest_monthly_peak_mw'],32764.1)
        self.assertEqual(out['latest_monthly_peak_date'],'2026-07-20')
        self.assertAlmostEqual(out['latest_monthly_change_pct'],-6.25)

    def test_renewable_funnel_and_basis(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db)
            vals=[db.latest(k)['value'] for k in ['K04','K10','K11']]
            self.assertAlmostEqual(sum(vals),17375.38,places=2)
            self.assertEqual(db.latest('K04')['scope_version'],'v2.0')
            self.assertAlmostEqual(db.latest('K12')['value'],5978.90,places=2)
            rows=db.conn.execute("select distinct capacity_basis from renewable_capacity_snapshot order by capacity_basis").fetchall()
            self.assertEqual({r['capacity_basis'] for r in rows},{'contracted_sale_mw','installed_mw'})
            db.close()

    def test_procurement_nested_and_source_warning(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db)
            rows=db.conn.execute("select stage,capacity_mw from procurement_stage_snapshot where cohort_id='FIT2022_NO_FUEL' and technology='Total'").fetchall()
            s={r['stage']:r['capacity_mw'] for r in rows}
            self.assertGreaterEqual(s['selected'],s['ppa'])
            self.assertGreaterEqual(s['ppa'],s['cop'])
            self.assertGreaterEqual(s['cop'],s['licensed'])
            self.assertGreaterEqual(s['licensed'],s['cod'])
            issues=qa(db)
            self.assertFalse(any(x[0]=='BLOCK' for x in issues))
            self.assertTrue(any(x[1]=='FIT2024_SCOD' and x[0]=='WARN' for x in issues))
            db.close()

    def test_project_overlap_guardrails(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db)
            n=db.conn.execute("select count(*) n from project_registry where overlap_group is not null").fetchone()['n']
            self.assertGreaterEqual(n,7)
            gulf=db.conn.execute("select * from project_registry where project_id='GULF-LNE-2026'").fetchone()
            self.assertAlmostEqual(gulf['contracted_mw'],68.0)
            self.assertAlmostEqual(gulf['installed_mw'],90.6)
            db.close()

    def test_seed_idempotent_export_and_qa(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db)
            before=db.conn.execute("select count(*) n from metric_observation").fetchone()['n']
            seed(db)
            after=db.conn.execute("select count(*) n from metric_observation").fetchone()['n']
            self.assertEqual(before,after)
            n=db.conn.execute("select count(*) n from metric_definition").fetchone()['n']
            self.assertGreaterEqual(n,13)
            projects=db.conn.execute("select count(*) n from project_registry").fetchone()['n']
            self.assertEqual(projects,8)
            out=Path(td)/'dashboard.json'; obj=export_dashboard(db,out)
            self.assertIn('renewables',obj)
            self.assertEqual(len(obj['renewables']['projects']),8)
            self.assertFalse(any(x[0]=='BLOCK' for x in qa(db)))
            db.close()

if __name__=='__main__': unittest.main()
