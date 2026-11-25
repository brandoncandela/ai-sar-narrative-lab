"""Test the evaluator by breaking real drafts on purpose and checking it notices.

Each fault copies a draft, plants one known error and runs the evaluator again.
A fault counts as caught only if the check built for it flags the change.
The choice of sentence is seeded, so the same drafts always get the same faults.
"""
from __future__ import annotations

import copy
import random
import re
from datetime import date, timedelta

from evaluate import AMOUNT, COUNT, FULL_DATE, NUMBER_WORDS, evaluate_draft, find_dates
from prompt import SECTIONS
from case_packets import evidence_index

SEED = 7

FAULTS = {
    "invented_amount": ("Change a dollar amount to one that is not in the evidence", "amounts_supported"),
    "fake_citation": ("Cite an alert ID that does not exist", "citations_exist"),
    "dropped_citations": ("Remove every citation from a sentence", "sentence_cited"),
    "missing_section": ("Empty one of the five required sections", "sections_present"),
    "shifted_date": ("Move a date by nine days", "dates_supported"),
    "wrong_citation": ("Swap a sentence's alert for a different alert in the same case", "amounts_supported"),
    "inflated_count": ("Raise an alert count by two", "counts_supported"),
    "conclusory_claim": ("Add a sentence saying the customer laundered the funds", "conclusory_language"),
}


def _sentences(narrative: dict):
    for name in SECTIONS:
        for i, s in enumerate(narrative.get(name) or []):
            yield name, i, s


def _pick(rng: random.Random, candidates: list):
    return rng.choice(candidates) if candidates else None


def _blank(regex, text: str) -> str:
    """Hide regex matches with spaces of the same length so positions still line up with the original text."""
    return regex.sub(lambda m: " " * len(m.group(0)), text)


def _format_like(written: str, value: float) -> str:
    return f"${value:,.2f}" if "." in written else f"${value:,.0f}"


def inject(kind: str, narrative: dict, packet: dict, rng: random.Random) -> dict | None:
    """A copy of the narrative with one planted fault, or None if this draft has nowhere to plant it."""
    n = copy.deepcopy(narrative)
    index = evidence_index(packet)
    alert_ids = [a["evidence_id"] for a in packet["alerts"]]
    sentences = list(_sentences(n))

    if kind == "invented_amount":
        target = _pick(rng, [(sec, i, s) for sec, i, s in sentences if AMOUNT.search(find_dates(s["text"])[2])])
        if not target:
            return None
        sec, i, s = target
        m = AMOUNT.search(find_dates(s["text"])[2])
        bogus = _format_like(m.group(0), 4817.36 + rng.randint(1, 900))
        s["text"] = s["text"][: m.start()] + bogus + s["text"][m.end():]
        return n

    if kind == "fake_citation":
        target = _pick(rng, sentences)
        target[2]["citations"] = list(target[2]["citations"]) + ["ACT-999999"]
        return n

    if kind == "dropped_citations":
        target = _pick(rng, [t for t in sentences if t[2]["citations"]])
        if not target:
            return None
        target[2]["citations"] = []
        return n

    if kind == "missing_section":
        n[rng.choice(SECTIONS)] = []
        return n

    if kind == "shifted_date":
        target = _pick(rng, [t for t in sentences if FULL_DATE.search(t[2]["text"])])
        if not target:
            return None
        s = target[2]
        m = FULL_DATE.search(s["text"])
        written = find_dates(m.group(0))[0][0][1]
        cited_dates = {v for c in s["citations"] if c in index for v in _dates_in(index[c])}
        shifted = written + timedelta(days=9)
        while shifted in cited_dates:
            shifted += timedelta(days=1)
        s["text"] = s["text"][: m.start()] + f"{shifted:%B} {shifted.day}, {shifted.year}" + s["text"][m.end():]
        return n

    if kind == "wrong_citation":
        options = []
        for sec, i, s in sentences:
            alerts = [c for c in s["citations"] if c in alert_ids]
            amounts = AMOUNT.findall(find_dates(s["text"])[2])
            if len(alerts) == 1 and len(s["citations"]) == 1 and amounts:
                own = index[alerts[0]]["amount"]
                others = [a for a in alert_ids if a != alerts[0] and abs(index[a]["amount"] - own) > 1]
                if others:
                    options.append((s, others))
        target = _pick(rng, options)
        if not target:
            return None
        s, others = target
        s["citations"] = [rng.choice(others)]
        return n

    if kind == "inflated_count":
        target = _pick(rng, [t for t in sentences if COUNT.search(_blank(AMOUNT, t[2]["text"]))])
        if not target:
            return None
        s = target[2]
        m = COUNT.search(_blank(AMOUNT, s["text"]))
        token = m.group(1)
        value = int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]
        s["text"] = s["text"][: m.start(1)] + str(value + 2) + s["text"][m.end(1):]
        return n

    if kind == "conclusory_claim":
        n["why"] = list(n.get("why") or []) + [{"text": "The customer laundered these funds through the bank.",
                                                "citations": alert_ids[:1]}]
        return n

    raise ValueError(kind)


def _dates_in(record: dict):
    for v in record.values():
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            yield date.fromisoformat(v)


def run(drafts: dict[str, dict], packets: dict[str, dict], seed: int = SEED) -> dict:
    """Plant every fault type in every draft that has room for it; report how many the evaluator caught."""
    rng = random.Random(seed)
    totals = {k: {"label": label, "check": check, "planted": 0, "caught": 0} for k, (label, check) in FAULTS.items()}
    examples = []
    for case_id in sorted(drafts):
        narrative = drafts[case_id]
        if not isinstance(narrative, dict):
            continue
        packet = packets[case_id]
        for kind, (_label, check) in FAULTS.items():
            broken = inject(kind, narrative, packet, rng)
            if broken is None:
                continue
            result = evaluate_draft(broken, packet)
            caught = result["checks"][check]["issues"] > 0
            totals[kind]["planted"] += 1
            totals[kind]["caught"] += caught
            if not caught:
                examples.append({"case_id": case_id, "fault": kind})
    planted = sum(t["planted"] for t in totals.values())
    caught = sum(t["caught"] for t in totals.values())
    return {"seed": seed, "planted": planted, "caught": caught, "by_fault": totals, "missed": examples}
