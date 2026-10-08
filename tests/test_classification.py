"""Prevent domestic/tour data, missing codes and gender guesses entering senior records."""
import json
from pathlib import Path
import unittest
from src import constructor, validator
from helpers import details,ball


def make_match(general=6,international=None,fmt='T20',competition='Big Bash League',gender=None):
    raw=details();raw['match'].update(generalClassId=general,internationalClassId=international,format=fmt)
    raw['match']['series']['name']=competition
    if gender is not None:raw['match']['gender']=gender
    return raw


def build(raw):
    m,error=constructor.construct_match_v3('100',raw,{1:[ball()]},{},{},{})
    if error:raise AssertionError(error)
    return m


class SourceClassificationTests(unittest.TestCase):
    def test_domestic_t20_does_not_require_a_whitelisted_league(self):
        m=build(make_match());self.assertEqual(m['meta']['format_bucket'],'T20')
        self.assertEqual(m['meta'].get('international_status'),'non_international')

    def test_same_raw_t20_requires_source_international_code(self):
        m=build(make_match(international=3,competition='India tour of England'))
        self.assertEqual(m['meta']['format_bucket'],'T20I')
        self.assertEqual(m['meta'].get('record_class'),'t20i')

    def test_first_class_is_not_test_despite_test_raw_format(self):
        m=build(make_match(4,None,'TEST','County Championship'))
        self.assertEqual(m['meta']['format_bucket'],'FC')
        self.assertEqual(m['meta'].get('playing_format'),'multi_day')

    def test_only_source_test_international_code_promotes_test(self):
        m=build(make_match(4,1,'TEST','India tour of Australia'))
        self.assertEqual(m['meta']['format_bucket'],'Test')
        self.assertTrue(m['meta'].get('record_stats_eligible'))

    def test_list_a_is_not_odi_despite_odi_raw_format(self):
        self.assertEqual(build(make_match(5,None,'ODI','One-Day Cup'))['meta']['format_bucket'],'ListA')
        self.assertEqual(build(make_match(5,2,'ODI','India tour of England'))['meta']['format_bucket'],'ODI')

    def test_other_formats_are_kept_out_of_normal_record_classes(self):
        for general,fmt,bucket in [(11,'TEST','OtherMDM'),(12,'ODI','OtherODM'),(13,'T20','OtherT20')]:
            with self.subTest(general=general):
                meta=build(make_match(general,None,fmt,'Tour Matches'))['meta']
                self.assertEqual(meta['format_bucket'],bucket)
                self.assertFalse(meta.get('record_stats_eligible',True))

    def test_missing_international_code_is_not_treated_as_explicit_null(self):
        raw=make_match();raw['match'].pop('internationalClassId')
        meta=build(raw)['meta'];self.assertEqual(meta['format_bucket'],'Unknown')
        self.assertEqual(meta.get('international_status'),'unknown')
        self.assertFalse(meta.get('source_classification_presence',{}).get('international_class_id',True))

    def test_unknown_source_codes_do_not_fall_back_to_senior_labels(self):
        for general,international in [(999,None),(6,999),(None,None)]:
            with self.subTest(codes=(general,international)):
                meta=build(make_match(general,international))['meta']
                self.assertEqual(meta['format_bucket'],'Unknown')
                self.assertFalse(meta.get('record_stats_eligible',True))

    def test_conflicting_family_codes_require_review(self):
        meta=build(make_match(4,3,'T20'))['meta']
        self.assertEqual(meta['format_bucket'],'Unknown')
        self.assertTrue(meta.get('classification_issues'))

    def test_raw_format_conflicting_with_class_family_requires_review(self):
        meta=build(make_match(6,3,'TEST'))['meta']
        self.assertEqual(meta['format_bucket'],'Unknown')
        self.assertTrue(meta.get('classification_issues'))

    def test_ipl_name_does_not_override_non_t20_source_class(self):
        meta=build(make_match(4,None,'TEST','Indian Premier League'))['meta']
        self.assertEqual(meta['format_bucket'],'FC')
        self.assertEqual(build(make_match(6,None,'T20','Indian Premier League'))['meta']['format_bucket'],'IPL')

    def test_domestic_multi_day_dates_use_actual_duration(self):
        raw=make_match(4,None,'TEST');raw['match']['actualDays']=3
        self.assertEqual(build(raw)['meta']['dates'],['2023-04-01','2023-04-02','2023-04-03'])

    def test_womens_api_general17_is_not_statsguru_class23(self):
        meta=build(make_match(17,10,'T20',"Asian Games Women's Cricket Competition"))['meta']
        self.assertEqual(meta['format_bucket'],'T20I')
        self.assertEqual(meta['gender'],'female')
        self.assertEqual(meta.get('source_classification',{}).get('general_class_id'),17)

    def test_known_womens_international_classes_have_correct_families(self):
        for international,fmt,bucket in [(8,'TEST','Test'),(9,'ODI','ODI')]:
            meta=build(make_match(None,international,fmt))['meta']
            self.assertEqual(meta['format_bucket'],bucket)
            self.assertEqual(meta['gender'],'female')

    def test_under19_class15_does_not_enter_senior_odi(self):
        meta=build(make_match(12,15,'ODI','Australia Under-19s in India'))['meta']
        self.assertNotEqual(meta['format_bucket'],'ODI')
        self.assertFalse(meta.get('record_stats_eligible',True))

    def test_missing_gender_is_unknown_not_default_male(self):
        self.assertEqual(build(make_match())['meta']['gender'],'unknown')

    def test_explicit_match_gender_is_preserved(self):
        self.assertEqual(build(make_match(gender='F'))['meta']['gender'],'female')

    def test_official_gender_does_not_determine_match_gender(self):
        raw=make_match();raw['match']['umpires']=[{'player':{'gender':'F'}}]
        self.assertEqual(build(raw)['meta']['gender'],'unknown')

    def test_unanimous_playing_roster_gender_is_source_evidence(self):
        raw=make_match()
        for g in raw['content']['matchPlayers']['teamPlayers']:
            for p in g['players']:p['player']['gender']='M'
        self.assertEqual(build(raw)['meta']['gender'],'male')

    def test_conflicting_explicit_gender_and_womens_class_remains_unknown(self):
        meta=build(make_match(17,10,gender='M'))['meta']
        self.assertEqual(meta['gender'],'unknown')
        self.assertTrue(meta.get('gender_issues'))

    def test_womens_class_not_overridden_by_one_bad_roster_gender(self):
        raw=make_match(17,10)
        for g in raw['content']['matchPlayers']['teamPlayers']:
            for p in g['players']:p['player']['gender']='F'
        raw['content']['matchPlayers']['teamPlayers'][0]['players'][0]['player']['gender']='M'
        meta=build(raw)['meta'];self.assertEqual(meta['gender'],'female')
        self.assertTrue(meta.get('gender_issues'))

    def test_missing_class_evidence_is_preserved(self):
        raw=make_match();raw['match'].pop('generalClassId')
        meta=build(raw)['meta'];self.assertIn('source_classification_presence',meta)
        self.assertFalse(meta['source_classification_presence']['general_class_id'])

    def test_new_classification_contract_is_versioned_and_gated(self):
        m=build(make_match());self.assertEqual(m['meta']['schema_version'],'3.3.0')
        self.assertEqual(validator.validate_match_data(m),[])
        m['meta']['format_bucket']='T20I'
        self.assertTrue(validator.validate_match_data(m))

    def test_classification_status_and_evidence_cannot_be_removed(self):
        m=build(make_match());m['meta'].pop('classification_status',None)
        self.assertTrue(validator.validate_match_data(m))


class ArchivedClassRegressionTests(unittest.TestCase):
    def test_actual_domestic_tour_and_womens_source_classes(self):
        from src.classification import classify
        data=json.loads((Path(__file__).parent/'fixtures/classification/source_classes.json').read_text())
        for record in data:
            with self.subTest(match_id=record['match_id']):
                meta=classify(record['match_fields'],record['roster_gender_values'])
                self.assertEqual(meta['format_bucket'],record['expected_bucket'])
                self.assertEqual(meta['gender'],record['expected_gender'])


if __name__=='__main__':unittest.main()
