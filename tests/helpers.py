"""Shared test setup: import paths, the committed packets and the hand-written fixture narrative."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PACKETS = json.loads((ROOT / "data" / "case_packets.json").read_text())


def packet(case_id="CASE-0033"):
    return copy.deepcopy(next(p for p in PACKETS["packets"] if p["case_id"] == case_id))


def good_narrative():
    data = json.loads((ROOT / "tests" / "fixtures" / "good_narrative_CASE-0033.json").read_text())
    data.pop("_note", None)
    return data


def sentence(text, *citations):
    return {"text": text, "citations": list(citations)}


def draft_record(narrative, case_id="CASE-0033", generated_at="2026-11-25T12:00:00Z"):
    return {"case_id": case_id, "prompt_version": "v1", "model_requested": "claude-opus-5-5",
            "model_served": "claude-opus-5-5", "fallback_used": False, "effort": "high",
            "generated_at": generated_at, "stop_reason": "end_turn",
            "usage": {"input_tokens": 3000, "output_tokens": 2000}, "estimated_cost_usd": 0.052,
            "narrative": narrative, "error": None}
