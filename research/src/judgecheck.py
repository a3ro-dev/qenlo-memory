"""E1 amendment 2: re-judge 50 random answers with a second judge (nemotron-ultra, thinking on) and report agreement.

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

SECOND = "nvidia/nemotron-3-ultra-550b-a55b"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("-n", type=int, default=50)
    a = ap.parse_args()
    qs = {q["question_id"]: q for q in json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))}
    rows = [r for r in map(json.loads, (DATA / f"qa_{a.config}.jsonl").open(encoding="utf-8")) if "error" not in r]
    sample = random.Random(0).sample(rows, min(a.n, len(rows)))
    llm, agree, flips = LLM(Path(a.key_file).read_text().strip(), 35), 0, []
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
