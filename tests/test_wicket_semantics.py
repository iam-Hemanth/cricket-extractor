import copy
import unittest

from src import validator
from src.cricsheet_audit import CricsheetAuditor


class WicketSemanticsTests(unittest.TestCase):
    def count(self, kind):
        counter = getattr(validator, 'count_team_wickets', None)
        self.assertTrue(callable(counter), 'Team wicket counter is not implemented')
        return counter([{'wickets': [{'kind': kind}]}])

    def test_retired_hurt_is_not_team_wicket(self):
        self.assertEqual(self.count('retired hurt'), 0)
        self.assertEqual(self.count('retired not out'), 0)

    def test_retired_out_counts(self):
        self.assertEqual(self.count('retired out'), 1)

    def test_run_out_counts_for_team(self):
        self.assertEqual(self.count('run out'), 1)

    def test_unknown_kind_requires_review(self):
        with self.assertRaises(ValueError):
            self.count('unmapped_kind')

    def test_retirement_event_does_not_create_false_hard_wicket_difference(self):
        delivery = {'is_legal': True, 'runs': {'batter': 0, 'extras': 0, 'total': 0},
                    'extras': {}, 'wickets': []}
        normalized = {'match_id': '247475', 'playing_xi': {},
                      'innings': [{'overs': [{'over': 0, 'deliveries': [delivery]}]}]}
        cs_delivery = copy.deepcopy(delivery)
        cs_delivery['wickets'] = [{'kind': 'retired hurt', 'player_out': 'S Dhaniram'}]
        cricsheet = {'info': {'registry': {'people': {}}},
                    'innings': [{'overs': [{'over': 0, 'deliveries': [cs_delivery]}]}]}
        result = CricsheetAuditor().audit_match(normalized, cricsheet)
        self.assertTrue(result.is_valid, result.hard_errors)
        self.assertTrue(result.soft_warnings, 'Retirement event differences must remain visible')

    def test_innings_count_mismatch_is_not_certified_complete(self):
        normalized = {'match_id': 'coverage', 'playing_xi': {}, 'innings': []}
        cricsheet = {'info': {}, 'innings': [{'overs': []}]}
        result = CricsheetAuditor().audit_match(normalized, cricsheet)
        self.assertFalse(result.is_valid)


if __name__ == '__main__':
    unittest.main()
