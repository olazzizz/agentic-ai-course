#!/usr/bin/env python3
"""Lab 2: an agent loop from scratch. No framework; the only LLM dependency is the OpenAI client.

    uv run agent.py "What is 15% of 2340?"          # one task
    uv run agent.py --tasks tasks.json               # the whole task set, with pass/fail
    uv run agent.py --profile local --tasks tasks.json

    # same non-deterministic model, N runs per task: pass rate, pass^k, cost per successful run
    uv run agent.py --tasks tasks.json --repeat 5
    uv run agent.py --repeat 5 --expect 41550 --expect 13850 "Read q1_sales.txt. Total and average Q1 revenue?"

    # harness-side check: flag numbers in the answer that came from no tool call, tool result or the task
    uv run agent.py --tasks tasks.json --verify
"""
import argparse, ast, csv, json, operator as op, os, re, sys, time
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
HERE = Path(__file__).parent
SANDBOX = (HERE / os.getenv("SANDBOX_DIR", "sandbox")).resolve()
MAX_FILE_CHARS = 4000
TEMPERATURE = os.getenv("TEMPERATURE", "0")

# ------------------------------------------------------------------ tools
_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow,
        ast.USub: op.neg, ast.UAdd: op.pos}


def _eval(node):
    """Tiny arithmetic evaluator. Never use eval() on model output."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression: use only numbers and + - * / // % ** ( )")


def calculator(expression: str) -> str:
    result = _eval(ast.parse(expression.strip(), mode="eval").body)
    return str(round(result, 10)) if isinstance(result, float) else str(result)


def read_file(path: str) -> str:
    """Read a text file inside the sandbox. A directory path lists its entries."""
    target = (SANDBOX / path).resolve()  # resolves '..' and symlinks
    if not target.is_relative_to(SANDBOX):
        raise PermissionError("path is outside the sandbox")
    if not target.exists():
        raise FileNotFoundError(f"'{path}' does not exist. Call read_file with path '.' to list files.")
    if target.is_dir():
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir()))
    text = target.read_text(errors="replace")
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n[truncated: {len(text)} chars in total]"
    return text


REGISTRY = {"calculator": calculator, "read_file": read_file}
TOOLS = [
    {"type": "function", "function": {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression exactly, e.g. '(12450+13890)/3'. "
                       "Supports + - * / // % ** and parentheses. Use it for ALL arithmetic.",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "The arithmetic expression"}},
            "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file from the sandbox folder. Pass '.' to list the available files.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path, e.g. 'pricing.txt' or '.'"}},
            "required": ["path"]}}},
]

SYSTEM = (
    "You are a careful assistant with two tools: calculator and read_file (files live in a sandbox; "
    "read_file('.') lists them). Use a tool only when it is needed. Never do arithmetic in your head: "
    "use the calculator. If a tool returns an ERROR, read it, then adapt or tell the user plainly what "
    "went wrong. Never invent file contents. When you have the answer, reply with it and make no tool call."
)


# ------------------------------------------------------------------ the loop
def execute(call, seen: set) -> str:
    """Run one tool call. Every failure becomes text the model can act on; nothing crashes the loop."""
    name = call.function.name
    try:
        args = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        return f"ERROR: arguments were not valid JSON ({e}). Send a JSON object."
    key = (name, json.dumps(args, sort_keys=True))
    if key in seen:
        return "ERROR: you already made this exact call; the result will not change. Use it or try something else."
    seen.add(key)
    fn = REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'. Available tools: {', '.join(REGISTRY)}."
    try:
        return str(fn(**args))
    except Exception as e:  # noqa: BLE001 - deliberate: report to the model
        return f"ERROR: {type(e).__name__}: {e}"


def run_agent(cfg: dict, task: str, max_steps: int, max_tokens: int, log,
              max_output: int = 2000, max_seconds: float = 120) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    st = dict(steps=0, tokens_in=0, tokens_out=0, tool_calls=0, tool_errors=0, max_parallel=0)
    seen, observed, answer, stop, t0 = set(), [], None, "max_steps", time.time()
    extra = {"temperature": float(TEMPERATURE)} if TEMPERATURE != "" else {}

    for step in range(1, max_steps + 1):                     # guard 1: hard step limit
        resp = cfg["client"].chat.completions.create(         # REASON
            model=cfg["model"], messages=messages, tools=TOOLS,
            max_tokens=max_output, **extra)                   # guard 3: cap on ONE call's output
        st["steps"] = step
        if resp.usage:
            st["tokens_in"] += resp.usage.prompt_tokens
            st["tokens_out"] += resp.usage.completion_tokens
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        st["max_parallel"] = max(st["max_parallel"], len(calls))   # >1: calls issued before seeing each other's results
        log({"step": step, "event": "model", "content": msg.content,
             "tool_calls": [f"{c.function.name}({c.function.arguments})" for c in calls]})

        if resp.choices[0].finish_reason == "length":         # cut off at the cap: reasoning models can
            stop = "truncated"                                 # spend the whole cap thinking, then say nothing
            break

        if not calls:                                         # stop condition: model answered
            answer, stop = (msg.content or "").strip(), "final"
            break

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
        for c in calls:                                       # ACT, then OBSERVE
            result = execute(c, seen)
            observed.append(f"{c.function.arguments or ''}\n{result}")
            st["tool_calls"] += 1
            st["tool_errors"] += result.startswith("ERROR")        # attempts are not successes
            log({"step": step, "event": "tool", "name": c.function.name, "result": result[:300]})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})

        if st["tokens_in"] + st["tokens_out"] > max_tokens:   # guard 2: token budget
            stop = "budget"
            break
        if time.time() - t0 > max_seconds:                    # guard 4: wall-clock limit per run
            stop = "timeout"
            break

    cost = (st["tokens_in"] * cfg["price_in"] + st["tokens_out"] * cfg["price_out"]) / 1e6
    return {"answer": answer, "stop": stop, "seconds": round(time.time() - t0, 1), "cost": cost,
            "observed": "\n".join(observed), **st}


# ------------------------------------------------------------------ plumbing
def load_profile(name: str, timeout: float | None = None) -> dict:
    p = name.upper()
    base, key, model = (os.getenv(f"{p}_{k}", "") for k in ("BASE_URL", "API_KEY", "MODEL"))
    if not (base and model):
        sys.exit(f"Profile '{name}' is not configured: set {p}_BASE_URL and {p}_MODEL in .env")
    return {"client": OpenAI(base_url=base, api_key=key or "none", timeout=timeout or float(os.getenv("REQUEST_TIMEOUT", "120")), max_retries=1),
            "model": model,
            "price_in": float(os.getenv(f"{p}_PRICE_IN") or 0),
            "price_out": float(os.getenv(f"{p}_PRICE_OUT") or 0)}


def grade(task: dict, answer: str | None) -> str:
    if answer is None:
        return "FAIL"
    text = _norm(answer.lower()).replace(",", "")
    ok = all(s.lower() in text for s in task.get("expect", []))
    ok &= not task.get("expect_any") or any(s.lower() in text for s in task["expect_any"])
    ok &= not any(s.lower() in text for s in task.get("forbid", []))
    return "PASS" if ok else "FAIL"


_NUM = re.compile(r"\d+(?:\.\d+)?")
_GROUP = re.compile(r"(?<=\d)[,\u00a0\u202f\u2009 ](?=\d{3}(?!\d))")   # thousands separators: , space, NBSP, thin space


def _norm(text: str) -> str:
    """'41,550' / '41 550' / '41\u202f550' -> '41550', so formatting never decides a verdict."""
    return _GROUP.sub("", text or "")


def _numbers(text: str) -> set:
    return {round(float(m), 6) for m in _NUM.findall(_norm(text))}


def ungrounded(answer: str | None, task: str, observed: str) -> list:
    """Harness-side check: numbers in the answer that appear neither in the task nor in any tool result.
    It catches INVENTED numbers. It does NOT catch a real number attached to the wrong label
    (for example reporting the average as the total): use `expect` in tasks.json for that."""
    known = _numbers(task) | _numbers(observed)
    return sorted(n for n in _numbers(answer) if n not in known)


def make_logger(path: Path, echo: bool = True):
    def log(event: dict):
        with path.open("a") as f:
            f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **event}) + "\n")
        if echo:
            detail = event.get("tool_calls") or event.get("name", "") or ""
            text = (event.get("content") or event.get("result") or "").replace("\n", " ")[:90]
            print(f"    step {event['step']} {event['event']:<5} {detail} {text}")
    return log


def summarise(rows: list, repeat: int) -> None:
    """Per-task and overall reliability numbers. Graded runs only (PASS, FAIL or UNGROUNDED)."""
    GRADED = ("PASS", "FAIL", "UNGROUNDED")
    by_task: dict = {}
    for r in rows:
        by_task.setdefault(r["id"], []).append(r)

    print(f"\n{'task':<22}{'pass':>7}{'rate':>7}{'all ' + str(repeat):>7}{'ungr':>6}{'errs':>6}{'steps':>7}{'$/run':>10}{'$/success':>11}{'secs':>7}")
    complete_tasks = all_pass = runs = passes = ungr = 0
    for tid, rs in by_task.items():
        g = [r for r in rs if r["verdict"] in GRADED]
        avg_steps = sum(r["steps"] for r in rs) / len(rs)
        avg_cost = sum(r["cost"] for r in rs) / len(rs)
        avg_secs = sum(r["seconds"] for r in rs) / len(rs)
        errs = sum(r.get("tool_errors", 0) for r in rs)
        if not g:
            print(f"{tid:<22}{'n/a':>7}{'n/a':>7}{'n/a':>7}{'':>6}{errs or '':>6}{avg_steps:>7.1f}{avg_cost:>10.5f}{'n/a':>11}{avg_secs:>7.1f}")
            continue
        p = sum(r["verdict"] == "PASS" for r in g)
        u = sum(r["verdict"] == "UNGROUNDED" for r in g)
        cost_ok = f"{sum(r['cost'] for r in g) / p:.5f}" if p else "never"
        done = len(g) == repeat                          # an interrupted task has fewer runs than --repeat
        flag = ("yes" if p == len(g) else "no") if done else ("part." if p == len(g) else "no")
        print(f"{tid:<22}{f'{p}/{len(g)}':>7}{p / len(g):>7.0%}{flag:>7}{u or '':>6}{errs or '':>6}"
              f"{avg_steps:>7.1f}{avg_cost:>10.5f}{cost_ok:>11}{avg_secs:>7.1f}")
        runs += len(g); passes += p; ungr += u
        if done:
            complete_tasks += 1; all_pass += p == len(g)

    total = sum(r["cost"] for r in rows)
    stops = Counter(r["stop"] for r in rows if r["stop"] != "final")
    if stops:
        print("\nRuns that did NOT end with a final answer: " + ", ".join(f"{k}={v}" for k, v in stops.items()))
    if not runs:
        print(f"\nNo graded tasks (add 'expect' in tasks.json or --expect), so no pass rate. Total cost ${total:.5f}")
        return
    cps = f"${sum(r['cost'] for r in rows if r['verdict'] in GRADED) / passes:.5f}" if passes else "n/a (no passes)"
    print(f"\nPass rate: {passes}/{runs} runs ({passes / runs:.0%})"
          f" | pass^{repeat}: {all_pass}/{complete_tasks} tasks passed on ALL {repeat} runs"
          f" | cost per successful run (failures included): {cps} | total ${total:.5f}")
    if ungr:
        print(f"{ungr} run(s) UNGROUNDED: the answer was right, but a number did not come from a tool "
              f"(the model did arithmetic itself). Not counted as a pass.")
    print("errs = tool calls that returned ERROR. 'N tool calls' on a run line counts attempts, not successes.")
    print("pass^k is the production number: a task that passes 4 times out of 5 is not reliable.")
    if complete_tasks < len(by_task):
        print("Interrupted: pass^k counts only the tasks that finished all their runs.")


def save_csv(rows: list, path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?")
    ap.add_argument("--tasks", help="JSON file with a list of tasks")
    ap.add_argument("--profile", default="hosted", help="hosted | local (reads HOSTED_* / LOCAL_* from .env)")
    ap.add_argument("--max-steps", type=int, default=int(os.getenv("MAX_STEPS", "8")))
    ap.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOTAL_TOKENS", "30000")),
                    help="token budget for a whole run (checked after each step)")
    ap.add_argument("--max-output-tokens", type=int, default=int(os.getenv("MAX_OUTPUT_TOKENS", "2000")),
                    help="cap on the output of ONE model call; reasoning tokens usually count against it")
    ap.add_argument("--max-seconds", type=float, default=float(os.getenv("MAX_SECONDS", "120")),
                    help="wall-clock limit per run, checked after each step")
    ap.add_argument("--timeout", type=float, default=float(os.getenv("REQUEST_TIMEOUT", "120")),
                    help="HTTP timeout for ONE model call, in seconds")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="run every task N times and report pass rate, pass^k and cost per successful run")
    ap.add_argument("--expect", action="append", metavar="TEXT",
                    help="for a single ad hoc task: text the answer must contain (repeatable)")
    ap.add_argument("--verify", action="store_true",
                    help="verdict UNGROUNDED when a correct answer contains numbers that came from no tool call or result "
                         "(arithmetic done in the model's head)")
    a = ap.parse_args()
    if not a.task and not a.tasks:
        ap.error("give a task or --tasks file")
    if a.repeat < 1:
        ap.error("--repeat must be at least 1")

    cfg = load_profile(a.profile, a.timeout)
    if a.tasks:
        tasks = json.load(open(a.tasks))
    else:
        tasks = [{"id": "adhoc", "task": a.task, **({"expect": a.expect} if a.expect else {})}]
    (HERE / "logs").mkdir(exist_ok=True)
    logfile = HERE / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}-{a.profile}.jsonl"
    log = make_logger(logfile, echo=a.repeat == 1)       # with --repeat, only one line per run on screen
    out = HERE / f"results-{a.profile}.csv"

    rows = []
    try:
        for t in tasks:
            for k in range(1, a.repeat + 1):
                tag = f" (run {k}/{a.repeat})" if a.repeat > 1 else ""
                print(f"\n== [{t['id']}]{tag} {t['task']}")
                log({"step": 0, "event": "task", "content": f"{t['task']}{tag}"})
                r = run_agent(cfg, t["task"], a.max_steps, a.max_tokens, log,
                              max_output=a.max_output_tokens, max_seconds=a.max_seconds)
                graded = "expect" in t or "expect_any" in t or "forbid" in t
                verdict = grade(t, r["answer"]) if graded else "-"
                bad = ungrounded(r["answer"], t["task"], r["observed"]) if a.verify else []
                if bad and verdict == "PASS":
                    verdict = "UNGROUNDED"                   # right answer, but not backed by any tool result
                note = f" | numbers not from the task or any tool: {bad}" if bad else ""
                parts = ([f"{r['tool_errors']} failed"] if r["tool_errors"] else []) + \
                        ([f"up to {r['max_parallel']} in one step"] if r["max_parallel"] > 1 else [])
                calls_txt = f"{r['tool_calls']} tool calls" + (f" ({', '.join(parts)})" if parts else "")
                print(f"   -> {r['stop']} | {r['steps']} steps | {calls_txt} | "
                      f"{r['tokens_in']}+{r['tokens_out']} tok | ${r['cost']:.5f} | {r['seconds']}s | {verdict}{note}")
                print(f"   answer: {r['answer']}")
                rows.append({"id": t["id"], "run": k, "verdict": verdict, "ungrounded": " ".join(map(str, bad)),
                             **{key: r[key] for key in
                                ("stop", "steps", "tool_calls", "tool_errors", "max_parallel", "tokens_in", "tokens_out", "cost", "seconds")}})
                save_csv(rows, out)                       # saved after EVERY run: Ctrl+C loses nothing
    except KeyboardInterrupt:
        print("\n\nInterrupted: summarising the runs completed so far.")

    if rows:
        if len(rows) > 1:
            print(f"\nSaved {out.name}")
            summarise(rows, a.repeat)
    print(f"Full trace: {logfile.relative_to(HERE)}")


if __name__ == "__main__":
    main()
