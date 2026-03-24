# UTH Exact Exposed-Card EV Solver

Exact preflop EV solver for Ultimate Texas Hold'em with:

- fixed hero hole cards
- a fixed set of exposed/dead cards
- exact enumeration over all remaining flops, turns, rivers, and dealer hands
- a batch sampler for many random exposed-card states

## Files

- `uth_exact_solver.c`
  Exact C solver for one fixed hero hand plus one fixed exposed-card set.
- `sample_random_exposed_ev.py`
  Batch runner that samples many random exposed-card sets and calls the C solver for each one.

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

`EV(check)` includes later decisions:

- on the flop: choose `max(2x, check)`
- on the river after checking flop: choose `max(1x, fold)`

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

Important columns in the CSV:

- `ev_4x`
- `ev_check`
- `best_ev`
- `best_action`
- `gain_vs_baseline`

For pocket pairs like `33`, `--baseline 4x` is the natural comparison if you want the value of exposed-card information relative to a normal blind preflop `4x`.

## Notes

- The exact solver is in C. Python is only used for random sampling, parallel process orchestration, and summary stats.
- The solver uses full-game UTH accounting. In particular, a river fold is treated as losing ante plus blind.
