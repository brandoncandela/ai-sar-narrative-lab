"""Human review of the drafts: accept, edit or reject with a reason, and where the reviewer and the evaluator disagree.

Reviews are made in the demo page (Review panel), downloaded as JSON and imported here:

  python3 src/review.py import ~/Downloads/sar-review-log.json
  python3 src/review.py summary

The log in reviews/review_log.json is append only. The latest review of a case is the one that counts,
and a review is set aside as stale if the draft was regenerated after it was made.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import evaluate_draft  # noqa: E402
from prompt import SECTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "reviews" / "review_log.json"

DECISIONS = ("accept", "edit", "reject")
REJECT_REASONS = {
    "unsupported_claim": "States something the evidence does not support",
    "missing_activity": "Leaves out flagged activity",
    "wrong_typology": "Describes the activity as the wrong typology",
    "conclusory": "Draws a legal conclusion",
    "unclear": "Hard to follow or badly ordered",
    "other": "Other",
}
EDIT_REASONS = {
    "wording": "Wording or tone",
    "added_context": "Added context that is in the evidence",
    "removed_claim": "Removed an unsupported or unneeded claim",
    "fixed_citation": "Fixed a citation",
    "other": "Other",
}
DISAGREEMENTS = {
    "agree": "Reviewer and evaluator agree",
    "evaluator_missed": "Evaluator passed it, reviewer rejected it",
    "evaluator_overruled": "Evaluator failed it, reviewer accepted it",
    "edited_passing_draft": "Evaluator passed it, reviewer still edited it",
    "edited_failing_draft": "Evaluator failed it, reviewer fixed it by editing",
}


def validate(entry: dict, drafts: dict[str, dict]) -> list[str]:
    """Problems with one review entry; an empty list means it is valid."""
    problems = []
    case_id = entry.get("case_id")
    if case_id not in drafts:
        problems.append(f"unknown case {case_id!r}")
    decision = entry.get("decision")
    if decision not in DECISIONS:
        problems.append(f"decision must be one of {', '.join(DECISIONS)}")
    if not str(entry.get("reviewer") or "").strip():
        problems.append("reviewer name is required")
    try:
        datetime.fromisoformat(str(entry.get("reviewed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        problems.append("reviewed_at must be an ISO timestamp")
    reason = str(entry.get("reason") or "").strip()
    if decision == "reject":
        if entry.get("reason_code") not in REJECT_REASONS:
            problems.append("a rejection needs a reason code")
        if not reason:
            problems.append("a rejection needs a written reason")
    if decision == "edit":
        if entry.get("reason_code") not in EDIT_REASONS:
            problems.append("an edit needs a reason code")
        edited = entry.get("edited_narrative")
        if not isinstance(edited, dict) or any(not isinstance(edited.get(s), list) for s in SECTIONS):
            problems.append("an edit needs the edited narrative with all five sections")
    return problems


def load_log(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("reviews", [])


def save_log(reviews: list[dict], path: Path = LOG) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"reviews": reviews}, indent=1, ensure_ascii=False) + "\n")


def merge(existing: list[dict], incoming: list[dict], drafts: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Append valid new entries. Exact duplicates are skipped. Returns the new log and the problems found."""
    seen = {json.dumps(e, sort_keys=True) for e in existing}
    merged = list(existing)
    problems = []
    for i, entry in enumerate(incoming):
        issues = validate(entry, drafts)
        if issues:
            problems.append(f"entry {i + 1} ({entry.get('case_id')}): {'; '.join(issues)}")
            continue
        key = json.dumps(entry, sort_keys=True)
        if key not in seen:
            merged.append(entry)
            seen.add(key)
    merged.sort(key=lambda e: (e["reviewed_at"], e["case_id"]))
    return merged, problems


def latest(reviews: list[dict], drafts: dict[str, dict]) -> tuple[dict[str, dict], list[dict]]:
    """The latest current review per case, and the reviews set aside because their draft was regenerated."""
    current: dict[str, dict] = {}
    stale = []
    for r in sorted(reviews, key=lambda e: e["reviewed_at"]):
        draft = drafts.get(r["case_id"])
        if draft is None:
            continue
        if r.get("draft_generated_at") and r["draft_generated_at"] != draft.get("generated_at"):
            stale.append(r)
            continue
        current[r["case_id"]] = r
    return current, stale


def classify(verdict: str, decision: str) -> str:
    passed = verdict in ("pass", "warn")
    if decision == "accept":
        return "agree" if passed else "evaluator_overruled"
    if decision == "reject":
        return "evaluator_missed" if passed else "agree"
    return "edited_passing_draft" if passed else "edited_failing_draft"


def changed_sentences(original: dict, edited: dict) -> int:
    count = 0
    for s in SECTIONS:
        before = [x.get("text", "") for x in original.get(s) or []]
        after = [x.get("text", "") for x in edited.get(s) or []]
        count += sum(1 for i in range(max(len(before), len(after)))
                     if (before[i] if i < len(before) else None) != (after[i] if i < len(after) else None))
    return count


def disagreements(reviews: list[dict], drafts: dict[str, dict], evaluations: dict[str, dict], packets: dict[str, dict]) -> dict:
    """Join each case's latest review with its evaluation verdict and re-score any edited narrative."""
    current, stale = latest(reviews, drafts)
    rows = []
    for case_id, r in sorted(current.items()):
        verdict = evaluations[case_id]["verdict"]
        row = {
            "case_id": case_id,
            "decision": r["decision"],
            "reason_code": r.get("reason_code"),
            "reason": r.get("reason", ""),
            "reviewer": r["reviewer"],
            "reviewed_at": r["reviewed_at"],
            "evaluator_verdict": verdict,
            "outcome": classify(verdict, r["decision"]),
        }
        if r["decision"] == "edit":
            edited_eval = evaluate_draft(r["edited_narrative"], packets[case_id])
            row["edited_verdict"] = edited_eval["verdict"]
            row["sentences_changed"] = changed_sentences(drafts[case_id].get("narrative") or {}, r["edited_narrative"])
        rows.append(row)
    counts = {k: sum(1 for x in rows if x["outcome"] == k) for k in DISAGREEMENTS}
    decisions = {k: sum(1 for x in rows if x["decision"] == k) for k in DECISIONS}
    reasons: dict[str, int] = {}
    for x in rows:
        if x["decision"] in ("reject", "edit") and x["reason_code"]:
            reasons[x["reason_code"]] = reasons.get(x["reason_code"], 0) + 1
    return {"reviewed": len(rows), "stale": len(stale), "decisions": decisions, "outcomes": counts,
            "reasons": dict(sorted(reasons.items())), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import and summarize human reviews.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="merge a review log downloaded from the demo")
    imp.add_argument("file", type=Path)
    sub.add_parser("summary", help="print decisions and disagreements")
    args = parser.parse_args(argv)

    from evaluate import load_drafts

    drafts = load_drafts(ROOT / "drafts")
    if args.cmd == "import":
        data = json.loads(args.file.read_text())
        incoming = data.get("reviews", data if isinstance(data, list) else [])
        merged, problems = merge(load_log(), incoming, drafts)
        for p in problems:
            print("Skipped", p, file=sys.stderr)
        save_log(merged)
        print(f"Review log now has {len(merged)} entries ({len(problems)} skipped).")
        return 1 if problems else 0

    packets = {p["case_id"]: p for p in json.loads((ROOT / "data" / "case_packets.json").read_text())["packets"]}
    evaluations = {r["case_id"]: r for r in json.loads((ROOT / "results" / "evaluation.json").read_text())["results"]}
    d = disagreements(load_log(), drafts, evaluations, packets)
    print(f"{d['reviewed']} drafts reviewed ({d['stale']} stale reviews set aside).")
    for k, label in DISAGREEMENTS.items():
        print(f"  {label:<50} {d['outcomes'][k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
