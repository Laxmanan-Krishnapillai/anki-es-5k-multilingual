"""
download_tamil_dictionary.py
============================

This script automates the process of fetching and converting authoritative
English→Tamil and Tamil lexicon dictionaries into simple tab‑separated
format.  It downloads remote dictionary files (JSON or Babylon) from
specified URLs, parses the contents, and writes TSV files with one
translation per line.  The goal is to provide a reproducible way to
consume formal Tamil lexical data for downstream processing (for example,
as part of a multilingual flash‑card project).

Features
--------
* Downloads a JSON‑based English→Tamil dictionary (derived from the
  University of Madras/Tamil Virtual University dictionary) and writes
  `english_tamil.tsv`.
* Downloads a Babylon‑format Tamil lexicon (scraped from the Digital
  Dictionaries of South Asia) and writes `tamil_lexicon.tsv`.  The
  parser is intentionally simple: it treats the first line of each
  entry as the headword and concatenates the remaining lines as
  definitions.
* Uses only the Python standard library, so there are no external
  dependencies.

License notice
--------------
The underlying dictionary data are subject to their own licences.  In
particular, the Tamil lexicon is licensed under the Creative
Commons BY‑NC‑ND licence (non‑commercial, no derivatives) and the
English→Tamil dictionary JSON data are licensed under GPL‑3.0.  This
script merely changes the container format (which does not create a
derivative work【843825045568171†L71-L88】), but any downstream use must
abide by the original licences.
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
from typing import Iterable, Tuple


def download_file(url: str, dest_path: str) -> None:
    """Download a remote file to the given destination path.

    Uses urllib to avoid external dependencies.  The file is streamed
    directly to disk to minimise memory usage.

    Args:
        url: Remote URL to fetch.
        dest_path: Local path where the file should be written.
    """
    print(f"Downloading {url} → {dest_path}…")
    with urllib.request.urlopen(url) as response, open(dest_path, "wb") as out_fp:
        # Stream in chunks to handle large files
        chunk_size = 8192
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_fp.write(chunk)
    print(f"Downloaded {dest_path} ({os.path.getsize(dest_path)} bytes)")


def parse_eng2tam_json(json_path: str) -> Iterable[Tuple[str, str]]:
    """Yield English→Tamil pairs from a JSON dictionary file.

    The expected JSON structure is a dict mapping English headwords to
    either a single Tamil translation (string) or a list of translations.
    Entries with non‑string values are ignored.

    Args:
        json_path: Path to the JSON file.

    Yields:
        (english, tamil) tuples.
    """
    with open(json_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    if isinstance(data, dict):
        for eng, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        yield eng.strip(), item.strip()
            elif isinstance(value, str):
                yield eng.strip(), value.strip()
            # silently ignore non‑string, non‑list entries
        return
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            eng = item.get("eng") or item.get("english")
            tamil = item.get("tamil")
            if isinstance(eng, str) and isinstance(tamil, str):
                yield eng.strip(), tamil.strip()
        return
    raise ValueError("Expected top‑level JSON object (dictionary) or list of entries")


def write_tsv(pairs: Iterable[Tuple[str, str]], output_path: str) -> None:
    """Write translation pairs to a tab‑separated values file.

    Args:
        pairs: Iterable of (source, target) tuples.
        output_path: Destination TSV filename.
    """
    with open(output_path, "w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp, delimiter="\t")
        # Write header row
        writer.writerow(["source", "target"])
        for src, tgt in pairs:
            writer.writerow([src, tgt])
    print(f"Wrote {output_path}")


def parse_babylon(babylon_path: str) -> Iterable[Tuple[str, str]]:
    """Parse a simple Babylon (.babylon) dictionary file.

    Babylon files are plain text where entries are separated by blank
    lines.  The first line of each entry is treated as the headword
    (source), and the remaining lines (if any) are joined with
    semicolons to form the target string.  This parser ignores
    comments (lines starting with '#').

    Note: This approach may not perfectly capture all formatting in
    complex Babylon files, but it suffices for extracting basic
    headword–definition pairs for our purposes.  For more complex
    conversions consider using the `pyglossary` package.

    Args:
        babylon_path: Path to the Babylon file.

    Yields:
        (source, target) tuples.
    """
    with open(babylon_path, "r", encoding="utf-8", errors="ignore") as fp:
        entry_lines = []
        for line in fp:
            stripped = line.strip()
            # Detect blank line as entry separator
            if stripped == "":
                if entry_lines:
                    src, tgt = _process_babylon_entry(entry_lines)
                    if src and tgt:
                        yield src, tgt
                    entry_lines = []
                continue
            # Skip comment lines
            if stripped.startswith("#"):
                continue
            entry_lines.append(stripped)
        # process last entry if file does not end with blank line
        if entry_lines:
            src, tgt = _process_babylon_entry(entry_lines)
            if src and tgt:
                yield src, tgt


def _process_babylon_entry(lines: Iterable[str]) -> Tuple[str, str]:
    """Convert raw Babylon entry lines into a (source, target) tuple.

    Args:
        lines: List of lines comprising a single entry (no blank lines).

    Returns:
        (source, target) tuple where source is the headword and target
        is a concatenation of remaining lines separated by semicolons.
    """
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return "", ""
    src = lines[0]
    if len(lines) == 1:
        tgt = ""
    else:
        tgt = "; ".join(lines[1:])
    return src, tgt


def main(argv: Iterable[str] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download and convert English–Tamil and Tamil lexicon dictionaries to TSV."
    )
    parser.add_argument(
        "--json-url",
        default="https://raw.githubusercontent.com/linuxkathirvel/eng2tamildictionary/master/dictionary.json",
        help="URL of the JSON dictionary file (English→Tamil).",
    )
    parser.add_argument(
        "--babylon-url",
        default=(
            "https://raw.githubusercontent.com/indic-dict/stardict-tamil/master/"
            "ta-head/en-entries/tamil_lexicon_decorated/tamil_lexicon/tamil_lexicon.babylon"
        ),
        help="URL of the Babylon dictionary file (Tamil lexicon).",
    )
    parser.add_argument(
        "--output-dir",
        default="./data_tamil_dicts",
        help="Directory where the downloaded and converted files will be stored.",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)

    # Download files
    json_path = os.path.join(args.output_dir, "dictionary.json")
    babylon_path = os.path.join(args.output_dir, "tamil_lexicon.babylon")
    download_file(args.json_url, json_path)
    download_file(args.babylon_url, babylon_path)

    # Convert JSON to TSV
    json_pairs = parse_eng2tam_json(json_path)
    json_tsv_path = os.path.join(args.output_dir, "english_tamil.tsv")
    write_tsv(json_pairs, json_tsv_path)

    # Convert Babylon to TSV
    babylon_pairs = parse_babylon(babylon_path)
    babylon_tsv_path = os.path.join(args.output_dir, "tamil_lexicon.tsv")
    write_tsv(babylon_pairs, babylon_tsv_path)

    print("All tasks completed.")


if __name__ == "__main__":
    main()
