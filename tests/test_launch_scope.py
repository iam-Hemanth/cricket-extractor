"""Launch admission is a product policy, separate from official source classes."""
import unittest
from src import match_filter


def facts(teams=('Afghanistan','Ireland'),record='t20i',competition='Afghanistan in Ireland',trophy=None,date='2023-04-01'):
    return {'teams':list(teams),'record_class':record,'format_bucket':{'test':'Test','odi':'ODI','t20i':'T20I','t20':'T20'}.get(record,'OtherT20'),
        'classification_status':'source_codes','record_stats_eligible':not record.startswith('other'),
        'gender':'male','competition':competition,'trophy_id':trophy,'dates':[date],'state':'POST'}


def decide(info,mid='100'):
    fn=getattr(match_filter,'scope_decision',None)
    if fn is None:raise AssertionError('Shared launch policy is not implemented')
    return fn(info,mid,verified=True)


class LaunchScopeTests(unittest.TestCase):
    def test_afghanistan_against_priority_opponent_is_included(self):
        self.assertEqual(decide(facts(('Afghanistan','Australia')))['status'],'selected')

    def test_afghanistan_no_bilateral_exception_against_nonpriority(self):
        self.assertEqual(decide(facts())['status'],'excluded')

    def test_afghanistan_qualifies_through_flagship_event(self):
        self.assertEqual(decide(facts(competition='World T20',trophy=89))['status'],'selected')

    def test_afghanistan_qualifier_has_no_exception(self):
        self.assertEqual(decide(facts(competition='ICC Cricket World Cup Qualifier',trophy=12))['status'],'excluded')

    def test_ilt20_is_excluded_even_with_priority_country_names(self):
        info=facts(('India','Australia'),competition='International League T20',trophy=946)
        self.assertEqual(decide(info)['status'],'excluded')

    def test_sponsored_ilt20_is_excluded_by_source_trophy(self):
        self.assertEqual(decide(facts(record='t20',competition='DP World ILT20',trophy=946))['status'],'excluded')

    def test_four_leagues_and_sponsored_names_are_included(self):
        for name,trophy in [('Pepsi Indian Premier League',117),('Betway SA20',987),("The Hundred Men's Competition",826),('Major League Cricket',985)]:
            with self.subTest(league=name):self.assertEqual(decide(facts(('Club A','Club B'),record='t20',competition=name,trophy=trophy))['status'],'selected')

    def test_other_domestic_league_is_excluded(self):
        self.assertEqual(decide(facts(('Sydney Sixers','Perth Scorchers'),record='t20',competition='Big Bash League',trophy=158))['status'],'excluded')

    def test_priority_names_cannot_promote_nonwhitelisted_domestic(self):
        self.assertEqual(decide(facts(('India','Australia'),record='t20',competition='Tour tournament'))['status'],'excluded')

    def test_other_tour_record_class_does_not_enter_senior_snapshot(self):
        self.assertEqual(decide(facts(('India','England'),record='other_t20'))['status'],'excluded')

    def test_source_classification_uncertainty_is_held(self):
        info=facts(('India','England'));info.update(classification_status='needs_review',record_class='unknown',record_stats_eligible=False)
        self.assertEqual(decide(info)['status'],'held')

    def test_unknown_gender_is_held_and_womens_scope_excluded(self):
        info=facts(('India','England'));info['gender']='unknown';self.assertEqual(decide(info)['status'],'held')
        info['gender']='female';self.assertEqual(decide(info)['status'],'excluded')

    def test_cutoffs_are_preserved(self):
        self.assertEqual(decide(facts(('India','England'),record='test',date='2010-12-31'))['status'],'excluded')
        self.assertEqual(decide(facts(('India','England'),record='odi',date='2006-12-31'))['status'],'excluded')
        self.assertEqual(decide(facts(('India','England'),record='odi',date='2007-01-01'))['status'],'selected')

    def test_missing_cutoff_date_or_team_identity_is_held(self):
        info=facts(record='test');info['dates']=[];self.assertEqual(decide(info)['status'],'held')
        info=facts(competition='ICC World Cup',trophy=12);info['teams']=[];self.assertEqual(decide(info)['status'],'held')

    def test_regional_and_blocked_match_rules_remain(self):
        self.assertEqual(decide(facts(('India','England'),competition='Regional Cup'))['status'],'excluded')
        self.assertEqual(decide(facts(('India','England')),'1473495')['status'],'excluded')

    def test_uncompleted_source_is_not_selected(self):
        info=facts(('India','England'));info['state']='PRE';self.assertEqual(decide(info)['status'],'held')

    def test_legacy_discovery_does_not_add_afghanistan_or_ilt20(self):
        info={'match_type':'T20I','teams':['Afghanistan','Ireland'],'dates':['2023-01-01'],'event':{'name':'Bilateral'}}
        self.assertFalse(match_filter.should_ingest_match(info)[0])
        info.update(teams=['India','Australia'],event={'name':'International League T20'})
        self.assertFalse(match_filter.should_ingest_match(info)[0])

    def test_source_trophy_is_not_used_as_international_class(self):
        self.assertEqual(decide(facts(('Club A','Club B'),record='t20',competition='ICC World Cup',trophy=12))['status'],'excluded')


if __name__=='__main__':unittest.main()
