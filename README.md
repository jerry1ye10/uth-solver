# UTH Exact Exposed-Card EV Solver

Exact preflop EV solver for Ultimate Texas Hold'em with:

- fixed hero hole cards
- a fixed set of exposed/dead cards
- exact enumeration over all remaining flops, turns, rivers, and dealer hands
- a batch sampler for many random exposed-card states

This repo is aimed at questions like:

- "What is the exact EV of pocket 2s if I can see 10 dead cards?"
- "How much is exposed-card information worth for a given starting hand?"
- "Across random exposed-card states, how often should a borderline preflop `4x` become a `check`?"

## Files

- `uth_exact_solver.c`
  Exact C solver for one fixed hero hand plus one fixed exposed-card set.
- `sample_random_exposed_ev.py`
  Batch runner that samples many random exposed-card sets and calls the C solver for each one.
- `analyze_dead_out_thresholds.py`
  Post-processes the published per-hand sample CSVs into "how many dead-card outs make `check` beat `4x`?" tables.

## What The Solver Computes

For one fixed state:

- hero hole cards are fixed
- 10 exposed/dead cards are fixed
- all remaining unseen cards are enumerated exactly

The solver returns:

- `EV(4x)`: exact EV of raising `4x` preflop
- `EV(check)`: exact EV of checking preflop, then playing optimally later
- `Best EV`: `max(EV(4x), EV(check))`

`EV(check)` means:

- preflop: do not `4x`
- flop: choose `max(2x, check)`
- river after checking flop: choose `max(1x, fold)`

So `EV(check)` is not "check forever." It is "check now, then play the remaining decision tree optimally."

## Algorithm

The core solver is exact dynamic programming over the remaining deck.

Given 10 exposed cards:

- live cards left: `40`
- possible flops: `C(40,3) = 9,880`
- unique completed boards: `C(40,5) = 658,008`
- dealer hole-card combos per completed board: `C(35,2) = 595`

The main structure is:

1. Enumerate all possible flops from the 40 live cards.
2. For each flop, enumerate all ordered turn/river pairs from the remaining 37 cards.
3. For each completed board, enumerate all dealer 2-card hands from the remaining 35 cards.
4. Compute showdown EV once for that completed board and reuse it for:
   - river `1x`
   - flop `2x`
   - preflop `4x`
5. Cache completed-board results by a 52-bit board mask.
6. Cache flop results by a 52-bit flop mask.

This avoids Monte Carlo variance and avoids recomputing the same completed board across many flop/turn/river paths.

## Implementation Details

The C solver uses:

- `uint8_t` cards encoded as `0..51`
- `uint64_t` bitmasks for fast card-set operations
- precomputed pair and flop index arrays
- open-addressed hash tables for board and flop caches
- a branch-light 7-card evaluator specialized for:
  - 5 board cards
  - 2 private cards

Important implementation choices:

- no heap allocation inside the hot solve loops
- no Monte Carlo inside the solver
- one completed-board pass produces all of `EV(1x)`, `EV(2x)`, and `EV(4x)`
- Python is not used in the exact inner solve; it is only used for batch sampling/orchestration

## Rules / EV Convention

This solver uses full-game UTH accounting:

- ante and blind are both live forced bets
- dealer qualifies with at least a pair
- if the dealer does not qualify:
  - player wins play bet normally if hero wins
  - ante pushes
- blind pays according to the standard blind pay table
- river fold is treated as losing ante plus blind, so fold EV is `-2.0`

That fold convention is important. Some rough specs write river fold as `-1`, but under full-game UTH accounting a fold forfeits both ante and blind.

## Build

```bash
gcc -O3 -march=native -std=c11 -Wall -Wextra -pedantic uth_exact_solver.c -o uth_exact_solver
```

## Run One Fixed State

Default built-in example:

```bash
./uth_exact_solver
```

Custom hero hand plus 10 exposed cards:

```bash
./uth_exact_solver 3c 3d 2h 5c 6s 7d 8h 9c Td Js Qc As
```

Output:

- `EV(4x)`: exact EV of raising `4x` preflop
- `EV(check)`: exact EV of checking preflop, then playing optimally later
- `Best EV`: `max(EV(4x), EV(check))`

## Audit Mode

Audit mode verifies the exact state counts used by the solver:

```bash
./uth_exact_solver --audit
```

Expected audit totals for 10 exposed cards:

- `live_cards = 40`
- `flops_visited = 9880`
- `ordered_turn_river_paths = 13160160`
- `unique_completed_boards = 658008`
- `total_dealer_worlds = 391514760`

It also checks invariants such as:

- no duplicate hero/exposed cards
- every completed board contains exactly 5 distinct live cards
- no board overlaps the hero or exposed cards
- every completed board sees exactly 595 dealer combinations

If audit mode completes successfully, it is strong evidence there is no duplicate-card or state-counting bug in the exact enumeration.

## Sample Many Random Exposed States

Pocket 3s with 1000 random exposed-10 samples:

```bash
python3 -u sample_random_exposed_ev.py \
  --hero 3c 3d \
  --samples 1000 \
  --jobs 8 \
  --seed 0 \
  --baseline 4x \
  --csv pocket_3s_random10_1000.csv
```

What the batch script does:

- samples `10` random exposed cards uniformly from the remaining deck
- rejects duplicate exposed-card samples
- runs the exact C solver for each sampled state
- prints per-sample progress lines
- writes a CSV if requested

Each sample is:

- one fixed hero hand
- one random 10-card exposed/dead set
- one exact solve over all future legal worlds consistent with that exposed set

Important columns in the CSV:

- `ev_4x`
- `ev_check`
- `best_ev`
- `best_action`
- `gain_vs_baseline`

For pocket pairs like `33`, `--baseline 4x` is the natural comparison if you want the value of exposed-card information relative to a normal blind preflop `4x`.

## Methodology For Information Value

If your goal is "What is the EV gain from having access to 10 random dead cards?", then for a fixed starting hand class `h` the quantity of interest is:

`Gain(h) = E_D[ BestEV(h, D) - BaselineEV(h, D) ]`

where `D` is a random 10-card exposed set.

Examples:

- for pocket `33`, a natural baseline is blind preflop `4x`
- for some weaker hands, baseline may be `check`

If you want whole-strategy value across many starting hands, weight each hand class by its preflop probability:

- pocket pair class: `6 / 1326`
- suited non-pair class: `4 / 1326`
- offsuit non-pair class: `12 / 1326`

Then total strategy gain is:

`TotalGain = sum_h P(h) * Gain(h)`

## How To Solve The Full Edge Gain From 10 Dead Cards

If the real goal is not just one hand like `33`, but the full-game edge from being able to see 10 dead cards before the preflop decision, the problem can be framed as:

`FullEdgeGain = sum_h P(h) * E_D[ BestEV(h, D) - BaselineEV(h, D) ]`

where:

- `h` is a starting-hand class
- `P(h)` is the preflop probability of that class
- `D` is a random 10-card exposed/dead set drawn from the remaining deck

### Hand-Class Weights

For preflop class weighting:

- pocket pair class: `6 / 1326`
- suited non-pair class: `4 / 1326`
- offsuit non-pair class: `12 / 1326`

So if `Gain(33)` is the average value of exposed-card information for pocket 3s, then its contribution to the full-game edge is:

`(6 / 1326) * Gain(33)`

### Exact In Principle vs Practical In Practice

For one fixed hero hand and one fixed exposed-card set, this repo computes the EV exactly.

For the full "10 dead cards" edge, there are two conceptual layers:

1. Inner solve:
   - exact
   - for a fixed hero hand and fixed exposed cards, the solver enumerates every legal future world
2. Outer exposed-card average:
   - either exact or sampled
   - exact would mean averaging over all possible 10-card exposed sets for that hero hand

That exact outer average is usually too large to be practical. For a fixed 2-card hero hand, the number of possible exposed-10 sets is:

`C(50, 10)`

which is enormous. So the practical approach is:

- sample many random exposed-card states for a hand class
- estimate `Gain(h)` from those samples
- repeat for the hand classes you care about
- weight and sum the per-class gains

### Practical Workflow For Full Edge Gain

1. Choose a baseline policy.
   - Example: for `33`, blind preflop baseline is often `4x`
2. For each candidate hand class `h`, run the sampler:

```bash
python3 -u sample_random_exposed_ev.py --hero 3c 3d --samples 1000 --jobs 8 --seed 0 --baseline 4x --csv pocket_3s_random10_1000.csv
```

3. Compute the average sampled gain:

`GainHat(h) = average(BestEV - BaselineEV)`

4. Weight it by hand frequency:

`ContributionHat(h) = P(h) * GainHat(h)`

5. Sum contributions across all modeled hand classes:

`FullEdgeGainHat = sum_h ContributionHat(h)`

### Confidence / Error Bars For The Full Edge

For one hand class `h`, let:

- `s_h` = sample standard deviation of `BestEV - BaselineEV`
- `n_h` = number of exposed-card samples
- `SE_h = s_h / sqrt(n_h)`

Then the standard error of the weighted contribution is:

`WeightedSE_h = P(h) * SE_h`

If the sampled hand-class estimates are generated independently, then a simple approximation for the total standard error is:

`FullSE = sqrt(sum_h WeightedSE_h^2)`

and a rough 95% confidence interval is:

`FullEdgeGainHat ± 1.96 * FullSE`

### What This Repo Currently Supports

This repo already supports:

- exact EV for one fixed hero hand plus one fixed exposed-10 state
- batch sampling over many random exposed-10 states for one hero hand
- CSV output that can be aggregated outside the solver

It does not yet automate:

- running every starting-hand class end-to-end
- choosing a baseline action per class automatically
- combining all classes into one final full-game estimate

But the pieces are already here to do that aggregation cleanly.

## Statistical Interpretation Of The Batch Sampler

The C solve is exact for each fixed exposed-card state.

The randomness only comes from sampling many exposed-card states in `sample_random_exposed_ev.py`.

So for batch results:

- sample standard deviation measures how much EV varies across exposed-card states
- standard error is `SD / sqrt(n)`
- a rough 95% confidence interval for the mean is:
  - `mean ± 1.96 * SE`

This is useful when you want to estimate how much exposed-card information is worth on average for a given hand class.

## Example Workflow

1. Build the C solver.
2. Run a pilot sample for one hand:

```bash
python3 -u sample_random_exposed_ev.py --hero 3c 3d --samples 100 --jobs 8 --seed 0 --baseline 4x
```

3. If the estimated gain is nontrivial, increase to 1000 or more samples:

```bash
python3 -u sample_random_exposed_ev.py --hero 3c 3d --samples 1000 --jobs 8 --seed 0 --baseline 4x --csv pocket_3s_random10_1000.csv
```

4. Repeat for other "edge" hand classes.
5. Weight each hand-class gain by its preflop probability.
6. Sum those weighted gains to estimate the total value of the exposed-card strategy.

## Run A Whole Family Of Edge Hands

This repo also includes a higher-level batch runner:

- `run_edge_family_sampling.py`

It expands and runs the following canonical hand classes:

- pairs: `22` through `55`
- `A2s` through `A9s`
- `A2o` through `A9o`
- all `Kx` suited and offsuit classes below `AK`
- all `Qx` suited and offsuit classes below `QK`
- all `Jx` suited and offsuit classes below `JQ`
- `T8s`, `T9s`, `T8o`, `T9o`

The runner uses one canonical representative per hand class, for example:

- `A2s` -> `Ac 2c`
- `A2o` -> `Ac 2d`
- `33` -> `3c 3d`

This is enough for class-level averaging because the batch job is working at the suited / offsuit / pair class level, not at the raw 1326-combo level.

Example:

```bash
python3 -u run_edge_family_sampling.py \
  --samples 1000 \
  --sample-jobs 8 \
  --seed 0 \
  --default-baseline 4x \
  --output-dir edge_family_sampling_out
```

The current family list contains `84` hand classes total.

### Split Work Across Multiple Machines

The family runner can shard the hand-class list across multiple boxes:

```bash
python3 -u run_edge_family_sampling.py \
  --samples 1000 \
  --sample-jobs 4 \
  --seed 0 \
  --default-baseline 4x \
  --shard-count 4 \
  --shard-index 0 \
  --output-dir edge_family_sampling_shard0
```

Run the same command on each machine, changing only `--shard-index`:

- box 1: `--shard-index 0`
- box 2: `--shard-index 1`
- box 3: `--shard-index 2`
- box 4: `--shard-index 3`

This is the cleanest way to use multiple compute boxes:

- each box gets a disjoint subset of hand classes
- no exposed-card samples are duplicated across boxes
- each box writes its own `summary.csv` and `per_hand/*.csv`

For GitHub Actions or other smaller Linux VMs, a good starting point is:

- `--sample-jobs 4`
- `--shard-count 8` to `12`

That keeps each shard comfortably under typical hosted-runner time limits while still parallelizing the full batch.

## Run The Full Family On GCP Batch

This repo includes helper scripts under `gcp/` for the cleanest cloud setup:

- one Google Cloud Batch task per hand class
- `84` tasks total
- each task runs exactly one shard
- each task uploads its `summary.csv` and `per_hand/*.csv` files to Cloud Storage

Files:

- `gcp/make_batch_job.py`
  - generates a Batch job config JSON
- `gcp/batch_task_entrypoint.sh`
  - bootstraps a VM, compiles the solver, and runs one shard
- `gcp/upload_dir_to_gcs.py`
  - uploads one shard's output directory to GCS
- `gcp/merge_shard_summaries.py`
  - merges downloaded shard summaries into one combined estimate

### Credential / Project Checklist

You need:

- a GCP project ID
- a region, for example `us-central1`
- a Cloud Storage bucket for outputs
- local submit credentials
- a VM service account that can write to that bucket

Local submit credentials can be either:

- `gcloud auth login`
- or a service-account key JSON activated with:
  - `gcloud auth activate-service-account --key-file /path/to/key.json`

The submit identity should be able to:

- submit Batch jobs
- use the chosen VM service account
- read/write the output bucket if you also want to inspect or download results locally

The VM service account attached to the Batch job should be able to:

- write objects to the chosen Cloud Storage bucket
- write logs normally through Batch / Compute Engine defaults

### Generate A Batch Job Config

Example:

```bash
python3 gcp/make_batch_job.py \
  --job-name uth-edge-20260323 \
  --region us-central1 \
  --bucket YOUR_BUCKET_NAME \
  --samples 1000 \
  --sample-jobs 2 \
  --parallelism 24 \
  --machine-type e2-standard-2 \
  --spot \
  --service-account-email YOUR_VM_SERVICE_ACCOUNT@YOUR_PROJECT.iam.gserviceaccount.com
```

That writes `gcp/batch-job.json` and prints the `gcloud batch jobs submit ...` command.

Recommended starting point:

- `task-count = 84`
- `sample-jobs = 2`
- `machine-type = e2-standard-2`
- `parallelism = 24`
- `--spot` if you want cheaper but interruptible compute

### Submit The Batch Job

```bash
gcloud batch jobs submit uth-edge-20260323 \
  --location us-central1 \
  --config gcp/batch-job.json
```

Each task will:

- clone the public repo
- compile `uth_exact_solver.c`
- run exactly one hand-class shard
- upload results to:
  - `gs://YOUR_BUCKET_NAME/uth-edge-family/uth-edge-20260323/shard_<task_index>/`

### Collect And Merge Results

Download the outputs locally:

```bash
gcloud storage cp --recursive \
  gs://YOUR_BUCKET_NAME/uth-edge-family/uth-edge-20260323 \
  ./gcp-results
```

Merge the shard summaries:

```bash
python3 gcp/merge_shard_summaries.py \
  --input-root ./gcp-results/uth-edge-20260323 \
  --output ./gcp-results/merged-summary.csv
```

That prints:

- total weighted gain
- total weighted 1-sigma
- total weighted 95% confidence interval

Outputs:

- `edge_family_sampling_out/hand_manifest.csv`
  - hand classes, canonical hero cards, baselines, weights
- `edge_family_sampling_out/per_hand/*.csv`
  - full per-sample results for each hand class
- `edge_family_sampling_out/summary.csv`
  - one summary row per hand class

Summary columns include:

- average `EV(4x)`
- average `EV(check)`
- average gain versus baseline
- 1-sigma and 95% confidence estimates for gain
- weighted gain contribution to the full strategy

If you want to override the baseline action for specific labels, provide a CSV like:

```csv
label,baseline
K2o,check
Q6o,check
J8o,check
```

and run:

```bash
python3 -u run_edge_family_sampling.py --baseline-map baseline_overrides.csv
```

Because this expands to many hand classes, a full `1000`-sample run can take many hours. A small smoke test is:

```bash
python3 -u run_edge_family_sampling.py --limit-hands 2 --samples 20 --sample-jobs 8 --output-dir edge_family_sampling_smoke
```

## Published 84-Hand Run

This repo now includes one completed `1000`-sample family run for the hand set above under:

- `results/edge_family_sampling_84x1000/summary.csv`
- `results/edge_family_sampling_84x1000/switch_gain_dollars.csv`
- `results/edge_family_sampling_84x1000/outs_threshold_summary.csv`
- `results/edge_family_sampling_84x1000/outs_bucket_summary.csv`

The helper script:

```bash
python3 summarize_switch_gain_dollars.py
```

computes the cleaner "dead-card switching" value:

`switch_gain = avg_best_ev - max(avg_ev_4x, avg_ev_check)`

and then converts that to dollars using:

`dollar_contribution = switch_gain * hand_weight * ante`

For the published run:

- samples per hand class: `1000`
- ante used for the dollar conversion: `$1000`
- covered hand classes: `84`
- covered combos: `664 / 1326`
- total weighted EV gain for this subset: `0.010437276867`
- total dollar value for this subset: `$10.437276867`

Top hands by `switch_gain` in the published run:

- `J8s`: `0.079304`
- `T9s`: `0.076637`
- `Q8o`: `0.071027`
- `Q6s`: `0.069945`
- `K2s`: `0.068102`
- `J9o`: `0.067926`
- `JTo`: `0.061709`
- `Q5s`: `0.060373`
- `33`: `0.057957`
- `K5o`: `0.055294`

Top hands by weighted dollar contribution for this subset:

- `Q8o`: `$0.642776`
- `J9o`: `$0.614711`
- `JTo`: `$0.558451`
- `K5o`: `$0.500394`
- `K4o`: `$0.495050`
- `Q7o`: `$0.438450`
- `Q9o`: `$0.399505`
- `K3o`: `$0.338675`
- `K6o`: `$0.335751`
- `J8o`: `$0.319147`

Family totals in the published run:

- `Qx`: `$3.333306`
- `Kx`: `$3.051030`
- `Jx`: `$2.363328`
- `Tx explicit (T8/T9)`: `$0.720004`
- `Ax`: `$0.487609`
- `22`-`55`: `$0.482000`

## Dead-Card Outs Thresholds

The published run also includes an outs-based view of the same `84 x 1000` samples.

Here, **outs** means:

- every exposed/dead card whose rank matches either hero rank
- for `KQo`, a dead `K` or dead `Q` counts as one out
- for `33`, a dead `3` counts as one out

The helper script:

```bash
python3 analyze_dead_out_thresholds.py
```

builds two published tables:

- `results/edge_family_sampling_84x1000/outs_threshold_summary.csv`
- `results/edge_family_sampling_84x1000/outs_bucket_summary.csv`
- `results/edge_family_sampling_84x1000/outs_threshold_summary.md`

If you want the full hand-by-hand table rendered directly on GitHub instead of opening the CSVs, use:

- `results/edge_family_sampling_84x1000/outs_threshold_summary.md`

### How To Read The Tables

`outs_bucket_summary.csv` is the raw per-hand, per-outs table. For each exact outs count it reports:

- samples in that bucket
- average `EV(4x)`
- average `EV(check)`
- average `Best EV`
- `check_better_rate`
- which action has the higher average EV in that exact bucket

`outs_threshold_summary.csv` is the compact "roughly how many outs?" view. It reports two switch points:

- `first_exact_outs_where_avg_check_exceeds_4x`
  - the first exact outs bucket where average `EV(check)` beats average `EV(4x)`
- `rough_threshold_outs_pooled`
  - the smallest outs count `t` such that, after pooling all samples with `outs >= t`, average `EV(check)` beats average `EV(4x)`

The first number is the cleaner exact-bucket crossover. The pooled number is often a better "rule of thumb" threshold when the high-outs buckets are small and noisy.

If the rough threshold is `0`, `check` already beats `4x` on average across the full published sample set for that hand. If the threshold fields are blank, then even at the highest observed outs counts the published `1000` samples never made average `EV(check)` exceed average `EV(4x)`.

### Representative Thresholds

| Hand | Exact bucket where `check` first beats `4x` | Avg `EV(4x)` there | Avg `EV(check)` there | Rough pooled threshold | Pooled avg `EV(4x)` | Pooled avg `EV(check)` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `33` | `1` | `-0.455857` | `-0.346497` | `1` | `-0.553947` | `-0.402851` |
| `A2o` | `3` | `-0.606421` | `-0.450688` | `2` | `-0.339925` | `-0.300856` |
| `A2s` | `3` | `-0.144912` | `-0.140712` | `3` | `-0.193119` | `-0.164967` |
| `KQo` | `4` | `-0.711398` | `-0.441801` | `3` | `-0.178097` | `-0.160984` |
| `JTo` | `2` | `-0.166970` | `-0.068374` | `1` | `0.008836` | `0.025773` |
| `T9s` | `2` | `-0.141430` | `0.050422` | `0` | `0.180101` | `0.211384` |
| `Q2o` | `0` | `-0.264635` | `-0.173940` | `0` | `-0.789214` | `-0.454802` |
| `A6s` | none | — | — | none | — | — |

Interpretation:

- `33` flips quickly: once one of the two remaining `3`s is dead, `check` already beats `4x` on the exact-bucket average.
- `A2o` is more gradual: the exact buckets switch at `3` outs, but pooling all `2+`-outs samples already favors `check`.
- `KQo` is still resilient: it takes heavy damage to the `K/Q` ranks before `check` beats `4x` on average.
- `Q2o` is already a `check` on average with `0` outs, so its threshold is `0`.
- `A6s` and `A7s` never crossed in this `1000`-sample run, even in their highest observed outs buckets.

### Quick Examples

- `33`
  - `0` dead `3`s: average `EV(4x)=0.456751`, average `EV(check)=0.254931`
  - `1` dead `3`: average `EV(4x)=-0.455857`, average `EV(check)=-0.346497`
  - practical read: pocket 3s flips fast once one set out is dead
- `A2o`
  - `1` out: average `EV(4x)=0.108721`, average `EV(check)=-0.041494`
  - `2` outs: average `EV(4x)=-0.237463`, average `EV(check)=-0.243518`
  - `3` outs: average `EV(4x)=-0.606421`, average `EV(check)=-0.450688`
  - practical read: still basically a `4x` at `2` outs, but by `3` outs `check` has pulled ahead
- `KQo`
  - `2` outs: average `EV(4x)=0.418349`, average `EV(check)=0.159202`
  - `3` outs: average `EV(4x)=-0.103143`, average `EV(check)=-0.121816`
  - `4` outs: average `EV(4x)=-0.711398`, average `EV(check)=-0.441801`
  - practical read: broadway strength carries it for a while, but heavy K/Q damage eventually makes `check` better
- `JTo`
  - `1` out: average `EV(4x)=0.278601`, average `EV(check)=0.163404`
  - `2` outs: average `EV(4x)=-0.166970`, average `EV(check)=-0.068374`
  - practical read: one dead `J/T` still leaves `4x` ahead, but two dead shared ranks are enough to flip it
- `Q2o`
  - `0` outs: average `EV(4x)=-0.264635`, average `EV(check)=-0.173940`
  - practical read: this hand is already a `check` on average even before any shared-rank dead cards show up
- `A6s`
  - `4` outs: average `EV(4x)=-0.111955`, average `EV(check)=-0.149202`
  - practical read: in this sample set, even the highest observed shared-rank damage did not make average `check` beat average `4x`

## Rare-Action Examples

The tables below answer a practical question:

- if one action was best in fewer than `5%` of the `1000` sampled exposed-card states,
- what is one concrete `10`-dead-card example that still made that rare action best?

These are not strategy rules by themselves. They are examples from the published run that show what the rare action looked like when it actually happened.

### Rare Checks (`check% < 5%`)

| Hand | Rare action rate | Example exposed cards | EV(4x) | EV(check) | Check gain |
| --- | ---: | --- | ---: | ---: | ---: |
| `55` | `3.5%` | `2d 3h 4d 4s 5h 5s 8c 9s Qd Ad` | `-0.888511` | `-0.641757` | `0.246754` |
| `A3s` | `3.8%` | `3h 5c 5d 6c 8h Tc Js Ad Ah As` | `-0.849626` | `-0.604302` | `0.245323` |
| `A4s` | `2.6%` | `3s 4h 4s 5c 8c Jc Qc Ks Ah As` | `-0.737621` | `-0.519789` | `0.217832` |
| `A5o` | `4.3%` | `4c 4d 4h 4s 5c 5h 6s 9d Qd Ah` | `-0.737146` | `-0.472679` | `0.264468` |
| `A5s` | `1.6%` | `2h 3d 5h 5s 6d 9c Jc Ad Ah As` | `-0.694848` | `-0.471212` | `0.223635` |
| `A6o` | `2.5%` | `2d 2h 3d 3s 4s 6c 6h 9d Ad As` | `-0.755005` | `-0.537744` | `0.217261` |
| `A6s` | `0.7%` | `2c 2d 2h 3c 4d 6s 9c 9s Ad As` | `-0.362791` | `-0.307861` | `0.054929` |
| `A7o` | `1.7%` | `5c 5s 7c 7h 8c Ts Jd Ad Ah As` | `-1.096235` | `-0.750827` | `0.345408` |
| `A7s` | `0.3%` | `3c 3h 6s 7s 8c Js Qc Ad Ah As` | `-0.567041` | `-0.457705` | `0.109336` |
| `A8o` | `1.3%` | `2h 3c 5c 7c 8h 8s Jc Ad Ah As` | `-1.015827` | `-0.718860` | `0.296966` |
| `A8s` | `0.2%` | `3d 3h 8h 8s Jc Kd Ks Ad Ah As` | `-0.438557` | `-0.339709` | `0.098848` |
| `A9o` | `1.5%` | `4c 7s 9c 9h 9s Th Jd Qc Ah As` | `-0.861406` | `-0.609455` | `0.251951` |
| `A9s` | `0.2%` | `4d 8c 9d 9h 9s Qc Qd Ks Ah As` | `-0.543724` | `-0.391703` | `0.152021` |
| `K9s` | `3.5%` | `2s 8c 9d 9s Qc Kd Kh Ks Ad As` | `-1.003415` | `-0.589325` | `0.414090` |
| `KTo` | `3.9%` | `2h 3h 6d 9c Tc Ts Jc Jh Kd Kh` | `-0.941291` | `-0.569924` | `0.371367` |
| `KTs` | `1.8%` | `2c 5h 6s Td Th Ts Jd Qc Kd Ks` | `-0.929695` | `-0.521472` | `0.408223` |
| `KJo` | `3.6%` | `2s 6h 9c 9s Th Jc Jh Js Qh Kd` | `-0.847724` | `-0.498468` | `0.349257` |
| `KJs` | `1.9%` | `5c 5d 6d 8c Th Jd Js Qc Kd Kh` | `-0.591773` | `-0.376422` | `0.215351` |
| `KQo` | `4.0%` | `3h 3s 7d Td Qc Qh Kd Kh Ks Ad` | `-1.374689` | `-0.767015` | `0.607674` |
| `KQs` | `1.3%` | `3c 4h 9c 9h Ts Js Qh Qs Kh Ks` | `-0.136185` | `0.132622` | `0.268807` |

### Rare 4x (`4x% < 5%`)

| Hand | Rare action rate | Example exposed cards | EV(4x) | EV(check) | 4x gain |
| --- | ---: | --- | ---: | ---: | ---: |
| `J2o` | `0.0%` | No `4x` sample appeared in the `1000` exposed-card draws. | — | — | — |
| `J2s` | `3.0%` | `5h 5s 7s 8d 9d Td Qs Kd Kh Ah` | `0.445893` | `0.312902` | `0.132991` |
| `J3o` | `0.0%` | No `4x` sample appeared in the `1000` exposed-card draws. | — | — | — |
| `J3s` | `4.9%` | `2d 4s 6s 7h 8d Td Ts Qh Ad As` | `0.447512` | `0.311057` | `0.136455` |
| `J4o` | `0.2%` | `5h 6s 9d Th Qs Kd Kh Ks Ad Ah` | `0.075700` | `0.050738` | `0.024962` |
| `J5o` | `1.6%` | `6h 8c 8d 9s Tc Qh Qs Kh Ks Ac` | `0.110666` | `0.038864` | `0.071802` |
| `Q2o` | `1.0%` | `3s 5h 6h 7s 8d 8h 9h Ks Ac Ad` | `-0.020497` | `-0.054733` | `0.034236` |

## Notes

- The exact solver is in C. Python is only used for random sampling, parallel process orchestration, and summary stats.
- The solver uses full-game UTH accounting. In particular, a river fold is treated as losing ante plus blind.
