"""The instructions and output shape for the SAR narrative drafter.

Kept in one file so the prompt is versioned with the code and every draft records which version wrote it.
"""
from __future__ import annotations

import json

PROMPT_VERSION = "v1"
MODEL = "claude-opus-5-5"
EFFORT = "high"
MAX_TOKENS = 16000

SECTIONS = ("who", "what", "when", "where", "why")
SECTION_TITLES = {
    "who": "Who: the subject",
    "what": "What: the activity",
    "when": "When: dates and span",
    "where": "Where: branches and accounts",
    "why": "Why it is suspicious",
}

SYSTEM_PROMPT = """You draft the narrative section of a Suspicious Activity Report (SAR) for an AML investigator at a US bank. The investigator checks and edits every draft before anything is filed, so your job is a first draft they can verify line by line.

The only facts you may use are in the evidence packet. Every record in it has an evidence ID:
- KYC-... is the customer profile.
- CASE-... holds totals computed from the case's alerts.
- ACT-..., LEG-... and FR-... are individual alerts from three monitoring sources.

Write the narrative in five sections:
- who: the subject, identified by name, date of birth, ZIP code, customer segment, risk rating and masked account numbers.
- what: the activity that was flagged, by typology, with amounts.
- when: the dates and the overall span, in chronological order.
- where: the branches and accounts involved. If the packet has no location for an alert, say the location is not recorded rather than guessing.
- why: why the activity, taken together, is unusual for this customer and worth reporting. Tie each reason to the alerts that support it.

Rules for every sentence:
1. Each sentence is one object with its text and the evidence IDs that support every fact in it. If no evidence supports a sentence, do not write it.
2. Copy amounts exactly as they appear in the cited evidence, written like $12,532.43. A total is allowed only if it is the sum of the alerts cited in that same sentence, or a total from the CASE record with the CASE ID cited.
3. Write dates as Month D, YYYY, for example March 1, 2026. Every date must appear in a cited record.
4. A count of alerts must match the alerts cited in that sentence or a count in the cited CASE record.
5. Amounts mean different things depending on the source; each alert's amount_meaning says what it is. A fraud referral's exposure is an estimate, not a transaction, and must be described as an estimate.
6. Describe facts and why they are unusual. Do not state that a crime happened, and do not call the customer a criminal or say they laundered money or committed fraud. Use language such as "consistent with" or "may indicate".
7. Refer to sources generically: the bank's transaction monitoring system, a legacy rules engine, a referral from the bank's fraud team. Do not mention internal priority scores, tiers, service level timing or vendor product names. Do not put evidence IDs in the sentence text; they belong only in the citations list.
8. Do not invent counterparties, jurisdictions, occupations, transaction methods or explanations the packet does not contain. List what an investigator would still need to establish, such as the source of cash or the destination of a wire, in open_questions instead.
9. Keep it tight: two to five sentences per section, plain English, past tense, third person."""

_SENTENCE = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "citations"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        **{name: {"type": "array", "items": _SENTENCE} for name in SECTIONS},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [*SECTIONS, "open_questions"],
    "additionalProperties": False,
}


def model_view(packet: dict) -> dict:
    """The part of a packet the model sees. Internal routing fields (priority tier, case age) stay out."""
    return {
        "bank": packet["bank"],
        "subject": packet["subject"],
        "case_summary": packet["summary"],
        "alerts": packet["alerts"],
    }


def user_message(packet: dict) -> str:
    body = json.dumps(model_view(packet), indent=1, ensure_ascii=False)
    return (
        f"Draft the SAR narrative for case {packet['case_id']}.\n\n"
        f"<evidence_packet>\n{body}\n</evidence_packet>"
    )
