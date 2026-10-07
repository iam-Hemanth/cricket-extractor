import copy
import json
from pathlib import Path
import unittest

from src import constructor, validator
from helpers import details,ball


class RunNormalizationTests(unittest.TestCase):
    def normalize(self, ball, year=2023, competition="The Hundred Men's Competition"):
        normalizer = getattr(constructor, 'normalize_delivery_runs', None)
        self.assertTrue(callable(normalizer), 'Run normalization is not implemented')
        return normalizer(ball, {'competition': competition, 'dates': [f'{year}-08-01']})

    def test_four_real_hundred_records_keep_two_total_runs(self):
        records = json.loads((Path(__file__).parent/'fixtures/repair/hundred_noballs.json').read_text())
        for record in records:
            with self.subTest(match=record['match_id']):
                ball = dict(record['source_fields'], id=record['source_ball_id'])
                original = copy.deepcopy(ball)
                year = 2021 if record['match_id']=='1252678' else 2023
                runs, extras, note = self.normalize(ball, year)
                self.assertEqual(runs, {'batter': 0, 'extras': 2, 'total': 2})
                self.assertEqual(extras, {'noballs': 2})
                self.assertEqual(note['source_value'], 1)
                self.assertEqual(note['normalized_value'], 2)
                self.assertEqual(note['source_ball_id'], record['source_ball_id'])
                self.assertEqual(ball, original)

    def test_correct_two_run_no_ball_is_not_doubled(self):
        runs, extras, note = self.normalize({'noballs':2,'totalRuns':2})
        self.assertEqual(runs['total'], 2)
        self.assertEqual(extras, {'noballs':2})
        self.assertIsNone(note)

    def test_ordinary_one_run_no_ball_is_preserved(self):
        runs, extras, note = self.normalize({'noballs':1,'totalRuns':1}, competition='Indian Premier League')
        self.assertEqual(runs, {'batter':0,'extras':1,'total':1})
        self.assertEqual(extras, {'noballs':1})
        self.assertIsNone(note)

    def test_no_ball_plus_batter_runs_preserves_attribution(self):
        runs, extras, _ = self.normalize({'noballs':1,'batsmanRuns':4,'totalRuns':6})
        self.assertEqual(runs, {'batter':4,'extras':2,'total':6})
        self.assertEqual(extras, {'noballs':2})

    def test_no_ball_plus_byes_preserves_attribution(self):
        runs, extras, _ = self.normalize({'noballs':1,'byes':3,'totalRuns':5})
        self.assertEqual(runs, {'batter':0,'extras':5,'total':5})
        self.assertEqual(extras, {'noballs':2,'byes':3})

    def test_unsupported_season_requires_review(self):
        with self.assertRaises(ValueError):
            self.normalize({'noballs':1,'totalRuns':2}, year=2026)

    def test_two_byes_remain_two_extras(self):
        runs, extras, note = self.normalize({'byes':2,'totalRuns':2})
        self.assertEqual(runs, {'batter':0,'extras':2,'total':2})
        self.assertEqual(extras, {'byes':2})
        self.assertIsNone(note)

    def test_invalid_components_fail_even_when_innings_total_agrees(self):
        delivery={'actual_delivery':'0.1','is_legal':False,'runs':{'batter':0,'extras':1,'total':2},'extras':{'noballs':1},'wickets':[]}
        valid, errors=validator.validate_innings({'innings_number':1,'overs':[{'over':0,'deliveries':[delivery]}]}, {'runs':2,'wickets':0})
        self.assertFalse(valid)
        self.assertTrue(any('component' in error.lower() for error in errors), errors)

    def test_extras_breakdown_is_validated_independently(self):
        delivery={'actual_delivery':'0.1','is_legal':False,'runs':{'batter':0,'extras':2,'total':2},'extras':{'noballs':1},'wickets':[]}
        valid,_=validator.validate_innings({'innings_number':1,'overs':[{'over':0,'deliveries':[delivery]}]})
        self.assertFalse(valid)

    def test_positive_scorecard_with_no_deliveries_is_incomplete(self):
        valid,_=validator.validate_innings({'overs':[]},{'runs':120,'wickets':4})
        self.assertFalse(valid)

    def test_two_run_no_ball_does_not_count_as_legal_delivery(self):
        delivery=ball();delivery.update(noballs=1,totalRuns=2,totalInningRuns=2)
        raw=details();raw['match']['series']['name']="The Hundred Men's Competition"
        match,error=constructor.construct_match_v3('100',raw,{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        self.assertFalse(match['innings'][0]['overs'][0]['deliveries'][0]['is_legal'])

    def test_helmet_penalty_is_added_once(self):
        first=ball();first.update(batsmanRuns=1,totalRuns=1,totalInningRuns=1)
        second=ball();second.update(id=101,oversActual=0.2,oversUnique=0.2,totalInningRuns=6)
        match,error=constructor.construct_match_v3('100',details(),{1:[first,second]},{},{},{})
        self.assertIsNone(error)
        result=match['innings'][0]['overs'][0]['deliveries'][1]
        self.assertEqual(result['runs'],{'batter':0,'extras':5,'total':5})
        self.assertEqual(result['extras'],{'penalty':5})

    def test_explicit_helmet_penalty_is_not_added_twice(self):
        first=ball();first.update(batsmanRuns=1,totalRuns=1,totalInningRuns=1)
        second=ball();second.update(id=101,oversActual=0.2,oversUnique=0.2,totalInningRuns=6,totalRuns=5,penalties=5)
        match,error=constructor.construct_match_v3('100',details(),{1:[first,second]},{},{},{})
        self.assertIsNone(error)
        self.assertEqual(match['innings'][0]['overs'][0]['deliveries'][1]['runs']['total'],5)


if __name__=='__main__':
    unittest.main()
