import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from src.constructor import construct_match_v3
from helpers import details,ball


class SchemaContractTests(unittest.TestCase):
    def setUp(self):
        self.schema=json.loads((Path(__file__).parents[1]/'schema/schema.json').read_text())
        Draft202012Validator.check_schema(self.schema)
        self.validator=Draft202012Validator(self.schema)

    def test_new_candidate_validates_with_participant_registry(self):
        match,error=construct_match_v3('fixture',details(),{1:[ball()]},{},{},{})
        self.assertIsNone(error)
        errors=list(self.validator.iter_errors(match))
        self.assertEqual(errors,[],[error.message for error in errors])

    def test_candidate_without_registry_is_rejected(self):
        match,_=construct_match_v3('fixture',details(),{1:[ball()]},{},{},{})
        match.pop('players',None)
        self.assertTrue(list(self.validator.iter_errors(match)))

    def test_unmapped_cricsheet_identity_and_unknown_event_timing_are_valid(self):
        raw=details()
        raw['content']['participationEvents']=[{'type':'CONCUSSION_REPLACEMENT','incomingPlayer':{'id':30,'objectId':1030,'name':'Incoming'},'outgoingPlayer':{'id':1,'objectId':1001,'name':'Original'}}]
        match,error=construct_match_v3('fixture',raw,{1:[ball()]},{},{},{})
        self.assertIsNone(error)
        self.assertIsNone(match['players']['espn_1030']['cricsheet_id'])
        self.assertIsNone(match['participation_events'][0]['source_ball_id'])
        self.assertEqual(list(self.validator.iter_errors(match)),[])


if __name__=='__main__':
    unittest.main()
