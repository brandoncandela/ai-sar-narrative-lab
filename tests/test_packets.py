import json
import re
import unittest

from helpers import PACKETS, ROOT
from case_packets import evidence_index, mask_account, select_cases, build

BACKLOG = ROOT.parent / "aml-backlog-command-center"


class PacketTests(unittest.TestCase):
    def test_sample_size_and_variety(self):
        packets = PACKETS["packets"]
        self.assertEqual(len(packets), 24)
        self.assertGreaterEqual(len({p["primary_typology"] for p in packets}), 8)
        for p in packets:
            self.assertIn(p["priority_tier"], ("P1", "P2"))
            self.assertTrue(3 <= len(p["alerts"]) <= 10)

    def test_evidence_ids_are_unique_and_well_formed(self):
        for p in PACKETS["packets"]:
            ids = [a["evidence_id"] for a in p["alerts"]] + [p["subject"]["evidence_id"], p["summary"]["evidence_id"]]
            self.assertEqual(len(ids), len(set(ids)), p["case_id"])
            for i in ids:
                self.assertRegex(i, r"^(ACT-\d{6}|LEG-\d{5}|FR-\d{4}|KYC-\d{8}|CASE-\d{4})$")
            self.assertEqual(set(evidence_index(p)), set(ids))

    def test_case_totals_are_worked_out_from_the_alerts(self):
        for p in PACKETS["packets"]:
            s, alerts = p["summary"], p["alerts"]
            self.assertEqual(s["alert_count"], len(alerts))
            self.assertAlmostEqual(s["total_amount"], sum(a["amount"] for a in alerts), places=2)
            self.assertEqual(s["first_alert_date"], min(a["date"] for a in alerts))
            self.assertEqual(s["last_alert_date"], max(a["date"] for a in alerts))
            self.assertEqual(sum(s["alerts_by_typology"].values()), len(alerts))
            self.assertEqual([a["date"] for a in alerts], sorted(a["date"] for a in alerts))

    def test_every_alert_says_what_its_amount_means(self):
        for p in PACKETS["packets"]:
            for a in p["alerts"]:
                self.assertTrue(a["amount_meaning"])
                if a["evidence_id"].startswith("FR-"):
                    self.assertIn("estimated exposure", a["amount_meaning"])

    def test_no_full_account_numbers(self):
        self.assertEqual(mask_account("1261080655"), "account ending 0655")
        text = json.dumps(PACKETS)
        self.assertIsNone(re.search(r"\b\d{10}\b", text), "an unmasked account number is in the packets")

    def test_selection_is_deterministic_and_respects_eligibility(self):
        def case(i, tier, n, typ):
            return {"case_id": f"CASE-{i:04d}", "tier": tier, "alerts": [{}] * n,
                    "score_parts": [[f"Most severe typology: {typ}", 25]]}
        cases = [case(i, "P1" if i % 3 else "P3", 2 + i % 9, ["a", "b", "c"][i % 3]) for i in range(1, 200)]
        first, second = select_cases(cases, size=10, seed=1), select_cases(cases, size=10, seed=1)
        self.assertEqual([c["case_id"] for c in first], [c["case_id"] for c in second])
        self.assertTrue(all(c["tier"] != "P3" and 3 <= len(c["alerts"]) <= 10 for c in first))

    @unittest.skipUnless((BACKLOG / "dist" / "data" / "command_center.json").exists(), "backlog repo not cloned alongside")
    def test_rebuild_from_the_backlog_repo_matches_the_committed_packets(self):
        rebuilt = json.loads(json.dumps(build(BACKLOG)))
        self.assertEqual(rebuilt, PACKETS)


if __name__ == "__main__":
    unittest.main()
