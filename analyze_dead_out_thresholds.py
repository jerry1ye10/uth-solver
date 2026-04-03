#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize preflop 4x-vs-check behavior by dead-card 'outs' count, "
            "where outs means exposed cards matching either hero rank."
        )
    )
    parser.add_argument(
        "--per-hand-dir",
        default="edge_family_sampling_out/per_hand",
        help="Directory containing per-hand sample CSVs. Default: edge_family_sampling_out/per_hand",
    )
    parser.add_argument(
        "--manifest",
        default="edge_family_sampling_out/hand_manifest.csv",
        help="Manifest CSV listing hand labels and hero cards. Default: edge_family_sampling_out/hand_manifest.csv",
    )
    parser.add_argument(
        "--bucket-output",
        default="results/edge_family_sampling_84x1000/outs_bucket_summary.csv",
        help="Output CSV for per-hand, per-outs buckets.",
    )
    parser.add_argument(
        "--threshold-output",
        default="results/edge_family_sampling_84x1000/outs_threshold_summary.csv",
        help="Output CSV for per-hand rough thresholds.",
    )
    return parser.parse_args()


def card_rank(card: str) -> str:
    return card[0]


def hero_rank_set(hero_text: str) -> set[str]:
    return {card_rank(card) for card in hero_text.split()}


def count_dead_outs(hero_text: str, exposed_text: str) -> int:
    ranks = hero_rank_set(hero_text)
    return sum(1 for card in exposed_text.split() if card_rank(card) in ranks)


def avg(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    per_hand_dir = Path(args.per_hand_dir)
    manifest_path = Path(args.manifest)
    bucket_output = Path(args.bucket_output)
    threshold_output = Path(args.threshold_output)

    manifest_rows = list(csv.DictReader(manifest_path.open()))

    bucket_rows: list[dict] = []
    threshold_rows: list[dict] = []

    for manifest_row in manifest_rows:
        label = manifest_row["label"]
        hero = manifest_row["hero"]
        per_hand_path = per_hand_dir / f"{label}.csv"
        if not per_hand_path.exists():
            continue

        rows = list(csv.DictReader(per_hand_path.open()))
        by_outs: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            outs = count_dead_outs(hero, row["exposed"])
            by_outs[outs].append(row)

        observed_outs = sorted(by_outs)
        first_exact_outs = None
        first_exact_samples = None
        first_exact_avg_ev_4x = None
        first_exact_avg_ev_check = None
        rough_threshold_outs = None
        rough_threshold_samples = None
        rough_threshold_avg_ev_4x = None
        rough_threshold_avg_ev_check = None

        for outs in observed_outs:
            group = by_outs[outs]
            ev4_values = [float(row["ev_4x"]) for row in group]
            evc_values = [float(row["ev_check"]) for row in group]
            best_values = [float(row["best_ev"]) for row in group]
            avg_ev_4x = avg(ev4_values)
            avg_ev_check = avg(evc_values)
            avg_best_ev = avg(best_values)
            check_better_rate = sum(1 for row in group if float(row["ev_check"]) > float(row["ev_4x"])) / len(group)
            avg_check_minus_4x = avg_ev_check - avg_ev_4x
            preferred_action = "check" if avg_ev_check > avg_ev_4x else "4x"

            bucket_rows.append(
                {
                    "label": label,
                    "hero": hero,
                    "outs": outs,
                    "samples": len(group),
                    "avg_ev_4x": f"{avg_ev_4x:.9f}",
                    "avg_ev_check": f"{avg_ev_check:.9f}",
                    "avg_best_ev": f"{avg_best_ev:.9f}",
                    "avg_check_minus_4x": f"{avg_check_minus_4x:.9f}",
                    "check_better_rate": f"{check_better_rate:.6f}",
                    "preferred_action_by_average": preferred_action,
                }
            )

            if first_exact_outs is None and avg_ev_check > avg_ev_4x:
                first_exact_outs = outs
                first_exact_samples = len(group)
                first_exact_avg_ev_4x = avg_ev_4x
                first_exact_avg_ev_check = avg_ev_check

        for outs in observed_outs:
            pooled_rows = []
            for pooled_outs in observed_outs:
                if pooled_outs >= outs:
                    pooled_rows.extend(by_outs[pooled_outs])
            pooled_avg_ev_4x = avg([float(row["ev_4x"]) for row in pooled_rows])
            pooled_avg_ev_check = avg([float(row["ev_check"]) for row in pooled_rows])
            if rough_threshold_outs is None and pooled_avg_ev_check > pooled_avg_ev_4x:
                rough_threshold_outs = outs
                rough_threshold_samples = len(pooled_rows)
                rough_threshold_avg_ev_4x = pooled_avg_ev_4x
                rough_threshold_avg_ev_check = pooled_avg_ev_check

        threshold_rows.append(
            {
                "label": label,
                "hero": hero,
                "observed_outs": " ".join(str(outs) for outs in observed_outs),
                "max_outs": max(observed_outs) if observed_outs else "",
                "first_exact_outs_where_avg_check_exceeds_4x": "" if first_exact_outs is None else first_exact_outs,
                "first_exact_bucket_samples": "" if first_exact_samples is None else first_exact_samples,
                "first_exact_avg_ev_4x": "" if first_exact_avg_ev_4x is None else f"{first_exact_avg_ev_4x:.9f}",
                "first_exact_avg_ev_check": "" if first_exact_avg_ev_check is None else f"{first_exact_avg_ev_check:.9f}",
                "rough_threshold_outs_pooled": "" if rough_threshold_outs is None else rough_threshold_outs,
                "rough_threshold_samples": "" if rough_threshold_samples is None else rough_threshold_samples,
                "rough_threshold_avg_ev_4x": "" if rough_threshold_avg_ev_4x is None else f"{rough_threshold_avg_ev_4x:.9f}",
                "rough_threshold_avg_ev_check": "" if rough_threshold_avg_ev_check is None else f"{rough_threshold_avg_ev_check:.9f}",
            }
        )

    bucket_rows.sort(key=lambda row: (row["label"], int(row["outs"])))
    threshold_rows.sort(key=lambda row: row["label"])

    write_csv(
        bucket_output,
        [
            "label",
            "hero",
            "outs",
            "samples",
            "avg_ev_4x",
            "avg_ev_check",
            "avg_best_ev",
            "avg_check_minus_4x",
            "check_better_rate",
            "preferred_action_by_average",
        ],
        bucket_rows,
    )
    write_csv(
        threshold_output,
        [
            "label",
            "hero",
            "observed_outs",
            "max_outs",
            "first_exact_outs_where_avg_check_exceeds_4x",
            "first_exact_bucket_samples",
            "first_exact_avg_ev_4x",
            "first_exact_avg_ev_check",
            "rough_threshold_outs_pooled",
            "rough_threshold_samples",
            "rough_threshold_avg_ev_4x",
            "rough_threshold_avg_ev_check",
        ],
        threshold_rows,
    )

    print(f"Wrote bucket summary: {bucket_output}")
    print(f"Wrote threshold summary: {threshold_output}")
    print(f"Hands processed: {len(threshold_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
