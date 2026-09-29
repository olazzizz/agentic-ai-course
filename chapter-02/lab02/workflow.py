#!/usr/bin/env python3
"""Lab 2, part C: the SAME Q1-revenue task as a fixed workflow (code decides the path, the model does one job).

    uv run workflow.py [--profile hosted|local]
"""
import argparse, json, re

from agent import calculator, load_profile, read_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="hosted")
    cfg = load_profile(ap.parse_args().profile)

    text = read_file("q1_sales.txt")                                   # step 1: plain code
    resp = cfg["client"].chat.completions.create(                      # step 2: ONE model call
        model=cfg["model"], temperature=0,
        messages=[{"role": "system", "content": "Extract the monthly revenue numbers. "
                   "Reply with ONLY a JSON list of numbers, for example [1, 2, 3]."},
                  {"role": "user", "content": text}])
    numbers = json.loads(re.search(r"\[.*\]", resp.choices[0].message.content, re.S).group(0))
    total = calculator("+".join(str(n) for n in numbers))              # step 3: plain code
    average = calculator(f"({total})/{len(numbers)}")

    u = resp.usage
    cost = (u.prompt_tokens * cfg["price_in"] + u.completion_tokens * cfg["price_out"]) / 1e6 if u else 0
    print(f"Q1 total: {total} | monthly average: {average}")
    print(f"1 model call | {u.prompt_tokens if u else '?'}+{u.completion_tokens if u else '?'} tok | ${cost:.5f}")


if __name__ == "__main__":
    main()
