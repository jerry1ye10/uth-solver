#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from sample_random_exposed_ev import (
    generate_unique_exposed_samples,
    maybe_write_csv,
    percentile,
    run_solver,
    validate_cards,
)


TOTAL_STARTING_HANDS = 1326
PAIR_WEIGHT = 6.0 / TOTAL_STARTING_HANDS
SUITED_WEIGHT = 4.0 / TOTAL_STARTING_HANDS
OFFSUIT_WEIGHT = 12.0 / TOTAL_STARTING_HANDS
LOW_TO_HIGH_RANKS = "23456789TJQKA"


@dataclass(frozen=True)
class HandSpec:
    label: str
    hero: Tuple[str, str]
    family: str
    baseline: str
    weight: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run random exposed-card sampling for a fixed family of UTH preflop hands, "
            "write per-hand CSVs, and keep a master summary with weighted contributions."
        )
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Random exposed-card samples per hand class. Default: 1000",
    )
    parser.add_argument(
        "--exposed-count",
        type=int,
        default=10,
        help="How many exposed/dead cards to reveal per sample. Default: 10",
    )
    parser.add_argument(
        "--sample-jobs",
        type=int,
        default=max(1, (os.cpu_count() or 1) - 1),
        help="Parallel solver processes per hand class. Default: cpu_count - 1",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Base random seed. Each hand class derives its own deterministic seed from this. Default: 0",
    )
    parser.add_argument(
        "--binary",
        default="./uth_exact_solver",
        help="Path to the compiled exact solver binary. Default: ./uth_exact_solver",
    )
    parser.add_argument(
        "--default-baseline",
        choices=("4x", "check"),
        default="4x",
        help="Baseline action for gain calculations unless overridden by --baseline-map. Default: 4x",
    )
    parser.add_argument(
        "--baseline-map",
        help=(
            "Optional CSV with columns label,baseline to override the default baseline "
            "for specific hand classes."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="edge_family_sampling_out",
        help="Directory to write per-hand CSVs and summary files. Default: edge_family_sampling_out",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="How many highest-gain samples to print per hand class. Default: 3",
    )
    parser.add_argument(
        "--limit-hands",
        type=int,
        help="Optional limit for smoke testing only the first N hand classes.",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help=(
            "Split the hand-class list into this many shards. "
            "Use with --shard-index on multiple boxes."
        ),
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help=(
            "Zero-based shard index to run from the hand-class list. "
            "Must satisfy 0 <= shard-index < shard-count."
        ),
    )
    parser.add_argument(
        "--quiet-samples",
        action="store_true",
        help="If set, suppress per-sample lines and only print per-hand summaries.",
    )
    return parser.parse_args()


def suited_hero(high_rank: str, low_rank: str) -> Tuple[str, str]:
    return (f"{high_rank}c", f"{low_rank}c")


def offsuit_hero(high_rank: str, low_rank: str) -> Tuple[str, str]:
    return (f"{high_rank}c", f"{low_rank}d")


def pair_hero(rank: str) -> Tuple[str, str]:
    return (f"{rank}c", f"{rank}d")


def pair_specs(default_baseline: str) -> List[HandSpec]:
    specs: List[HandSpec] = []
    for rank in "2345":
        specs.append(
            HandSpec(
                label=f"{rank}{rank}",
                hero=pair_hero(rank),
                family="pairs_22_55",
                baseline=default_baseline,
                weight=PAIR_WEIGHT,
            )
        )
    return specs


def rank_family_specs(
    high_rank: str,
    low_ranks: Sequence[str],
    family: str,
    default_baseline: str,
) -> List[HandSpec]:
    specs: List[HandSpec] = []
    for low_rank in low_ranks:
        specs.append(
            HandSpec(
                label=f"{high_rank}{low_rank}s",
                hero=suited_hero(high_rank, low_rank),
                family=family,
                baseline=default_baseline,
                weight=SUITED_WEIGHT,
            )
        )
        specs.append(
            HandSpec(
                label=f"{high_rank}{low_rank}o",
                hero=offsuit_hero(high_rank, low_rank),
                family=family,
                baseline=default_baseline,
                weight=OFFSUIT_WEIGHT,
            )
        )
    return specs


def explicit_specs(default_baseline: str) -> List[HandSpec]:
    return [
        HandSpec("T8s", suited_hero("T", "8"), "tx_explicit", default_baseline, SUITED_WEIGHT),
        HandSpec("T9s", suited_hero("T", "9"), "tx_explicit", default_baseline, SUITED_WEIGHT),
        HandSpec("T8o", offsuit_hero("T", "8"), "tx_explicit", default_baseline, OFFSUIT_WEIGHT),
        HandSpec("T9o", offsuit_hero("T", "9"), "tx_explicit", default_baseline, OFFSUIT_WEIGHT),
    ]


def build_hand_specs(default_baseline: str) -> List[HandSpec]:
    specs: List[HandSpec] = []
    specs.extend(pair_specs(default_baseline))
    specs.extend(rank_family_specs("A", "23456789", "ax_below_at", default_baseline))
    specs.extend(rank_family_specs("K", "23456789TJQ", "any_kx", default_baseline))
    specs.extend(rank_family_specs("Q", "23456789TJ", "any_qx", default_baseline))
    specs.extend(rank_family_specs("J", "23456789T", "any_jx", default_baseline))
    specs.extend(explicit_specs(default_baseline))

    seen = set()
    unique_specs: List[HandSpec] = []
    for spec in specs:
        if spec.label in seen:
            continue
        seen.add(spec.label)
        unique_specs.append(spec)
    return unique_specs


def read_baseline_overrides(path: str | None) -> Dict[str, str]:
    if not path:
        return {}
    overrides: Dict[str, str] = {}
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            label = row["label"].strip()
            baseline = row["baseline"].strip()
            if baseline not in {"4x", "check"}:
                raise ValueError(f"invalid baseline {baseline!r} for label {label!r}")
            overrides[label] = baseline
    return overrides


def apply_baseline_overrides(
    specs: Sequence[HandSpec],
    overrides: Dict[str, str],
) -> List[HandSpec]:
    applied: List[HandSpec] = []
    for spec in specs:
        baseline = overrides.get(spec.label, spec.baseline)
        applied.append(
            HandSpec(
                label=spec.label,
                hero=spec.hero,
                family=spec.family,
                baseline=baseline,
                weight=spec.weight,
            )
        )
    return applied


def hand_seed(base_seed: int, label: str) -> int:
    acc = base_seed
    for char in label:
        acc = (acc * 131 + ord(char)) % (2**32)
    return acc


def summary_row_for_results(
    spec: HandSpec,
    results: Sequence[dict],
    elapsed: float,
) -> dict:
    gains = sorted(row["gain_vs_baseline"] for row in results)
    ev_4x_values = [row["ev_4x"] for row in results]
    ev_check_values = [row["ev_check"] for row in results]
    best_values = [row["best_ev"] for row in results]
    baseline_values = [row["baseline_ev"] for row in results]
    check_better_count = sum(1 for row in results if row["ev_check"] > row["ev_4x"])
    positive_gain_count = sum(1 for row in results if row["gain_vs_baseline"] > 0.0)

    gain_mean = statistics.mean(gains)
    gain_sd = statistics.stdev(gains) if len(gains) > 1 else 0.0
    gain_se = gain_sd / math.sqrt(len(gains)) if gains else 0.0

    return {
        "label": spec.label,
        "hero": " ".join(spec.hero),
        "family": spec.family,
        "baseline_action": spec.baseline,
        "weight": spec.weight,
        "samples": len(results),
        "avg_ev_4x": statistics.mean(ev_4x_values),
        "avg_ev_check": statistics.mean(ev_check_values),
        "avg_best_ev": statistics.mean(best_values),
        "avg_baseline_ev": statistics.mean(baseline_values),
        "avg_gain_vs_baseline": gain_mean,
        "median_gain_vs_baseline": statistics.median(gains),
        "p05_gain_vs_baseline": percentile(gains, 0.05),
        "p95_gain_vs_baseline": percentile(gains, 0.95),
        "gain_sd": gain_sd,
        "gain_se": gain_se,
        "gain_ci95_low": gain_mean - 1.96 * gain_se,
        "gain_ci95_high": gain_mean + 1.96 * gain_se,
        "check_better_rate": check_better_count / len(results),
        "positive_gain_rate": positive_gain_count / len(results),
        "weighted_gain_contribution": spec.weight * gain_mean,
        "weighted_gain_se": spec.weight * gain_se,
        "elapsed_seconds": elapsed,
    }


def write_summary_csv(path: Path, rows: Sequence[dict]) -> None:
    fieldnames = [
        "label",
        "hero",
        "family",
        "baseline_action",
        "weight",
        "samples",
        "avg_ev_4x",
        "avg_ev_check",
        "avg_best_ev",
        "avg_baseline_ev",
        "avg_gain_vs_baseline",
        "median_gain_vs_baseline",
        "p05_gain_vs_baseline",
        "p95_gain_vs_baseline",
        "gain_sd",
        "gain_se",
        "gain_ci95_low",
        "gain_ci95_high",
        "check_better_rate",
        "positive_gain_rate",
        "weighted_gain_contribution",
        "weighted_gain_se",
        "elapsed_seconds",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_hand_summary(row: dict) -> None:
    print(
        f"{row['label']:>4s} | baseline={row['baseline_action']:<5s} "
        f"| avg_gain={row['avg_gain_vs_baseline']:+.6f} "
        f"| ci95=[{row['gain_ci95_low']:+.6f}, {row['gain_ci95_high']:+.6f}] "
        f"| weighted={row['weighted_gain_contribution']:+.6f} "
        f"| check_rate={row['check_better_rate']:.2%}"
    )


def print_aggregate(rows: Sequence[dict]) -> None:
    total_weighted_gain = sum(row["weighted_gain_contribution"] for row in rows)
    total_weighted_se = math.sqrt(sum(row["weighted_gain_se"] ** 2 for row in rows))
    print()
    print("Aggregate")
    print(f"  Hand classes:                {len(rows)}")
    print(f"  Total weighted gain:        {total_weighted_gain:+.6f}")
    print(f"  Total weighted 1-sigma:     {total_weighted_se:.6f}")
    print(
        f"  Total weighted 95% CI:      "
        f"[{total_weighted_gain - 1.96 * total_weighted_se:+.6f}, "
        f"{total_weighted_gain + 1.96 * total_weighted_se:+.6f}]"
    )


def run_one_hand_class(
    spec: HandSpec,
    binary_path: Path,
    output_csv_path: Path,
    samples: int,
    exposed_count: int,
    sample_jobs: int,
    seed: int,
    top: int,
    quiet_samples: bool,
) -> dict:
    validate_cards(spec.hero, spec.label)
    sample_states = generate_unique_exposed_samples(
        hero=spec.hero,
        exposed_count=exposed_count,
        sample_count=samples,
        seed=seed,
    )

    print()
    print(
        f"Running {spec.label} ({spec.hero[0]} {spec.hero[1]}) "
        f"| family={spec.family} | baseline={spec.baseline} | samples={samples} | seed={seed}"
    )

    start = time.time()
    results: List[dict] = []
    completed = 0
    progress_step = max(1, samples // 10)

    with ThreadPoolExecutor(max_workers=sample_jobs) as executor:
        future_to_state = {
            executor.submit(run_solver, str(binary_path), spec.hero, exposed, spec.baseline): (index, exposed)
            for index, exposed in enumerate(sample_states, start=1)
        }
        for future in as_completed(future_to_state):
            sample_index, exposed = future_to_state[future]
            row = future.result()
            results.append(row)
            completed += 1
            if not quiet_samples:
                print(
                    f"[{spec.label} {completed:4d}/{samples}] "
                    f"sample={sample_index:4d} "
                    f"best={row['best_action']:>5s} "
                    f"gain={row['gain_vs_baseline']:+.6f} "
                    f"ev4={row['ev_4x']:+.6f} "
                    f"evcheck={row['ev_check']:+.6f} "
                    f"exposed=[{' '.join(exposed)}]"
                )
            if completed % progress_step == 0 or completed == samples:
                elapsed = time.time() - start
                rate = completed / elapsed if elapsed > 0 else 0.0
                print(
                    f"{spec.label}: completed {completed}/{samples} "
                    f"({rate:.2f} samples/sec, elapsed {elapsed:.1f}s)"
                )

    results.sort(key=lambda row: row["gain_vs_baseline"], reverse=True)
    maybe_write_csv(str(output_csv_path), results)

    summary = summary_row_for_results(spec, results, time.time() - start)
    print_hand_summary(summary)

    if top > 0:
        print(f"Top {min(top, len(results))} samples for {spec.label}")
        for row in results[:top]:
            print(
                f"  gain={row['gain_vs_baseline']:+.6f} "
                f"best={row['best_action']} "
                f"ev4={row['ev_4x']:+.6f} "
                f"evcheck={row['ev_check']:+.6f} "
                f"exposed=[{row['exposed']}]"
            )

    print(f"Wrote per-hand CSV: {output_csv_path}")
    return summary


def main() -> int:
    args = parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    binary_path = Path(args.binary).resolve()
    if not binary_path.exists():
        raise FileNotFoundError(
            f"solver binary not found at {binary_path}. "
            f"Compile it first with: gcc -O3 -march=native -std=c11 -Wall -Wextra -pedantic uth_exact_solver.c -o uth_exact_solver"
        )

    specs = build_hand_specs(args.default_baseline)
    specs = apply_baseline_overrides(specs, read_baseline_overrides(args.baseline_map))
    if args.limit_hands is not None:
        specs = specs[: args.limit_hands]
    if args.shard_count <= 0:
        raise ValueError("--shard-count must be positive")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise ValueError("--shard-index must satisfy 0 <= shard-index < shard-count")
    if args.shard_count > 1:
        specs = [
            spec
            for index, spec in enumerate(specs)
            if index % args.shard_count == args.shard_index
        ]

    output_dir = Path(args.output_dir)
    per_hand_dir = output_dir / "per_hand"
    output_dir.mkdir(parents=True, exist_ok=True)
    per_hand_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "hand_manifest.csv"
    with open(manifest_path, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["label", "hero", "family", "baseline_action", "weight"],
        )
        writer.writeheader()
        for spec in specs:
            writer.writerow(
                {
                    "label": spec.label,
                    "hero": " ".join(spec.hero),
                    "family": spec.family,
                    "baseline_action": spec.baseline,
                    "weight": spec.weight,
                }
            )

    print(f"Prepared {len(specs)} hand classes.")
    print(f"Output directory: {output_dir}")
    print(f"Manifest: {manifest_path}")
    if args.limit_hands is not None:
        print(f"Limit applied: first {args.limit_hands} hand classes only")
    if args.shard_count > 1:
        print(f"Shard: {args.shard_index + 1}/{args.shard_count}")

    overall_start = time.time()
    summary_rows: List[dict] = []
    summary_csv_path = output_dir / "summary.csv"

    for hand_index, spec in enumerate(specs, start=1):
        print()
        print(f"=== Hand {hand_index}/{len(specs)}: {spec.label} ===")
        summary_row = run_one_hand_class(
            spec=spec,
            binary_path=binary_path,
            output_csv_path=per_hand_dir / f"{spec.label}.csv",
            samples=args.samples,
            exposed_count=args.exposed_count,
            sample_jobs=args.sample_jobs,
            seed=hand_seed(args.seed, spec.label),
            top=args.top,
            quiet_samples=args.quiet_samples,
        )
        summary_rows.append(summary_row)
        write_summary_csv(summary_csv_path, summary_rows)
        print(f"Updated summary CSV: {summary_csv_path}")

    print()
    print(f"Total wall time: {time.time() - overall_start:.1f}s")
    print_aggregate(summary_rows)
    print()
    print(f"Final summary CSV: {summary_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
