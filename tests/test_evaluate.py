import unittest

from helpers import good_narrative, packet, sentence
from evaluate import evaluate_draft, find_amounts, find_counts, find_dates, summarize


def issues(result, check):
    return [i for rows in result["sections"].values() for row in rows for i in row["issues"] if i["check"] == check] + \
        [i for i in result["draft_issues"] if i["check"] == check]


def with_sentence(section, s):
    n = good_narrative()
    n[section] = n[section] + [s]
    return n


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.p = packet("CASE-0033")

    def test_a_grounded_draft_passes_cleanly(self):
        r = evaluate_draft(good_narrative(), self.p)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["cited_alerts"], 5)
        self.assertGreaterEqual(r["values_checked"], 12)

    def test_invented_amount_fails(self):
        r = evaluate_draft(with_sentence("what", sentence("A wire of $8,200.00 was also sent.", "LEG-01581")), self.p)
        self.assertEqual(r["verdict"], "fail")
        self.assertTrue(issues(r, "amounts_supported"))

    def test_amount_cited_to_the_wrong_alert_fails(self):
        r = evaluate_draft(with_sentence("what", sentence("The rapid movement transaction was $6,452.00.", "ACT-263121")), self.p)
        self.assertTrue(issues(r, "amounts_supported"))

    def test_sums_of_cited_alerts_and_rounded_totals_are_accepted(self):
        n = with_sentence("what", sentence("The two monitoring alerts totaled $1,228.96.", "ACT-263121", "ACT-262710"))
        n["why"].append(sentence("In all about $11,596 was flagged.", "CASE-0033"))
        self.assertEqual(evaluate_draft(n, self.p)["verdict"], "pass")

    def test_a_sum_that_needs_an_uncited_alert_fails(self):
        r = evaluate_draft(with_sentence("what", sentence("Together they totaled $7,680.96.", "ACT-263121", "ACT-262710")), self.p)
        self.assertTrue(issues(r, "amounts_supported"))

    def test_citation_that_does_not_exist_fails(self):
        r = evaluate_draft(with_sentence("what", sentence("Another alert was raised.", "ACT-000001")), self.p)
        self.assertTrue(issues(r, "citations_exist"))

    def test_uncited_sentence_fails(self):
        r = evaluate_draft(with_sentence("why", sentence("The customer appears to be a money mule.")), self.p)
        self.assertTrue(issues(r, "sentence_cited"))

    def test_missing_section_and_missing_draft_fail(self):
        n = good_narrative()
        n["where"] = []
        self.assertTrue(issues(evaluate_draft(n, self.p), "sections_present"))
        self.assertEqual(evaluate_draft(None, self.p)["verdict"], "fail")

    def test_dates(self):
        bad = evaluate_draft(with_sentence("when", sentence("A wire was flagged on April 14, 2026.", "LEG-01581")), self.p)
        self.assertTrue(issues(bad, "dates_supported"))
        ok = with_sentence("when", sentence("The wires were flagged in April 2026, on 04/13/2026.", "LEG-01581"))
        ok["when"].append(sentence("Activity ran from April 5 to October 15, 2026.", "CASE-0033"))
        self.assertEqual(evaluate_draft(ok, self.p)["verdict"], "pass")
        month = evaluate_draft(with_sentence("when", sentence("The wires were flagged in May 2026.", "LEG-01581")), self.p)
        self.assertTrue(issues(month, "dates_supported"))

    def test_counts(self):
        r = evaluate_draft(with_sentence("when", sentence("Four alerts were raised in total.", "CASE-0033")), self.p)
        self.assertTrue(issues(r, "counts_supported"))
        ok = with_sentence("what", sentence("Two monitoring alerts concerned payments.", "ACT-263121", "ACT-262710"))
        self.assertEqual(evaluate_draft(ok, self.p)["verdict"], "pass")

    def test_identifiers(self):
        for text in ("Activity occurred at branch NYC03.", "Funds moved through the account ending 9999.", "The customer lives in ZIP 10016."):
            r = evaluate_draft(with_sentence("where", sentence(text, "KYC-00576315", "ACT-263121")), self.p)
            self.assertTrue(issues(r, "identifiers_supported"), text)

    def test_style_problems_warn_without_failing(self):
        r = evaluate_draft(with_sentence("why", sentence("The customer laundered the funds, see ACT-263121.", "ACT-263121")), self.p)
        self.assertEqual(r["verdict"], "warn")
        self.assertTrue(issues(r, "conclusory_language"))
        self.assertTrue(issues(r, "ids_in_text"))
        r = evaluate_draft(with_sentence("why", sentence("Actimize gave it a high priority score.", "ACT-263121")), self.p)
        self.assertTrue(issues(r, "internal_terms"))

    def test_uncited_alerts_are_reported(self):
        n = good_narrative()
        n["what"] = n["what"][:1]
        n["when"] = n["when"][1:]
        n["where"] = n["where"][:1]
        n["why"] = n["why"][2:]
        r = evaluate_draft(n, self.p)
        self.assertEqual(r["verdict"], "warn")
        self.assertEqual({i["detail"].split()[0] for i in issues(r, "alert_coverage")}, {"LEG-00739"})

    def test_rule_thresholds_and_rounded_exposures_count_as_evidence(self):
        p = packet("CASE-0197")
        fr = next(a for a in p["alerts"] if a["evidence_id"].startswith("FR-"))
        n = {k: [sentence("The subject is a customer of the bank.", p["subject"]["evidence_id"])] for k in ("who", "what", "when", "where", "why")}
        n["what"].append(sentence(f"The fraud team estimated exposure at {fr['exposure_as_reported']}.", fr["evidence_id"]))
        self.assertEqual(evaluate_draft(n, p)["checks"]["amounts_supported"]["issues"], 0)
        structuring = next((q for q in __import__("helpers").PACKETS["packets"]
                            for a in q["alerts"] if a.get("rule_name") == "Cash deposits over 10k in 3 days"), None)
        if structuring:
            a = next(a for a in structuring["alerts"] if a.get("rule_name") == "Cash deposits over 10k in 3 days")
            m = {k: [sentence("The rule looks for cash deposits over $10,000 in three days.", a["evidence_id"])] for k in ("who", "what", "when", "where", "why")}
            self.assertEqual(evaluate_draft(m, structuring)["checks"]["amounts_supported"]["issues"], 0)

    def test_parsers(self):
        self.assertEqual([v for _, v, _ in find_amounts("$1,974 and $546.01 and $7.9k")], [1974.0, 546.01, 7900.0])
        self.assertEqual([n for _, n in find_counts("one of the five alerts and 3 legacy alerts")], [5, 3])
        full, month_year, _ = find_dates("June 3, 1988 and 2026-04-05 and October 2026")
        self.assertEqual(len(full), 2)
        self.assertEqual(month_year[0][1:], (2026, 10))

    def test_summary_counts_verdicts(self):
        good = evaluate_draft(good_narrative(), self.p)
        bad = evaluate_draft(None, self.p)
        s = summarize([good, bad])
        self.assertEqual((s["drafts"], s["pass"], s["fail"]), (2, 1, 1))
        self.assertEqual(s["by_check"]["sections_present"]["drafts_passing"], 1)


if __name__ == "__main__":
    unittest.main()
