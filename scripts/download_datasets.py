#!/usr/bin/env python3
"""Download LoCoMo and LongMemEval benchmark datasets."""

import argparse
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATASETS = {
    "locomo": [
        {
            "url": "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
            "dest": os.path.join(_ROOT, "datasets", "locomo", "locomo10.json"),
            "label": "LoCoMo (locomo10.json)",
        }
    ],
    "longmemeval": {
        "oracle": {
            "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json",
            "dest": os.path.join(_ROOT, "datasets", "longmemeval", "longmemeval_oracle.json"),
            "label": "LongMemEval oracle (~15 MB)",
        },
        "s": {
            "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
            "dest": os.path.join(_ROOT, "datasets", "longmemeval", "longmemeval_s_cleaned.json"),
            "label": "LongMemEval S (~277 MB)",
        },
        "m": {
            "url": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_m_cleaned.json",
            "dest": os.path.join(_ROOT, "datasets", "longmemeval", "longmemeval_m_cleaned.json"),
            "label": "LongMemEval M (~2.7 GB)",
        },
    },
}


def _progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        sys.stdout.write(f"\r  {mb:.1f}/{total_mb:.1f} MB ({pct}%)")
    else:
        mb = downloaded / (1024 * 1024)
        sys.stdout.write(f"\r  {mb:.1f} MB")
    sys.stdout.flush()


def download_file(url: str, dest: str, label: str, force: bool = False):
    if os.path.exists(dest) and not force:
        print(f"  [skip] {label} already exists at {dest}")
        return

    print(f"  Downloading {label}...")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"\n  [done] {size_mb:.1f} MB -> {dest}")
    except Exception as e:
        print(f"\n  [error] Failed to download {label}: {e}")
        if os.path.exists(dest):
            os.remove(dest)
        sys.exit(1)


def download_locomo(force: bool):
    print("LoCoMo dataset:")
    for entry in DATASETS["locomo"]:
        download_file(entry["url"], entry["dest"], entry["label"], force)



def download_longmemeval(variant: str, force: bool):
    print("LongMemEval dataset:")
    entry = DATASETS["longmemeval"][variant]
    download_file(entry["url"], entry["dest"], entry["label"], force)


def main():
    parser = argparse.ArgumentParser(description="Download benchmark datasets")
    parser.add_argument(
        "--dataset",
        choices=["locomo", "longmemeval",  "all"],
        default="all",
        help="Which dataset to download (default: all)",
    )
    parser.add_argument(
        "--variant",
        choices=["oracle", "s", "m"],
        default="oracle",
        help="LongMemEval variant (default: oracle, ~15 MB)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if file already exists",
    )
    args = parser.parse_args()

    if args.dataset in ("locomo", "all"):
        download_locomo(args.force)
    if args.dataset in ("longmemeval", "all"):
        download_longmemeval(args.variant, args.force)

    print("\nDone!")


if __name__ == "__main__":
    main()
