"""E1 amendments 2 and 5: re-judge 50 random answers with a cross-family second judge (step-5-preview) and report agreement.

usage: python -I research/src/judgecheck.py <config> --key-file F
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python -I leaves the script's folder off sys.path
from qa import DATA, JUDGE_PROMPTS, LLM, RAW

SECOND = "stepfun-ai/step-5-preview"  # a different family from both the reader and the judge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--key-file", required=True, action="append", help="repeat for more keys; each gets its own pacer")
    ap.add_argument("-n", type=int, default=50)
    a = ap.parse_args()
    qs = {q["question_id"]: q for q in json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))}
    rows = [r for r in map(json.loads, (DATA / f"qa_{a.config}.jsonl").open(encoding="utf-8")) if "error" not in r]
    sample = random.Random(0).sample(rows, min(a.n, len(rows)))
    llm, agree, flips = LLM([Path(f).read_text().strip() for f in a.key_file], 20), 0, []
    for r in sample:
        q = qs[r["id"]]
        typ = "abstention" if r["abs"] else r["type"]
        second = "yes" in llm(SECOND, JUDGE_PROMPTS[typ].format(q["question"], q["answer"], r["hyp"]), 4096).lower()
        agree += second == r["ok"]
        if second != r["ok"]:
            flips.append({"id": r["id"], "type": r["type"], "first": r["ok"], "second": second})
    out = {"config": a.config, "n": len(sample), "agreement": round(100 * agree / len(sample), 1), "flips": flips}
    print(json.dumps(out, indent=1))
    with (DATA / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ranker": f"judgecheck-{a.config}", "result": out}) + "\n")


if __name__ == "__main__":
    main()
