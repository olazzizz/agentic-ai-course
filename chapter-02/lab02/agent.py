#!/usr/bin/env python3
"""Lab 2: an agent loop from scratch. No framework; the only LLM dependency is the OpenAI client.

    uv run agent.py "What is 15% of 2340?"          # one task
    uv run agent.py --tasks tasks.json               # the whole task set, with pass/fail
    uv run agent.py --profile local --tasks tasks.json
"""
import argparse, ast, csv, json, operator as op, os, sys, time
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


def run_agent(cfg: dict, task: str, max_steps: int, max_tokens: int, log) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    st = dict(steps=0, tokens_in=0, tokens_out=0, tool_calls=0)
    seen, answer, stop, t0 = set(), None, "max_steps", time.time()
    extra = {"temperature": float(TEMPERATURE)} if TEMPERATURE != "" else {}

    for step in range(1, max_steps + 1):                     # guard 1: hard step limit
        resp = cfg["client"].chat.completions.create(         # REASON
            model=cfg["model"], messages=messages, tools=TOOLS, **extra)
        st["steps"] = step
        if resp.usage:
            st["tokens_in"] += resp.usage.prompt_tokens
            st["tokens_out"] += resp.usage.completion_tokens
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        log({"step": step, "event": "model", "content": msg.content,
             "tool_calls": [f"{c.function.name}({c.function.arguments})" for c in calls]})

        if not calls:                                         # stop condition: model answered
            answer, stop = (msg.content or "").strip(), "final"
            break

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
        for c in calls:                                       # ACT, then OBSERVE
            result = execute(c, seen)
            st["tool_calls"] += 1
            log({"step": step, "event": "tool", "name": c.function.name, "result": result[:300]})
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})

        if st["tokens_in"] + st["tokens_out"] > max_tokens:   # guard 2: token budget
            stop = "budget"
            break

    cost = (st["tokens_in"] * cfg["price_in"] + st["tokens_out"] * cfg["price_out"]) / 1e6
    return {"answer": answer, "stop": stop, "seconds": round(time.time() - t0, 1), "cost": cost, **st}


# ------------------------------------------------------------------ plumbing
def load_profile(name: str) -> dict:
    p = name.upper()
    base, key, model = (os.getenv(f"{p}_{k}", "") for k in ("BASE_URL", "API_KEY", "MODEL"))
    if not (base and model):
        sys.exit(f"Profile '{name}' is not configured: set {p}_BASE_URL and {p}_MODEL in .env")
    return {"client": OpenAI(base_url=base, api_key=key or "none", timeout=300, max_retries=2),
            "model": model,
            "price_in": float(os.getenv(f"{p}_PRICE_IN") or 0),
            "price_out": float(os.getenv(f"{p}_PRICE_OUT") or 0)}


def grade(task: dict, answer: str | None) -> str:
    if answer is None:
        return "FAIL"
    text = answer.lower().replace(",", "")
    ok = all(s.lower() in text for s in task.get("expect", []))
    ok &= not task.get("expect_any") or any(s.lower() in text for s in task["expect_any"])
    ok &= not any(s.lower() in text for s in task.get("forbid", []))
    return "PASS" if ok else "FAIL"


def make_logger(path: Path):
    def log(event: dict):
        with path.open("a") as f:
            f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **event}) + "\n")
        detail = event.get("tool_calls") or event.get("name", "") or ""
        text = (event.get("content") or event.get("result") or "").replace("\n", " ")[:90]
        print(f"    step {event['step']} {event['event']:<5} {detail} {text}")
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?")
    ap.add_argument("--tasks", help="JSON file with a list of tasks")
    ap.add_argument("--profile", default="hosted", help="hosted | local (reads HOSTED_* / LOCAL_* from .env)")
    ap.add_argument("--max-steps", type=int, default=int(os.getenv("MAX_STEPS", "8")))
    ap.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOTAL_TOKENS", "30000")))
    a = ap.parse_args()
    if not a.task and not a.tasks:
        ap.error("give a task or --tasks file")

    cfg = load_profile(a.profile)
    tasks = json.load(open(a.tasks)) if a.tasks else [{"id": "adhoc", "task": a.task}]
    (HERE / "logs").mkdir(exist_ok=True)
    logfile = HERE / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}-{a.profile}.jsonl"
    log = make_logger(logfile)

    rows = []
    for t in tasks:
        print(f"\n== [{t['id']}] {t['task']}")
        log({"step": 0, "event": "task", "content": t["task"]})
        r = run_agent(cfg, t["task"], a.max_steps, a.max_tokens, log)
        verdict = grade(t, r["answer"]) if "expect" in t or "expect_any" in t or "forbid" in t else "-"
        print(f"   -> {r['stop']} | {r['steps']} steps | {r['tool_calls']} tool calls | "
              f"{r['tokens_in']}+{r['tokens_out']} tok | ${r['cost']:.5f} | {r['seconds']}s | {verdict}")
        print(f"   answer: {r['answer']}")
        rows.append({"id": t["id"], "verdict": verdict, **{k: r[k] for k in
                     ("stop", "steps", "tool_calls", "tokens_in", "tokens_out", "cost", "seconds")}})

    if len(rows) > 1:
        out = HERE / f"results-{a.profile}.csv"
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
        passed = sum(r["verdict"] == "PASS" for r in rows)
        print(f"\nPassed {passed}/{len(rows)} | total cost ${sum(r['cost'] for r in rows):.5f} | saved {out.name}")
    print(f"Full trace: {logfile.relative_to(HERE)}")


if __name__ == "__main__":
    main()
