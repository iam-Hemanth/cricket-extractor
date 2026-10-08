import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import extract
from scripts import discover_recent_matches as discovery
from helpers import ball
from test_launch_builder import raw


class RuntimeScopeTests(unittest.TestCase):
    def test_curated_extractor_skips_outside_source_even_for_explicit_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);values={'100':raw(),'101':raw('International League T20',946)}
            values['101']['match']['objectId']=101
            argv=['extract.py','--match-ids','100,101','--raw-dir',str(root/'raw'),
                  '--output-dir',str(root/'out'),'--delay','0']
            with patch.object(sys,'argv',argv),patch.object(extract,'CricinfoClient',return_value=MagicMock()), \
                 patch.object(extract,'load_references',return_value=({},{},{})), \
                 patch.object(extract,'fetch_match_details',side_effect=lambda **k:(values[k['match_id']],None)), \
                 patch.object(extract,'fetch_innings_commentary',return_value=([ball()],None)), \
                 patch.object(extract.time,'sleep'):
                with self.assertRaises(SystemExit) as result:extract.main()
                self.assertEqual(result.exception.code,0)
            self.assertTrue((root/'out/100.json').exists())
            self.assertFalse((root/'out/101.json').exists())
            decisions=json.loads((root/'out/scope_decisions.json').read_text())
            self.assertEqual(decisions['101']['reason'],'ilt20_removed')

    def test_discovery_passes_id_and_stable_trophy_to_same_policy(self):
        values={
            '100':{'match_id':'100','format':'T20','teams':['Club A','Club B'],'date':'2023-01-01','competition':'Pepsi IPL','trophy_id':117},
            '1473495':{'match_id':'1473495','format':'T20','teams':['Club A','Club B'],'date':'2023-01-01','competition':'Indian Premier League','trophy_id':117},
            '101':{'match_id':'101','format':'T20','teams':['India','Australia'],'date':'2023-01-01','competition':'International League T20','trophy_id':946}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir();(root/'data/manifest.json').write_text('[]')
            argv=['discover_recent_matches.py','--output-curated','data/discovered.json']
            with patch.object(sys,'argv',argv),patch.object(discovery,'REPO_ROOT',root),patch.object(discovery,'discover_matches',return_value=values):discovery.main()
            data=json.loads((root/'data/discovered.json').read_text())
            self.assertEqual([r['match_id'] for r in data],['100'])
            self.assertFalse((root/'data/manifest_unfiltered_delta.json').exists())


if __name__=='__main__':unittest.main()
