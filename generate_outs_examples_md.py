#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "edge_family_sampling_out/summary.csv"
THRESHOLDS = ROOT / "results/edge_family_sampling_84x1000/outs_threshold_summary.csv"
BUCKETS = ROOT / "results/edge_family_sampling_84x1000/outs_bucket_summary.csv"
PER_HAND_DIR = ROOT / "edge_family_sampling_out/per_hand"
OUT = ROOT / "results/edge_family_sampling_84x1000/outs_examples.md"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def count_outs(hero: str, exposed: str) -> int:
    hero_ranks = {card[0] for card in hero.split()}
    return sum(1 for card in exposed.split() if card[0] in hero_ranks)


def fmt_rate(rate: str) -> str:
    return f"{100 * float(rate):.1f}%"


def fmt_num(value: str | float | None) -> str:
    if value in (None, ""):
        return "—"
    return f"{float(value):.6f}"


def write_table(handle, headers: list[str], rows: list[list[str]]) -> None:
    handle.write("| " + " | ".join(headers) + " |\n")
    handle.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
    for row in rows:
        handle.write("| " + " | ".join(row) + " |\n")
    handle.write("\n")


def representative_sample(
    label: str,
    hero: str,
    outs: int,
    bucket_rows: list[dict[str, str]],
    preferred_action: str | None = None,
) -> dict[str, str] | None:
    rows = load_csv(PER_HAND_DIR / f"{label}.csv")
    candidates: list[tuple[dict[str, str], float]] = []
    for row in rows:
        if count_outs(hero, row["exposed"]) != outs:
            continue
        diff = float(row["ev_check"]) - float(row["ev_4x"])
        candidates.append((row, diff))
    if not candidates:
        return None

    bucket_avg_diff = None
    matching_bucket = [row for row in bucket_rows if int(row["outs"]) == outs]
    if matching_bucket:
        bucket_avg_diff = float(matching_bucket[0]["avg_check_minus_4x"])
        if preferred_action is None:
            preferred_action = matching_bucket[0]["preferred_action_by_average"]

    def key(item: tuple[dict[str, str], float]) -> tuple[float, float, float]:
        row, diff = item
        action_match = 0 if preferred_action is None or row["best_action"] == preferred_action else 1
        diff_distance = abs(diff - bucket_avg_diff) if bucket_avg_diff is not None else 0.0
        gain = abs(float(row["ev_check"]) - float(row["ev_4x"]))
        return (action_match, diff_distance, -gain)

    row, _ = sorted(candidates, key=key)[0]
    return row


def main() -> int:
    summary_rows = {row["label"]: row for row in load_csv(SUMMARY)}
    threshold_rows = {row["label"]: row for row in load_csv(THRESHOLDS)}

    bucket_summary_raw = load_csv(BUCKETS)
    bucket_summary: dict[str, list[dict[str, str]]] = {}
    for row in bucket_summary_raw:
        bucket_summary.setdefault(row["label"], []).append(row)

    with OUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# Expanded Dead-Card Outs Examples\n\n")
        handle.write(
            "These examples are built from the published `84 x 1000` run. "
            "The game tree is still standard hero-vs-dealer UTH; the `10` exposed cards "
            "are treated as known dead cards.\n\n"
        )
        handle.write(
            "An **out** here means an exposed/dead card whose rank matches either hero rank. "
            "So for `KQo`, any dead `K` or dead `Q` counts as one out.\n\n"
        )

        handle.write("## T8 / T9 Examples\n\n")
        rows: list[list[str]] = []
        for label in ["T8o", "T8s", "T9o", "T9s"]:
            summary = summary_rows[label]
            threshold = threshold_rows[label]
            exact_threshold = threshold["first_exact_outs_where_avg_check_exceeds_4x"] or "none"
            sample = None
            if exact_threshold != "none":
                sample = representative_sample(
                    label,
                    summary["hero"],
                    int(exact_threshold),
                    bucket_summary[label],
                )
            rows.append(
                [
                    f"`{label}`",
                    fmt_rate(summary["check_better_rate"]),
                    fmt_num(summary["avg_ev_4x"]),
                    fmt_num(summary["avg_ev_check"]),
                    exact_threshold,
                    threshold["rough_threshold_outs_pooled"] or "none",
                    "—" if sample is None else f"`{sample['exposed']}`",
                    "—" if sample is None else fmt_num(sample["ev_4x"]),
                    "—" if sample is None else fmt_num(sample["ev_check"]),
                ]
            )
        write_table(
            handle,
            [
                "Hand",
                "Check%",
                "Avg EV(4x)",
                "Avg EV(check)",
                "Exact threshold",
                "Rough threshold",
                "Representative threshold sample",
                "Sample EV(4x)",
                "Sample EV(check)",
            ],
            rows,
        )

        handle.write("## Suited Qx Examples\n\n")
        rows = []
        for label in ["Q2s", "Q3s", "Q4s", "Q5s", "Q6s", "Q7s", "Q8s", "Q9s", "QTs", "QJs"]:
            summary = summary_rows[label]
            threshold = threshold_rows[label]
            exact_threshold = threshold["first_exact_outs_where_avg_check_exceeds_4x"] or "none"
            sample = None
            if exact_threshold != "none":
                sample = representative_sample(
                    label,
                    summary["hero"],
                    int(exact_threshold),
                    bucket_summary[label],
                )
            rows.append(
                [
                    f"`{label}`",
                    fmt_rate(summary["check_better_rate"]),
                    fmt_num(summary["avg_ev_4x"]),
                    fmt_num(summary["avg_ev_check"]),
                    exact_threshold,
                    threshold["rough_threshold_outs_pooled"] or "none",
                    "—" if sample is None else f"`{sample['exposed']}`",
                ]
            )
        write_table(
            handle,
            [
                "Hand",
                "Check%",
                "Avg EV(4x)",
                "Avg EV(check)",
                "Exact threshold",
                "Rough threshold",
                "Representative threshold sample",
            ],
            rows,
        )

        for cutoff, title in [(0.10, "10%"), (0.20, "20%")]:
            handle.write(f"## Mostly-4x Hands: Check Better {title} Or Less\n\n")
            rows = []
            for label in sorted(summary_rows):
                summary = summary_rows[label]
                if float(summary["check_better_rate"]) > cutoff:
                    continue
                threshold = threshold_rows[label]
                rows.append(
                    [
                        f"`{label}`",
                        fmt_rate(summary["check_better_rate"]),
                        fmt_num(summary["avg_ev_4x"]),
                        fmt_num(summary["avg_ev_check"]),
                        threshold["first_exact_outs_where_avg_check_exceeds_4x"] or "none",
                        threshold["rough_threshold_outs_pooled"] or "none",
                    ]
                )
            write_table(
                handle,
                ["Hand", "Check%", "Avg EV(4x)", "Avg EV(check)", "Exact threshold", "Rough threshold"],
                rows,
            )

        for target_outs in [3, 4]:
            handle.write(f"## Hands That First Flip At Exactly {target_outs} Outs\n\n")
            rows = []
            labels = [
                label
                for label, threshold in threshold_rows.items()
                if threshold["first_exact_outs_where_avg_check_exceeds_4x"] == str(target_outs)
            ]
            for label in sorted(labels):
                summary = summary_rows[label]
                threshold = threshold_rows[label]
                sample = representative_sample(
                    label,
                    summary["hero"],
                    target_outs,
                    bucket_summary[label],
                    preferred_action="check",
                )
                rows.append(
                    [
                        f"`{label}`",
                        fmt_rate(summary["check_better_rate"]),
                        fmt_num(threshold["first_exact_avg_ev_4x"]),
                        fmt_num(threshold["first_exact_avg_ev_check"]),
                        "—" if sample is None else f"`{sample['exposed']}`",
                        "—" if sample is None else fmt_num(sample["ev_4x"]),
                        "—" if sample is None else fmt_num(sample["ev_check"]),
                    ]
                )
            write_table(
                handle,
                [
                    "Hand",
                    "Check%",
                    f"Bucket avg EV(4x) at {target_outs} outs",
                    f"Bucket avg EV(check) at {target_outs} outs",
                    f"Representative {target_outs}-out sample",
                    "Sample EV(4x)",
                    "Sample EV(check)",
                ],
                rows,
            )

    print(f"Wrote expanded examples: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
