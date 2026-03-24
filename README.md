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

## Notes

- The exact solver is in C. Python is only used for random sampling, parallel process orchestration, and summary stats.
- The solver uses full-game UTH accounting. In particular, a river fold is treated as losing ante plus blind.
