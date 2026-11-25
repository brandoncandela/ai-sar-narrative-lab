"""Score the saved drafts and write everything the static demo needs.

Reads data/case_packets.json, drafts/ and reviews/review_log.json. Writes results/*.json and dist/data/lab.json.
No network and no API key: the demo only ever shows what was generated and committed.
Run: python3 src/build.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import faults  # noqa: E402
import prompt  # noqa: E402
import review  # noqa: E402
from evaluate import evaluate_draft, load_drafts, summarize  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n")


def build(root: Path = ROOT) -> dict:
    packet_data = json.loads((root / "data" / "case_packets.json").read_text())
    packets = {p["case_id"]: p for p in packet_data["packets"]}
    drafts = {cid: d for cid, d in load_drafts(root / "drafts").items() if cid in packets}
    evaluations = {cid: evaluate_draft(drafts[cid].get("narrative") if cid in drafts else None, packets[cid])
                   for cid in packets}
    drafted = {cid: e for cid, e in evaluations.items() if cid in drafts}
    summary = summarize(list(drafted.values()))
    fault_report = faults.run({cid: d.get("narrative") for cid, d in drafts.items()}, packets)
    reviews = review.disagreements(review.load_log(root / "reviews" / "review_log.json"), drafts, evaluations, packets)

    usage = {"input_tokens": sum(d["usage"]["input_tokens"] for d in drafts.values()),
             "output_tokens": sum(d["usage"]["output_tokens"] for d in drafts.values()),
             "estimated_cost_usd": round(sum(d.get("estimated_cost_usd") or 0 for d in drafts.values()), 2)}
    stamps = sorted(d["generated_at"] for d in drafts.values())
    cases = []
    for cid, packet in packets.items():
        d = drafts.get(cid)
        cases.append({
            "case_id": cid,
            "primary_typology": packet["primary_typology"],
            "subject": packet["subject"],
            "summary": packet["summary"],
            "alerts": packet["alerts"],
            "draft": None if d is None else {
                "narrative": d.get("narrative"),
                "model_served": d["model_served"],
                "fallback_used": d.get("fallback_used", False),
                "stop_reason": d["stop_reason"],
                "error": d.get("error"),
                "generated_at": d["generated_at"],
                "usage": d["usage"],
            },
            "evaluation": evaluations[cid] if d else None,
        })

    lab = {
        "meta": {
            "model": prompt.MODEL,
            "effort": prompt.EFFORT,
            "prompt_version": prompt.PROMPT_VERSION,
            "drafts": len(drafts),
            "cases": len(packets),
            "eligible_cases": packet_data["eligible_cases"],
            "total_cases": packet_data["total_cases"],
            "selection": packet_data["selection"],
            "first_generated": stamps[0] if stamps else None,
            "last_generated": stamps[-1] if stamps else None,
            "fallbacks_used": sum(1 for d in drafts.values() if d.get("fallback_used")),
            "usage": usage,
        },
        "system_prompt": prompt.SYSTEM_PROMPT,
        "sections": [{"key": k, "title": prompt.SECTION_TITLES[k]} for k in prompt.SECTIONS],
        "evaluation": summary,
        "faults": fault_report,
        "reviews": reviews,
        "review_options": {"reject": review.REJECT_REASONS, "edit": review.EDIT_REASONS,
                           "outcomes": review.DISAGREEMENTS},
        "cases": cases,
    }
    dump(root / "results" / "evaluation.json", {"summary": summary, "results": list(drafted.values())})
    dump(root / "results" / "faults.json", fault_report)
    dump(root / "results" / "review_summary.json", reviews)
    dump(root / "dist" / "data" / "lab.json", lab)
    return lab


def main() -> int:
    lab = build()
    s, f = lab["evaluation"], lab["faults"]
    print(f"{lab['meta']['drafts']} of {lab['meta']['cases']} cases drafted.")
    if s["drafts"]:
        print(f"Evaluation: {s['pass']} pass, {s['warn']} pass with warnings, {s['fail']} fail; "
              f"{s['sentences']} sentences, {s['values_checked']} values checked.")
        print(f"Fault injection: {f['caught']} of {f['planted']} planted errors caught.")
    print(f"Reviews: {lab['reviews']['reviewed']} current.")
    print("Wrote dist/data/lab.json and results/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
