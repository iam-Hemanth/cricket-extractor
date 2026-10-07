import copy
import json
from pathlib import Path
import unittest

from src import constructor, validator
from helpers import details, player, ball


class ParticipantTests(unittest.TestCase):
    def registry(self, raw, comments=None, people=None):
        build=getattr(constructor,'build_participant_registry',None)
        self.assertTrue(callable(build),'Participant registry is not implemented')
        return build(raw, comments or {}, {}, people or {})

    def construct(self, raw, comments=None):
        match,error=constructor.construct_match_v3('fixture',raw,comments or {1:[ball()]},{},{},{})
        self.assertIsNone(error,error)
        return match

    def test_team_that_only_bowls_has_registry_metadata(self):
        raw=details()
        id_map,players,issues=self.registry(raw,{1:[ball()]})
        self.assertEqual(id_map[12],'espn_1012')
        self.assertIn('espn_1012',players)
        self.assertIn('bowler',players['espn_1012']['participant_roles'])
        self.assertEqual(issues,[])

    def test_nested_commentary_recovers_explicit_mapping(self):
        raw=details();delivery=ball(bowler=50)
        delivery['over']={'overEndBowlers':[{'player':player(50,5000)}]}
        id_map,players,issues=self.registry(raw,{1:[delivery]})
        self.assertEqual(id_map[50],'espn_5000')
        self.assertIn('espn_5000',players)
        self.assertEqual(issues,[])

    def test_conflicting_object_ids_are_rejected(self):
        raw=details()
        raw['content']['innings'][0]['inningBatsmen'].append({'player':player(1,9999)})
        _,_,issues=self.registry(raw)
        self.assertTrue(any(i['reason']=='conflicting_object_id' for i in issues),issues)

    def test_identical_surnames_do_not_merge(self):
        raw=details()
        entries=raw['content']['matchPlayers']['teamPlayers'][0]['players']
        entries[0]['player']['name']='A Smith';entries[1]['player']['name']='B Smith'
        _,players,_=self.registry(raw)
        self.assertIn('espn_1001',players);self.assertIn('espn_1002',players)

    def test_participants_are_not_truncated_to_eleven(self):
        raw=details()
        raw['content']['matchPlayers']['teamPlayers'][0]['players'].extend([{'player':player(i)} for i in range(30,35)])
        _,players,_=self.registry(raw,{1:[ball(batter=34)]})
        self.assertEqual(len(players),27)
        match=self.construct(raw,{1:[ball(batter=34)]})
        self.assertEqual(match['playing_xi']['A'],[])
        self.assertEqual(match['meta']['playing_xi_status']['A'],'unconfirmed')

    def test_source_unknown_internal_id_is_not_fabricated(self):
        raw=details();match=self.construct(raw,{1:[ball(non_striker=6259)]})
        delivery=match['innings'][0]['overs'][0]['deliveries'][0]
        self.assertIsNone(delivery['non_striker_id'])
        self.assertNotIn('espn_6259',match['players'])
        valid,issues=validator.validate_player_registry(match)
        self.assertFalse(valid)
        self.assertTrue(any('6259' in str(issue) for issue in issues))

    def test_missing_mandatory_actor_prevents_construction(self):
        _,error=constructor.construct_match_v3('missing',details(),{1:[ball(bowler=999)]},{},{},{})
        self.assertIsNotNone(error)

    def test_valid_unmapped_player_is_accepted(self):
        match=self.construct(details())
        self.assertIsNone(match['players']['espn_1001']['cricsheet_id'])
        self.assertTrue(validator.validate_player_registry(match)[0])

    def test_official_cannot_resolve_delivery_reference(self):
        match={'meta':{'schema_version':'3.1.0'},'players':{},'playing_xi':{'_umpires':['espn_1001']},
               'innings':[{'overs':[{'deliveries':[{'batter_id':'espn_1001','bowler_id':'espn_1012'}]}]}]}
        self.assertFalse(validator.validate_player_registry(match)[0])

    def test_zero_innings_preserves_source_people(self):
        raw=details();raw['content']['innings']=[]
        match=self.construct(raw,{})
        self.assertEqual(len(match['players']),22)

    def test_source_absent_optional_non_striker_stays_null(self):
        match=self.construct(details(),{1:[ball(non_striker=None)]})
        self.assertTrue(validator.validate_player_registry(match)[0])
        self.assertEqual(match['meta']['missing_non_striker_deliveries'],1)

    def test_impact_preserves_starter_and_activated_player(self):
        raw=details();incoming=player(30,1030)
        raw['content']['matchPlayers']['teamPlayers'][0]['players'].append({'player':incoming,'note':'Impact Player','isImpactPlayer':True})
        delivery=ball(batter=30)
        delivery['events']=[{'type':'IMPACT_PLAYER','teamId':10,'incomingPlayer':incoming,'outgoingPlayer':player(1)}]
        match=self.construct(raw,{1:[delivery]})
        self.assertEqual(len(match['playing_xi']['A']),11)
        self.assertEqual(match['participation_events'][0]['incoming_player_id'],'espn_1030')
        self.assertEqual(match['participation_events'][0]['outgoing_player_id'],'espn_1001')
        self.assertEqual(match['innings'][0]['overs'][0]['deliveries'][0]['batter_id'],'espn_1030')

    def test_concussion_replacement_preserves_history_and_restrictions(self):
        raw=details();delivery=ball(batter=30)
        delivery['events']=[{'type':'CONCUSSION_REPLACEMENT','teamId':10,'incomingPlayer':player(30),
                             'outgoingPlayer':player(1),'restrictions':{'may_bowl':False}}]
        match=self.construct(raw,{1:[delivery]})
        event=match['participation_events'][0]
        self.assertEqual(event['type'],'concussion_replacement')
        self.assertEqual(event['restrictions'],{'may_bowl':False})
        self.assertEqual(len(match['playing_xi']['A']),11)

    def test_unused_option_is_not_activated(self):
        raw=details()
        raw['content']['matchPlayers']['teamPlayers'].append({'type':'SUBSTITUTE','team':{'id':10,'name':'A'},'players':[{'player':player(30)}]})
        match=self.construct(raw)
        self.assertIn('espn_1030',match['players'])
        self.assertEqual(match['players']['espn_1030']['participant_roles'],['listed'])
        self.assertEqual(match['participation_events'],[])

    def test_fielding_substitute_is_not_full_replacement(self):
        raw=details();delivery=ball()
        delivery['events']=[{'type':'SUBSTITUTE_FIELDER','incomingPlayer':player(30),'teamId':20}]
        match=self.construct(raw,{1:[delivery]})
        self.assertEqual(match['participation_events'][0]['type'],'substitute_fielder')
        self.assertEqual(len(match['playing_xi']['B']),11)

    def test_combined_replacements_have_no_hard_participant_cap(self):
        raw=details();delivery=ball()
        delivery['events']=[{'type':'IMPACT_PLAYER','incomingPlayer':player(30),'outgoingPlayer':player(1)},
                            {'type':'CONCUSSION_REPLACEMENT','incomingPlayer':player(31),'outgoingPlayer':player(2)}]
        match=self.construct(raw,{1:[delivery]})
        self.assertEqual(len(match['players']),24)
        self.assertEqual(len(match['participation_events']),2)

    def test_unknown_replacement_metadata_preserves_known_facts(self):
        raw=details();delivery=ball(batter=30)
        delivery['over']={'overEndBatsmen':[{'player':player(30)}]}
        match=self.construct(raw,{1:[delivery]})
        self.assertTrue(validator.validate_player_registry(match)[0])
        self.assertEqual(match['participation_events'],[])

    def test_retired_hurt_return_uses_same_identity(self):
        raw=details();delivery=ball()
        delivery['events']=[{'type':'RETIRED_HURT','player':player(1)}]
        match=self.construct(raw,{1:[delivery,dict(delivery,id=101,oversActual=0.2)]})
        self.assertEqual(len(match['players']),22)
        self.assertEqual(match['participation_events'],[])

    def test_historical_replacement_not_classified_by_current_ipl_rules(self):
        raw=details();raw['match']['format']='ODI';raw['match']['series']['name']='2005 ODI'
        delivery=ball();delivery['events']=[{'type':'SUPER_SUB','incomingPlayer':player(30),'outgoingPlayer':player(1)}]
        match=self.construct(raw,{1:[delivery]})
        self.assertEqual(match['participation_events'][0]['type'],'historical_supersub')

    def test_nomination_revision_is_not_in_match_replacement(self):
        raw=details();raw['content']['participationEvents']=[{'type':'PRE_MATCH_REPLACEMENT','incomingPlayer':player(30),'outgoingPlayer':player(1),'teamId':10}]
        raw['content']['matchPlayers']['teamPlayers'][0]['players'][0]={'player':player(30)}
        match=self.construct(raw,{1:[ball(batter=30)]})
        self.assertIn('espn_1030',[p['player_id'] for p in match['playing_xi']['A']])
        self.assertNotIn('espn_1001',[p['player_id'] for p in match['playing_xi']['A']])

    def test_archived_ipl_event_shape_preserves_actual_replacements(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/repair/1359476_replacement.json').read_text())
        raw=details();raw['content']['matchPlayers']['teamPlayers']=fixture['groups']
        raw['content']['innings']=[]
        raw['match']['teams']=[{'team':group['team']} for group in fixture['groups']]
        comments={2:[dict(item['ball_context'],events=[item['raw_event']]) for item in fixture['events']]}
        id_map,players,issues=self.registry(raw,comments)
        extract=getattr(constructor,'extract_participation_events',None)
        self.assertTrue(callable(extract))
        events=extract(raw,comments,id_map,players)
        self.assertEqual(len(events),2)
        self.assertEqual({e['type'] for e in events},{'impact_player'})
        iyer=next(e for e in events if e['incoming_player_id']=='espn_851403')
        self.assertEqual(iyer['outgoing_player_id'],'espn_1108375')
        from src.participants import confirmed_lineups
        lineups,status=confirmed_lineups(raw,players,events)
        self.assertEqual(len(lineups['KKR']),11)
        self.assertIn('espn_1108375',[p['player_id'] for p in lineups['KKR']])
        self.assertNotIn('espn_851403',[p['player_id'] for p in lineups['KKR']])

    def test_archived_concussion_roster_does_not_invent_missing_event(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/repair/1152847_replacement.json').read_text())
        raw=details();raw['content']['matchPlayers']['teamPlayers']=fixture['groups']
        raw['content']['innings']=[]
        raw['match']['teams']=[{'team':group['team']} for group in fixture['groups']]
        _,players,_=self.registry(raw,{})
        from src.participants import confirmed_lineups
        lineups,status=confirmed_lineups(raw,players,[])
        self.assertEqual(len(players),23)
        self.assertEqual(status['Australia'],'unconfirmed')
        self.assertEqual(lineups['Australia'],[])


if __name__=='__main__':
    unittest.main()
