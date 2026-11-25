import random
import unittest

from helpers import good_narrative, packet, sentence
import faults


class FaultInjectionTests(unittest.TestCase):
    def test_every_planted_fault_is_caught_on_a_grounded_draft(self):
        report = faults.run({"CASE-0033": good_narrative()}, {"CASE-0033": packet()})
        self.assertEqual(report["planted"], len(faults.FAULTS))
        self.assertEqual(report["caught"], report["planted"], report["missed"])

    def test_same_seed_same_faults(self):
        a = faults.run({"CASE-0033": good_narrative()}, {"CASE-0033": packet()}, seed=3)
        b = faults.run({"CASE-0033": good_narrative()}, {"CASE-0033": packet()}, seed=3)
        self.assertEqual(a, b)

    def test_faults_change_a_copy_not_the_original(self):
        original = good_narrative()
        before = repr(original)
        faults.inject("invented_amount", original, packet(), random.Random(1))
        self.assertEqual(repr(original), before)

    def test_skips_faults_with_nowhere_to_go(self):
        bare = {k: [sentence("The subject is a customer of the bank.", "KYC-00576315")] for k in ("who", "what", "when", "where", "why")}
        rng = random.Random(1)
        for kind in ("invented_amount", "shifted_date", "inflated_count", "wrong_citation"):
            self.assertIsNone(faults.inject(kind, bare, packet(), rng), kind)

    def test_drafts_without_a_narrative_are_ignored(self):
        report = faults.run({"CASE-0033": None}, {"CASE-0033": packet()})
        self.assertEqual(report["planted"], 0)


if __name__ == "__main__":
    unittest.main()
