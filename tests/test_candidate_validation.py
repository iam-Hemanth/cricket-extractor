import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src import validator
from src.constructor import construct_match_v3
from helpers import details,ball


class CandidateValidationTests(unittest.TestCase):
    def validate(self,match,scorecard=None):
        check=getattr(validator,'validate_match_data',None)
        self.assertTrue(callable(check),'Unified candidate validation is not implemented')
        return check(match,scorecard)

    def test_valid_candidate_passes_schema_identity_and_scoring(self):
        match,_=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        self.assertEqual(self.validate(match),[])

    def test_invalid_format_fails_full_schema(self):
        match,_=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        match['meta']['format_bucket']='Unknown'
        self.assertTrue(self.validate(match))

    def test_missing_role_is_rejected(self):
        match,_=construct_match_v3('100',details(),{1:[ball()]},{},{},{})
        match['players'].pop('espn_1012')
        self.assertTrue(self.validate(match))

    def test_strict_reparse_reports_missing_requested_match(self):
        repo=Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir()
            result=subprocess.run([sys.executable,'-B',str(repo/'reparse.py'),'--raw-dir',str(raw),'--output-dir',str(root/'out'),'--match-ids','999','--strict'],cwd=repo,env=os.environ.copy(),capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue((root/'out/failed_matches.json').exists())


if __name__=='__main__':unittest.main()
