#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge per-shard summary.csv files from GCP Batch or multi-box runs "
            "and print the combined weighted edge estimate."
        )
    )
    parser.add_argument(
        "--input-root",
        required=True,
        help="Root directory containing shard output folders.",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the merged summary CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_root = Path(args.input_root).resolve()
    summary_paths = sorted(input_root.rglob("summary.csv"))
    if not summary_paths:
        raise FileNotFoundError(f"no summary.csv files found under {input_root}")

    merged_rows: list[dict] = []
    seen_labels: set[str] = set()
    for path in summary_paths:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                label = row["label"]
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                merged_rows.append(row)

    merged_rows.sort(key=lambda row: row["label"])

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(merged_rows[0].keys()))
            writer.writeheader()
            for row in merged_rows:
                writer.writerow(row)
        print(f"Wrote merged summary: {output_path}")

    total_weighted_gain = sum(float(row["weighted_gain_contribution"]) for row in merged_rows)
    total_weighted_se = math.sqrt(sum(float(row["weighted_gain_se"]) ** 2 for row in merged_rows))

    print(f"Shard summaries read:      {len(summary_paths)}")
    print(f"Unique hand classes:       {len(merged_rows)}")
    print(f"Total weighted gain:       {total_weighted_gain:+.6f}")
    print(f"Total weighted 1-sigma:    {total_weighted_se:.6f}")
    print(
        "Total weighted 95% CI:     "
        f"[{total_weighted_gain - 1.96 * total_weighted_se:+.6f}, "
        f"{total_weighted_gain + 1.96 * total_weighted_se:+.6f}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
