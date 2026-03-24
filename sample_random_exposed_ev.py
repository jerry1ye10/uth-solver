#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import random
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


RANKS = "23456789TJQKA"
SUITS = "cdhs"
FULL_DECK = [f"{rank}{suit}" for rank in RANKS for suit in SUITS]
CARD_INDEX = {card: index for index, card in enumerate(FULL_DECK)}
EV_PATTERN = re.compile(
    r"EV\(4x\)\s+=\s+([-0-9.]+)\s*\n"
    r"EV\(check\)\s+=\s+([-0-9.]+)\s*\n"
    r"Best EV\s+=\s+([-0-9.]+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sample many random exposed-card states and evaluate exact preflop EVs "
            "with the uth_exact_solver binary."
        )
    )
    parser.add_argument(
        "--hero",
        nargs=2,
        default=["3c", "3d"],
        metavar=("CARD1", "CARD2"),
        help="Hero hole cards. Default: 3c 3d",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Number of unique random exposed-card samples to evaluate. Default: 1000",
    )
    parser.add_argument(
        "--exposed-count",
        type=int,
        default=10,
        help="How many random exposed/dead cards to reveal. Default: 10",
    )
    parser.add_argument(
        "--baseline",
        choices=("4x", "check"),
        default="4x",
        help=(
            "Baseline action for gain calculations. For pocket 3s, "
            "the normal preflop baseline is 4x."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for reproducible exposed-card samples. Default: 0",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 1) - 1),
        help="How many solver processes to run in parallel. Default: cpu_count - 1",
    )
    parser.add_argument(
        "--binary",
        default="./uth_exact_solver",
        help="Path to the compiled exact solver binary. Default: ./uth_exact_solver",
    )
    parser.add_argument(
        "--csv",
        help="Optional path to write per-sample results as CSV.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many highest-gain samples to print. Default: 5",
    )
    return parser.parse_args()


def validate_cards(cards: Sequence[str], label: str) -> None:
    if len(cards) != len(set(cards)):
        raise ValueError(f"duplicate cards found in {label}: {cards}")
    invalid = [card for card in cards if card not in CARD_INDEX]
    if invalid:
        raise ValueError(f"invalid card(s) in {label}: {invalid}")


def generate_unique_exposed_samples(
    hero: Sequence[str],
    exposed_count: int,
    sample_count: int,
    seed: int,
) -> List[Tuple[str, ...]]:
    rng = random.Random(seed)
    remaining = [card for card in FULL_DECK if card not in set(hero)]
    seen = set()
    samples: List[Tuple[str, ...]] = []

    while len(samples) < sample_count:
        exposed = tuple(sorted(rng.sample(remaining, exposed_count), key=CARD_INDEX.__getitem__))
        if exposed in seen:
            continue
        seen.add(exposed)
        samples.append(exposed)
    return samples


def run_solver(
    binary: str,
    hero: Sequence[str],
    exposed: Sequence[str],
    baseline_action: str,
) -> dict:
    proc = subprocess.run(
        [binary, *hero, *exposed],
        check=True,
        capture_output=True,
        text=True,
    )
    match = EV_PATTERN.search(proc.stdout)
    if not match:
        raise RuntimeError(f"could not parse solver output:\n{proc.stdout}")

    ev_4x = float(match.group(1))
    ev_check = float(match.group(2))
    best_ev = float(match.group(3))
    best_action = "4x" if ev_4x >= ev_check else "check"
    baseline_ev = ev_4x if baseline_action == "4x" else ev_check

    return {
        "hero": " ".join(hero),
        "exposed": " ".join(exposed),
        "ev_4x": ev_4x,
        "ev_check": ev_check,
        "best_ev": best_ev,
        "best_action": best_action,
        "baseline_action": baseline_action,
        "baseline_ev": baseline_ev,
        "gain_vs_baseline": best_ev - baseline_ev,
    }


def maybe_write_csv(path: str | None, rows: Iterable[dict]) -> None:
    if not path:
        return
    fieldnames = [
        "hero",
        "exposed",
        "ev_4x",
        "ev_check",
        "best_ev",
        "best_action",
        "baseline_action",
        "baseline_ev",
        "gain_vs_baseline",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def main() -> int:
    args = parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    hero = [card.strip() for card in args.hero]
    validate_cards(hero, "hero")

    binary_path = Path(args.binary).resolve()
    if not binary_path.exists():
        raise FileNotFoundError(
            f"solver binary not found at {binary_path}. "
            f"Compile it first with: gcc -O3 -march=native -std=c11 -Wall -Wextra -pedantic uth_exact_solver.c -o uth_exact_solver"
        )

    if args.exposed_count <= 0:
        raise ValueError("--exposed-count must be positive")
    if args.samples <= 0:
        raise ValueError("--samples must be positive")
    if args.exposed_count > len(FULL_DECK) - len(hero):
        raise ValueError("--exposed-count is too large for the remaining deck")

    sample_states = generate_unique_exposed_samples(
        hero=hero,
        exposed_count=args.exposed_count,
        sample_count=args.samples,
        seed=args.seed,
    )

    print(
        f"Running {args.samples} exact samples for hero {' '.join(hero)} "
        f"with {args.exposed_count} random exposed cards each."
    )
    print(
        f"Baseline action: {args.baseline} | Jobs: {args.jobs} | Seed: {args.seed} | Binary: {binary_path}"
    )

    start = time.time()
    results: List[dict] = []
    completed = 0
    progress_step = max(1, args.samples // 20)

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        future_to_state = {
            executor.submit(run_solver, str(binary_path), hero, exposed, args.baseline): (index, exposed)
            for index, exposed in enumerate(sample_states, start=1)
        }
        for future in as_completed(future_to_state):
            sample_index, exposed = future_to_state[future]
            row = future.result()
            results.append(row)
            completed += 1
            print(
                f"[{completed:4d}/{args.samples}] "
                f"sample={sample_index:4d} "
                f"best={row['best_action']:>5s} "
                f"gain={row['gain_vs_baseline']:+.6f} "
                f"ev4={row['ev_4x']:+.6f} "
                f"evcheck={row['ev_check']:+.6f} "
                f"exposed=[{' '.join(exposed)}]"
            )
            if completed % progress_step == 0 or completed == args.samples:
                elapsed = time.time() - start
                rate = completed / elapsed if elapsed > 0 else 0.0
                print(
                    f"Completed {completed}/{args.samples} samples "
                    f"({rate:.2f} samples/sec, elapsed {elapsed:.1f}s)"
                )

    results.sort(key=lambda row: row["gain_vs_baseline"], reverse=True)
    maybe_write_csv(args.csv, results)

    gains = sorted(row["gain_vs_baseline"] for row in results)
    ev_4x_values = [row["ev_4x"] for row in results]
    ev_check_values = [row["ev_check"] for row in results]
    best_values = [row["best_ev"] for row in results]
    baseline_values = [row["baseline_ev"] for row in results]
    check_better_count = sum(1 for row in results if row["ev_check"] > row["ev_4x"])
    four_x_better_count = sum(1 for row in results if row["ev_4x"] >= row["ev_check"])
    positive_gain_count = sum(1 for row in results if row["gain_vs_baseline"] > 0.0)

    print()
    print("Summary")
    print(f"  Average EV(4x):            {statistics.mean(ev_4x_values):.6f}")
    print(f"  Average EV(check):         {statistics.mean(ev_check_values):.6f}")
    print(f"  Average best EV:           {statistics.mean(best_values):.6f}")
    print(f"  Average baseline EV:       {statistics.mean(baseline_values):.6f}")
    print(f"  Average gain vs baseline:  {statistics.mean(gains):.6f}")
    print(f"  Median gain vs baseline:   {statistics.median(gains):.6f}")
    print(f"  P05 / P95 gain:            {percentile(gains, 0.05):.6f} / {percentile(gains, 0.95):.6f}")
    print(f"  Check better rate:         {check_better_count / args.samples:.2%}")
    print(f"  4x better-or-equal rate:   {four_x_better_count / args.samples:.2%}")
    print(f"  Positive gain rate:        {positive_gain_count / args.samples:.2%}")
    print(f"  Total elapsed:             {time.time() - start:.1f}s")

    print()
    print(f"Top {min(args.top, len(results))} samples by gain")
    for row in results[: args.top]:
        print(
            f"  gain={row['gain_vs_baseline']:+.6f} "
            f"best={row['best_action']} "
            f"ev4={row['ev_4x']:+.6f} "
            f"evcheck={row['ev_check']:+.6f} "
            f"exposed=[{row['exposed']}]"
        )

    if args.csv:
        print()
        print(f"Wrote CSV to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
