"""Source scorecard adjustments do not create balls or weaken coverage gates."""
import json
import tempfile
from pathlib import Path
import unittest
from src import constructor,validator
from consolidate import scan_and_consolidate
from helpers import details,ball


def raw_and_balls(penalty=5):
    raw=details();inn=raw['content']['innings'][0]
    inn.update(runs=7,wickets=0,balls=1,penalties=penalty,wides=0,noballs=0,byes=0,legbyes=0)
    b=ball();b.update(batsmanRuns=2,totalRuns=2,totalInningRuns=2)
    return raw,[b]


def build(raw,bs):
    m,error=constructor.construct_match_v3('100',raw,{1:bs},{},{},{})
    if error:raise AssertionError(error)
    return m


class SourceCoverageTests(unittest.TestCase):
    def test_explicit_innings_penalty_is_separate_from_a_delivery(self):
        raw,bs=raw_and_balls();m=build(raw,bs);inn=m['innings'][0]
        self.assertEqual(inn.get('innings_penalty_runs'),5)
        self.assertEqual(len(inn['overs'][0]['deliveries']),1)
        self.assertEqual(inn['overs'][0]['deliveries'][0]['runs'],{'batter':2,'extras':0,'total':2})
        self.assertEqual(validator.validate_match_data(m,raw['content']['innings']),[])

    def test_penalty_already_in_delivery_extras_is_not_counted_twice(self):
        raw,bs=raw_and_balls();bs[0].update(penalties=5,totalRuns=7,totalInningRuns=7)
        m=build(raw,bs);self.assertEqual(m['innings'][0].get('innings_penalty_runs'),0)
        self.assertEqual(validator.validate_match_data(m,raw['content']['innings']),[])

    def test_missing_delivery_does_not_get_disguised_as_a_penalty(self):
        raw,bs=raw_and_balls();raw['content']['innings'][0]['balls']=2
        self.assertTrue(validator.validate_match_data(build(raw,bs),raw['content']['innings']))

    def test_unexplained_run_gap_cannot_be_inferred_as_five_penalty_runs(self):
        raw,bs=raw_and_balls(penalty=0)
        self.assertTrue(validator.validate_match_data(build(raw,bs),raw['content']['innings']))

    def test_contradictory_penalty_and_final_runs_requires_review(self):
        raw,bs=raw_and_balls();raw['content']['innings'][0]['runs']=8
        self.assertTrue(validator.validate_match_data(build(raw,bs),raw['content']['innings']))

    def test_consolidation_retains_source_ball_coverage_gate(self):
        raw,bs=raw_and_balls();raw['content']['innings'][0]['balls']=2
        m=build(raw,bs)
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['total_matches'],0)

    def test_penalty_changes_team_manifest_runs_without_extra_balls(self):
        raw,bs=raw_and_balls();m=build(raw,bs)
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['totals']['runs'],7)
            self.assertEqual(manifest['totals']['deliveries'],1)

    def test_penalty_amount_cannot_be_forged_in_candidate(self):
        raw,bs=raw_and_balls();m=build(raw,bs);m['innings'][0]['innings_penalty_runs']=10
        self.assertTrue(validator.validate_match_data(m))

    def test_integer_first_ball_marker_requires_independent_corroboration(self):
        raw=details();b=ball();b.update(overNumber=7,ballNumber=1,oversActual=6,oversUnique=6.01)
        m=build(raw,[b]);d=m['innings'][0]['overs'][0]['deliveries'][0]
        self.assertEqual(d['actual_delivery'],'6.1')
        self.assertEqual(d.get('source_actual_delivery'),'6')
        self.assertTrue(m['meta']['normalization_notes'])

    def test_ambiguous_integer_marker_is_not_normalized(self):
        raw=details();b=ball();b.update(overNumber=7,ballNumber=2,oversActual=6,oversUnique=6.01)
        m=build(raw,[b]);d=m['innings'][0]['overs'][0]['deliveries'][0]
        self.assertEqual(d['actual_delivery'],'6')
        self.assertTrue(validator.validate_match_data(m))

    def test_explicit_retired_not_out_overrides_numeric_retired_out(self):
        raw=details();raw['content']['innings'][0]['inningBatsmen'][0]['dismissalText']={'short':'retired not out'}
        b=ball();b.update(isWicket=True,dismissalType=11,outPlayerId=1)
        m=build(raw,[b]);d=m['innings'][0]['overs'][0]['deliveries'][0]
        self.assertEqual(d['wickets'][0]['kind'],'retired not out')
        self.assertEqual(validator.count_team_wickets([d]),0)

    def test_plain_not_out_does_not_invent_a_retirement_reason(self):
        raw=details();raw['content']['innings'][0]['inningBatsmen'][0]['dismissalText']={'short':'not out'}
        b=ball();b.update(isWicket=True,dismissalType=12,outPlayerId=1)
        m=build(raw,[b]);self.assertTrue(validator.validate_match_data(m))

    def test_downgrade_cannot_bypass_new_classification_and_adjustment_gates(self):
        raw,bs=raw_and_balls();m=build(raw,bs);m['meta']['schema_version']='3.1.0'
        self.assertTrue(validator.validate_match_data(m))

    def test_correct_run_total_does_not_hide_a_missing_legal_ball(self):
        raw,bs=raw_and_balls(penalty=0);raw['content']['innings'][0].update(runs=2,balls=2)
        self.assertTrue(validator.validate_match_data(build(raw,bs)))

    def test_retired_not_out_batter_can_return_and_be_dismissed(self):
        raw=details();raw['content']['innings'][0]['inningBatsmen'][0]['dismissalText']={'short':'retired not out'}
        first=ball();first.update(isWicket=True,dismissalType=11,outPlayerId=1)
        second=ball();second.update(id=101,oversActual=0.2,oversUnique=0.2,isWicket=True,dismissalType=1,outPlayerId=1)
        m=build(raw,[first,second]);events=[w['kind'] for d in m['innings'][0]['overs'][0]['deliveries'] for w in d['wickets']]
        self.assertEqual(events,['retired not out','caught'])

    def test_one_run_source_discrepancy_is_reviewed_in_new_contract(self):
        raw,bs=raw_and_balls(penalty=0);raw['content']['innings'][0]['runs']=3
        self.assertTrue(validator.validate_match_data(build(raw,bs)))

    def test_float_integer_first_ball_marker_is_corroborated(self):
        raw=details();b=ball();b.update(overNumber=7,ballNumber=1,oversActual=6.0,oversUnique=6.01)
        d=build(raw,[b])['innings'][0]['overs'][0]['deliveries'][0]
        self.assertEqual(d['actual_delivery'],'6.1')
        self.assertEqual(d['source_actual_delivery'],'6.0')

    def test_canonical_marker_cannot_be_changed_without_source_fields(self):
        raw=details();b=ball();b.update(overNumber=7,ballNumber=2,oversActual=6,oversUnique=6.01)
        m=build(raw,[b]);m['innings'][0]['overs'][0]['deliveries'][0]['actual_delivery']='6.1'
        self.assertTrue(validator.validate_match_data(m))

    def test_corroborated_marker_note_passes_full_schema(self):
        raw=details();b=ball();b.update(overNumber=7,ballNumber=1,oversActual=6,oversUnique=6.01)
        self.assertEqual(validator.validate_match_data(build(raw,[b])),[])

    def test_metadata_only_response_is_not_a_complete_match_candidate(self):
        raw=details();raw.pop('content')
        m,error=constructor.construct_match_v3('100',raw,{},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('scorecard',error or '')

    def test_source_pre_match_state_is_not_accepted_as_completed_data(self):
        raw=details();raw['match']['state']='PRE'
        m,error=constructor.construct_match_v3('100',raw,{1:[ball()]},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('uncompleted',error or '')

    def test_retirement_normalization_keeps_source_pointer_and_numeric_code(self):
        raw=details();raw['content']['innings'][0]['inningBatsmen'][0]['dismissalText']={'short':'retired not out'}
        b=ball();b.update(isWicket=True,dismissalType=11,outPlayerId=1)
        m=build(raw,[b]);notes=m['meta']['normalization_notes']
        self.assertTrue(any(n.get('source_value')==11 and n.get('normalized_value')=='retired not out'
                            and 'dismissalText' in n.get('source_ref','') for n in notes))

    def test_numeric_string_actor_id_does_not_lose_explicit_retirement_fact(self):
        raw=details();p=raw['content']['innings'][0]['inningBatsmen'][0]
        p['player']['id']='1';p['dismissalText']={'short':'retired not out'}
        b=ball();b.update(isWicket=True,dismissalType=11,outPlayerId=1)
        m=build(raw,[b]);self.assertEqual(m['innings'][0]['overs'][0]['deliveries'][0]['wickets'][0]['kind'],'retired not out')

    def test_running_counter_corruption_is_not_silently_ignored(self):
        raw,bs=raw_and_balls(penalty=0);raw['content']['innings'][0]['runs']=2
        bs[0]['totalInningRuns']=100
        self.assertTrue(validator.validate_match_data(build(raw,bs)))


if __name__=='__main__':unittest.main()
