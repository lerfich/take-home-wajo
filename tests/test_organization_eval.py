import unittest

from mail_agent.organization_eval import metrics


class OrganizationEvalTests(unittest.TestCase):
    def test_metrics_separate_kind_transfer_and_false_importance(self):
        rows = [
            {"expected": {"important": True}, "kind_matches": True,
             "before": {"important": False}, "after": {"important": True},
             "before_matches": False, "after_matches": True},
            {"expected": {"important": False}, "kind_matches": False,
             "before": {"important": False}, "after": {"important": True},
             "before_matches": True, "after_matches": False},
            {"expected": {"important": False}, "error": "ProviderError"},
        ]
        result = metrics(rows)
        self.assertEqual(result, {"attempted": 3, "completed": 2, "kind_matches": 1,
                         "before_matches": 1, "after_matches": 1, "expected_important": 1,
                         "important_transferred_before": 0, "important_transferred_after": 1,
                         "false_important_before": 0, "false_important_after": 1,
                         "provider_errors": 1})


if __name__ == "__main__":
    unittest.main()
