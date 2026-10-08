import json
from pathlib import Path
import tempfile
import unittest

from consolidate import scan_and_consolidate
from src.constructor import construct_match_v3
from helpers import details,ball,legacy_match


class MetricsTests(unittest.TestCase):
    def consolidate(self,match):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'100.json').write_text(json.dumps(match))
            return scan_and_consolidate([source],root/'out',root/'manifest.json')

    def test_manifest_wickets_exclude_retirement_events(self):
        match,_=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        match['innings'][0]['overs'][0]['deliveries'][0]['wickets']=[{'kind':'retired hurt','player_out_id':'espn_1001'}]
        result=self.consolidate(match)
        self.assertEqual(result['totals']['wickets'],0)
        self.assertEqual(result['totals']['wicket_events'],1)

    def test_pitch_line_presence_is_not_claimed_as_coordinates(self):
        match,_=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        match['innings'][0]['overs'][0]['deliveries'][0]['tactical']['pitch_line']='OUTSIDE_OFFSTUMP'
        result=self.consolidate(match)
        self.assertEqual(result['totals']['deliveries_with_pitch_line'],1)
        self.assertNotIn('deliveries_with_pitch_coordinates',result['totals'])
        self.assertNotIn('pitch_coordinates_coverage_pct',result['totals'])

    def test_legacy_input_does_not_advertise_new_match_contract(self):
        match=legacy_match()
        result=self.consolidate(match)
        self.assertEqual(result['schema_version'],'3.0.0')

    def test_unknown_legacy_dismissal_is_reported_without_aborting_scan(self):
        match=legacy_match()
        match['innings'][0]['overs'][0]['deliveries'][0]['wickets']=[{'kind':'unknown','player_out_id':'espn_1001'}]
        result=self.consolidate(match)
        self.assertEqual(result['total_matches'],0)
        self.assertEqual(result['unreconciled_failures']['count'],1)

    def test_officials_do_not_inflate_new_participant_counts(self):
        match,error=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        self.assertIsNone(error)
        match['playing_xi']['_umpires']=['Umpire']
        match['players']['espn_1001']['cricsheet_id']='existing-id'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'100.json').write_text(json.dumps(match))
            result=scan_and_consolidate([source],root/'out',root/'manifest.json')
        self.assertEqual(result['players']['unique_players_count'],22)
        self.assertEqual(result['players']['players_with_cricsheet_mapping'],1)

    def test_missing_optional_references_and_zero_delivery_records_are_reported(self):
        raw=details();raw['content']['innings']=[]
        match,_=construct_match_v3('101',raw,{},{},{},{})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'101.json').write_text(json.dumps(match))
            result=scan_and_consolidate([source],root/'out',root/'manifest.json')
        self.assertEqual(result['coverage']['zero_delivery_matches'],1)

    def test_rejected_identity_candidate_is_not_consolidated_as_valid(self):
        match,_=construct_match_v3('102',details(),{1:[ball(non_striker=6259)]},{},{},{})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'102.json').write_text(json.dumps(match))
            result=scan_and_consolidate([source],root/'out',root/'manifest.json')
            self.assertFalse((root/'out/102.json').exists())
        self.assertEqual(result['total_matches'],0)
        self.assertEqual(result['unreconciled_failures']['count'],1)

    def test_rejected_component_candidate_is_not_counted(self):
        match,_=construct_match_v3('103',details(),{1:[ball()]},{},{},{})
        match['innings'][0]['overs'][0]['deliveries'][0]['runs']['total']=2
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'103.json').write_text(json.dumps(match))
            result=scan_and_consolidate([source],root/'out',root/'manifest.json')
        self.assertEqual(result['total_matches'],0)


if __name__=='__main__':unittest.main()
