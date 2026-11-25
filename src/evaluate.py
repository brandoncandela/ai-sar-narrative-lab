"""Check every sentence of a SAR narrative draft against the evidence it cites.

No model is involved: these are plain rules, so the same draft always gets the same result.

Checks that fail a draft:
  sections_present       who, what, when, where and why each have at least one sentence
  sentence_cited         every sentence cites at least one evidence ID
  citations_exist        every cited ID is in the case packet
  amounts_supported      every dollar amount matches a cited record, or a sum of cited alerts
  dates_supported        every date appears in a cited record
  counts_supported       every "N alerts" matches the cited alerts or a count in a cited record
  identifiers_supported  every branch code, ZIP code and account ending appears in a cited record

Checks that only warn (a reviewer should look):
  conclusory_language    words that state a crime happened instead of describing activity
  ids_in_text            evidence IDs typed into the prose instead of the citations list
  internal_terms         priority scores, tiers, SLA timing or vendor names
  alert_coverage         alerts in the packet that the narrative never cites

Run: python3 src/evaluate.py   (reads drafts/, writes results/evaluation.json)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_packets import evidence_index  # noqa: E402
from prompt import SECTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

FAIL_CHECKS = ("sections_present", "sentence_cited", "citations_exist", "amounts_supported",
               "dates_supported", "counts_supported", "identifiers_supported")
WARN_CHECKS = ("conclusory_language", "ids_in_text", "internal_terms", "alert_coverage")
CHECK_LABELS = {
    "sections_present": "All five sections present",
    "sentence_cited": "Every sentence cites evidence",
    "citations_exist": "Every citation exists in the packet",
    "amounts_supported": "Amounts match cited evidence",
    "dates_supported": "Dates match cited evidence",
    "counts_supported": "Alert counts match cited evidence",
    "identifiers_supported": "Branches, ZIPs and accounts match",
    "conclusory_language": "No legal conclusions",
    "ids_in_text": "No evidence IDs in the prose",
    "internal_terms": "No internal scores or vendor names",
    "alert_coverage": "Every alert is cited",
}

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
_MONTH_ALT = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
FULL_DATE = re.compile(_MONTH_ALT + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
MONTH_YEAR = re.compile(r"\b" + _MONTH_ALT + r"\s+(?:of\s+)?(\d{4})\b", re.I)
AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?(?:\s?(k|thousand|million|m)\b)?", re.I)
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
_NUM = r"(?:\d+|" + "|".join(NUMBER_WORDS) + r")"
# "five alerts", "three structuring alerts", "two separate monitoring alerts"; never reads through "of" or another number
COUNT = re.compile(r"\b(" + _NUM + r")\s+(?:(?!(?:of|and|or|to|the)\b)(?!" + _NUM + r"\b)[a-z\-]+\s+){0,3}?alerts?\b", re.I)
DAY_MONTH = re.compile(_MONTH_ALT + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+\d{4})", re.I)
THOUSANDS = re.compile(r"\b(\d+(?:\.\d+)?)k\b", re.I)
ACCOUNT_ENDING = re.compile(r"\bending(?:\s+in)?\s+(\d{4})\b", re.I)
BRANCH = re.compile(r"\b([A-Z]{3}\d{2})\b")
ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
EVIDENCE_ID = re.compile(r"\b(?:ACT-\d{6}|LEG-\d{5}|FR-\d{4}|KYC-\d{8}|CASE-\d{4})\b")
CONCLUSORY = re.compile(
    r"\b(laundered|launderer|committed (?:fraud|a crime|money laundering)|is guilty|guilty of|criminal(?:s)?\b(?! activity)"
    r"|illegal(?:ly)?|proceeds of crime|fraudster|definitely|without (?:a )?doubt)\b", re.I)
INTERNAL = re.compile(r"\b(priority (?:score|tier)|tier P\d|P[123] case|SLA|service level|confidence score|Actimize|score parts?)\b", re.I)


def _walk(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def _iso(text: str) -> date | None:
    m = ISO_DATE.fullmatch(text) if isinstance(text, str) else None
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _month_number(token: str) -> int:
    token = token.lower().rstrip(".")
    for i, name in enumerate(MONTHS, 1):
        if name.startswith(token[:3]):
            return i
    raise ValueError(token)


def find_dates(text: str) -> tuple[list[tuple[str, date]], list[tuple[str, int, int]], str]:
    """Full dates, month-year mentions, and the text with dates blanked out (so their digits aren't read as amounts or ZIPs)."""
    full: list[tuple[str, date]] = []
    blanked = text

    def take(match, d):
        nonlocal blanked
        full.append((match.group(0), d))
        blanked = blanked.replace(match.group(0), " " * len(match.group(0)), 1)

    for m in FULL_DATE.finditer(text):
        try:
            take(m, date(int(m[3]), _month_number(m[1]), int(m[2])))
        except ValueError:
            full.append((m.group(0), None))
    for m in ISO_DATE.finditer(blanked):
        try:
            take(m, date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            full.append((m.group(0), None))
    for m in US_DATE.finditer(blanked):
        try:
            take(m, date(int(m[3]), int(m[1]), int(m[2])))
        except ValueError:
            full.append((m.group(0), None))
    for m in DAY_MONTH.finditer(blanked):
        try:
            # a month and day with the year left off, as in "between March 1 and March 28, 2026"
            full.append((m.group(0), (None, _month_number(m[1]), int(m[2]))))
        except ValueError:
            continue
        blanked = blanked.replace(m.group(0), " " * len(m.group(0)), 1)
    month_year = []
    for m in MONTH_YEAR.finditer(blanked):
        month_year.append((m.group(0), int(m[2]), _month_number(m[1])))
        blanked = blanked.replace(m.group(0), " " * len(m.group(0)), 1)
    return full, month_year, blanked


def find_amounts(text: str) -> list[tuple[str, float, float]]:
    """Dollar amounts as (as written, value, tolerance). Tolerance follows the precision the writer used."""
    out = []
    for m in AMOUNT.finditer(text):
        value = float(m[1].replace(",", "") + (m[2] or ""))
        scale = (m[3] or "").lower()
        if scale in ("k", "thousand"):
            value, tol = value * 1000, 50.0
        elif scale in ("m", "million"):
            value, tol = value * 1_000_000, 50_000.0
        elif m[2]:
            tol = 0.005
        else:
            tol = 1.0  # whole dollars: allow rounding of the cents
        out.append((m.group(0).strip(), round(value, 2), tol))
    return out


def find_counts(text: str) -> list[tuple[str, int]]:
    out = []
    for m in COUNT.finditer(text):
        token = m[1].lower()
        out.append((m.group(0), int(token) if token.isdigit() else NUMBER_WORDS[token]))
    return out


def allowed_values(records: list[dict], alerts: list[dict]) -> tuple[set[float], set[date], set[int], set[str]]:
    amounts: set[float] = set()
    dates: set[date] = set()
    counts: set[int] = {len(alerts)}
    strings: set[str] = set()
    for r in records:
        for v in _walk(r):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                amounts.add(round(float(v), 2))
                if isinstance(v, int):
                    counts.add(v)
            elif isinstance(v, str):
                strings.add(v)
                d = _iso(v)
                if d:
                    dates.add(d)
                # thresholds written into rule names ("over 10k") and exposures reported as "$7.9k"
                for m in THOUSANDS.finditer(v):
                    amounts.add(round(float(m[1]) * 1000, 2))
    alert_amounts = [a["amount"] for a in alerts if isinstance(a.get("amount"), (int, float))]
    if len(alert_amounts) <= 12:
        for k in range(2, len(alert_amounts) + 1):
            for combo in combinations(alert_amounts, k):
                amounts.add(round(sum(combo), 2))
    for key in ("scenario", "system"):
        groups: dict[str, int] = {}
        for a in alerts:
            groups[a.get(key)] = groups.get(a.get(key), 0) + 1
        counts.update(groups.values())
    return amounts, dates, counts, strings


def check_sentence(sentence: dict, index: dict[str, dict]) -> tuple[list[dict], int]:
    """Issues for one sentence (each {check, detail}) and how many amounts, dates, counts and identifiers it states."""
    issues = []
    checked = 0
    text = sentence.get("text", "") or ""
    cites = [c.strip() for c in sentence.get("citations", []) or [] if isinstance(c, str) and c.strip()]
    if not cites:
        issues.append({"check": "sentence_cited", "detail": "no evidence cited"})
    unknown = [c for c in cites if c not in index]
    for c in unknown:
        issues.append({"check": "citations_exist", "detail": f"{c} is not in the case packet"})
    records = [index[c] for c in cites if c in index]
    alerts = [r for r in records if "amount_meaning" in r]
    amounts, dates, counts, strings = allowed_values(records, alerts)

    full_dates, month_years, blanked = find_dates(EVIDENCE_ID.sub(lambda m: " " * len(m.group(0)), text))
    for written, d in full_dates:
        checked += 1
        if isinstance(d, tuple):  # month and day without a year
            ok = any(x.month == d[1] and x.day == d[2] for x in dates)
        else:
            ok = d is not None and d in dates
        if not ok:
            issues.append({"check": "dates_supported", "detail": f"{written} is not in the cited evidence"})
    for written, year, month in month_years:
        checked += 1
        if not any(d.year == year and d.month == month for d in dates):
            issues.append({"check": "dates_supported", "detail": f"{written} has no cited record in that month"})

    for written, value, tol in find_amounts(blanked):
        checked += 1
        if not any(abs(value - a) <= tol for a in amounts):
            issues.append({"check": "amounts_supported", "detail": f"{written} does not match the cited evidence"})
    no_amounts = AMOUNT.sub(" ", blanked)

    for written, n in find_counts(no_amounts):
        checked += 1
        if n not in counts:
            issues.append({"check": "counts_supported", "detail": f'"{written}" does not match the cited alerts'})

    joined = " ".join(strings)
    for m in ACCOUNT_ENDING.finditer(text):
        checked += 1
        if f"ending {m[1]}" not in joined:
            issues.append({"check": "identifiers_supported", "detail": f"account ending {m[1]} is not in the cited evidence"})
    for m in BRANCH.finditer(text):
        checked += 1
        if m[1] not in strings:
            issues.append({"check": "identifiers_supported", "detail": f"branch {m[1]} is not in the cited evidence"})
    for m in ZIP.finditer(ACCOUNT_ENDING.sub(" ", no_amounts)):
        checked += 1
        if m[1] not in strings:
            issues.append({"check": "identifiers_supported", "detail": f"{m[1]} is not a ZIP code or number in the cited evidence"})

    for m in CONCLUSORY.finditer(text):
        issues.append({"check": "conclusory_language", "detail": f'"{m.group(0)}" states a conclusion'})
    for m in EVIDENCE_ID.finditer(text):
        issues.append({"check": "ids_in_text", "detail": f"{m.group(0)} typed into the sentence"})
    for m in INTERNAL.finditer(text):
        issues.append({"check": "internal_terms", "detail": f'"{m.group(0)}" is an internal term'})
    return issues, checked


def evaluate_draft(narrative: dict | None, packet: dict) -> dict:
    """Full result for one draft: per sentence issues, per check totals and a verdict."""
    index = evidence_index(packet)
    checks = {name: {"label": CHECK_LABELS[name], "severity": "fail" if name in FAIL_CHECKS else "warn", "issues": 0}
              for name in FAIL_CHECKS + WARN_CHECKS}
    result = {"case_id": packet["case_id"], "sections": {}, "checks": checks, "draft_issues": [],
              "sentences": 0, "values_checked": 0, "cited_alerts": 0, "total_alerts": len(packet["alerts"])}
    if not isinstance(narrative, dict):
        result["draft_issues"].append({"check": "sections_present", "detail": "no draft to evaluate"})
        checks["sections_present"]["issues"] += 1
        result["verdict"] = "fail"
        return result

    cited_alerts: set[str] = set()
    for name in SECTIONS:
        sentences = narrative.get(name) or []
        if not isinstance(sentences, list) or not sentences:
            result["draft_issues"].append({"check": "sections_present", "detail": f"the {name} section is empty"})
            checks["sections_present"]["issues"] += 1
            sentences = []
        rows = []
        for s in sentences:
            if not isinstance(s, dict):
                s = {"text": str(s), "citations": []}
            issues, checked = check_sentence(s, index)
            for issue in issues:
                checks[issue["check"]]["issues"] += 1
            result["values_checked"] += checked
            cited_alerts.update(c for c in s.get("citations", []) or [] if c in index and "amount_meaning" in index[c])
            rows.append({"text": s.get("text", ""), "citations": s.get("citations", []), "issues": issues})
        result["sections"][name] = rows
        result["sentences"] += len(rows)

    uncited = [a["evidence_id"] for a in packet["alerts"] if a["evidence_id"] not in cited_alerts]
    for a in uncited:
        result["draft_issues"].append({"check": "alert_coverage", "detail": f"{a} is never cited"})
        checks["alert_coverage"]["issues"] += 1
    result["cited_alerts"] = len(cited_alerts)

    failed = any(c["issues"] for n, c in checks.items() if c["severity"] == "fail")
    warned = any(c["issues"] for n, c in checks.items() if c["severity"] == "warn")
    result["verdict"] = "fail" if failed else ("warn" if warned else "pass")
    return result


def summarize(results: list[dict]) -> dict:
    total = len(results)
    by_check = {}
    for name in FAIL_CHECKS + WARN_CHECKS:
        bad = sum(1 for r in results if r["checks"][name]["issues"])
        by_check[name] = {"label": CHECK_LABELS[name], "severity": "fail" if name in FAIL_CHECKS else "warn",
                          "drafts_passing": total - bad, "drafts": total,
                          "issues": sum(r["checks"][name]["issues"] for r in results)}
    return {
        "drafts": total,
        "pass": sum(r["verdict"] == "pass" for r in results),
        "warn": sum(r["verdict"] == "warn" for r in results),
        "fail": sum(r["verdict"] == "fail" for r in results),
        "sentences": sum(r["sentences"] for r in results),
        "values_checked": sum(r["values_checked"] for r in results),
        "by_check": by_check,
    }


def load_drafts(folder: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("CASE-*.json"))}


def main() -> int:
    packets = {p["case_id"]: p for p in json.loads((ROOT / "data" / "case_packets.json").read_text())["packets"]}
    drafts = load_drafts(ROOT / "drafts")
    if not drafts:
        print("No drafts yet. Run src/generate.py first.")
        return 1
    results = [evaluate_draft(d.get("narrative"), packets[cid]) for cid, d in drafts.items()]
    out = {"summary": summarize(results), "results": results}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "evaluation.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    s = out["summary"]
    print(f"{s['drafts']} drafts: {s['pass']} pass, {s['warn']} pass with warnings, {s['fail']} fail. "
          f"{s['sentences']} sentences and {s['values_checked']} amounts, dates and counts checked.")
    for name, c in s["by_check"].items():
        print(f"  {c['label']:<40} {c['drafts_passing']}/{c['drafts']} drafts clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
