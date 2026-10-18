#!/usr/bin/env python3
"""Lab 3 experiment runner: the same tasks under several tool configurations, side by side.

    uv run experiment.py --configs good vague swapped --repeat 3 --max-cost 1.00
    uv run experiment.py --configs good decoys10 decoys20 --repeat 3 --only sql-top-category,api-rate-chf,file-two-calcs
    uv run experiment.py --list-configs

Runs are ordered task by task, config by config, so if you stop early (Ctrl+C or --max-cost)
every config has seen the same tasks and the comparison stays fair.
"""
import argparse, json, os, sys
from collections import defaultdict
from datetime import datetime

import agent, tools

GRADED = ("PASS", "FAIL", "UNGROUNDED")


def compare(rows: list, configs: list, repeat: int) -> None:
    print(f"\n{'config':<20}{'runs':>6}{'pass':>7}{'rate':>7}{'pass^' + str(repeat):>8}{'ungr':>6}{'steps':>7}{'errs':>6}{'decoy':>7}{'$/run':>10}{'$/success':>11}{'secs':>7}")
    for c in configs:
        rs = [r for r in rows if r["config"] == c]
        g = [r for r in rs if r["verdict"] in GRADED]
        if not g:
            continue
        by_task = defaultdict(list)
        for r in g:
            by_task[r["id"]].append(r["verdict"] == "PASS")
        complete = [v for v in by_task.values() if len(v) == repeat]
        passes = sum(r["verdict"] == "PASS" for r in g)
        cost_ok = f"{sum(r['cost'] for r in g) / passes:.5f}" if passes else "never"
        pk = f"{sum(all(v) for v in complete)}/{len(complete)}"
        print(f"{c:<20}{len(g):>6}{passes:>7}{passes / len(g):>7.0%}{pk:>8}{sum(r['verdict'] == 'UNGROUNDED' for r in g) or '':>6}"
              f"{sum(r['steps'] for r in rs) / len(rs):>7.1f}{sum(r['tool_errors'] for r in rs) or '':>6}"
              f"{sum(r['decoy_calls'] for r in rs) or '':>7}{sum(r['cost'] for r in rs) / len(rs):>10.5f}{cost_ok:>11}"
              f"{sum(r['seconds'] for r in rs) / len(rs):>7.1f}")
    print("\nThe baseline is the first config. Small gaps between configs are noise at these run counts: "
          "look for gaps of 15+ points, and read the traces of the tasks that changed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="tasks.json")
    ap.add_argument("--configs", nargs="+", default=["good", "vague", "swapped"], help="see --list-configs")
    ap.add_argument("--max-cost", type=float, default=float(os.getenv("MAX_EXPERIMENT_COST", "1.00")),
                    help="stop the whole experiment when the summed cost passes this many USD (needs HOSTED_PRICE_IN/OUT)")
    ap.add_argument("--list-configs", action="store_true")
    agent.add_common_args(ap)
    a = ap.parse_args()
    if a.list_configs:
        for name, kw in tools.CONFIGS.items():
            print(f"{name:<20}{kw or '(baseline)'}")
        return
    for c in a.configs:
        agent.setup_config(c)                                  # fail early on a typo
    cfg = agent.load_profile(a.profile, a.timeout)
    if cfg["price_in"] == 0 and cfg["price_out"] == 0:
        print("Note: prices are 0 in .env, so --max-cost cannot protect you. Set HOSTED_PRICE_IN/OUT "
              "(bash ../../lab-env/labenv.sh price <model-id>) or keep the run count small.")
    with open(a.tasks) as f:
        tasks = agent.select_tasks(json.load(f), a.only)
    total_runs = len(tasks) * a.repeat * len(a.configs)
    print(f"{len(tasks)} tasks x {a.repeat} repeats x {len(a.configs)} configs = {total_runs} runs | cost cap ${a.max_cost:.2f}")

    (agent.HERE / "logs").mkdir(exist_ok=True)
    logfile = agent.HERE / "logs" / f"{datetime.now():%Y%m%d-%H%M%S}-{a.profile}-experiment.jsonl"
    log = agent.make_logger(logfile, echo=False)
    out = agent.HERE / f"results-experiment-{a.profile}.csv"
    settings = agent.settings_from(a)

    rows, spent, stopped = [], 0.0, None
    try:
        for t in tasks:
            for k in range(1, a.repeat + 1):
                for c in a.configs:
                    print(f"[{c}] {t['id']} (run {k}/{a.repeat})", end="")
                    rows.append(agent.run_task(cfg, t, k, a.repeat, c, settings, log, a.verify, quiet=True))
                    agent.save_csv(rows, out)
                    spent += rows[-1]["cost"]
                    if spent > a.max_cost:
                        stopped = f"cost cap reached (${spent:.4f} > ${a.max_cost:.2f})"
                        raise StopIteration
    except StopIteration:
        print(f"\nStopped early: {stopped}")
    except KeyboardInterrupt:
        print("\nInterrupted: comparing the runs completed so far.")

    if rows:
        print(f"\nSaved {out.name} | total ${spent:.5f}")
        compare(rows, a.configs, a.repeat)
    print(f"Full trace: {logfile.relative_to(agent.HERE)}")


if __name__ == "__main__":
    main()
