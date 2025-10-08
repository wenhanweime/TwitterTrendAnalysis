#!/usr/bin/env python3
"""Merge Page Content Saver TXT exports into a CSV for trend analysis."""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple


def parse_txt(path: Path) -> Tuple[str, str, str, str]:
    """Extract (captured_at_iso, url, title, body) from a TXT export."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    title = ""
    url = ""
    captured_at = ""
    meta_end_index = -1

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Title:"):
            title = stripped[len("Title:"):].strip()
        elif stripped.startswith("URL:"):
            url = stripped[len("URL:"):].strip()
        elif stripped.startswith("Captured At:"):
            captured_at = stripped[len("Captured At:"):].strip()
        elif stripped == "" and idx >= 2:
            meta_end_index = idx
            break

    if meta_end_index == -1:
        meta_end_index = min(len(lines), 3)

    body = "\n".join(lines[meta_end_index:]).strip()
    return captured_at, url, title, body


def build_rows(files: List[Path]) -> List[dict]:
    rows = []
    for path in files:
        try:
            captured_at, url, title, body = parse_txt(path)
        except Exception as exc:  # pragma: no cover - defensive
            sys.stderr.write(f"Warning: failed to parse {path}: {exc}\n")
            continue

        rows.append({
            "captured_at": captured_at,
            "captured_local": format_local_time(captured_at),
            "url": url,
            "title": title,
            "word_count": len(body.split()),
            "char_count": len(body),
            "content": body,
            "source_file": path.name,
        })
    return rows


def format_local_time(iso_ts: str) -> str:
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return iso_ts
    return dt.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge Page Content Saver TXT exports into a CSV for analysis.")
    parser.add_argument(
        "input",
        nargs="?",
        default=str(Path.home() / "Downloads"),
        help="Directory containing TXT exports (default: ~/Downloads)")
    parser.add_argument(
        "-o",
        "--output",
        default=str(Path.cwd() / "merged_page_content.csv"),
        help="Output CSV path (default: ./merged_page_content.csv)")
    parser.add_argument(
        "--patterns",
        default="page-content-*.txt,下载*.txt",
        help=(
            "Comma-separated glob patterns (default: "
            "'page-content-*.txt,下载*.txt')"))

    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.is_dir():
        sys.stderr.write(f"Error: {input_dir} is not a directory\n")
        sys.exit(1)

    files = []
    seen = set()
    for pattern in [p.strip() for p in args.patterns.split(',') if p.strip()]:
        for path in input_dir.rglob(pattern):
            if path not in seen:
                files.append(path)
                seen.add(path)
    files.sort()
    if not files:
        sys.stderr.write(
            "No TXT exports found. Checked patterns: "
            f"{args.patterns} in {input_dir}\n")
        sys.exit(1)

    rows = build_rows(files)

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "captured_at",
        "captured_local",
        "url",
        "title",
        "word_count",
        "char_count",
        "content",
        "source_file",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
