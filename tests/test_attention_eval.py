import unittest

from mail_agent.attention_eval import metrics


class AttentionEvalTests(unittest.TestCase):
    def test_metrics_separate_attention_and_escalation_errors(self):
        rows = [
            {"expected": {"attention": True, "autonomy": "escalate"}, "cue_matches": True,
             "before": {"attention": False}, "after": {"attention": True, "autonomy": "escalate"}},
            {"expected": {"attention": False, "autonomy": "notify"}, "cue_matches": False,
             "before": {"attention": False}, "after": {"attention": True, "autonomy": "escalate"}},
            {"expected": {"attention": True, "autonomy": "silent"}, "cue_matches": True,
             "before": {"attention": False}, "after": {"attention": False, "autonomy": "silent"}},
            {"expected": {"attention": False, "autonomy": "silent"}, "error": "ProviderError"},
        ]
        result = metrics(rows)
        self.assertEqual(result, {
            "attempted": 4, "completed": 3, "cue_matches": 2, "expected_attention": 2,
            "attention_before": 0, "attention_true_positive_after": 1,
            "attention_missed_after": 1, "attention_false_positive_after": 1,
            "attention_true_negative_after": 0, "expected_escalations": 1,
            "escalation_matched": 1, "escalation_missed": 0, "false_escalations": 1,
            "provider_errors": 1})


if __name__ == "__main__":
    unittest.main()
