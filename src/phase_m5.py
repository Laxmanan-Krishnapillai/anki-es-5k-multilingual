"""
phase_m5
=========

This module implements **Phase M5** of the Spanish 5 k multilingual deck
build as described in the technical implementation plan.  Phase M5 is
responsible for **merging**, **scoring** and **selecting** translation
candidates for French (FR), German (DE) and Tamil (TA), integrating
IPA and example sentences, applying simple heuristics to choose
primary versus alternate translations, and writing the resulting
records into a SQLite database.

The script reads intermediate TSV outputs produced by earlier phases:

* ``01_lemmas.tsv`` — frozen lemma list from Phase M1.
* ``02_ipa.tsv`` — Spanish lemma → IPA mapping from Phase M2.
* ``03_examples.tsv`` — curated example sentences (may be empty if
  Phase M4 has not been run or no examples are available).
* ``04_es_fr.tsv`` and ``04_es_de.tsv`` — Spanish→French/German
  candidate translations from Phase M3 (primary dictionaries and
  Wiktionary fallback).
* ``05_es_ta.tsv`` and ``05_es_ta_pivoted.tsv`` — Spanish→Tamil
  direct and pivot candidates from Phase M4.

For each lemma the script collects all candidate translations along
with their provenance (dictionary versus Wiktionary) and any gloss
information.  It then assigns a **score** to each candidate using
simple, deterministic rules drawn from the plan: candidates from
primary dictionary datasets receive a higher base score; those from
Wiktionary receive a smaller bonus; candidates appearing in multiple
sources gain an extra point; very long terms are penalised.  For
Tamil the direct (es→ta) path starts with a higher base score than
the pivoted (es→en→ta) path, and pivot candidates set a ``ta_pivot``
flag.

After scoring, the top candidate for each language becomes the
**primary** translation.  Up to three additional high‑scoring
candidates are kept as **alternate** translations and surfaced in the
``notes`` field in a consistent format (e.g., ``FR alt: … | …``).
If no candidates are available for a given language the script leaves
the primary empty and sets a corresponding ``missing_*`` flag.  When
the primary Tamil candidate comes from the pivot path, a
``low_confidence_ta`` flag is added.

Finally, the script constructs a row per lemma with the fields
specified in the plan (id, lemma, POS, rank, zipf, IPA, example
sentence and source, primary translations, alternate translations,
compact provenance string, flags and notes) and inserts it into a
SQLite database.  The database schema matches the stub defined in
``merge_score_select.py`` within the repository.

Example usage::

    python3 phase_m5.py --config config.yml

The YAML configuration may define custom paths in its ``outputs``
section (``es_fr_tsv``, ``es_de_tsv``, ``es_ta_tsv``,
``es_ta_pivoted_tsv``), and override the locations of the lemma,
IPA and example files.  See ``phase_m3`` and ``phase_m4`` for
examples of the configuration schema.

Copyright
---------

This code is released under the MIT licence.  See the root of the
repository for licence information.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None  # type: ignore

CSV_FIELD_SIZE_LIMIT = 2_000_000
try:
    csv.field_size_limit(max(csv.field_size_limit(), CSV_FIELD_SIZE_LIMIT))
except Exception:  # pragma: no cover - best effort for large TSV fields
    pass


###############################################################################
# Configuration helpers
###############################################################################

def load_config(config_path: Path) -> dict:
    """Load a YAML configuration file and return a dictionary.

    If PyYAML is not installed or the file cannot be parsed, an empty
    dict is returned instead.  Relative paths in the config will be
    resolved relative to the configuration file when later passed to
    :func:`resolve_path`.

    Parameters
    ----------
    config_path : Path
        Path to a YAML configuration file.

    Returns
    -------
    dict
        The parsed configuration dictionary or an empty dict.
    """
    if yaml is None:
        logging.warning(
            "PyYAML not installed; using default paths and empty configuration"
        )
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logging.warning("Configuration file not found: %s", config_path)
        return {}
    except Exception as exc:
        logging.warning("Failed to load configuration %s: %s", config_path, exc)
        return {}


def resolve_path(base: Path, target: str) -> Path:
    """Resolve a user‑supplied path relative to a base file.

    If ``target`` is an absolute path it is returned verbatim.  If it
    is relative, it is interpreted relative to the directory containing
    ``base``.

    Parameters
    ----------
    base : Path
        The reference file from which to resolve relative paths (e.g.
        the configuration YAML).
    target : str
        User‑supplied path (absolute or relative).

    Returns
    -------
    Path
        A resolved absolute path.
    """
    path = Path(target)
    if path.is_absolute():
        return path
    return (base.parent / path).resolve()


###############################################################################
# TSV loading helpers
###############################################################################

SOURCE_META_TAGS = {"pivot"}
WIKTIONARY_TAGS = {"wiktionary"}
DICTIONARY_TAGS = {
    "apertium",
    "ding",
    "freedict",
    "tamil_dictionary_tsv",
    "tamil_dictionary_json",
}


def find_column(fieldnames: Iterable[str], candidates: Set[str]) -> Optional[str]:
    """Return the first matching fieldname (case-insensitive)."""
    for name in fieldnames:
        if not name:
            continue
        normalized = name.strip().lower()
        if normalized in candidates:
            return name
    return None


def split_sources(source_field: str) -> Set[str]:
    """Split a semicolon-delimited source field into normalized tags."""
    sources: Set[str] = set()
    if not source_field:
        return sources
    for part in source_field.split(";"):
        tag = part.strip().lower()
        if not tag or tag in SOURCE_META_TAGS:
            continue
        sources.add(tag)
    return sources


def read_examples(path: Path) -> Dict[str, Tuple[str, str]]:
    """Read example TSV and return mapping from es_norm to (example, source)."""
    examples: Dict[str, Tuple[str, str]] = {}
    if not path.exists():
        return examples
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return examples
        key_col = find_column(reader.fieldnames, {"es_norm"})
        example_col = find_column(reader.fieldnames, {"example", "example_es", "sentence"})
        source_col = find_column(reader.fieldnames, {"source", "example_source"})
        if key_col is None or example_col is None:
            logging.warning("Examples TSV missing required columns in %s", path)
            return examples
        for row in reader:
            key = (row.get(key_col) or "").strip()
            if not key:
                continue
            example = (row.get(example_col) or "").strip()
            source = (row.get(source_col) or "").strip() if source_col else ""
            examples[key] = (example, source)
    return examples


def read_lemmas(lemma_path: Path) -> List[Dict[str, str]]:
    """Read the lemma TSV from Phase M1 and return a list of dictionaries.

    The TSV must include at least the columns ``id`` and ``es_norm``.
    Additional columns (``es_display``, ``pos``, ``rank``, ``zipf``) are
    also read if present.  Missing optional columns are filled with
    empty strings.

    Parameters
    ----------
    lemma_path : Path
        Path to the ``01_lemmas.tsv`` file.

    Returns
    -------
    list of dict
        Each dictionary contains the lemma fields keyed by column name.
    """
    lemmas: List[Dict[str, str]] = []
    with lemma_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = set(reader.fieldnames or [])
        # ``es_norm`` is mandatory for joins; ``id`` is recommended but can be
        # synthesised when absent.  If ``id`` is missing we generate a
        # sequential id based on row order (1‑indexed).
        if "es_norm" not in fieldnames:
            raise ValueError(
                f"Lemma file {lemma_path} missing required column 'es_norm'"
            )
        has_id = "id" in fieldnames
        for idx, row in enumerate(reader, start=1):
            if not has_id:
                # Generate an id if not provided
                row = dict(row)
                row["id"] = str(idx)
            lemmas.append(row)
    return lemmas


def read_simple_map(path: Path, key_col: str, val_col: str) -> Dict[str, str]:
    """Load a two‑column TSV into a mapping from key to value.

    If a key appears multiple times the last occurrence wins.  Missing
    files result in an empty mapping.

    Parameters
    ----------
    path : Path
        Path to the TSV file.
    key_col : str
        The column to use as dictionary keys.
    val_col : str
        The column to use as dictionary values.

    Returns
    -------
    dict
        Mapping from ``key_col`` to ``val_col``.
    """
    mapping: Dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not reader.fieldnames:
                return mapping
            key_name = find_column(reader.fieldnames, {key_col.lower()})
            val_name = find_column(reader.fieldnames, {val_col.lower()})
            if key_name is None or val_name is None:
                logging.warning(
                    "Missing expected columns %s and %s in %s",
                    key_col,
                    val_col,
                    path,
                )
                return mapping
            for row in reader:
                key = row.get(key_name, "").strip()
                val = row.get(val_name, "").strip()
                if key:
                    mapping[key] = val
    except FileNotFoundError:
        logging.warning("File not found: %s", path)
    return mapping


def load_translation_candidates(
    path: Path, key_col: str, val_col: str
) -> Dict[str, List[Tuple[str, Set[str], str]]]:
    """Load translation candidates from a TSV file.

    Returns a mapping from the key (e.g. Spanish lemma) to a list of
    tuples ``(candidate_text, sources_set, gloss)`` where
    ``sources_set`` is a set of provenance tags (e.g. ``{"freedict"}`` or
    ``{"wiktionary", "freedict"}`` when a candidate appears in both).  The
    TSV is expected to have at least the columns specified by
    ``key_col`` and ``val_col``, as well as ``source`` and ``gloss``.

    Parameters
    ----------
    path : Path
        Path to the TSV file to read.
    key_col : str
        Column containing the key (e.g. ``es_norm``).
    val_col : str
        Column containing the candidate translation (e.g. ``fr``).

    Returns
    -------
    dict
        Mapping from keys to lists of candidate tuples.
    """
    result: Dict[str, Dict[str, Tuple[Set[str], str]]] = defaultdict(dict)
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not reader.fieldnames:
                return {}
            key_name = find_column(reader.fieldnames, {key_col.lower()})
            val_name = find_column(reader.fieldnames, {val_col.lower()})
            if key_name is None or val_name is None:
                logging.warning(
                    "Missing required columns %s and %s in %s",
                    key_col,
                    val_col,
                    path,
                )
                return {}
            source_name = find_column(reader.fieldnames, {"source"})
            gloss_name = find_column(reader.fieldnames, {"gloss"})
            if source_name is None:
                logging.warning("Translation file missing source column: %s", path)
            if gloss_name is None:
                logging.warning("Translation file missing gloss column: %s", path)
            for row in reader:
                key = (row.get(key_name) or "").strip()
                val = (row.get(val_name) or "").strip()
                if not key or not val:
                    continue
                source_field = (row.get(source_name) or "").strip() if source_name else ""
                sources_set = split_sources(source_field)
                gloss = (row.get(gloss_name) or "").strip() if gloss_name else ""
                existing = result[key].get(val)
                if existing is None:
                    result[key][val] = (set(sources_set), gloss)
                else:
                    existing_sources, existing_gloss = existing
                    existing_sources.update(sources_set)
                    if not existing_gloss and gloss:
                        existing_gloss = gloss
                    result[key][val] = (existing_sources, existing_gloss)
    except FileNotFoundError:
        logging.warning("Translation file not found: %s", path)
    # Flatten to the expected list-of-tuples structure
    flattened: Dict[str, List[Tuple[str, Set[str], str]]] = {}
    for key, mapping in result.items():
        flattened[key] = [
            (text, sources, gloss) for text, (sources, gloss) in mapping.items()
        ]
    return flattened


###############################################################################
# Scoring and selection logic
###############################################################################

def is_dictionary_source(source: str) -> bool:
    if source in WIKTIONARY_TAGS:
        return False
    if source in DICTIONARY_TAGS:
        return True
    if "dictionary" in source or "lexicon" in source or "dict" in source:
        return True
    return False


def has_dictionary_source(sources: Set[str]) -> bool:
    return any(is_dictionary_source(source) for source in sources)


def score_candidate(
    candidate: Tuple[str, Set[str], str],
    is_tamil: bool = False,
    is_pivot: bool = False,
) -> int:
    """Compute a score for a translation candidate.

    The scoring rules follow the guidelines laid out in the technical
    implementation plan: primary dictionary entries receive a larger
    boost, Wiktionary entries a smaller bonus, candidates appearing in
    multiple sources gain an extra point, and overly long terms are
    penalised.  For Tamil, direct candidates start higher than pivot
    candidates.

    Parameters
    ----------
    candidate : tuple
        A tuple ``(text, sources_set, gloss)`` where ``sources_set``
        contains provenance strings (e.g. ``{"freedict", "wiktionary"}``).
    is_tamil : bool, optional
        Whether the candidate is for Tamil.  Used to adjust base scores
        between direct and pivot routes.
    is_pivot : bool, optional
        True if the Tamil candidate comes via the pivot path.

    Returns
    -------
    int
        The computed score.
    """
    text, sources, _gloss = candidate
    score = 0
    # Base scores from dictionary and Wiktionary
    if has_dictionary_source(sources):
        score += 2
    if WIKTIONARY_TAGS.intersection(sources):
        score += 1
    # Extra point for multiple distinct sources
    if len(sources) > 1:
        score += 1
    # Tamil direct vs pivot adjustment
    if is_tamil:
        if is_pivot:
            # pivoted candidates start slightly lower
            score -= 1
        else:
            # direct candidates start slightly higher
            score += 1
    # Penalty for very long terms (>25 chars)
    if len(text) > 25:
        score -= 1
    return score


def select_translations(
    candidates: List[Tuple[str, Set[str], str]],
    is_tamil: bool = False,
    pivot_flags: Optional[List[bool]] = None,
    max_alts: int = 3,
) -> Tuple[str, List[str], Set[str], List[str]]:
    """Select primary and alternate translations from a list of candidates.

    Parameters
    ----------
    candidates : list of tuple
        List of candidate tuples ``(text, sources_set, gloss)`` for a
        given language.
    is_tamil : bool, optional
        Whether the candidates are for Tamil.  Influences scoring
        logic when ``pivot_flags`` is provided.
    pivot_flags : list of bool, optional
        Parallel list indicating whether each candidate is a pivot
        candidate (only used when ``is_tamil`` is True).  If provided
        it must have the same length as ``candidates``.
    max_alts : int, optional
        Maximum number of alternate translations to return.

    Returns
    -------
    (primary, alt_list, combined_sources, flag_list)
        ``primary`` is the selected primary translation (empty if none).
        ``alt_list`` contains up to ``max_alts`` alternate translations.
        ``combined_sources`` is the union of all provenance strings for
        the candidates.
        ``flag_list`` contains any flags inferred from the selection,
        currently only ``ta_pivot`` when the primary Tamil candidate
        comes via the pivot path.
    """
    if not candidates:
        return "", [], set(), []

    # Compute scores for each candidate
    scores: List[int] = []
    for idx, candidate in enumerate(candidates):
        is_pivot = False
        if is_tamil and pivot_flags is not None and idx < len(pivot_flags):
            is_pivot = pivot_flags[idx]
        scores.append(score_candidate(candidate, is_tamil=is_tamil, is_pivot=is_pivot))

    # Sort candidates by (score desc, length asc, lex order asc)
    sorted_indices = sorted(
        range(len(candidates)),
        key=lambda i: (
            -scores[i],
            len(candidates[i][0]),
            candidates[i][0]
        ),
    )
    primary_idx = sorted_indices[0]
    primary = candidates[primary_idx][0]

    # Determine flags for Tamil pivot
    flags: List[str] = []
    if is_tamil and pivot_flags is not None:
        if pivot_flags[primary_idx]:
            flags.append("ta_pivot")

    # Gather alternates excluding primary; preserve ordering by score
    alt_list: List[str] = []
    seen_alts: Set[str] = {primary}
    for idx in sorted_indices[1:]:
        text = candidates[idx][0]
        if text in seen_alts:
            continue  # skip duplicates
        seen_alts.add(text)
        alt_list.append(text)
        if len(alt_list) >= max_alts:
            break

    # Combine sources across all candidates for provenance summary
    combined_sources: Set[str] = set()
    for cand in candidates:
        combined_sources.update(cand[1])

    return primary, alt_list, combined_sources, flags


###############################################################################
# Main processing function
###############################################################################

def merge_score_select(cfg: dict, config_path: Path) -> None:
    """Merge, score and select translation candidates into a SQLite DB.

    Parameters
    ----------
    cfg : dict
        Parsed configuration dictionary.
    config_path : Path
        Path to the configuration file (used to resolve relative paths).
    """
    # Resolve input paths with defaults
    outputs_cfg = cfg.get("outputs", {}) if isinstance(cfg.get("outputs"), dict) else {}
    wiktionary_cfg = cfg.get("wiktionary", {}) if isinstance(cfg.get("wiktionary"), dict) else {}

    def get_path(key: str, default: str) -> Path:
        if key in outputs_cfg:
            return resolve_path(config_path, outputs_cfg[key])
        return resolve_path(config_path, default)

    # Input files
    lemma_path = resolve_path(
        config_path,
        wiktionary_cfg.get("lemma_tsv", "data_intermediate/01_lemmas.tsv"),
    )
    ipa_path = resolve_path(
        config_path,
        wiktionary_cfg.get("ipa_tsv", "data_intermediate/02_ipa.tsv"),
    )
    examples_path = get_path("examples_tsv", "data_intermediate/03_examples.tsv")
    es_fr_path = get_path("es_fr_tsv", "data_intermediate/04_es_fr.tsv")
    es_de_path = get_path("es_de_tsv", "data_intermediate/04_es_de.tsv")
    es_ta_path = get_path("es_ta_tsv", "data_intermediate/05_es_ta.tsv")
    es_ta_pivoted_path = get_path(
        "es_ta_pivoted_tsv", "data_intermediate/05_es_ta_pivoted.tsv"
    )

    # Output database
    db_path = resolve_path(
        config_path,
        outputs_cfg.get("deck_db", "deck.sqlite"),
    )

    # Load data
    lemmas = read_lemmas(lemma_path)
    ipa_map = read_simple_map(ipa_path, key_col="es_norm", val_col="ipa")
    examples_map = read_examples(examples_path)

    # Translation candidates
    fr_cands = load_translation_candidates(es_fr_path, "es_norm", "fr")
    de_cands = load_translation_candidates(es_de_path, "es_norm", "de")
    ta_direct_cands = load_translation_candidates(es_ta_path, "es_norm", "ta")
    ta_pivot_cands = load_translation_candidates(es_ta_pivoted_path, "es_norm", "ta")

    # Prepare SQLite
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # Create table if needed
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS lemma_records (
    id INTEGER PRIMARY KEY,
    es TEXT NOT NULL,
    es_norm TEXT NOT NULL,
    pos TEXT,
    rank INTEGER,
    zipf REAL,
    ipa TEXT,
    example_es TEXT,
    example_source TEXT,
    fr_primary TEXT,
    de_primary TEXT,
    ta_primary TEXT,
    fr_alt TEXT,
    de_alt TEXT,
    ta_alt TEXT,
    sources TEXT,
    flags TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_lemma_records_es_norm ON lemma_records(es_norm);
"""
        )

        # Prepare insertion
        insert_sql = (
            "INSERT OR REPLACE INTO lemma_records "
            "(id, es, es_norm, pos, rank, zipf, ipa, example_es, example_source, "
            "fr_primary, de_primary, ta_primary, fr_alt, de_alt, ta_alt, sources, flags, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

        # Process each lemma
        for lemma in lemmas:
            es_id = int(lemma.get("id", "0"))
            es_norm = lemma.get("es_norm", "").strip()
            es_display = lemma.get("es_display") or lemma.get("es", es_norm)
            pos = lemma.get("pos", "").strip()
            rank = lemma.get("rank")
            rank_int = int(rank) if rank and rank.isdigit() else None
            zipf_str = lemma.get("zipf", "")
            try:
                zipf = float(zipf_str) if zipf_str else None
            except ValueError:
                zipf = None

            # IPA
            ipa = ipa_map.get(es_norm, "")

            # Example
            example, example_source = examples_map.get(es_norm, ("", ""))

            # Flags
            flags: List[str] = []

            # French translations
            fr_list = fr_cands.get(es_norm, [])
            fr_primary, fr_alts, fr_sources, _ = select_translations(fr_list)
            if not fr_primary:
                flags.append("missing_fr")

            # German translations
            de_list = de_cands.get(es_norm, [])
            de_primary, de_alts, de_sources, _ = select_translations(de_list)
            if not de_primary:
                flags.append("missing_de")

            # Tamil translations: combine direct and pivot lists
            ta_list: List[Tuple[str, Set[str], str]] = []
            pivot_flags: List[bool] = []
            # Add direct candidates first; mark pivot_flags False
            for cand in ta_direct_cands.get(es_norm, []):
                ta_list.append(cand)
                pivot_flags.append(False)
            # Add pivot candidates; mark pivot_flags True
            for cand in ta_pivot_cands.get(es_norm, []):
                ta_list.append(cand)
                pivot_flags.append(True)

            ta_primary, ta_alts, ta_sources, ta_flags = select_translations(
                ta_list, is_tamil=True, pivot_flags=pivot_flags
            )
            if not ta_primary:
                flags.append("missing_ta")
            flags.extend(ta_flags)

            # Missing example
            if not example:
                flags.append("missing_example")

            # Compile sources string (language:sorted sources)
            src_parts: List[str] = []
            if fr_sources:
                src_parts.append("fr:" + ",".join(sorted(fr_sources)))
            if de_sources:
                src_parts.append("de:" + ",".join(sorted(de_sources)))
            if ta_sources:
                # Use short label for tamil: direct vs pivot not encoded here
                src_parts.append("ta:" + ",".join(sorted(ta_sources)))
            sources_str = ";".join(src_parts)

            # Build notes string from alternate translations
            notes_parts: List[str] = []
            if fr_alts:
                notes_parts.append("FR alt: " + " | ".join(fr_alts))
            if de_alts:
                notes_parts.append("DE alt: " + " | ".join(de_alts))
            if ta_alts:
                notes_parts.append("TA alt: " + " | ".join(ta_alts))
            notes = "; ".join(notes_parts)

            # Consolidate flags into comma‑separated string
            flags_str = ",".join(sorted(set(flags))) if flags else ""

            # Prepare fields for insertion (use None for missing numeric values)
            row = (
                es_id,
                es_display,
                es_norm,
                pos if pos else None,
                rank_int,
                zipf,
                ipa if ipa else None,
                example if example else None,
                example_source if example_source else None,
                fr_primary if fr_primary else None,
                de_primary if de_primary else None,
                ta_primary if ta_primary else None,
                " | ".join(fr_alts) if fr_alts else None,
                " | ".join(de_alts) if de_alts else None,
                " | ".join(ta_alts) if ta_alts else None,
                sources_str if sources_str else None,
                flags_str if flags_str else None,
                notes if notes else None,
            )
            conn.execute(insert_sql, row)

        conn.commit()
        print(f"[phase_m5] completed merging and inserted {len(lemmas)} records into {db_path}")
    finally:
        conn.close()


def main() -> None:
    """Command‑line entry point for Phase M5."""
    parser = argparse.ArgumentParser(description="Merge, score, and select candidates")
    parser.add_argument("--config", default="config.yml", help="Path to YAML configuration")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    merge_score_select(cfg, config_path)


if __name__ == "__main__":  # pragma: no cover
    main()
