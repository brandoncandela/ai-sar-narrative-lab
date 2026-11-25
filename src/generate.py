"""Ask Claude to draft a SAR narrative for each case packet and save the result in drafts/.

This is the only script that talks to the Claude API, and it only runs on your machine.
The key is read from the ANTHROPIC_API_KEY environment variable or a local .env file,
which git ignores. Nothing here ever prints, logs or saves the key.

  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  cp .env.example .env            # then paste your key into .env
  .venv/bin/python src/generate.py --all
  .venv/bin/python src/generate.py --case CASE-0033 --force
  python3 src/generate.py --all --dry-run   # show the requests without calling the API
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PACKETS = ROOT / "data" / "case_packets.json"
DRAFTS = ROOT / "drafts"
ENV_FILE = ROOT / ".env"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Dollars per million tokens, from the Claude API price list. Output includes thinking tokens.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-opus-4-8": (5.00, 25.00)}


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Read KEY=VALUE lines into os.environ without overriding values already set. Returns the names set."""
    if not path.exists():
        return []
    names = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if value and name not in os.environ:
            os.environ[name] = value
            names.append(name)
    return names


def build_request(packet: dict) -> dict:
    """Keyword arguments for client.beta.messages.create. Structured output keeps the reply in the citation schema."""
    return {
        "model": prompt.MODEL,
        "max_tokens": prompt.MAX_TOKENS,
        "system": prompt.SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt.user_message(packet)}],
        "output_config": {
            "effort": prompt.EFFORT,
            "format": {"type": "json_schema", "schema": prompt.OUTPUT_SCHEMA},
        },
        # If a safety classifier declines, the API reruns the request on a fallback model it picks.
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    inp, out = PRICES[model]
    return round(input_tokens / 1e6 * inp + output_tokens / 1e6 * out, 4)


def record_from_response(packet: dict, response, generated_at: str) -> dict:
    """Turn an API response into the draft record saved in drafts/. Never includes headers or credentials."""
    content = list(getattr(response, "content", []) or [])
    text = next((b.text for b in content if getattr(b, "type", None) == "text"), None)
    fallback_used = any(getattr(b, "type", None) == "fallback" for b in content) or response.model != prompt.MODEL
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    record = {
        "case_id": packet["case_id"],
        "prompt_version": prompt.PROMPT_VERSION,
        "model_requested": prompt.MODEL,
        "model_served": response.model,
        "fallback_used": fallback_used,
        "effort": prompt.EFFORT,
        "generated_at": generated_at,
        "stop_reason": response.stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "estimated_cost_usd": estimate_cost(response.model, input_tokens, output_tokens),
        "narrative": None,
        "error": None,
    }
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        record["error"] = f"refused ({getattr(details, 'category', None) or 'no category'})"
    elif response.stop_reason == "max_tokens":
        record["error"] = "output cut off at max_tokens"
    elif text is None:
        record["error"] = "no text block in the response"
    else:
        try:
            record["narrative"] = json.loads(text)
        except json.JSONDecodeError as exc:
            record["error"] = f"reply was not valid JSON: {exc}"
            record["raw_text"] = text
    return record


def draft_one(client, packet: dict) -> dict:
    response = client.beta.messages.create(**build_request(packet))
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return record_from_response(packet, response, generated_at)


def save(record: dict, folder: Path = DRAFTS) -> Path:
    folder.mkdir(exist_ok=True)
    path = folder / f"{record['case_id']}.json"
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    return path


def make_client():
    import anthropic  # imported here so the evaluator and tests run without the SDK installed

    return anthropic.Anthropic(max_retries=4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft SAR narratives with Claude.")
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--all", action="store_true", help="every case packet")
    pick.add_argument("--case", action="append", help="one case ID; repeat for more")
    parser.add_argument("--force", action="store_true", help="redo cases that already have a draft")
    parser.add_argument("--dry-run", action="store_true", help="print the requests and stop; no key needed")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    packets = json.loads(PACKETS.read_text())["packets"]
    if args.case:
        wanted = set(args.case)
        packets = [p for p in packets if p["case_id"] in wanted]
        missing = wanted - {p["case_id"] for p in packets}
        if missing:
            parser.error(f"no packet for {', '.join(sorted(missing))}")
    if not args.force:
        packets = [p for p in packets if not (DRAFTS / f"{p['case_id']}.json").exists()]
    if not packets:
        print("Nothing to draft. Use --force to redo existing drafts.")
        return 0

    if args.dry_run:
        for p in packets:
            req = build_request(p)
            print(json.dumps({k: v for k, v in req.items() if k != "system"}, indent=1)[:4000])
        print(f"\n{len(packets)} request(s) would be sent to {prompt.MODEL}. System prompt: {len(prompt.SYSTEM_PROMPT)} characters.")
        return 0

    load_env_file()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and paste your key there.", file=sys.stderr)
        return 2

    client = make_client()
    total_cost = 0.0
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(draft_one, client, p): p["case_id"] for p in packets}
        for fut in as_completed(futures):
            case_id = futures[fut]
            try:
                record = fut.result()
            except Exception as exc:  # report the error type only; SDK messages never contain the key
                failures += 1
                print(f"{case_id}: request failed ({type(exc).__name__})", file=sys.stderr)
                continue
            save(record)
            total_cost += record["estimated_cost_usd"] or 0
            status = "ok" if record["narrative"] else record["error"]
            print(f"{case_id}: {status} · {record['usage']['output_tokens']} output tokens")
    print(f"Done. Estimated cost ${total_cost:.2f}. {failures} request(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
