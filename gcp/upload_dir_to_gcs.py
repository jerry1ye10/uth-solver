#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import storage


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {uri!r}")
    remainder = uri[5:]
    bucket, _, prefix = remainder.partition("/")
    if not bucket:
        raise ValueError(f"missing bucket in gs:// URI {uri!r}")
    return bucket, prefix.rstrip("/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a local directory tree to a GCS prefix using ADC credentials."
    )
    parser.add_argument("--source-dir", required=True, help="Local directory to upload.")
    parser.add_argument("--dest-uri", required=True, help="Destination prefix like gs://bucket/path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    bucket_name, prefix = parse_gs_uri(args.dest_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    uploaded = 0
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir).as_posix()
        blob_name = f"{prefix}/{rel}" if prefix else rel
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(path)
        uploaded += 1
        print(f"uploaded gs://{bucket_name}/{blob_name}")

    print(f"uploaded {uploaded} files from {source_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
