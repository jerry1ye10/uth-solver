#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


TOTAL_STARTING_HANDS = 1326


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert family sampling summary.csv into per-hand dollar contributions using "
            "switch_gain = avg_best_ev - max(avg_ev_4x, avg_ev_check)."
        )
    )
    parser.add_argument(
        "--summary",
        default="edge_family_sampling_out/summary.csv",
        help="Input summary CSV. Default: edge_family_sampling_out/summary.csv",
    )
    parser.add_argument(
        "--manifest",
        default="edge_family_sampling_out/hand_manifest.csv",
        help="Input manifest CSV to detect missing hand classes. Default: edge_family_sampling_out/hand_manifest.csv",
    )
    parser.add_argument(
        "--ante",
        type=float,
        default=1000.0,
        help="Ante value in dollars for EV=1. Default: 1000",
    )
    parser.add_argument(
        "--output",
        default="edge_family_sampling_out/switch_gain_dollars.csv",
        help="Output CSV path. Default: edge_family_sampling_out/switch_gain_dollars.csv",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary_path = Path(args.summary)
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)

    rows = list(csv.DictReader(summary_path.open()))
    manifest_rows = list(csv.DictReader(manifest_path.open()))
    manifest_labels = [row["label"] for row in manifest_rows]
    summary_labels = {row["label"] for row in rows}
    missing_labels = [label for label in manifest_labels if label not in summary_labels]

    converted_rows = []
    total_dollars = 0.0
    covered_weight = 0.0

    for row in rows:
        avg_ev_4x = float(row["avg_ev_4x"])
        avg_ev_check = float(row["avg_ev_check"])
        avg_best_ev = float(row["avg_best_ev"])
        weight = float(row["weight"])
        switch_gain = avg_best_ev - max(avg_ev_4x, avg_ev_check)
        dollar_contribution = switch_gain * weight * args.ante
        total_dollars += dollar_contribution
        covered_weight += weight

        converted_rows.append(
            {
                "label": row["label"],
                "family": row["family"],
                "hero": row["hero"],
                "avg_ev_4x": f"{avg_ev_4x:.9f}",
                "avg_ev_check": f"{avg_ev_check:.9f}",
                "avg_best_ev": f"{avg_best_ev:.9f}",
                "switch_gain": f"{switch_gain:.9f}",
                "weight": f"{weight:.12f}",
                "combos": f"{weight * TOTAL_STARTING_HANDS:.0f}",
                "dollar_contribution": f"{dollar_contribution:.9f}",
            }
        )

    converted_rows.sort(key=lambda row: row["label"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        fieldnames = [
            "label",
            "family",
            "hero",
            "avg_ev_4x",
            "avg_ev_check",
            "avg_best_ev",
            "switch_gain",
            "weight",
            "combos",
            "dollar_contribution",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in converted_rows:
            writer.writerow(row)

    print(f"Completed hand classes: {len(rows)} / {len(manifest_rows)}")
    print(f"Covered combos:         {covered_weight * TOTAL_STARTING_HANDS:.0f} / {TOTAL_STARTING_HANDS}")
    print(f"Covered combo share:    {covered_weight * 100:.6f}%")
    print(f"Total dollar value:     ${total_dollars:.9f}")
    if missing_labels:
        print(f"Missing hand classes:   {', '.join(missing_labels)}")
    else:
        print("Missing hand classes:   none")
    print(f"Wrote detailed CSV:     {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
