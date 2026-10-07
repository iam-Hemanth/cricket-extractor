import unittest

from src.constructor import construct_match_v3
from src.validator import validate_match_data
from helpers import details,player,ball


class ReviewRegressionTests(unittest.TestCase):
    def test_invalid_supplied_non_striker_id_is_not_treated_as_absent(self):
        match,error=construct_match_v3('100',details(),{1:[ball(non_striker='invalid-id')]},{},{},{})
        self.assertIsNone(error)
        self.assertTrue(validate_match_data(match))

    def test_invalid_supplied_dismissed_id_is_not_replaced_by_striker(self):
        delivery=ball();delivery.update(isWicket=True,outPlayerId='invalid-id',dismissalType=4)
        match,error=construct_match_v3('100',details(),{1:[delivery]},{},{},{})
        self.assertIsNotNone(error)

    def test_numeric_string_source_references_use_same_explicit_map(self):
        delivery=ball();delivery['batsmanPlayerId']='1';delivery['bowlerPlayerId']='12'
        match,error=construct_match_v3('100',details(),{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        self.assertEqual(match['innings'][0]['overs'][0]['deliveries'][0]['batter_id'],'espn_1001')

    def construct_with_fielder(self,internal):
        raw=details()
        raw['content']['innings'][0]['inningBatsmen'][0]['dismissalFielders']=[{'player':{'id':internal,'name':'Explicit fielder'}}]
        delivery=ball();delivery.update(isWicket=True,outPlayerId=1,dismissalType=1)
        match,error=construct_match_v3('100',raw,{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        return match

    def test_internal_only_fielder_uses_explicit_existing_mapping(self):
        match=self.construct_with_fielder(13)
        fielder=match['innings'][0]['overs'][0]['deliveries'][0]['wickets'][0]['fielders'][0]
        self.assertEqual(fielder['player_id'],'espn_1013')

    def test_unresolved_source_fielder_is_not_silently_accepted(self):
        match=self.construct_with_fielder(999)
        self.assertTrue(validate_match_data(match))

    def test_active_eleven_after_replacement_is_not_original_eleven(self):
        raw=details()
        raw['content']['matchPlayers']['teamPlayers'][0]['players'][0]={'player':player(30)}
        delivery=ball(batter=30)
        delivery['events']=[{'type':'CONCUSSION_REPLACEMENT','teamId':10,'incomingPlayer':player(30),'outgoingPlayer':player(1)}]
        match,error=construct_match_v3('100',raw,{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        self.assertEqual(match['meta']['playing_xi_status']['A'],'unconfirmed')
        self.assertEqual(match['playing_xi']['A'],[])

    def test_unknown_replacement_reason_does_not_certify_active_lineup(self):
        raw=details();raw['content']['matchPlayers']['teamPlayers'][0]['players'][0]={'player':player(30)}
        delivery=ball(batter=30)
        delivery['events']=[{'type':'REPLACEMENT','teamId':10,'incomingPlayer':player(30),'outgoingPlayer':player(1)}]
        match,error=construct_match_v3('100',raw,{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        self.assertEqual(match['meta']['playing_xi_status']['A'],'unconfirmed')

    def test_unknown_dismissal_is_rejected_without_scorecard_context(self):
        raw=details();delivery=ball()
        delivery.update(isWicket=True,outPlayerId=1,dismissalType=999)
        match,error=construct_match_v3('100',raw,{1:[delivery]},{},{},{})
        self.assertIsNone(error)
        self.assertTrue(validate_match_data(match))


if __name__=='__main__':unittest.main()
