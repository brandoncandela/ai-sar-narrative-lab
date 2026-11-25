import copy
import unittest

from helpers import draft_record, good_narrative, packet, sentence
import review
from evaluate import evaluate_draft

DRAFTS = {"CASE-0033": draft_record(good_narrative())}


def entry(decision="accept", **extra):
    e = {"case_id": "CASE-0033", "decision": decision, "reviewer": "Test Reviewer",
         "reviewed_at": "2026-11-25T15:00:00Z", "draft_generated_at": "2026-11-25T12:00:00Z", "reason": ""}
    e.update(extra)
    return e


class ReviewTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(review.validate(entry(), DRAFTS), [])
        self.assertTrue(review.validate(entry("reject"), DRAFTS))
        self.assertTrue(review.validate(entry("reject", reason_code="unclear"), DRAFTS))
        self.assertEqual(review.validate(entry("reject", reason_code="unclear", reason="Why section is vague"), DRAFTS), [])
        self.assertTrue(review.validate(entry("edit", reason_code="wording", reason="x"), DRAFTS))
        self.assertTrue(review.validate(entry(case_id="CASE-9999"), DRAFTS))
        self.assertTrue(review.validate(entry(reviewer=" "), DRAFTS))
        self.assertTrue(review.validate(entry(decision="maybe"), DRAFTS))

    def test_merge_skips_invalid_and_duplicate_entries(self):
        good = entry()
        merged, problems = review.merge([], [good, good, entry("reject")], DRAFTS)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(problems), 1)
        merged, problems = review.merge(merged, [good], DRAFTS)
        self.assertEqual(len(merged), 1)

    def test_latest_review_wins_and_stale_reviews_are_set_aside(self):
        first = entry("reject", reason_code="unclear", reason="vague", reviewed_at="2026-11-25T13:00:00Z")
        second = entry("accept", reviewed_at="2026-11-25T14:00:00Z")
        stale = entry("accept", reviewed_at="2026-11-25T16:00:00Z", draft_generated_at="2026-11-20T00:00:00Z")
        current, set_aside = review.latest([first, second, stale], DRAFTS)
        self.assertEqual(current["CASE-0033"]["decision"], "accept")
        self.assertEqual(len(set_aside), 1)

    def test_classification_covers_every_combination(self):
        self.assertEqual(review.classify("pass", "accept"), "agree")
        self.assertEqual(review.classify("warn", "accept"), "agree")
        self.assertEqual(review.classify("fail", "reject"), "agree")
        self.assertEqual(review.classify("pass", "reject"), "evaluator_missed")
        self.assertEqual(review.classify("fail", "accept"), "evaluator_overruled")
        self.assertEqual(review.classify("pass", "edit"), "edited_passing_draft")
        self.assertEqual(review.classify("fail", "edit"), "edited_failing_draft")

    def test_edits_are_scored_again_by_the_same_evaluator(self):
        edited = copy.deepcopy(good_narrative())
        edited["what"][0] = sentence("The monitoring system flagged a $9,999.00 transaction.", "ACT-263121")
        p = packet()
        evaluations = {"CASE-0033": evaluate_draft(good_narrative(), p)}
        d = review.disagreements([entry("edit", reason_code="wording", reason="tightened", edited_narrative=edited)],
                                 DRAFTS, evaluations, {"CASE-0033": p})
        row = d["rows"][0]
        self.assertEqual(row["outcome"], "edited_passing_draft")
        self.assertEqual(row["edited_verdict"], "fail")
        self.assertEqual(row["sentences_changed"], 1)
        self.assertEqual(d["reasons"], {"wording": 1})


if __name__ == "__main__":
    unittest.main()
