import csv
import json
import tarfile
import zipfile
from pathlib import Path
import tempfile
import unittest
from scripts.build_lake_catalog import generate_catalog
from src.constructor import construct_match_v3
from helpers import details,ball


class CatalogContractTests(unittest.TestCase):
    def test_catalog_keeps_classification_penalty_and_non_dismissal_counts(self):
        raw=details();raw['match']['series']['name']='Big Bash League'
        raw['content']['innings'][0].update(runs=5,wickets=0,balls=1,penalties=5)
        m,error=construct_match_v3('100',raw,{1:[ball()]},{},{},{})
        self.assertIsNone(error)
        m['innings'][0]['overs'][0]['deliveries'][0]['wickets']=[{'kind':'retired hurt','player_out_id':'espn_1001'}]
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            generate_catalog(source,root/'out')
            row=next(csv.DictReader((root/'out/matches_index.csv').open()))
            self.assertEqual(row['format_bucket'],'T20')
            self.assertEqual(row.get('playing_format'),'t20')
            self.assertEqual(row.get('international_status'),'non_international')
            self.assertEqual(row.get('schema_version'),'3.3.0')
            self.assertEqual(row['total_runs'],'5')
            self.assertEqual(row['total_wickets'],'0')
            self.assertNotIn('pitch_map_balls',row)
            self.assertIn('pitch_line_balls',row)

    def test_invalid_candidate_is_not_repackaged_by_catalog(self):
        m,error=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        self.assertIsNone(error);m['players'].pop('espn_1012')
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'in';source.mkdir();(source/'100.json').write_text(json.dumps(m))
            generate_catalog(source,root/'out')
            self.assertEqual(list(csv.DictReader((root/'out/matches_index.csv').open())),[])
            with zipfile.ZipFile(root/'out/cricket_lake_v3.zip') as archive:
                self.assertNotIn('100.json',archive.namelist())
            with tarfile.open(root/'out/cricket_lake_v3.tar.gz') as archive:
                self.assertNotIn('100.json',archive.getnames())

    def test_empty_catalog_preserves_stable_header_and_zero_counts(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'in';source.mkdir()
            generate_catalog(source,root/'out')
            with (root/'out/matches_index.csv').open() as f:
                reader=csv.DictReader(f)
                self.assertIn('record_class',reader.fieldnames)
                self.assertEqual(list(reader),[])


if __name__=='__main__':unittest.main()
