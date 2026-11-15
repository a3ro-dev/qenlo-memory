"""E1: end-to-end QA on LongMemEval-S. the LLM lives only here, in the benchmark, never in qenlo-memory.

usage: python -I research/src/qa.py dense|hybrid|oracle --key-file F [--limit N] [--rpm 35]

reader and judge are pinned NVIDIA NIM models (free tier, rate limited ~40 req/min per key). every response is
cached in data/llmcache.sqlite keyed by model + prompt, so reruns and crashes never spend calls twice.
prompts are LongMemEval's own (src/generation/run_generation.py, src/evaluation/evaluate_qa.py), verbatim.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import hashlib
import json
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src")]

from product import StoreEmbed  # noqa: E402
from suites import RAW, ms  # noqa: E402
from bench import DATA  # noqa: E402

from qenlo_memory.store import Store, redact  # noqa: E402

URL = "https://integrate.api.nvidia.com/v1/chat/completions"
READER, JUDGE = "nvidia/nemotron-3-ultra-550b-a55b", "google/gemma-4-31b-it"
# the reader runs with thinking off: a big model answering directly, the role GPT-4o plays in published setups
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
TOPK = 10

READ = ("I will give you several history chats between you and a user. Please answer the question based on the "
        "relevant chat history.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:")
_TAIL = "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_BASE = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
         "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or "
         "contains all the intermediate steps to get the correct answer, you should also answer yes. If the response "
         "only contains a subset of the information required by the answer, answer no. ")
JUDGE_PROMPTS = {
    "single-session-user": _BASE + _TAIL, "single-session-assistant": _BASE + _TAIL, "multi-session": _BASE + _TAIL,
    "temporal-reasoning": _BASE + "In addition, do not penalize off-by-one errors for the number of days. If the "
    "question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting "
    "19 days when the answer is 18), the model's response is still correct. " + _TAIL,
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes "
    "if the response contains the correct answer. Otherwise, answer no. If the response contains some previous "
    "information along with an updated answer, the response should be considered as correct as long as the updated "
    "answer is the required answer." + _TAIL,
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a "
    "response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. "
    "The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls "
    "and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
    "yes if the model correctly identifies the question as unanswerable. The model could say that the information is "
    "incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: "
    "{}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only.",
}


class LLM:
    """one shared pacer for the whole process: NIM's free tier counts requests per minute per key."""

    def __init__(self, key: str, rpm: float):
        self.key, self.gap, self.next, self.lock = key, 60.0 / rpm, 0.0, threading.Lock()
        self.db = sqlite3.connect(DATA / "llmcache.sqlite", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS c(k BLOB PRIMARY KEY, model TEXT, out TEXT, at TEXT)")
        self.dbl, self.calls = threading.Lock(), 0

    def _pace(self, extra: float = 0.0):
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.gap + extra
        time.sleep(wait)

    def __call__(self, model: str, prompt: str, max_tokens: int, extra: dict | None = None) -> str:
        extra = extra or {}
        k = hashlib.sha1(f"{model}\0{max_tokens}\0{json.dumps(extra, sort_keys=True)}\0{prompt}".encode()).digest()
        with self.dbl:
            row = self.db.execute("SELECT out FROM c WHERE k = ?", (k,)).fetchone()
        if row:
            return row[0]
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": max_tokens, "temperature": 0, **extra}).encode()
        for attempt in range(8):
            self._pace()
            try:
                req = urllib.request.Request(URL, body, {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
                d = json.load(urllib.request.urlopen(req, timeout=180))
                self.calls += 1
                got = d.get("model", model)
                if got != model:  # a substituted model would mix scores: retry, never count
                    raise RuntimeError(f"asked {model}, got {got}")
                out = d["choices"][0]["message"].get("content") or ""
                thought = ((d.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                if extra.get("chat_template_kwargs", {}).get("enable_thinking") is False and thought:
                    raise RuntimeError(f"thinking was off but {thought} reasoning tokens came back")
                if not out.strip():
                    raise RuntimeError("empty content (reasoning ran out of tokens?)")
                with self.dbl:
                    self.db.execute("INSERT OR REPLACE INTO c VALUES (?, ?, ?, ?)", (k, got, out, time.strftime("%F %T")))
                    self.db.commit()
                return out
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                retry = float(e.headers.get("Retry-After") or 0)
                self._pace(extra=max(retry, 2 ** attempt))  # push the whole pacer back, not just this thread
            except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError) as e:
                print(f"  retry {attempt + 1} ({model}): {str(e)[:80]}", file=sys.stderr)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{model}: gave up after 8 tries")


def fmt_date(ms_: int) -> str:
    return datetime.fromtimestamp(ms_ / 1000).strftime("%Y/%m/%d (%a) %H:%M")


def history(chunks: list[tuple[int, list[tuple[str, str]]]]) -> str:
    """LongMemEval's 'nl' history format: date-sorted chunks, each a list of (role, content)."""
    out = ""
    for i, (when, turns) in enumerate(sorted(chunks, key=lambda c: c[0])):
        body = "".join(f"\n\n{r}: {c.strip()}" for r, c in turns) if len(turns) > 1 else f"{turns[0][0]}: {turns[0][1].strip()}"
        out += f"\n### Session {i + 1}:\nSession Date: {fmt_date(when)}\nSession Content:\n{body}\n"
    return out


def retrieve(q: dict, config: str, embed) -> tuple[list, dict]:
    """returns (chunks for the reader, retrieval stats)."""
    sess = [(sid, ms(datetime.strptime(d, "%Y/%m/%d (%a) %H:%M")), s)
            for sid, d, s in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"])]
    gold = set(q["answer_session_ids"])
    if config == "oracle":
        return [(w, [(t["role"], t["content"]) for t in s]) for sid, w, s in sess if sid in gold], {"recall_any": 1.0, "recall_all": 1.0}
    if config.startswith("ranker:"):  # any bench ranker over the all-turns index (lme_retrieval.py's haystack)
        return _ranked(q, config.split(":", 1)[1], embed, gold)
    docs, origin = [], {}
    for sid, w, s in sess:
        for i, t in enumerate(s):
            if t["content"].strip():
                # raw content, no role prefix: user turns then hit the embedding cache the retrieval suite filled
                docs.append({"text": t["content"], "created": w + i * 1000})
                # keyed like Store._check stores it; exact duplicates collapse to the first, like the Store does
                origin.setdefault(redact(t["content"].strip()).lower(), (sid, t["role"]))
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(Path(tmp), embed)
        s.remember_many(docs)
        now = ms(datetime.strptime(q["question_date"], "%Y/%m/%d (%a) %H:%M"))
        if config == "dense":
            ids = [i for i, _ in s._dense(s.embed([q["question"]], query=True)[0], "", TOPK)]
            rows = s._rows(ids)
            hits = [rows[i] for i in ids if i in rows]
        else:
            hits = s.recall(q["question"], k=TOPK, now=now)
        s.close()
    chunks, found = [], set()
    for h in hits:
        sid, role = origin.get(h["text"].lower(), (None, "user"))
        chunks.append((h["created"], [(role, h["text"])]))
        found.add(sid)
    return chunks, {"recall_any": float(bool(gold & found)), "recall_all": float(gold <= found) if gold else 1.0}


def _ranked(q: dict, name: str, embed, gold: set) -> tuple[list, dict]:
    import rankers
    from bench import Index
    from lme_retrieval import lme_all_one

    h = lme_all_one(q)
    idx = Index(h, embed)
    qq = h.queries[0]
    ranked = getattr(rankers, name)(idx, qq, embed([qq.text], query=True)[0])[:TOPK]
    role = {d: r for d, r in h.role.items()}
    chunks = [(idx.created[d], [(role[d], idx.text[d])]) for d in ranked]
    found = {h.unit[d][0] for d in ranked}
    idx.close()
    return chunks, {"recall_any": float(bool(gold & found)), "recall_all": float(gold <= found) if gold else 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")  # dense | hybrid | oracle | ranker:<name in rankers.py>
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rpm", type=float, default=35)
    ap.add_argument("--workers", type=int, default=12)  # gemma-4 takes ~45 s per call on NIM; the pacer still caps rpm
    a = ap.parse_args()
    data = json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))
    if a.limit:  # stratified: the first N/6 of each type, so a smoke run still covers every type
        per, seen = max(1, a.limit // 6), collections.Counter()
        data = [q for q in data if (seen.update([q["question_type"]]) or seen[q["question_type"]] <= per)]
    llm, embed = LLM(Path(a.key_file).read_text().strip(), a.rpm), StoreEmbed()
    out_path = DATA / f"qa_{a.config.replace(':', '-')}.jsonl"

    t0 = time.time()
    prepared = []
    for n, q in enumerate(data):  # retrieval in the main thread: ollama embeds one request at a time anyway
        chunks, stats = retrieve(q, a.config, embed)
        prompt = READ.format(history(chunks), q["question_date"], q["question"])
        prepared.append((q, prompt, stats))
        if n % 25 == 0:
            print(f"retrieved {n + 1}/{len(data)}  {time.time() - t0:.0f}s", flush=True)

    def answer(item):
        q, prompt, stats = item
        try:
            hyp = llm(READER, prompt, 500, NO_THINK)
            typ = "abstention" if q["question_id"].endswith("_abs") else q["question_type"]
            verdict = llm(JUDGE, JUDGE_PROMPTS[typ].format(q["question"], q["answer"], hyp), 2048)
            ok = "yes" in verdict.lower()  # the official rule, evaluate_qa.py
            return {"id": q["question_id"], "type": q["question_type"], "abs": typ == "abstention", "ok": ok,
                    "hyp": hyp, "verdict": verdict[-200:], **stats, "reader": READER, "judge": JUDGE}
        except Exception as e:  # reported, never counted as right or wrong
            return {"id": q["question_id"], "type": q["question_type"], "error": str(e)[:200]}

    rows = []
    with cf.ThreadPoolExecutor(a.workers) as ex, out_path.open("w", encoding="utf-8") as f:
        for n, r in enumerate(ex.map(answer, prepared)):
            rows.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            if n % 25 == 0:
                print(f"answered {n + 1}/{len(prepared)}  {time.time() - t0:.0f}s  calls {llm.calls}", flush=True)

    good = [r for r in rows if "error" not in r]
    by = collections.defaultdict(list)
    for r in good:
        by[r["type"]].append(r["ok"])
    summary = {"config": a.config, "n": len(rows), "errors": len(rows) - len(good),
               "accuracy": round(100 * statistics.mean(r["ok"] for r in good), 2) if good else None,
               "task_avg": round(100 * statistics.mean(statistics.mean(v) for v in by.values()), 2) if by else None,
               "abstention": round(100 * statistics.mean(r["ok"] for r in good if r["abs"]), 2) if any(r["abs"] for r in good) else None,
               "per_type": {k: round(100 * statistics.mean(v), 1) for k, v in sorted(by.items())},
               "recall_any@10": round(100 * statistics.mean(r["recall_any"] for r in good), 1) if good else None,
               "recall_all@10": round(100 * statistics.mean(r["recall_all"] for r in good), 1) if good else None,
               "reader": READER, "judge": JUDGE, "topk": TOPK, "limit": a.limit, "at": time.strftime("%Y-%m-%d %H:%M")}
    print(json.dumps(summary, indent=1))
    with (DATA / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ranker": f"qa-{a.config}", "result": summary, "at": summary["at"]}) + "\n")


if __name__ == "__main__":
    main()
