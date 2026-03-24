#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORK_ROOT="${WORK_ROOT:-/tmp/uth-solver}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${WORK_ROOT}/outputs}"

SAMPLES="${SAMPLES:-1000}"
EXPOSED_COUNT="${EXPOSED_COUNT:-10}"
SAMPLE_JOBS="${SAMPLE_JOBS:-2}"
SEED="${SEED:-0}"
DEFAULT_BASELINE="${DEFAULT_BASELINE:-4x}"
TOP="${TOP:-0}"
QUIET_SAMPLES="${QUIET_SAMPLES:-1}"
SHARD_COUNT="${SHARD_COUNT:-84}"
SHARD_INDEX="${SHARD_INDEX:-${BATCH_TASK_INDEX:-0}}"
GCS_OUTPUT_URI="${GCS_OUTPUT_URI:-}"

echo "Batch task starting"
echo "  repo_dir:      ${REPO_DIR}"
echo "  samples:       ${SAMPLES}"
echo "  sample_jobs:   ${SAMPLE_JOBS}"
echo "  shard:         ${SHARD_INDEX}/${SHARD_COUNT}"
echo "  output_root:   ${OUTPUT_ROOT}"

cd "${REPO_DIR}"
gcc -O3 -march=native -std=c11 -Wall -Wextra -pedantic uth_exact_solver.c -o uth_exact_solver

OUTPUT_DIR="${OUTPUT_ROOT}/shard_${SHARD_INDEX}"
mkdir -p "${OUTPUT_DIR}"

RUN_ARGS=(
  python3 -u run_edge_family_sampling.py
  --samples "${SAMPLES}"
  --exposed-count "${EXPOSED_COUNT}"
  --sample-jobs "${SAMPLE_JOBS}"
  --seed "${SEED}"
  --default-baseline "${DEFAULT_BASELINE}"
  --shard-count "${SHARD_COUNT}"
  --shard-index "${SHARD_INDEX}"
  --top "${TOP}"
  --output-dir "${OUTPUT_DIR}"
)

if [[ "${QUIET_SAMPLES}" == "1" ]]; then
  RUN_ARGS+=(--quiet-samples)
fi

"${RUN_ARGS[@]}"

if [[ -n "${GCS_OUTPUT_URI}" ]]; then
  python3 -m pip install --no-input --quiet google-cloud-storage
  python3 gcp/upload_dir_to_gcs.py \
    --source-dir "${OUTPUT_DIR}" \
    --dest-uri "${GCS_OUTPUT_URI%/}/shard_${SHARD_INDEX}"
fi

echo "Batch task complete"
