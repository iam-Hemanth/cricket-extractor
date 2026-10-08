import copy
import gzip
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from prepare_curated_launch import build_source_index,prepare_launch
from src import match_filter
from src.constructor import construct_match_v3
from scripts import discover_recent_matches as discovery
from helpers import ball
from test_launch_builder import raw
from test_launch_scope import facts,decide


def index_for(value):
    return {'100':{'facts':match_filter.source_scope_facts(value),'source_conflict':False,'evidence':[]}}


class LaunchReviewTests(unittest.TestCase):
    def test_candidate_competition_date_team_scope_cannot_be_laundered(self):
        original=raw()
        for field in ('competition','date','team'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'in';source.mkdir();changed=copy.deepcopy(original)
                if field=='competition':changed['match']['series']['name']='ICC Cricket World Cup Qualifier'
                if field=='date':changed['match']['startDate']='2000-01-01'
                if field=='team':changed['match']['teams'][0]['team']['name']='Different Team'
                m,error=construct_match_v3('100',changed,{1:[ball()]},{},{},{})
                self.assertIsNone(error);(source/'100.json').write_text(json.dumps(m))
                r=prepare_launch(source,index_for(original),root/'out')
                self.assertEqual(r['counts']['held'],1)
                self.assertFalse((root/'out/matches/100.json').exists())

    def test_live_only_discovery_preserves_trophy_identity(self):
        match={'objectId':100,'startDate':'2026-10-01','statusText':'Club A won by 1 run','format':'T20',
            'teams':[{'team':{'name':'Club A'}},{'team':{'name':'Club B'}}],
            'series':{'longName':'Pepsi IPL','trophyId':117}}
        data={'props':{'appPageProps':{'data':{'data':{'content':{'matches':[match]}}}}}}
        class Response:
            status_code=200
            text='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        with patch.object(discovery.requests,'get',side_effect=lambda url,**k:Response()):
            rows=discovery.discover_matches('2026-09-01')
        self.assertEqual(rows['100'].get('trophy_id'),117)

    def test_source_requested_identity_mismatch_is_held(self):
        value=raw();value['match']['objectId']=999
        self.assertEqual(match_filter.source_scope_decision(value,'100')['status'],'held')

    def test_international_class_cannot_qualify_through_franchise_trophy(self):
        info=facts(('Club A','Club B'),competition='Indian Premier League',trophy=117)
        self.assertEqual(decide(info)['status'],'held')

    def test_invalid_calendar_date_is_held(self):
        self.assertEqual(decide(facts(('India','England'),record='test',date='9999-99-99'))['status'],'held')

    def test_known_failure_id_without_candidate_or_source_stays_held(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'in';source.mkdir();(source/'failed_matches.json').write_text('{"100":"source unavailable"}')
            r=prepare_launch(source,{},root/'out')
            self.assertEqual(r['counts']['held'],1)
            self.assertIn('100',r['rows'])

    def test_malformed_source_metadata_is_held_not_a_batch_exception(self):
        for change in ('competition','team'):
            value=raw()
            if change=='competition':value['match']['series']['name']=42
            if change=='team':value['match']['teams']=[42]
            self.assertEqual(match_filter.source_scope_decision(value,'100')['status'],'held')

    def test_malformed_archived_json_is_recorded_as_source_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'bad.gz';p.write_bytes(gzip.compress(b'not json'));a=root/'a.tar.gz'
            with tarfile.open(a,'w:gz') as tar:tar.add(p,arcname='raw/100/scorecard.json.gz')
            idx=build_source_index([a])
            self.assertTrue(idx['100']['source_conflict'])


if __name__=='__main__':unittest.main()
