import json, tempfile, unittest
from datetime import date
from pathlib import Path
from pipeline import DB, seed
from intelligence import (ROOT, extract_listing_items, classify_event, extract_capacities,
                          materiality, seed_intelligence, generate_digest, find_project_alias)

class IntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=json.loads((ROOT/'watch_sources.json').read_text())

    def test_listing_filter_and_dates(self):
        html=(ROOT/'fixtures/gulf_listing_sample.html').read_text()
        items=extract_listing_items(html,'https://investor.gulf.co.th/en/newsroom/set-announcements?page=1',self.cfg)
        titles=[x['title'] for x in items]
        self.assertEqual(len(items),2)
        self.assertTrue(any('commercial operation' in t for t in titles))
        self.assertTrue(any('project financing' in t for t in titles))
        cod=next(x for x in items if 'commercial operation' in x['title'])
        self.assertEqual(cod['event_date'],'2026-09-01')

    def test_classification_capacity_and_score(self):
        title='Notification of commercial operation of two solar projects'
        body='The projects have aggregate contracted capacity of 135 MW and began COD on 1 September 2026.'
        info=classify_event(title,body); caps=extract_capacities(body)
        self.assertEqual(info['category'],'Project & investment')
        self.assertEqual(info['status_after'],'COD')
        self.assertEqual(caps,[135.0])
        self.assertGreaterEqual(materiality(title,body,info,caps,'2026-09-01',date(2026,9,8)),8)

    def test_seed_digest_diversity(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db); seed_intelligence(db)
            dig=generate_digest(db,'2026-09-08')
            self.assertEqual(len(dig),3)
            self.assertEqual(dig[0]['category'],'Policy & regulation')
            self.assertTrue(any(x['category']=='Market design' for x in dig))
            self.assertTrue(any(x['category']=='Project & investment' for x in dig))
            self.assertTrue(any('GULF' in x['headline'] for x in dig))
            db.close()

    def test_project_alias_safe_match(self):
        with tempfile.TemporaryDirectory() as td:
            db=DB(Path(td)/'x.db'); db.init(); seed(db); seed_intelligence(db)
            self.assertEqual(find_project_alias(db,'GULF','LNE has reached commercial operation'),'GULF-LNE-2026')
            self.assertIsNone(find_project_alias(db,'GULF','LNE and SSE have reached commercial operation'))
            db.close()

if __name__=='__main__': unittest.main()
