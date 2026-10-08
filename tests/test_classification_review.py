"""Reproduce the independent reviewer findings at reader boundaries."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from src import constructor,validator
from src.constructor import construct_match_v3
from consolidate import scan_and_consolidate
from scripts.build_lake_catalog import generate_catalog
from helpers import details,ball,player,legacy_match
from test_hundred_rules import hundred,sequence




def build(raw=None,balls=None):
    raw=raw or details();m,error=construct_match_v3('100',raw,{1:balls or [ball()]},{},{},{})
    if error:raise AssertionError(error)
    return m


class ClassificationReviewTests(unittest.TestCase):
    def test_hundred_competition_cannot_promote_other_or_international_classes(self):
        for general,international,fmt in [(4,None,'TEST'),(5,None,'ODI'),(6,3,'T20')]:
            with self.subTest(source=(general,international,fmt)):
                raw=hundred();raw['match'].update(generalClassId=general,internationalClassId=international,format=fmt)
                raw['content']['innings'][0].update(runs=0,wickets=0,balls=40,penalties=0)
                m=build(raw,sequence(40));meta=m['meta']
                self.assertEqual(meta['playing_format'],'hundred_ball')
                self.assertEqual(meta['format_bucket'],'Unknown')
                self.assertFalse(meta['record_stats_eligible'])
                self.assertTrue(meta['classification_issues'])
                self.assertEqual(validator.validate_match_data(m,raw['content']['innings']),[])

    def test_removing_two_sentinels_does_not_allow_prior_version_bypass(self):
        raw=details();raw['content']['innings'][0].update(runs=1,balls=2,wickets=0)
        m=build(raw);m['meta']['schema_version']='3.1.0'
        m['meta'].pop('source_classification_presence');m['innings'][0].pop('innings_penalty_runs')
        self.assertTrue(validator.validate_match_data(m))

    def test_external_source_evidence_cannot_be_neutralized_by_null_snapshot(self):
        raw=details();raw['content']['innings'][0].update(runs=1,balls=2,wickets=0)
        m=build(raw);inn=m['innings'][0]
        inn['source_scorecard']['runs']=None;inn['source_scorecard']['balls']=None;inn['source_legal_balls']=None
        self.assertTrue(validator.validate_match_data(m,raw['content']['innings']))

    def test_modern_shaped_legacy_version_cannot_bypass_inventory_and_catalog(self):
        m=build();m['meta']['schema_version']='3.0.0'
        m['innings'][0]['overs'][0]['deliveries'][0]['bowler_id']='espn_999999'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['total_matches'],0)
            generate_catalog(source,root/'catalog')
            self.assertEqual(list(csv.DictReader((root/'catalog/matches_index.csv').open())),[])

    def test_genuine_legacy_stays_readable_but_explicitly_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(legacy_match()))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['total_matches'],1)
            self.assertEqual(manifest['coverage'].get('legacy_unverified_matches'),1)

    def test_unknown_contract_version_is_not_treated_as_legacy(self):
        m=legacy_match();m['meta']['schema_version']='9.0.0'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['total_matches'],0)

    def test_source_only_timed_out_wicket_is_in_team_totals_without_a_ball_event(self):
        raw=details();inn=raw['content']['innings'][0]
        inn.update(runs=0,wickets=1,balls=1,penalties=0)
        inn['inningBatsmen'][0].update(dismissalText={'short':'timed out'},balls=0)
        inn['inningBatsmen'].append({'player':player(2),'runs':0,'balls':1,'dismissalText':{'short':'not out'}})
        m=build(raw,[ball(batter=2,non_striker=3)])
        self.assertEqual(validator.validate_match_data(m,raw['content']['innings']),[])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['totals']['wickets'],1)
            self.assertEqual(manifest['totals'].get('source_only_timed_out_wickets'),1)
            self.assertEqual(manifest['totals']['wicket_events'],0)
            self.assertEqual(manifest['totals']['deliveries'],1)
            generate_catalog(source,root/'catalog')
            row=next(csv.DictReader((root/'catalog/matches_index.csv').open()))
            self.assertEqual(row['total_wickets'],'1')

    def test_timed_out_already_recorded_in_events_is_not_double_counted(self):
        raw=details();inn=raw['content']['innings'][0]
        inn.update(runs=0,wickets=1,balls=1,penalties=0)
        inn['inningBatsmen'][0]['dismissalText']={'short':'timed out'}
        m=build(raw);m['innings'][0]['overs'][0]['deliveries'][0]['wickets']=[{'kind':'timed out','player_out_id':'espn_1001'}]
        self.assertEqual(validator.validate_match_data(m,raw['content']['innings']),[])

    def test_reader_gate_rejects_non_object_metadata_without_throwing(self):
        validate=getattr(validator,'validate_inventory_candidate')
        self.assertTrue(validate({'meta':['bad metadata']}))
        self.assertTrue(validate({'meta':{'schema_version':[]}}))


if __name__=='__main__':unittest.main()
