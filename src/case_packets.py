"""Build the evidence packets the drafter is allowed to use.

A packet is everything the model may say about one customer case, and nothing else.
Each fact lives under an evidence ID so a narrative sentence can cite it:

  KYC-<cif>     the customer profile (name, date of birth, ZIP, segment, risk rating, masked accounts)
  CASE-<n>      case totals worked out here from the alerts (count, date range, total, per typology)
  ACT-/LEG-/FR- one entry per alert, copied from the three monitoring exports

Source data comes from the AML Backlog Command Center repo (fictional Candela Bank).
Run: python3 src/case_packets.py --backlog ../aml-backlog-command-center
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "case_packets.json"
SEED = 20261125
SAMPLE_SIZE = 24

SYSTEMS = {
    "actimize": "Actimize transaction monitoring",
    "legacy_rules": "Legacy SQL rules engine",
    "fraud_referrals": "Fraud team referral",
}

# What the amount on each kind of alert means. The drafter must not treat them as the same thing.
AMOUNT_MEANING = {
    "actimize": "amount of the transaction that triggered the alert",
    "legacy_rules": "amount the rule flagged over its lookback window",
    "fraud_referrals": "fraud team's estimated exposure",
}

SCENARIO_LABELS = {
    "structuring": "Cash structuring",
    "rapid_movement": "Rapid movement of funds",
    "high_risk_geography": "Activity involving a high risk jurisdiction",
    "large_cash": "Large cash transaction",
    "p2p_velocity": "High velocity peer to peer payments",
    "dormant_reactivation": "Dormant account reactivation",
    "round_dollar_wires": "Round dollar wires",
    "funnel_account": "Funnel account deposits",
    "check_kiting": "Check kiting pattern",
    "fraud_referral": "Referral from the fraud team",
}

# The legacy engine's own rule names, from the backlog repo's source mapping.
LEGACY_RULE_NAMES = {
    "structuring": "Cash deposits over 10k in 3 days",
    "high_risk_geography": "Wires to high risk jurisdiction",
    "round_dollar_wires": "Round dollar wires",
    "funnel_account": "Funnel account deposits",
    "check_kiting": "Check kiting pattern",
}


def mask_account(number: str) -> str:
    return f"account ending {number[-4:]}"


def primary_typology(case: dict) -> str:
    for label, _points in case["score_parts"]:
        if label.startswith("Most severe typology: "):
            return label.split(": ", 1)[1]
    return "unknown"


def select_cases(cases: list[dict], size: int = SAMPLE_SIZE, seed: int = SEED) -> list[dict]:
    """Pick a varied sample: priority P1 or P2, 3 to 10 alerts, round robin across the most severe typology."""
    eligible = [c for c in cases if c["tier"] in ("P1", "P2") and 3 <= len(c["alerts"]) <= 10]
    groups: dict[str, list[dict]] = {}
    for c in sorted(eligible, key=lambda c: c["case_id"]):
        groups.setdefault(primary_typology(c), []).append(c)
    rng = random.Random(seed)
    for key in sorted(groups):
        rng.shuffle(groups[key])
    picked: list[dict] = []
    while len(picked) < size and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(picked) < size:
                picked.append(groups[key].pop())
    return sorted(picked, key=lambda c: c["case_id"])


def load_customers(path: Path) -> dict[str, dict]:
    with path.open(newline="") as f:
        return {row["CIF"]: row for row in csv.DictReader(f)}


def load_branches(path: Path) -> dict[str, str]:
    branches: dict[str, str] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            branches.setdefault(row["ALERT_ID"].strip(), (row.get("BRANCH") or "").strip())
    return branches


def load_referrals(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text())
    return {r["ref"]: r for r in data["referrals"]}


def build_packet(case: dict, customers: dict, branches: dict, referrals: dict, as_of: str, bank: str) -> dict:
    cust = customers.get(case["cif"], {})
    accounts = [a for a in (cust.get("ACCOUNTS") or "").split(";") if a]
    alerts = []
    for a in sorted(case["alerts"], key=lambda a: (a["alert_date"], a["alert_id"])):
        entry = OrderedDict(
            evidence_id=a["alert_id"],
            system=SYSTEMS[a["source"]],
            scenario=a["scenario"],
            scenario_label=SCENARIO_LABELS.get(a["scenario"], a["scenario"].replace("_", " ")),
            date=a["alert_date"],
            amount=a["amount"],
            amount_meaning=AMOUNT_MEANING[a["source"]],
        )
        if a["source"] == "legacy_rules":
            entry["rule_name"] = LEGACY_RULE_NAMES.get(a["scenario"])
        if a["source"] == "actimize" and branches.get(a["alert_id"]):
            entry["branch"] = branches[a["alert_id"]]
        if a["source"] == "fraud_referrals":
            entry["referral_reason"] = a["detail"]
            ref = referrals.get(a["alert_id"])
            if ref:
                if ref["subject"].get("account"):
                    entry["account"] = mask_account(ref["subject"]["account"])
                entry["exposure_as_reported"] = ref["exposure"]
                if ref["exposure"].lower().endswith("k"):
                    entry["amount_meaning"] += f", reported as {ref['exposure']} so only accurate to the nearest $100"
        alerts.append(entry)

    by_typology = Counter(a["scenario"] for a in alerts)
    amount_by_typology: dict[str, float] = {}
    for a in alerts:
        amount_by_typology[a["scenario"]] = round(amount_by_typology.get(a["scenario"], 0) + a["amount"], 2)
    summary = OrderedDict(
        evidence_id=case["case_id"],
        alert_count=len(alerts),
        first_alert_date=alerts[0]["date"],
        last_alert_date=alerts[-1]["date"],
        total_amount=round(sum(a["amount"] for a in alerts), 2),
        total_amount_note="sum of the alert amounts; mixes transaction amounts, rule totals and fraud exposure estimates",
        systems=sorted({a["system"] for a in alerts}),
        alerts_by_typology=OrderedDict(sorted(by_typology.items())),
        amount_by_typology=OrderedDict(sorted(amount_by_typology.items())),
        branches=sorted({a["branch"] for a in alerts if a.get("branch")}),
    )
    subject = OrderedDict(
        evidence_id=f"KYC-{case['cif']}",
        cif=case["cif"],
        name=case["customer"],
        date_of_birth=cust.get("DOB"),
        zip_code=cust.get("ZIP"),
        segment=case["segment"],
        risk_rating={"H": "High", "M": "Medium", "L": "Low"}.get(case["risk_rating"], case["risk_rating"]),
        accounts=[mask_account(a) for a in accounts],
    )
    return OrderedDict(
        case_id=case["case_id"],
        bank=bank,
        as_of=as_of,
        primary_typology=primary_typology(case),
        priority_tier=case["tier"],
        subject=subject,
        summary=summary,
        alerts=alerts,
    )


def evidence_index(packet: dict) -> dict[str, dict]:
    """Every citable evidence ID in a packet mapped to its record."""
    index = {packet["subject"]["evidence_id"]: packet["subject"], packet["summary"]["evidence_id"]: packet["summary"]}
    for a in packet["alerts"]:
        index[a["evidence_id"]] = a
    return index


def build(backlog: Path) -> dict:
    center = json.loads((backlog / "dist" / "data" / "command_center.json").read_text())
    exports = backlog / "data" / "customer_exports"
    customers = load_customers(exports / "core_customers.csv")
    branches = load_branches(exports / "actimize_alert_export.csv")
    referrals = load_referrals(exports / "fraud_referrals.json")
    picked = select_cases(center["cases"])
    packets = [build_packet(c, customers, branches, referrals, center["as_of"], center["bank"]) for c in picked]
    return OrderedDict(
        source="brandoncandela/aml-backlog-command-center dist/data/command_center.json and data/customer_exports",
        selection=f"{SAMPLE_SIZE} cases, priority P1 or P2 with 3 to 10 alerts, round robin across most severe typology, seed {SEED}",
        eligible_cases=sum(1 for c in center["cases"] if c["tier"] in ("P1", "P2") and 3 <= len(c["alerts"]) <= 10),
        total_cases=len(center["cases"]),
        packets=packets,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backlog", type=Path, default=ROOT.parent / "aml-backlog-command-center")
    args = parser.parse_args()
    data = build(args.backlog)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"Wrote {len(data['packets'])} case packets to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
