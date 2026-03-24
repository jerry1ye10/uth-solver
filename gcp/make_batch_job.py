#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_TASK_COUNT = 84


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Google Cloud Batch job config that runs one UTH hand class per task "
            "and uploads results to GCS."
        )
    )
    parser.add_argument("--job-name", required=True, help="Batch job name.")
    parser.add_argument("--region", required=True, help="Batch job region, e.g. us-central1.")
    parser.add_argument("--bucket", required=True, help="GCS bucket for outputs, without gs://.")
    parser.add_argument(
        "--gcs-prefix",
        default="uth-edge-family",
        help="GCS prefix under the bucket. Default: uth-edge-family",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1000,
        help="Random exposed-card samples per hand class. Default: 1000",
    )
    parser.add_argument(
        "--sample-jobs",
        type=int,
        default=2,
        help="Parallel solver processes inside each VM. Default: 2",
    )
    parser.add_argument(
        "--task-count",
        type=int,
        default=DEFAULT_TASK_COUNT,
        help=f"Number of Batch tasks. Default: {DEFAULT_TASK_COUNT}",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=24,
        help="How many tasks may run concurrently. Default: 24",
    )
    parser.add_argument(
        "--machine-type",
        default="e2-standard-2",
        help="Machine type for each task VM. Default: e2-standard-2",
    )
    parser.add_argument(
        "--cpu-milli",
        type=int,
        default=2000,
        help="Requested CPU per task in millicores. Default: 2000",
    )
    parser.add_argument(
        "--memory-mib",
        type=int,
        default=8192,
        help="Requested memory per task in MiB. Default: 8192",
    )
    parser.add_argument(
        "--max-run-duration",
        default="14400s",
        help="Max task duration, e.g. 14400s for 4h. Default: 14400s",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/jerry1ye10/uth-solver.git",
        help="Git URL to clone on each VM.",
    )
    parser.add_argument(
        "--repo-ref",
        default="main",
        help="Git branch or tag to clone. Default: main",
    )
    parser.add_argument(
        "--default-baseline",
        choices=("4x", "check"),
        default="4x",
        help="Default baseline action. Default: 4x",
    )
    parser.add_argument(
        "--service-account-email",
        help="Optional service account email to attach to Batch VMs.",
    )
    parser.add_argument(
        "--spot",
        action="store_true",
        help="Use Spot VMs to reduce cost at the cost of preemption risk.",
    )
    parser.add_argument(
        "--output",
        default="gcp/batch-job.json",
        help="Where to write the generated job config. Default: gcp/batch-job.json",
    )
    return parser.parse_args()


def build_inline_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "export DEBIAN_FRONTEND=noninteractive",
            "apt-get update",
            "apt-get install -y --no-install-recommends build-essential ca-certificates git python3 python3-pip",
            "rm -rf /tmp/uth-solver",
            "mkdir -p /tmp/uth-solver",
            'echo "Cloning ${REPO_URL}@${REPO_REF}"',
            "git clone --depth 1 --branch \"${REPO_REF}\" \"${REPO_URL}\" /tmp/uth-solver/repo",
            'echo "Starting task index ${BATCH_TASK_INDEX}"',
            "bash /tmp/uth-solver/repo/gcp/batch_task_entrypoint.sh",
        ]
    )


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    gcs_output_uri = f"gs://{args.bucket}/{args.gcs_prefix.strip('/')}/{args.job_name}"

    config = {
        "taskGroups": [
            {
                "taskCount": args.task_count,
                "parallelism": args.parallelism,
                "taskSpec": {
                    "runnables": [
                        {
                            "script": {
                                "text": build_inline_script(),
                            }
                        }
                    ],
                    "environment": {
                        "variables": {
                            "REPO_URL": args.repo_url,
                            "REPO_REF": args.repo_ref,
                            "SAMPLES": str(args.samples),
                            "SAMPLE_JOBS": str(args.sample_jobs),
                            "DEFAULT_BASELINE": args.default_baseline,
                            "SHARD_COUNT": str(args.task_count),
                            "GCS_OUTPUT_URI": gcs_output_uri,
                            "QUIET_SAMPLES": "1",
                            "TOP": "0",
                        }
                    },
                    "computeResource": {
                        "cpuMilli": args.cpu_milli,
                        "memoryMib": args.memory_mib,
                    },
                    "maxRetryCount": 1,
                    "maxRunDuration": args.max_run_duration,
                },
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": args.machine_type,
                    }
                }
            ],
        },
        "logsPolicy": {
            "destination": "CLOUD_LOGGING",
        },
        "labels": {
            "app": "uth-solver",
            "mode": "edge-family",
        },
    }

    if args.spot:
        config["allocationPolicy"]["instances"][0]["policy"]["provisioningModel"] = "SPOT"

    if args.service_account_email:
        config["allocationPolicy"]["serviceAccount"] = {
            "email": args.service_account_email,
        }

    output_path.write_text(json.dumps(config, indent=2) + "\n")

    print(f"Wrote Batch config: {output_path}")
    print()
    print("Submit with:")
    print(
        f"  gcloud batch jobs submit {args.job_name} "
        f"--location {args.region} --config {output_path}"
    )
    print()
    print("Results will upload under:")
    print(f"  {gcs_output_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
