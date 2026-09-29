"""Lab 1: compare a local and a hosted model through ONE OpenAI-compatible client.

Measures per model: time-to-first-token, total latency, decode speed (tokens/s),
token counts, and cost. Then tests structured-output reliability
(prompt-only JSON vs. JSON-schema-constrained output).

Usage:  uv run compare.py
Config: see .env.example
"""
import csv
import os
import statistics
import time
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

load_dotenv()

TEMP = os.environ.get("TEMPERATURE", "0")  # set to "" to omit (some reasoning models reject it)


def load_profile(prefix: str):
    """Build a model profile from env vars, or None if not configured."""
    base_url = os.environ.get(f"{prefix}_BASE_URL")
    if not base_url:
        return None
    return {
        "name": prefix.lower(),
        "base_url": base_url,
        "api_key": os.environ.get(f"{prefix}_API_KEY", "not-needed"),
        "model": os.environ[f"{prefix}_MODEL"],
        "price_in": float(os.environ.get(f"{prefix}_PRICE_IN", "0")),    # USD per 1M input tokens
        "price_out": float(os.environ.get(f"{prefix}_PRICE_OUT", "0")),  # USD per 1M output tokens
        "delay_s": float(os.environ.get(f"{prefix}_DELAY_S", "0")),      # pause after each call (e.g. CPU cooldown)
    }


PROFILES = [p for p in (load_profile("LOCAL"), load_profile("HOSTED")) if p]

PROMPTS = [
    "In two sentences, explain what a context window is.",
    "What is 17% of 2,340? Show the calculation briefly.",
    "Summarize the difference between TCP and UDP in three bullet points.",
    "Write a Python function that returns the n-th Fibonacci number iteratively.",
    "List three risks of giving an LLM direct write access to a production database.",
    "Translate to Italian: 'The deployment failed because the certificate expired.'",
    "A train leaves at 09:40 and arrives at 13:15. How long is the journey?",
    "Explain Kubernetes namespaces to a finance manager in under 60 words.",
    "Give one advantage and one disadvantage of RAG compared to fine-tuning.",
    "Rewrite politely: 'Your report is late again and it is full of mistakes.'",
]


def timed_chat(client, model, messages, **extra):
    """Streaming chat call. Returns text, ttft, latency, and token usage."""
    kwargs = dict(model=model, messages=messages, stream=True, **extra)
    if TEMP != "":
        kwargs["temperature"] = float(TEMP)

    def run(with_usage: bool):
        k = dict(kwargs)
        if with_usage:
            k["stream_options"] = {"include_usage": True}
        start = time.perf_counter()
        first, parts, usage = None, [], None
        for chunk in client.chat.completions.create(**k):
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                if first is None:
                    first = time.perf_counter()
                parts.append(chunk.choices[0].delta.content)
        end = time.perf_counter()
        return "".join(parts), start, first, end, usage

    try:
        text, start, first, end, usage = run(with_usage=True)
    except Exception:  # some servers reject stream_options; retry without
        text, start, first, end, usage = run(with_usage=False)

    prompt_chars = sum(len(m["content"]) for m in messages)
    return {
        "text": text,
        "ttft": (first or end) - start,
        "latency": end - start,
        "in_tok": usage.prompt_tokens if usage else max(1, prompt_chars // 4),
        "out_tok": usage.completion_tokens if usage else max(1, len(text) // 4),
        "usage_reported": usage is not None,
    }


# ---------- Part A: latency / throughput / cost ----------
def benchmark(profile, client):
    rows = []
    for i, prompt in enumerate(PROMPTS):
        r = timed_chat(client, profile["model"], [{"role": "user", "content": prompt}])
        decode_time = max(r["latency"] - r["ttft"], 1e-6)
        cost = (r["in_tok"] * profile["price_in"] + r["out_tok"] * profile["price_out"]) / 1e6
        rows.append({
            "profile": profile["name"], "prompt": i, "ttft_s": round(r["ttft"], 3),
            "latency_s": round(r["latency"], 3), "in_tok": r["in_tok"], "out_tok": r["out_tok"],
            "tok_per_s": round(r["out_tok"] / decode_time, 1), "cost_usd": cost,
            "usage_reported": r["usage_reported"],
        })
        print(f"  [{profile['name']}] prompt {i}: {r['latency']:.2f}s, {r['out_tok']} out tokens")
        if profile["delay_s"] > 0 and i < len(PROMPTS) - 1:
            time.sleep(profile["delay_s"])
    return rows


# ---------- Part B: structured output reliability ----------
class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")  # -> additionalProperties:false (needed by strict mode)
    category: Literal["billing", "technical", "account", "other"]
    priority: Literal["low", "medium", "high"]
    summary: str


TICKETS = [
    "I was charged twice for my subscription this month, please fix ASAP!",
    "The VPN drops every 10 minutes since the last update. Very annoying.",
    "I can't reset my password, the email never arrives.",
    "Do you have a dark mode planned for the mobile app? Just curious.",
    "Production API is returning 500 errors for all customers right now!!",
]
SYSTEM_JSON = (
    "You triage support tickets. Reply with ONLY a JSON object with keys "
    "category (billing|technical|account|other), priority (low|medium|high), summary (string)."
)


def structured_test(profile, client, n=10):
    results = {}
    for mode in ("prompt_only", "json_schema"):
        ok = 0
        for i in range(n):
            msgs = [{"role": "system", "content": SYSTEM_JSON},
                    {"role": "user", "content": TICKETS[i % len(TICKETS)]}]
            extra = {}
            if mode == "json_schema":
                extra["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "ticket", "schema": Ticket.model_json_schema(), "strict": True},
                }
            try:
                r = timed_chat(client, profile["model"], msgs, **extra)
                Ticket.model_validate_json(r["text"])  # strict: raw text must be valid JSON
                ok += 1
            except (ValidationError, ValueError):
                pass
            except Exception as e:  # e.g. provider doesn't support json_schema
                print(f"  [{profile['name']}] {mode} error: {type(e).__name__}: {str(e)[:120]}")
                break
            if profile["delay_s"] > 0 and i < n - 1:
                time.sleep(profile["delay_s"])
        results[mode] = f"{ok}/{n}"
    return results


def summarize(rows):
    def med(key):
        return statistics.median(r[key] for r in rows)
    return {
        "median_ttft_s": round(med("ttft_s"), 2),
        "median_latency_s": round(med("latency_s"), 2),
        "median_tok_per_s": round(med("tok_per_s"), 1),
        "avg_out_tok": round(statistics.mean(r["out_tok"] for r in rows), 0),
        "cost_per_1k_req_usd": round(sum(r["cost_usd"] for r in rows) / len(rows) * 1000, 2),
    }


def main():
    if not PROFILES:
        raise SystemExit("No profiles configured. Copy .env.example to .env and fill it in.")
    all_rows, report = [], {}
    for p in PROFILES:
        print(f"\n== {p['name']} ({p['model']} @ {p['base_url']})")
        client = OpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=180)
        rows = benchmark(p, client)
        all_rows += rows
        report[p["name"]] = {**summarize(rows), **{f"struct_{k}": v for k, v in structured_test(p, client).items()}}

    with open("results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    metrics = list(next(iter(report.values())).keys())
    print("\n| metric | " + " | ".join(report) + " |")
    print("|---|" + "---|" * len(report))
    for m in metrics:
        print(f"| {m} | " + " | ".join(str(report[k][m]) for k in report) + " |")
    print("\nRaw data saved to results.csv")


if __name__ == "__main__":
    main()
