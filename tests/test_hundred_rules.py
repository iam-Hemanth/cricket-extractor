"""Catch six-ball assumptions, retrospective phases and silent contract bypasses."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from src import constructor, validator
from consolidate import scan_and_consolidate
from helpers import details, ball


def hundred(quota=100, season='2023', special=False):
    raw=details()
    raw['match'].update(format='HUNDRED_BALL',ballsPerOver=5,season=season,
                        startDate=f'{season}-08-01',generalClassId=6,internationalClassId=None)
    raw['match']['series']['name']="The Hundred Men's Competition"
    raw['content']['innings'][0].update(totalBalls=quota,isSuperOver=special,ballsPerOver=5)
    return raw


def sequence(count, illegal_before=None):
    records=[]
    for index in range(count):
        if illegal_before==index:
            extra=ball(); extra.update(id=1000+index,overNumber=index//5+1,
                oversActual=float(f'{index//5}.{index%5+1}'),oversUnique=index+0.1,
                wides=1,totalRuns=1,totalInningRuns=None)
            records.append(extra)
        delivery=ball(bowler=12+(index//10)%5)
        delivery.update(id=index+1,overNumber=index//5+1,
            oversActual=float(f'{index//5}.{index%5+1}'),oversUnique=index+0.2,totalInningRuns=None)
        records.append(delivery)
    legal=0
    for position,record in enumerate(records,1):
        record['ballsUnique']=position
        record['ballsActual']=legal+1
        legal+=int(not(record.get('wides') or record.get('noballs')))
    return records


def build(raw=None, balls=None):
    match,error=constructor.construct_match_v3('hundred',raw or hundred(),{1:balls or sequence(40)},{},{},{})
    if error: raise AssertionError(error)
    return match


def deliveries(match):
    return [d for o in match['innings'][0]['overs'] for d in o['deliveries']]


class HundredConstructionTests(unittest.TestCase):
    def test_ball_26_leaves_powerplay_without_six_over_assumption(self):
        ds=deliveries(build())
        self.assertEqual(ds[24]['phase'],'powerplay')
        self.assertEqual(ds[25]['phase'],'middle')

    def test_illegal_delivery_preserves_legal_clock_at_boundary(self):
        ds=deliveries(build(balls=sequence(30,illegal_before=24)))
        self.assertEqual([d.get('legal_balls_before') for d in ds[24:27]],[24,24,25])
        self.assertEqual([d.get('legal_balls_after') for d in ds[24:27]],[24,25,26])
        self.assertEqual([d['phase'] for d in ds[24:27]],['powerplay','powerplay','middle'])
        self.assertEqual([d.get('delivery_sequence') for d in ds[24:27]],[25,26,27])

    def test_no_ball_after_powerplay_does_not_advance_clock(self):
        bs=sequence(30,illegal_before=25);bs[25].update(wides=0,noballs=2,totalRuns=2)
        ds=deliveries(build(balls=bs))
        self.assertEqual(ds[25].get('legal_balls_before'),25)
        self.assertEqual(ds[25].get('legal_balls_after'),25)
        self.assertEqual(ds[25]['phase'],'middle')

    def test_late_phase_uses_allocation_not_short_chase_duration(self):
        short=build(balls=sequence(40))
        self.assertNotIn('death',[d['phase'] for d in deliveries(short)])
        full=deliveries(build(balls=sequence(100)))
        self.assertEqual(full[74]['phase'],'middle')
        self.assertEqual(full[75]['phase'],'death')

    def test_rain_quota_has_nominal_boundaries_without_claiming_actual_timing(self):
        m=build(hundred(quota=50),sequence(50)); inn=m['innings'][0];ds=deliveries(m)
        self.assertEqual(inn.get('allocated_legal_balls'),50)
        self.assertEqual(inn.get('nominal_powerplay_balls'),13)
        self.assertEqual(inn.get('nominal_death_start_ball'),38)
        self.assertEqual(ds[12].get('nominal_phase'),'powerplay')
        self.assertEqual(ds[13].get('nominal_phase'),'middle')
        self.assertEqual(ds[37].get('nominal_phase'),'death')
        self.assertEqual({d['phase'] for d in ds},{'unknown'})

    def test_missing_quota_is_unknown_not_guessed_from_chase(self):
        m=build(hundred(quota=None),sequence(40))
        self.assertIsNone(m['innings'][0].get('allocated_legal_balls'))
        self.assertEqual({d['phase'] for d in deliveries(m)},{'unknown'})

    def test_conflicting_over_quota_is_retained_for_review(self):
        bs=sequence(40);bs[4]['over']={'ballLimit':50,'isComplete':True}
        m=build(hundred(quota=100),bs)
        self.assertEqual({d['phase'] for d in deliveries(m)},{'unknown'})
        self.assertTrue(m['innings'][0].get('rule_review_issues'))

    def test_non_set_multiple_quota_keeps_score_but_has_unknown_phase(self):
        m=build(hundred(quota=44),sequence(44))
        self.assertEqual(m['innings'][0].get('allocated_legal_balls'),44)
        self.assertIsNone(m['innings'][0].get('nominal_powerplay_balls'))
        self.assertEqual({d['phase'] for d in deliveries(m)},{'unknown'})

    def test_unverified_season_is_not_assigned_current_conditions(self):
        m=build(hundred(season='2027'))
        self.assertEqual(m['meta'].get('ruleset_status'),'unverified')
        self.assertIsNone(m['meta'].get('ruleset_id'))
        self.assertEqual({d['phase'] for d in deliveries(m)},{'unknown'})

    def test_season_date_conflict_is_not_silently_resolved(self):
        raw=hundred();raw['match']['startDate']='2026-08-01'
        m=build(raw)
        self.assertEqual(m['meta'].get('ruleset_status'),'unverified')

    def test_super_five_is_separate_from_main_innings_phases(self):
        m=build(hundred(quota=5,special=True),sequence(3))
        self.assertEqual(m['innings'][0].get('innings_kind'),'super_five')
        self.assertFalse(m['innings'][0].get('include_in_standard_stats',True))
        self.assertEqual({d['phase'] for d in deliveries(m)},{'neutral'})
        self.assertEqual(m['innings'][0].get('allocated_legal_balls'),5)

    def test_format_preserves_source_and_rate_units(self):
        m=build();meta=m['meta']
        self.assertEqual(meta.get('playing_format'),'hundred_ball')
        self.assertEqual(meta['format_bucket'],'T20')
        self.assertEqual(meta.get('source_classification'),{'format':'HUNDRED_BALL','general_class_id':6,'international_class_id':None})
        self.assertEqual(meta.get('economy_basis_balls'),6)
        self.assertEqual(meta.get('display_unit'),'balls')
        self.assertEqual(meta['schema_version'],'3.2.0')

    def test_raw_format_detects_hundred_without_named_mens_competition(self):
        raw=hundred();raw['match'].pop('ballsPerOver');raw['match']['series']['name']="The Hundred Women's Competition"
        m=build(raw)
        self.assertEqual(m['meta']['balls_per_over'],5)
        self.assertEqual(m['meta']['gender'],'female')
        self.assertEqual(deliveries(m)[25]['phase'],'middle')

    def test_inconsistent_source_six_ball_hundred_requires_review(self):
        raw=hundred();raw['match']['ballsPerOver']=6
        m,error=constructor.construct_match_v3('x',raw,{1:sequence(30)},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('ballsPerOver',error or '')

    def test_ten_ball_spell_preserves_two_native_sets_and_source_markers(self):
        bs=sequence(10);bs[-1]['oversActual']='1.10'
        m=build(balls=bs);ds=deliveries(m)
        self.assertEqual(len(m['innings'][0]['overs']),2)
        self.assertEqual(m['innings'][0].get('legal_balls'),10)
        self.assertEqual(ds[-1]['actual_delivery'],'1.10')
        self.assertEqual(ds[-1].get('source_over_number'),2)

    def test_2026_one_run_no_ball_is_not_rewritten(self):
        b=ball();b.update(noballs=1,totalRuns=1,totalInningRuns=None)
        m=build(hundred(season='2026'),[b])
        self.assertEqual(deliveries(m)[0]['extras'],{'noballs':1})
        self.assertEqual(m['meta'].get('ruleset',{}).get('no_ball_penalty_runs'),1)

    def test_2023_two_run_rule_and_points_are_not_replaced_by_2026(self):
        b=ball();b.update(noballs=1,totalRuns=2,totalInningRuns=None)
        m=build(hundred(),[b])
        self.assertEqual(deliveries(m)[0]['extras'],{'noballs':2})
        self.assertEqual(m['meta'].get('ruleset',{}).get('points'),{'win':2,'tie_or_no_result':1,'loss':0})
        self.assertEqual(build(hundred(season='2026'))['meta'].get('ruleset',{}).get('points'),{'win':4,'tie_or_no_result':2,'loss':0})


class HundredGateTests(unittest.TestCase):
    def test_valid_hundred_candidate_passes_shared_gate(self):
        self.assertEqual(validator.validate_match_data(build()),[])

    def test_corrupted_ball_clock_is_rejected(self):
        m=build();deliveries(m)[25]['legal_balls_before']=24
        self.assertTrue(validator.validate_match_data(m))

    def test_missing_playing_format_is_rejected(self):
        m=build();m['meta'].pop('playing_format',None)
        self.assertTrue(validator.validate_match_data(m))

    def test_six_ball_completed_set_is_not_silently_tolerated(self):
        m=build();o=m['innings'][0]['overs'][0];o['deliveries'].append(copy.deepcopy(o['deliveries'][-1]))
        ok,errors=validator.validate_innings(m['innings'][0],balls_per_over=5)
        self.assertFalse(ok)
        self.assertTrue(any('legal balls' in e for e in errors),errors)

    def test_partial_final_set_is_valid(self):
        self.assertEqual(validator.validate_match_data(build(balls=sequence(42))),[])

    def test_overlong_final_set_is_rejected(self):
        m=build(balls=sequence(5));m['innings'][0]['overs'][0]['deliveries']+=copy.deepcopy(deliveries(m)[:1])
        ok,errors=validator.validate_innings(m['innings'][0],balls_per_over=5)
        self.assertFalse(ok)

    def test_main_innings_over_100_legal_balls_is_rejected(self):
        self.assertTrue(validator.validate_match_data(build(balls=sequence(101))))

    def test_bowler_exceeding_20_ball_quota_is_rejected(self):
        bs=sequence(25)
        for b in bs:b['bowlerPlayerId']=12
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_special_innings_over_five_balls_is_rejected(self):
        self.assertTrue(validator.validate_match_data(build(hundred(quota=5,special=True),sequence(6))))

    def test_changed_phase_or_rule_metadata_is_rejected(self):
        m=build();deliveries(m)[25]['phase']='powerplay'
        self.assertTrue(validator.validate_match_data(m))
        m=build();m['meta'].setdefault('ruleset',{})['no_ball_penalty_runs']=99
        self.assertTrue(validator.validate_match_data(m))

    def test_new_contract_preserves_identity_gate_and_participant_metrics(self):
        m=build();m['playing_xi']['_umpires']=['Umpire']
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertEqual(manifest['players']['unique_players_count'],22)
            m['players'].pop('espn_1012');(source/'100.json').write_text(json.dumps(m))
            manifest=scan_and_consolidate([source],root/'out2',root/'manifest2.json')
            self.assertEqual(manifest['total_matches'],0)


class HundredSourceRegressionTests(unittest.TestCase):
    def fixture(self,mid):
        data=json.loads((Path(__file__).parent/'fixtures/hundred'/f'{mid}.json').read_text())
        m,error=constructor.construct_match_v3(mid,data['details'],{int(k):v for k,v in data['commentary'].items()},{},{},{})
        self.assertIsNone(error)
        return m,data

    def test_real_2021_100_ball_and_97_ball_chase_keep_native_clocks(self):
        m,data=self.fixture('1252678')
        self.assertEqual([i.get('legal_balls') for i in m['innings']],[100,97])
        self.assertEqual([i.get('allocated_legal_balls') for i in m['innings']],[100,100])
        self.assertEqual(deliveries(m)[25]['phase'],'middle')
        self.assertEqual(validator.validate_match_data(m,data['details']['content']['innings']),[])

    def test_real_rain_record_does_not_reclassify_earlier_powerplay(self):
        m,data=self.fixture('1417818');inn=m['innings'][1]
        self.assertEqual(inn.get('allocated_legal_balls'),44)
        self.assertEqual(inn.get('legal_balls'),44)
        self.assertIn('conflicting_source_allocations',inn.get('rule_review_issues',[]))
        self.assertEqual({d['phase'] for o in inn['overs'] for d in o['deliveries']},{'unknown'})
        self.assertEqual(validator.validate_match_data(m,data['details']['content']['innings']),[])


class HundredFailureRegressionTests(unittest.TestCase):
    def test_missing_special_indicator_is_schema_error_not_crash(self):
        m=build();m['innings'][0].pop('super_over')
        self.assertTrue(validator.validate_match_data(m))

    def test_unknown_special_dismissal_is_review_error_not_crash(self):
        m=build(hundred(quota=5,special=True),sequence(3))
        deliveries(m)[0]['wickets']=[{'kind':'unknown','player_out_id':'espn_1001'}]
        self.assertTrue(validator.validate_match_data(m))

    def test_hundred_delivery_without_marker_is_not_silently_dropped(self):
        bs=sequence(30);bs[25].pop('oversActual')
        m,error=constructor.construct_match_v3('x',hundred(),{1:bs},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('delivery marker',error or '')

    def test_conflicting_season_cannot_normalize_with_wrong_penalty(self):
        raw=hundred();raw['match']['startDate']='2026-08-01'
        b=ball();b.update(noballs=1,totalRuns=2,totalInningRuns=None)
        m,error=constructor.construct_match_v3('x',raw,{1:[b]},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('component mismatch',error or '')

    def test_2025_two_run_correction_names_its_actual_season_rule(self):
        b=ball();b.update(noballs=1,totalRuns=2,totalInningRuns=None)
        m=build(hundred(season='2025'),[b])
        self.assertEqual(deliveries(m)[0]['extras'],{'noballs':2})
        self.assertEqual(m['meta']['normalization_notes'][0]['rule_id'],'hundred-2025-core-v1')

    def test_three_special_wickets_are_rejected(self):
        m=build(hundred(quota=5,special=True),sequence(3))
        for index,d in enumerate(deliveries(m)):
            d['wickets']=[{'kind':'bowled','player_out_id':f'espn_{1001+index}'}]
        self.assertTrue(validator.validate_match_data(m))

    def test_fifteen_consecutive_balls_by_one_bowler_require_review(self):
        bs=sequence(15)
        for b in bs:b['bowlerPlayerId']=12
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_malformed_source_marker_requires_review(self):
        bs=sequence(10);bs[0]['oversActual']='bad-marker'
        m,error=constructor.construct_match_v3('x',hundred(),{1:bs},{},{},{})
        self.assertTrue(m is None)
        self.assertIn('delivery marker',error or '')

    def test_native_group_must_match_retained_source_over_number(self):
        m=build();deliveries(m)[0]['source_over_number']=2
        self.assertTrue(validator.validate_match_data(m))

    def test_source_scorecard_detects_omitted_final_set_without_external_scorecard(self):
        raw=hundred();raw['content']['innings'][0]['balls']=40
        m=build(raw,sequence(35))
        self.assertTrue(validator.validate_match_data(m))

    def test_real_missing_completed_set_cannot_hide_behind_recomputed_clocks(self):
        data=json.loads((Path(__file__).parent/'fixtures/hundred/1252678.json').read_text())
        chunks={int(k):v for k,v in data['commentary'].items()}
        chunks[1]=[b for b in chunks[1] if b['overNumber']!=2]
        m,error=constructor.construct_match_v3('1252678',data['details'],chunks,{},{},{})
        self.assertIsNone(error)
        self.assertTrue(validator.validate_match_data(m,data['details']['content']['innings']))

    def test_missing_illegal_delivery_is_detected_by_source_unique_counter(self):
        bs=sequence(30,illegal_before=24);bs.pop(24)
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_corrupt_source_legal_position_is_not_treated_as_authoritative(self):
        m=build();deliveries(m)[25]['source_legal_ball_marker']=25
        self.assertTrue(validator.validate_match_data(m))

    def test_wide_after_allocation_completed_is_rejected(self):
        bs=sequence(100)
        extra=ball();extra.update(id=999,overNumber=21,oversActual=20.1,oversUnique=101,
                                  wides=1,totalRuns=1,totalInningRuns=None)
        bs.append(extra)
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_rain_or_missing_quota_does_not_remove_absolute_20_ball_ceiling(self):
        for quota in (50,None):
            with self.subTest(quota=quota):
                bs=sequence(45)
                for index,b in enumerate(bs):
                    b['bowlerPlayerId']=12 if index<10 or 20<=index<30 or index>=40 else 13
                self.assertTrue(validator.validate_match_data(build(hundred(quota=quota),bs)))

    def test_partial_set_appearances_count_toward_absolute_four_set_limit(self):
        bs=sequence(25)
        for index,b in enumerate(bs):
            b['bowlerPlayerId']=12 if index%5==0 else 13+index//5
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_partial_parts_of_three_consecutive_sets_require_review(self):
        bs=sequence(15)
        for index,b in enumerate(bs):
            b['bowlerPlayerId']=12 if index in (4,5,6,7,8,9,10) else 13+index//5
        self.assertTrue(validator.validate_match_data(build(balls=bs)))

    def test_super_five_stops_immediately_after_second_dismissal(self):
        m=build(hundred(quota=5,special=True),sequence(3))
        for index,d in enumerate(deliveries(m)[:2]):
            d['wickets']=[{'kind':'bowled','player_out_id':f'espn_{1001+index}'}]
        self.assertTrue(validator.validate_match_data(m))

    def test_zero_balls_rate_basis_remains_explicit(self):
        raw=hundred();raw['content']['innings']=[]
        m,error=constructor.construct_match_v3('x',raw,{},{},{},{})
        self.assertIsNone(error)
        self.assertEqual(m['meta'].get('economy_basis_balls'),6)
        self.assertEqual(validator.validate_match_data(m),[])


if __name__=='__main__':unittest.main()
