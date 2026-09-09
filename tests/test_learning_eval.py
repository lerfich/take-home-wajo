import unittest

from mail_agent.learning_eval import metrics


class LearningMetricsTests(unittest.TestCase):
    def test_incomplete_and_errors_do_not_count_as_passes(self):
        rows = [{"phase": "train"}, {"phase": "test", "error": "429"}]
        self.assertEqual(metrics(rows)["completed_tests"], 0)
        self.assertEqual(metrics(rows)["matched_tests"], 0)

    def test_wrong_archive_is_counted_separately_from_questions(self):
        row = {"phase": "test", "expected_auto_archive": False, "archived_after": True,
               "matched": False, "before": {"autonomy": "ask"}, "after": {"autonomy": "notify"}}
        result = metrics([row])
        self.assertEqual(result["questions_after"], 0)
        self.assertEqual(result["incorrect_auto_archives"], 1)
        self.assertEqual(result["matched_tests"], 0)
