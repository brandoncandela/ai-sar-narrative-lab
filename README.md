# AI SAR Narrative Lab

**Live demo: [brandoncandela.github.io/ai-sar-narrative-lab](https://brandoncandela.github.io/ai-sar-narrative-lab/)** · [Technical case study](https://brandoncandela.github.io/case-studies/ai-sar-narrative-lab.html) · [Portfolio](https://brandoncandela.github.io/)

Writing the narrative is the slowest part of a Suspicious Activity Report. A language model can write a first draft in seconds, but an investigator cannot file a sentence they cannot check. This project has Claude draft SAR narratives where every sentence lists the evidence behind it, then checks every draft with plain rules before a person reviews it.

The bank, customers and alerts are fictional. They come from my [AML Backlog Command Center](https://github.com/brandoncandela/aml-backlog-command-center), which turns 5,343 alerts from three monitoring systems into 1,240 customer cases.

## What it found

<!-- RESULTS -->

## How it works

1. **Build an evidence packet for each case.** 24 cases are picked with a fixed seed: priority P1 or P2, 3 to 10 alerts, spread across nine typologies. Every fact gets an ID the draft can cite: `KYC-...` for the customer profile, `CASE-...` for totals worked out from the alerts, and the alert ID itself (`ACT-`, `LEG-`, `FR-`) for each alert. Each alert also says what its amount means, because a flagged transaction, a rule's lookback total and a fraud team's exposure estimate are not the same thing.
2. **Draft with Claude.** `src/generate.py` sends each packet to Claude Opus 5.5 with structured output. The schema forces five sections (who, what, when, where, why) and a citations list on every sentence, plus a list of open questions the investigator still has to answer. The instructions say to copy amounts and dates exactly, never state that a crime happened, and put anything the packet does not contain into open questions instead of guessing.
3. **Check every sentence with rules.** `src/evaluate.py` reads each sentence and finds every dollar amount, date, alert count, branch code, ZIP code and account ending in it. Each one has to appear in the records that sentence cites, or be a sum of the alerts it cites. Cited IDs must exist, and all five sections must be there. Legal conclusions, IDs typed into the prose, internal scores and alerts the draft never mentions are flagged as warnings for a person to look at.
4. **Test the tester.** `src/faults.py` copies each draft, plants one known error (a changed amount, a fake alert ID, a missing section, a date moved nine days, a swapped citation, a raised count, a removed citation list, a sentence saying the customer laundered money) and checks that the right rule fires.
5. **Human review.** In the demo, a reviewer accepts, edits or rejects each draft. Rejections need a reason code and a written reason. Edits are scored again by the same rules when the log is imported. The lab records where the reviewer and the evaluator disagree, because that is where the rules need work.

## Keeping the API key safe

Nobody can spend my API credits through this project.

- Only `src/generate.py` calls the Claude API, and only on my machine. It reads the key from the `ANTHROPIC_API_KEY` environment variable or a local `.env` file.
- `.env` is in `.gitignore`, a pre-commit hook in `.githooks/` refuses any commit that adds an `.env` file or anything shaped like a key, and a test scans every file in the repo for keys.
- The drafts and their scores are committed as JSON. The demo is static files on GitHub Pages and never calls a model. Its Content Security Policy blocks connections to any other site, and a test checks that no script fetches anything outside the site.

## The demo

- **Draft workspace:** pick a case, read the draft, and select any citation to see the record behind it. Select an alert to see every sentence that relies on it. Flagged sentences show what failed.
- **Evaluation:** how many drafts passed each check, and how many planted errors each check caught.
- **Review log:** committed reviews and their disagreements with the evaluator, plus your own reviews, saved in your browser and downloadable as JSON.

## Run it

Python 3.9 or newer. The evaluator, build and tests use the standard library only. Generation needs the Anthropic SDK and a key.

```bash
python3 -m unittest discover -s tests -v   # no network, no key
python3 src/build.py                       # score the committed drafts, write dist/data/lab.json
python3 -m http.server 8000 --directory dist
```

To generate drafts yourself:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # paste your key into .env
git config core.hooksPath .githooks        # turn on the secret check
.venv/bin/python src/generate.py --all     # only cases without a draft; --force to redo
python3 src/build.py
```

To add reviews made in the demo:

```bash
python3 src/review.py import ~/Downloads/sar-review-log.json
python3 src/build.py
```

The tests cover the packet builder (rebuilds byte for byte from the backlog repo, no full account numbers), every evaluator rule with passing and failing sentences, the fault injector, the request sent to Claude and how refusals, cut-off replies and bad JSON are recorded, `.env` handling, review validation and disagreement scoring, the build, and the secret checks.

## Layout

```
data/case_packets.json   the 24 evidence packets
src/                     case_packets, prompt, generate, evaluate, faults, review, build
drafts/                  one JSON file per case: the model's reply, model name, tokens, cost
results/                 evaluation, fault injection and review summaries
reviews/review_log.json  human reviews, append only
dist/                    the static demo
tests/                   unittest suite and a hand-written fixture narrative
```

## Limits

- The data is synthetic and the alerts carry one amount each, so drafts cite alerts, not individual transactions.
- Rules can check that a number is in the evidence. They cannot tell whether the why section is convincing or whether the right facts were chosen. That is the reviewer's job, and the review log is how the gap gets measured.
- Prose checks use patterns, so an amount written in words ("five thousand dollars") is not checked.
- This is a drafting aid. A SAR is filed by people at a bank who own the decision.

## About

Built by [Brandon Candela](https://brandoncandela.github.io), AML consultant (CAMS, CRISC, PMP). I prepare SAR documentation at TD Bank, wrote SAR-ready narratives and supported SAR quality control at KPMG, and helped improve GenAI-assisted KYC workflows at IBM Promontory. This project is how I would put a model into that work: grounded drafts, checks anyone can read, and a person who signs off.

Built with AI coding tools.
