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
import re
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
READER, JUDGE = "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b"
# the reader runs with thinking off: a big model answering directly, the role GPT-4o plays in published setups
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
TOPK = 10
SPREAD, MIN_S, MAX_S = 0.8, 2, 6  # E7, fixed before the run, not tuned

READ = ("I will give you several history chats between you and a user. Please answer the question based on the "
        "relevant chat history.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:")
# LongMemEval's own --cot reader template (run_generation.py), verbatim; their generation budget for it is 800 tokens
READ_COT = ("I will give you several history chats between you and a user. Please answer the question based on the "
            "relevant chat history. Answer the question step by step: first extract all the relevant information, and "
            "then reason over the information to get the answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\n"
            "Question: {}\nAnswer (step by step):")
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
    """one pacer per key, shared by every thread: NIM's free tier counts requests per minute per key.
    each call goes to the key that is free soonest."""

    def __init__(self, keys: list[str], rpm: float):
        self.keys, self.gap, self.lock = keys, 60.0 / rpm, threading.Lock()
        self.next = [0.0] * len(keys)
        self.db = sqlite3.connect(DATA / "llmcache.sqlite", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS c(k BLOB PRIMARY KEY, model TEXT, out TEXT, at TEXT)")
        self.dbl, self.calls = threading.Lock(), 0

    def _pace(self) -> int:
        with self.lock:
            now = time.monotonic()
            i = min(range(len(self.keys)), key=self.next.__getitem__)
            wait = max(0.0, self.next[i] - now)
            self.next[i] = max(now, self.next[i]) + self.gap
        time.sleep(wait)
        return i

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
            ki = self._pace()
            try:
                req = urllib.request.Request(URL, body, {"Authorization": "Bearer " + self.keys[ki], "Content-Type": "application/json"})
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
                print(f"  retry {attempt + 1} ({model}, key {ki}): HTTP {e.code}, retry-after {retry:.0f}s", file=sys.stderr, flush=True)
                if retry:  # the server named a wait: that key waits. otherwise only this thread backs off
                    with self.lock:
                        self.next[ki] = max(self.next[ki], time.monotonic() + retry)
                time.sleep(min(60, 2 ** attempt))
            except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError) as e:
                print(f"  retry {attempt + 1} ({model}): {str(e)[:80]}", file=sys.stderr, flush=True)
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
    if config == "dynamic":  # E7: the fused retrieval scores decide how many whole sessions the reader gets
        return _ranked(q, "time_interp", embed, gold, sessions=-1)
    if config.startswith("sessions:"):  # sessions:<ranker>:<n> = the n best sessions (by their best turn), whole
        _, name, n = config.split(":")
        return _ranked(q, name, embed, gold, sessions=int(n))
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


def _ranked(q: dict, name: str, embed, gold: set, sessions: int = 0) -> tuple[list, dict]:
    import rankers
    from bench import Index
    from lme_retrieval import lme_all_one

    h = lme_all_one(q)
    idx = Index(h, embed)
    qq = h.queries[0]
    qv = embed([qq.text], query=True)[0]
    ranked = getattr(rankers, name)(idx, qq, qv)
    role = h.role
    if sessions < 0:
        # score-adaptive: every session whose best turn scores >= SPREAD x the best session gets read whole, between
        # MIN_S and MAX_S of them. evidence spread over many sessions (counts, totals) lifts many sessions close to the
        # top; a single-fact question has one clear winner. the scores are the 0.3 fusion of dense, bm25 and time.
        score = rankers._turn_scores(idx, qq, qv)
        best_of = {}
        for d, v in score.items():
            u = h.unit[d][0]
            best_of[u] = max(best_of.get(u, 0.0), v)
        order = sorted(best_of, key=best_of.get, reverse=True)
        top = best_of[order[0]] if order else 0.0
        sessions = min(MAX_S, max(MIN_S, sum(1 for u in order if best_of[u] >= SPREAD * top)))
        best = order[:sessions]
        docs = {s_: sorted((d for d in idx.text if h.unit[d][0] == s_), key=idx.created.get) for s_ in best}
        chunks = [(idx.created[ds[0]], [(role[d], idx.text[d]) for d in ds]) for ds in docs.values()]
        found = set(best)
    elif sessions:
        best = list(dict.fromkeys(h.unit[d][0] for d in ranked))[:sessions]
        docs = {s: sorted((d for d in idx.text if h.unit[d][0] == s), key=idx.created.get) for s in best}
        chunks = [(idx.created[ds[0]], [(role[d], idx.text[d]) for d in ds]) for ds in docs.values()]
        found = set(best)
    else:
        ranked = ranked[:TOPK]
        chunks = [(idx.created[d], [(role[d], idx.text[d])]) for d in ranked]
        found = {h.unit[d][0] for d in ranked}
    idx.close()
    return chunks, {"recall_any": float(bool(gold & found)), "recall_all": float(gold <= found) if gold else 1.0}


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return e


def main():
    global TOPK
    ap = argparse.ArgumentParser()
    ap.add_argument("config")  # dense | hybrid | oracle | ranker:<name in rankers.py>
    ap.add_argument("--key-file", required=True, action="append", help="repeat for more keys; each gets its own pacer")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rpm", type=float, default=20)  # per key; NIM 429s the judge at ~35/min on one key
    ap.add_argument("--cot", action="store_true", help="LongMemEval's step-by-step reader prompt (800-token budget)")
    ap.add_argument("--topk", type=int, default=TOPK, help="turns handed to the reader (ranker: and hybrid/dense configs)")
    ap.add_argument("--batch-read", type=int, default=1, help="questions per reader request (1 = official, one each)")
    ap.add_argument("--batch-judge", type=int, default=0, help="grade this many answers per judge request (0 = official, one each)")
    ap.add_argument("--workers", type=int, default=48)  # calls take up to ~100 s (550B reader, long contexts); the pacers cap rpm
    a = ap.parse_args()
    data = json.loads((RAW / "longmemeval_s.json").read_text(encoding="utf-8"))
    if a.limit:  # stratified: the first N/6 of each type, so a smoke run still covers every type
        per, seen = max(1, a.limit // 6), collections.Counter()
        data = [q for q in data if (seen.update([q["question_type"]]) or seen[q["question_type"]] <= per)]
    llm, embed = LLM([Path(f).read_text().strip() for f in a.key_file], a.rpm), StoreEmbed()
    TOPK = a.topk
    out_path = DATA / f"qa_{a.config.replace(':', '-')}{'' if TOPK == 10 else f'_k{TOPK}'}{'_cot' if a.cot else ''}{f'_b{a.batch_read}' if a.batch_read > 1 else ''}.jsonl"
    template, budget = (READ_COT, 800) if a.cot else (READ, 500)

    t0 = time.time()
    prepared = []
    for n, q in enumerate(data):  # retrieval in the main thread: ollama embeds one request at a time anyway
        chunks, stats = retrieve(q, a.config, embed)
        prompt = template.format(history(chunks), q["question_date"], q["question"])
        prepared.append((q, prompt, {**stats, "ctx_chars": len(prompt)}))
        if n % 25 == 0:
            print(f"retrieved {n + 1}/{len(data)}  {time.time() - t0:.0f}s", flush=True)

    def judge_prompt(q, hyp):
        typ = "abstention" if q["question_id"].endswith("_abs") else q["question_type"]
        return typ, JUDGE_PROMPTS[typ].format(q["question"], q["answer"], hyp)

    def read(item):  # phase 1: one reader call per question (contexts never share a request)
        q, prompt, stats = item
        try:
            return llm(READER, prompt, budget, NO_THINK)
        except Exception as e:
            return e

    def read_batch(items):  # several questions per request, each with its own full, verbatim reader prompt
        if len(items) == 1:
            return [read(items[0])]
        body = ("You will answer several separate tasks. Each task has its own chat history, current date and question; "
                "use only that task's history for that task.\n\n")
        body += "".join(f"=== Task {i + 1} ===\n{prompt}\n\n" for i, (_, prompt, _) in enumerate(items))
        body += "Answer every task in order. Begin each answer on its own line with `### Answer <task number>`."
        out = llm(READER, body, budget * len(items), NO_THINK)
        parts = re.split(r"^\s*#+\s*Answer\s+(\d+)\s*:?\s*$", out, flags=re.M)
        got = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2) if parts[i + 1].strip()}
        return [got[i + 1] if i + 1 in got else read(it) for i, it in enumerate(items)]  # unparsed -> single call

    groups = [prepared[i:i + max(1, a.batch_read)] for i in range(0, len(prepared), max(1, a.batch_read))]
    hyps = []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for n, hs in enumerate(ex.map(lambda g: _safe(read_batch, g), groups)):
            hyps += hs if not isinstance(hs, Exception) else [hs] * len(groups[n])
            print(f"read {len(hyps)}/{len(prepared)}  {time.time() - t0:.0f}s  calls {llm.calls}", flush=True)

    def judge_one(q, hyp):
        return "yes" in llm(JUDGE, judge_prompt(q, hyp)[1], 4096).lower()  # the official rule, evaluate_qa.py

    def judge_batch(items):  # phase 2: the per-item official prompts, numbered, several per request
        if len(items) == 1 or a.batch_judge <= 1:
            return [judge_one(q, h) for q, h in items]
        body = "You will grade several separate items. Each item has its own instructions; follow them for that item only.\n\n"
        body += "".join(f"### Item {i + 1}\n{judge_prompt(q, h)[1]}\n\n" for i, (q, h) in enumerate(items))
        body += f"Reply with exactly {len(items)} lines, one per item in order, formatted `<item number>: yes` or `<item number>: no`, and nothing else."
        got = {int(m.group(1)): m.group(2).lower() == "yes"
               for m in re.finditer(r"^\s*\**\s*(\d+)\s*\**\s*:\s*\**\s*(yes|no)\b", llm(JUDGE, body, 16384), re.M | re.I)}
        # anything the batch reply did not cover gets the official single-item call
        return [got[i + 1] if i + 1 in got else judge_one(q, h) for i, (q, h) in enumerate(items)]

    todo = [(i, item[0], h) for i, (item, h) in enumerate(zip(prepared, hyps)) if not isinstance(h, Exception)]
    size = max(1, a.batch_judge)
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    verdicts = {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for b, vs in zip(batches, ex.map(lambda b: _safe(judge_batch, [(q, h) for _, q, h in b]), batches)):
            for (i, _, _), v in zip(b, vs if not isinstance(vs, Exception) else [vs] * len(b)):
                verdicts[i] = v
    print(f"judged {len(verdicts)}  {time.time() - t0:.0f}s  calls {llm.calls}", flush=True)

    rows = []
    with out_path.open("w", encoding="utf-8") as f:
        for i, ((q, prompt, stats), h) in enumerate(zip(prepared, hyps)):
            v = verdicts.get(i)
            if isinstance(h, Exception) or isinstance(v, Exception) or v is None:  # reported, never counted
                r = {"id": q["question_id"], "type": q["question_type"], "error": str(h if isinstance(h, Exception) else v)[:200]}
            else:
                typ = judge_prompt(q, h)[0]
                r = {"id": q["question_id"], "type": q["question_type"], "abs": typ == "abstention", "ok": v, "hyp": h,
                     **stats, "reader": READER, "judge": JUDGE, "batch_read": a.batch_read, "batch_judge": a.batch_judge}
            rows.append(r)
            f.write(json.dumps(r) + "\n")

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
