"""
parse_wiktionary
=================

This module implements the **M2** milestone for the Spanish 5 000‑word
multilingual Anki deck.  It extracts International Phonetic Alphabet
(IPA) pronunciations and basic revision metadata for Spanish lemmas
from a preprocessed Wiktionary dump.  The script reads the frozen
lemma list produced in phase M1, looks up corresponding entries in
the dump and writes two tab‑separated files:

* ``02_ipa.tsv`` – mapping from normalised Spanish lemma (``es_norm``)
  to its IPA transcription.  Lemmas without a pronunciation are
  omitted.
* ``02_wikt_meta.tsv`` – provenance information (page ID,
  revision ID and timestamp) for each lemma.  This is used to
  regenerate or update IPA data when new dumps become available.

The expected input dump format is JSON or JSON Lines produced by
the ``wiktextract`` tool (see https://github.com/tatuylonen/wiktextract).
Each record in this format contains fields such as ``word``,
``lang``, ``lang_code``, ``page_id``, ``revision_id`` and
``sounds``.  The script only reads entries where ``lang_code`` is
``"es"`` (Spanish) and the normalised ``word`` matches one of the
lemmas in ``01_lemmas.tsv``.

Example usage
-------------

Run this script from the repository root:

```
python3 -m src.parse_wiktionary --config config.yml
```

The configuration file should include a ``wiktionary`` section with
the following keys:

```
wiktionary:
  dump_path: data_raw/wiktionary_dump/eswiktionary.json
  ipa_tsv: data_intermediate/02_ipa.tsv
  meta_tsv: data_intermediate/02_wikt_meta.tsv
  lemma_tsv: data_intermediate/01_lemmas.tsv
```

If these keys are missing, sensible defaults are used based on the
repository layout.  See ``plan.md`` for details.

Copyright
---------

This code is released under the MIT licence.  See the root of the
repository for licence information.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from common import load_config, normalize_es, resolve_path


def read_lemmas(
    lemma_path: Path, normalize: Optional[Callable[[str], str]] = None
) -> Dict[str, str]:
    """Read the lemma TSV and return a mapping of normalised lemma to display form.

    The TSV produced by M1 is expected to have a header with a column
    named ``es_norm``.  If an ``es_display`` column is present its
    values are stored as the display form; otherwise the normalised
    form is used for display as well.

    Parameters
    ----------
    lemma_path : Path
        Path to ``01_lemmas.tsv``.
    normalize : callable, optional
        Normalisation function for deriving ``es_norm`` when missing.

    Returns
    -------
    dict
        Mapping from normalised lemma to display lemma.
    """
    lemma_map: Dict[str, str] = {}
    import csv

    with lemma_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if "es_norm" not in reader.fieldnames:
            raise KeyError(f"es_norm column not found in {lemma_path}")
        for row in reader:
            es_norm = row.get("es_norm")
            if not es_norm:
                raw = row.get("es") or row.get("lemma") or row.get("word")
                if not raw:
                    continue
                es_norm = normalize(raw) if normalize else raw
            display = row.get("es_display", es_norm)
            lemma_map[es_norm] = display
    return lemma_map


def open_dump(path: Path) -> Iterable[str]:
    """Open a Wiktextract dump and yield individual JSON strings.

    The dump may be plain JSON Lines (.json or .jsonl), gzip-compressed
    (.gz) or bzip2-compressed (.bz2).  This helper detects the
    compression based on file extension.

    Parameters
    ----------
    path : Path
        Path to the dump file.

    Yields
    ------
    str
        A line containing a JSON object.
    """
    import bz2
    import gzip

    suffix = path.suffix.lower()
    if suffix == ".bz2":
        opener = bz2.open  # type: ignore
    elif suffix == ".gz":
        opener = gzip.open  # type: ignore
    else:
        opener = open  # type: ignore
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield line


def resolve_config_path(config_path: Path, value: object, default: str) -> Path:
    if value is None:
        value = default
    if isinstance(value, Path):
        path_value = str(value)
    else:
        path_value = str(value)
    return resolve_path(config_path, path_value)


def get_trailing_markers(cfg: Dict[str, object]) -> Optional[list[str]]:
    normalization = cfg.get("normalization", {})
    if not isinstance(normalization, dict):
        return None
    markers = normalization.get("strip_trailing_markers")
    if markers is None:
        return None
    if not isinstance(markers, list):
        raise SystemExit("normalization/strip_trailing_markers must be a list of strings")
    cleaned = [str(marker) for marker in markers if str(marker).strip()]
    return cleaned or None


def normalize_language_tag(value: object) -> str:
    import unicodedata

    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def iter_ipa_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for key in ("ipa", "text", "value"):
            if key in value:
                yield from iter_ipa_values(value[key])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_ipa_values(item)


def clean_ipa(value: str) -> str:
    import re

    text = value.strip()
    if not text:
        return ""
    text = text.strip("/[]")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_ipa_from_sounds(sounds: object) -> Optional[str]:
    if sounds is None:
        return None
    if isinstance(sounds, dict):
        items = [sounds]
    elif isinstance(sounds, (list, tuple)):
        items = sounds
    elif isinstance(sounds, str):
        items = [sounds]
    else:
        return None

    for item in items:
        if isinstance(item, dict):
            ipa_field = item.get("ipa")
            if ipa_field is None:
                continue
            for candidate in iter_ipa_values(ipa_field):
                cleaned = clean_ipa(candidate)
                if cleaned:
                    return cleaned
        elif isinstance(item, str):
            cleaned = clean_ipa(item)
            if cleaned:
                return cleaned
    return None


def extract_ipa(
    dump_path: Path,
    lemmas: Dict[str, str],
    normalize: Callable[[str], str],
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str, str]]]:
    """Extract IPA pronunciations and revision metadata for given lemmas.

    Parameters
    ----------
    dump_path : Path
        Path to the preprocessed Wiktionary dump (wiktextract JSON).
    lemmas : dict
        Mapping from normalised lemma to display form.
    normalize : callable
        Normalisation function for Spanish headwords.

    Returns
    -------
    Tuple[Dict[str, str], Dict[str, Tuple[str, str, str]]]
        Two dictionaries:

        * ``ipa_map`` – maps normalised lemma to IPA string.
        * ``meta_map`` – maps normalised lemma to a tuple
          ``(page_id, revision_id, revision_timestamp)``.  Entries
          without metadata are omitted.
    """
    ipa_map: Dict[str, str] = {}
    meta_map: Dict[str, Tuple[str, str, str]] = {}

    # Precompute lemma set for quick membership tests
    lemma_set = set(lemmas.keys())

    for line in open_dump(dump_path):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # Skip malformed entries
        # Check language
        lang_code = normalize_language_tag(
            entry.get("lang_code") or entry.get("lang") or entry.get("language")
        )
        if not lang_code:
            continue
        # Accept both language codes and names
        if lang_code not in {"es", "spa", "spanish", "espanol"}:
            continue
        word = entry.get("word") or entry.get("title") or entry.get("page_title")
        if not word:
            continue
        word_norm = normalize(str(word))
        if not word_norm:
            continue
        if word_norm not in lemma_set:
            continue
        # Extract first IPA from sounds/pronunciations
        sounds = entry.get("sounds")
        if not sounds:
            sounds = entry.get("pronunciations")
        ipa_value = extract_ipa_from_sounds(sounds)
        if ipa_value:
            ipa_map.setdefault(word_norm, ipa_value)
        # Extract revision metadata if available
        page_id = entry.get("page_id") or entry.get("pageId") or entry.get("pageid")
        revision_id = entry.get("revision_id") or entry.get("revisionId") or entry.get("revid")
        revision_time = (
            entry.get("revision_timestamp")
            or entry.get("revisionTimestamp")
            or entry.get("timestamp")
        )
        if page_id or revision_id or revision_time:
            meta_map.setdefault(
                word_norm,
                (
                    str(page_id) if page_id is not None else "",
                    str(revision_id) if revision_id is not None else "",
                    str(revision_time) if revision_time is not None else "",
                ),
            )
    return ipa_map, meta_map


def write_tsv(path: Path, rows: Iterable[Iterable[str]], header: Iterable[str]) -> None:
    """Write rows to a TSV file with the given header.

    Parameters
    ----------
    path : Path
        Output path.
    rows : iterable of iterables
        Data rows.
    header : iterable of str
        Header row.
    """
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


def parse_wiktionary(cfg: Dict[str, object], config_path: Path) -> None:
    """Main entry point for Wiktionary parsing.

    This function reads configuration, loads the lemma list, extracts
    IPA and metadata, and writes the output TSV files.  It does not
    return any values but raises exceptions on unrecoverable errors.

    Parameters
    ----------
    cfg : dict
        Parsed configuration.
    config_path : Path
        Path to the configuration file (used to resolve relative paths).
    """
    # Determine paths with sensible defaults
    wikt_cfg = cfg.get("wiktionary", {}) if isinstance(cfg.get("wiktionary"), dict) else {}
    dump_path = resolve_config_path(
        config_path, wikt_cfg.get("dump_path"), "data_raw/wiktionary_dump/wiktionary.json"
    )
    ipa_out = resolve_config_path(
        config_path, wikt_cfg.get("ipa_tsv"), "data_intermediate/02_ipa.tsv"
    )
    meta_out = resolve_config_path(
        config_path, wikt_cfg.get("meta_tsv"), "data_intermediate/02_wikt_meta.tsv"
    )
    lemma_path = resolve_config_path(
        config_path, wikt_cfg.get("lemma_tsv"), "data_intermediate/01_lemmas.tsv"
    )

    if not dump_path.exists():
        logging.warning("Wiktionary dump not found at %s. Nothing to parse.", dump_path)
        # Still write empty files with headers so downstream tasks can run
        write_tsv(ipa_out, [], ["es_norm", "ipa"])
        write_tsv(meta_out, [], ["es_norm", "page_id", "revision_id", "revision_timestamp"])
        return

    # Load lemmas
    if not lemma_path.exists():
        raise FileNotFoundError(f"Lemma file not found: {lemma_path}")
    trailing_markers = get_trailing_markers(cfg)
    normalizer = lambda text: normalize_es(text, trailing_markers)
    lemmas = read_lemmas(lemma_path, normalizer)
    if not lemmas:
        logging.warning("No lemmas loaded from %s", lemma_path)

    # Extract IPA and metadata
    ipa_map, meta_map = extract_ipa(dump_path, lemmas, normalizer)

    # Prepare rows for IPA TSV
    ipa_rows = [(lemma, ipa_map[lemma]) for lemma in sorted(ipa_map)]
    # Prepare rows for metadata TSV
    meta_rows = [
        (lemma,) + meta_map[lemma] for lemma in sorted(meta_map)
    ]

    # Write outputs
    write_tsv(ipa_out, ipa_rows, ["es_norm", "ipa"])
    write_tsv(meta_out, meta_rows, ["es_norm", "page_id", "revision_id", "revision_timestamp"])

    logging.info("Wrote %d IPA entries to %s", len(ipa_rows), ipa_out)
    logging.info("Wrote %d metadata entries to %s", len(meta_rows), meta_out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Wiktionary IPA and revision metadata for Spanish lemmas")
    parser.add_argument("--config", default="config.yml", help="Path to YAML configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    cfg = load_config(Path(args.config))
    parse_wiktionary(cfg, Path(args.config))


if __name__ == "__main__":
    main()
