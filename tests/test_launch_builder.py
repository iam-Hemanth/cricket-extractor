import gzip
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from src.constructor import construct_match_v3
from helpers import details,ball


def raw(comp='Indian Premier League',trophy=117):
    value=details();value['match']['series'].update(name=comp,trophyId=trophy)
    value['match']['objectId']=100;value['match']['state']='POST'
    for g in value['content']['matchPlayers']['teamPlayers']:
        for p in g['players']:p['player']['gender']='M'
    return value


class LaunchBuilderTests(unittest.TestCase):
    def functions(self):
        try:from prepare_curated_launch import build_source_index,prepare_launch
        except ImportError:raise AssertionError('Offline launch builder is not implemented')
        return build_source_index,prepare_launch

    def prepare(self,root,value,mutate=None):
        build_index,prepare=self.functions();archive=root/'raw.tar.gz'
        blob=gzip.compress(json.dumps(value).encode());payload=root/'scorecard.json.gz';payload.write_bytes(blob)
        with tarfile.open(archive,'w:gz') as tar:tar.add(payload,arcname='data/raw/100/scorecard.json.gz')
        index=build_index([archive]);source=root/'candidates';source.mkdir()
        m,error=construct_match_v3('100',value,{1:[ball()]},{},{},{})
        self.assertIsNone(error)
        if mutate:mutate(m)
        (source/'100.json').write_text(json.dumps(m))
        return prepare(source,index,root/'launch')

    def test_selected_snapshot_keeps_only_included_valid_archive_members(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);report=self.prepare(root,raw())
            self.assertEqual(report['counts'],{'selected':1,'held':0,'excluded':0})
            self.assertTrue((root/'launch/matches/100.json').exists())
            with tarfile.open(root/'launch/curated_launch.tar.gz') as tar:
                self.assertIn('100.json',tar.getnames())
                self.assertFalse(any(m.issym() for m in tar.getmembers()))

    def test_historical_ilt20_is_not_in_output_or_archive(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);report=self.prepare(root,raw('International League T20',946))
            self.assertEqual(report['counts'],{'selected':0,'held':0,'excluded':1})
            self.assertFalse((root/'launch/matches/100.json').exists())
            with tarfile.open(root/'launch/curated_launch.tar.gz') as tar:self.assertNotIn('100.json',tar.getnames())

    def test_invalid_candidate_is_held_without_leaking_into_snapshot(self):
        def corrupt(m):m['players'].pop('espn_1012')
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);report=self.prepare(root,raw(),corrupt)
            self.assertEqual(report['counts'],{'selected':0,'held':1,'excluded':0})
            self.assertFalse((root/'launch/matches/100.json').exists())

    def test_existing_output_is_refused_so_stale_rows_cannot_leak(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'launch').mkdir();(root/'launch/100.json').write_text('{}')
            _,prepare=self.functions()
            with self.assertRaises(ValueError):prepare(root,{},root/'launch')

    def test_conflicting_duplicate_source_ids_are_held(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);build_index,prepare=self.functions();archives=[]
            for i,value in enumerate((raw(),raw('International League T20',946))):
                payload=root/f'{i}.gz';payload.write_bytes(gzip.compress(json.dumps(value).encode()));path=root/f'{i}.tar.gz'
                with tarfile.open(path,'w:gz') as tar:tar.add(payload,arcname='data/raw/100/scorecard.json.gz')
                archives.append(path)
            index=build_index(archives)
            self.assertTrue(index['100']['source_conflict'])


if __name__=='__main__':unittest.main()
