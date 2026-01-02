"""
phase_m4
========

This module implements **Phase M4** of the Spanish 5 k multilingual deck
build as described in the technical plan.  Phase M4 focuses on
constructing candidate Tamil translations for each of the 5 000
Spanish lemmas using two complementary pathways:

1. **Direct translations** – When the Wiktionary dump contains
   Spanish→Tamil translations, those are taken as primary
   candidates.
2. **Pivot translations** – For lemmas without a direct Tamil
   translation (or as additional alternatives), the script builds
   Spanish→English and English→Tamil pairs via dictionary datasets
   produced in Phase M3.  Only dictionary‑derived edges are
   considered when pivoting to ensure high quality.

The script reads the frozen lemma list produced in Phase M1,
leverages the Wiktionary dump prepared in Phase M2, and consumes
the ``05_es_en.tsv`` and ``05_en_ta.tsv`` files emitted by
``parse_dictionaries.py`` in Phase M3.  It outputs two TSV files
under ``data_intermediate`` (or configurable paths):

* ``05_es_ta.tsv`` – direct Spanish→Tamil translation candidates.
* ``05_es_ta_pivoted.tsv`` – Spanish→Tamil candidates built via
  Spanish→English and English→Tamil dictionary lookups.

Each TSV row has the columns ``(es_norm, ta, source, gloss)``.
The ``source`` column records a provenance tag (``wiktionary`` for
direct translations and ``freedict_pivot`` for dictionary‑based
pivoted translations).  The ``gloss`` column contains the sense or
definition from the Spanish→English dictionary when available,
falling back to the English→Tamil dictionary gloss if present.

Example usage
-------------

Run from the repository root:

```
python3 -m phase_m4 --config config.yml
```

The YAML configuration may specify the following keys (all
optional; sensible defaults are provided):

* ``wiktionary.dump_path`` – path to the Wiktionary dump (JSON/JSONL)
  produced by ``wiktextract``.  Used to build direct es→ta
  translations.  If omitted or missing, the direct output will be
  empty (header only).
* ``wiktionary.lemma_tsv`` – path to the lemma list (defaults to
  ``data_intermediate/01_lemmas.tsv``).
* ``outputs.es_en_tsv`` – path to the es→en dictionary TSV
  (defaults to ``data_intermediate/05_es_en.tsv``).
* ``outputs.en_ta_tsv`` – path to the en→ta dictionary TSV
  (defaults to ``data_intermediate/05_en_ta.tsv``).
* ``outputs.es_ta_tsv`` – path to write direct es→ta candidates
  (defaults to ``data_intermediate/05_es_ta.tsv``).
* ``outputs.es_ta_pivoted_tsv`` – path to write pivoted es→ta
  candidates (defaults to ``data_intermediate/05_es_ta_pivoted.tsv``).

Copyright
---------

This code is released under the MIT licence.  See the root of the
repository for licence information.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from common import normalize_es

try:
    # ``yaml`` is optional.  If unavailable the config loader
    # gracefully falls back to empty configuration.
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


def load_config(config_path: Path) -> dict:
    """Load a YAML configuration file or return an empty dict.

    Parameters
    ----------
    config_path : Path
        Path to the YAML file.

    Returns
    -------
    dict
        Parsed configuration dictionary or empty if loading fails.
    """
    if yaml is None:
        logging.warning("PyYAML not installed; using empty configuration")
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logging.warning("Configuration file not found: %s", config_path)
        return {}
    except Exception as e:
        logging.warning("Failed to load configuration %s: %s", config_path, e)
        return {}


def resolve_path(base: Path, target: str) -> Path:
    """Resolve ``target`` relative to ``base``.

    If ``target`` is an absolute path it is returned unchanged.
    Otherwise it is interpreted as relative to the directory
    containing ``base``.

    Parameters
    ----------
    base : Path
        The reference file (e.g. config file) used to resolve
        relative paths.
    target : str
        The user‑specified path (absolute or relative).

    Returns
    -------
    Path
        The resolved path.
    """
    path = Path(target)
    if path.is_absolute():
        return path
    return (base.parent / path).resolve()


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


def read_lemmas(
    lemma_path: Path, normalize: Optional[Callable[[str], str]] = None
) -> Dict[str, str]:
    """Read the M1 lemma list and return a mapping from ``es_norm`` to
    ``es_display``.

    The TSV is expected to have at least an ``es_norm`` column.  If an
    ``es_display`` column is present its values are used as the
    display form; otherwise the normalised form is used for display.

    Parameters
    ----------
    lemma_path : Path
        Path to the ``01_lemmas.tsv`` file from phase M1.

    Returns
    -------
    dict
        Mapping from normalised lemma to display lemma.
    """
    lemmas: Dict[str, str] = {}
    try:
        with lemma_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if "es_norm" not in reader.fieldnames:
                raise KeyError(f"es_norm column not found in {lemma_path}")
            for row in reader:
                es_norm = row.get("es_norm")
                if not es_norm:
                    # fallback to any other reasonable field
                    raw = row.get("es") or row.get("lemma") or row.get("word")
                    if not raw:
                        continue
                    es_norm = normalize(raw) if normalize else normalize_generic(raw)
                display = row.get("es_display", es_norm)
                lemmas[es_norm] = display
    except FileNotFoundError:
        logging.error("Lemma file not found: %s", lemma_path)
        raise
    return lemmas


def normalize_generic(text: str) -> str:
    """Simplistic normalisation for non‑Spanish words.

    Converts text to NFC, lowercases and trims surrounding whitespace
    and punctuation.  This is a fallback for languages where
    ``normalize_es`` is not appropriate (e.g. English headwords).

    Parameters
    ----------
    text : str
        The input string.

    Returns
    -------
    str
        Normalised string.
    """
    if not text:
        return ""
    # Normalise to NFC
    text = unicodedata.normalize("NFC", text)
    # Lowercase
    text = text.lower().strip()
    # Remove surrounding punctuation and underscores
    text = re.sub(r"^[\s\W_]+|[\s\W_]+$", "", text)
    # Collapse multiple whitespace
    text = re.sub(r"\s+", " ", text)
    return text


EN_HEADWORD_SPLIT_RE = re.compile(r"\s*[,;/]\s*")
EN_PAREN_RE = re.compile(r"\s*\([^)]*\)")
EN_POS_MARKERS = {
    "n",
    "v",
    "adj",
    "adv",
    "pron",
    "prep",
    "conj",
    "interj",
    "pl",
    "vt",
    "vi",
}


def split_english_headwords(text: str) -> List[str]:
    if not text:
        return []
    text = text.strip().strip('"').strip("'")
    if not text:
        return []
    parts = EN_HEADWORD_SPLIT_RE.split(text)
    cleaned: List[str] = []
    for part in parts:
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        part = EN_PAREN_RE.sub("", part).strip()
        part = part.strip(" .;,:")
        part = re.sub(r"\s+", " ", part)
        if not part:
            continue
        marker = part.lower().strip(".")
        if marker in EN_POS_MARKERS:
            continue
        cleaned.append(part)
    return cleaned or [text]


EN_SLASH_SPLIT_RE = re.compile(r"\s*/\s*")


def expand_english_variants_for_pivot(text: str) -> List[str]:
    if not text:
        return []
    base = text.strip()
    if not base:
        return []
    variants = {base}
    no_paren = EN_PAREN_RE.sub("", base).strip()
    if no_paren:
        variants.add(no_paren)
    expanded: Set[str] = set()
    for variant in variants:
        if "/" in variant:
            for part in EN_SLASH_SPLIT_RE.split(variant):
                part = part.strip()
                if part:
                    expanded.add(part)
        expanded.add(variant)
    cleaned: Set[str] = set()
    for variant in expanded:
        cleaned_variant = variant.strip(" .;,:")
        if not cleaned_variant:
            continue
        cleaned.add(cleaned_variant)
        lower = cleaned_variant.lower()
        if lower.startswith("to "):
            stripped = cleaned_variant[3:].strip()
            if stripped and " " not in stripped:
                cleaned.add(stripped)
    return sorted(cleaned)


def normalize_language_tag(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def open_dump(path: Path) -> Iterable[str]:
    """Open a Wiktextract dump in JSON/JSONL with optional compression.

    Parameters
    ----------
    path : Path
        Path to the dump file (.json, .jsonl, .gz, .bz2).

    Yields
    ------
    str
        Each non‑empty line containing a JSON object.
    """
    import gzip
    import bz2

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


def parse_wiktionary_es_ta(
    dump_path: Path,
    lemma_set: Set[str],
    normalize: Optional[Callable[[str], str]] = None,
) -> Dict[str, List[Tuple[str, Optional[str]]]]:
    """Extract direct Spanish→Tamil translations from a wiktextract dump.

    The function scans a JSON or JSONL dump produced by ``wiktextract``
    and collects translation words where the target language is Tamil
    (ISO 639‑1 code ``ta``).  It restricts processing to entries
    where the ``lang_code`` corresponds to Spanish and the normalised
    lemma appears in the provided lemma set.  For each translation,
    the function records the Tamil word and any available sense/gloss
    information.

    Parameters
    ----------
    dump_path : Path
        Path to the wiktextract dump (.json, .jsonl, .gz, .bz2).
    lemma_set : set of str
        Normalised Spanish lemmas to include.

    Returns
    -------
    dict
        Mapping from ``es_norm`` to a list of ``(ta_word, gloss)`` tuples.
    """
    results: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    normalizer = normalize or normalize_generic
    if not dump_path.exists():
        logging.warning("Wiktionary dump not found at %s", dump_path)
        return results
    for line in open_dump(dump_path):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Accept both language codes and names; normalise to lower‑case
        lang_code = normalize_language_tag(entry.get("lang_code") or entry.get("lang") or "")
        if lang_code not in {"es", "spa", "spanish", "espanol"}:
            continue
        word = entry.get("word") or entry.get("title") or entry.get("page_title")
        if not word:
            continue
        es_norm = normalizer(str(word))
        if es_norm not in lemma_set:
            continue
        # helper to record a translation
        def add_trans(text: str, gloss: Optional[str]) -> None:
            if not text:
                return
            results.setdefault(es_norm, []).append((text.strip(), gloss))

        # Top‑level translations
        top_trans = entry.get("translations")
        if isinstance(top_trans, list):
            for trans in top_trans:
                lang = (trans.get("lang_code") or trans.get("lang") or "").lower()
                if lang != "ta":
                    continue
                text = trans.get("word") or trans.get("text") or trans.get("title")
                gloss = trans.get("sense") or trans.get("gloss")
                add_trans(str(text) if text else "", gloss)
        # Sense translations
        senses = entry.get("senses")
        if isinstance(senses, list):
            for sense in senses:
                sense_gloss = sense.get("gloss") or sense.get("raw_gloss")
                sense_trans = sense.get("translations")
                if not isinstance(sense_trans, list):
                    continue
                for trans in sense_trans:
                    lang = (trans.get("lang_code") or trans.get("lang") or "").lower()
                    if lang != "ta":
                        continue
                    text = trans.get("word") or trans.get("text") or trans.get("title")
                    gloss = trans.get("sense") or trans.get("gloss") or sense_gloss
                    add_trans(str(text) if text else "", gloss)
    return results


def read_translation_tsv(
    path: Path, src_col: str, tgt_col: str, source_col: str, gloss_col: str
) -> List[Tuple[str, str, str, str]]:
    """Read a translation TSV and return a list of rows.

    The function expects a header row with at least the specified
    column names.  Missing files result in an empty list.

    Parameters
    ----------
    path : Path
        Path to the TSV file.
    src_col : str
        Name of the column containing the source word.
    tgt_col : str
        Name of the column containing the target word.
    source_col : str
        Name of the column containing the provenance string.
    gloss_col : str
        Name of the column containing the gloss.

    Returns
    -------
    list of tuple
        Each tuple is ``(src, tgt, source, gloss)``.
    """
    rows: List[Tuple[str, str, str, str]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                src = row.get(src_col)
                tgt = row.get(tgt_col)
                src_tag = row.get(source_col)
                gloss = row.get(gloss_col)
                if src is None or tgt is None:
                    continue
                rows.append((src, tgt, src_tag or "", gloss or ""))
    except FileNotFoundError:
        logging.warning("Translation TSV not found: %s", path)
    return rows


def find_column(fieldnames: Iterable[str], candidates: Set[str]) -> Optional[str]:
    for name in fieldnames:
        if not name:
            continue
        normalized = name.strip().lower()
        if normalized in candidates:
            return name
    return None


def read_tamil_dictionary_tsv(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not reader.fieldnames:
                return rows
            src_col = find_column(
                reader.fieldnames, {"source", "src", "english", "eng", "en"}
            )
            tgt_col = find_column(reader.fieldnames, {"target", "tgt", "tamil", "ta"})
            if src_col is None or tgt_col is None:
                logging.warning("Tamil dictionary TSV missing columns: %s", path)
                return rows
            for row in reader:
                src = row.get(src_col)
                tgt = row.get(tgt_col)
                if not src or not tgt:
                    continue
                for headword in split_english_headwords(src):
                    if not headword:
                        continue
                    rows.append((headword.strip(), tgt.strip()))
    except FileNotFoundError:
        logging.warning("Tamil dictionary TSV not found: %s", path)
    return rows


def read_tamil_dictionary_json(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logging.warning("Tamil dictionary JSON not found: %s", path)
        return rows
    except Exception as e:
        logging.warning("Failed to read Tamil dictionary JSON %s: %s", path, e)
        return rows
    if isinstance(data, dict):
        for eng, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for headword in split_english_headwords(str(eng)):
                            if not headword:
                                continue
                            rows.append((headword.strip(), item.strip()))
            elif isinstance(value, str):
                for headword in split_english_headwords(str(eng)):
                    if not headword:
                        continue
                    rows.append((headword.strip(), value.strip()))
        return rows
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            eng = item.get("eng") or item.get("english")
            tamil = item.get("tamil")
            if isinstance(eng, str) and isinstance(tamil, str):
                for headword in split_english_headwords(eng):
                    if not headword:
                        continue
                    rows.append((headword.strip(), tamil.strip()))
        return rows
    logging.warning("Unexpected Tamil dictionary JSON format: %s", path)
    return rows


def load_tamil_dictionary_rows(
    cfg: dict, config_path: Path
) -> List[Tuple[str, str, str, str]]:
    tamil_cfg_raw = cfg.get("tamil_dictionary")
    tamil_cfg = tamil_cfg_raw if isinstance(tamil_cfg_raw, dict) else {}
    tsv_val = tamil_cfg.get("english_tamil_tsv")
    json_val = tamil_cfg.get("dictionary_json")
    tsv_path = resolve_path(
        config_path,
        tsv_val if tsv_val is not None else "data_tamil_dicts/english_tamil.tsv",
    )
    json_path = resolve_path(
        config_path,
        json_val if json_val is not None else "data_tamil_dicts/dictionary.json",
    )
    pairs = read_tamil_dictionary_tsv(tsv_path)
    source_tag = ""
    if pairs:
        source_tag = "tamil_dictionary_tsv"
    else:
        pairs = read_tamil_dictionary_json(json_path)
        if pairs:
            source_tag = "tamil_dictionary_json"
    if not pairs:
        return []
    rows: List[Tuple[str, str, str, str]] = []
    for eng, tamil in pairs:
        if not eng or not tamil:
            continue
        rows.append((eng, tamil, source_tag, ""))
    return rows


def merge_translation_rows(
    base_rows: List[Tuple[str, str, str, str]],
    extra_rows: List[Tuple[str, str, str, str]],
) -> None:
    if not extra_rows:
        return
    seen: Set[Tuple[str, str]] = set()
    for en_word, ta_word, _, _ in base_rows:
        if not en_word or not ta_word:
            continue
        seen.add((normalize_generic(en_word), ta_word.strip()))
    for en_word, ta_word, source, gloss in extra_rows:
        if not en_word or not ta_word:
            continue
        key = (normalize_generic(en_word), ta_word.strip())
        if not key[0] or not key[1] or key in seen:
            continue
        base_rows.append((en_word, ta_word, source, gloss))
        seen.add(key)


def write_tsv(path: Path, rows: Iterable[Iterable[str]], header: Iterable[str]) -> None:
    """Write a TSV file with the given header and rows.

    Parameters
    ----------
    path : Path
        Output file path.
    rows : iterable of iterables
        Data rows to write.
    header : iterable of str
        Header row specifying column names.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))


def build_direct_es_ta(
    lemma_set: Set[str],
    wikt_path: Optional[Path],
    es_ta_out: Path,
    normalize: Optional[Callable[[str], str]] = None,
) -> None:
    """Build direct es→ta translation candidates from the Wiktionary dump.

    Parameters
    ----------
    lemma_set : set of str
        Normalised Spanish lemmas to restrict output.
    wikt_path : Optional[Path]
        Path to the Wiktionary dump or ``None`` if not provided.
    es_ta_out : Path
        Path to write the direct es→ta TSV.
    """
    if wikt_path is None or not wikt_path.exists():
        logging.warning("Skipping direct es→ta extraction: no Wiktionary dump")
        write_tsv(es_ta_out, [], ["es_norm", "ta", "source", "gloss"])
        return
    trans_map = parse_wiktionary_es_ta(wikt_path, lemma_set, normalize=normalize)
    rows: List[Tuple[str, str, str, str]] = []
    for es_norm in sorted(lemma_set):
        items = trans_map.get(es_norm)
        if not items:
            continue
        seen: Dict[str, Optional[str]] = {}
        for ta_text, gloss in items:
            t = ta_text.strip()
            if not t:
                continue
            # keep the first gloss encountered for a translation
            if t not in seen:
                seen[t] = gloss
        for ta, gloss in seen.items():
            rows.append((es_norm, ta, "wiktionary", gloss or ""))
    if rows:
        write_tsv(es_ta_out, rows, ["es_norm", "ta", "source", "gloss"])
    else:
        write_tsv(es_ta_out, [], ["es_norm", "ta", "source", "gloss"])


def build_pivot_es_ta(
    lemma_set: Set[str],
    es_en_rows: List[Tuple[str, str, str, str]],
    en_ta_rows: List[Tuple[str, str, str, str]],
    es_ta_pivoted_out: Path,
) -> None:
    """Build es→ta translation candidates via dictionary pivot.

    Parameters
    ----------
    lemma_set : set of str
        Normalised Spanish lemmas to restrict output.
    es_en_rows : list of tuple
        Rows from the es→en dictionary TSV (``es_norm``, ``en``, ``source``, ``gloss``).
    en_ta_rows : list of tuple
        Rows from the en→ta dictionary TSV (``en_norm``, ``ta``, ``source``, ``gloss``).
    es_ta_pivoted_out : Path
        Path to write the pivoted es→ta TSV.
    """
    # Build mapping: es_norm -> list of (en_text, en_gloss, en_source)
    es_en_map: Dict[str, List[Tuple[str, str, str]]] = {}
    for es_norm, en_word, en_source, en_gloss in es_en_rows:
        if es_norm not in lemma_set:
            continue
        for variant in expand_english_variants_for_pivot(en_word):
            es_en_map.setdefault(es_norm, []).append((variant, en_gloss or "", en_source))
    # Build mapping: en_norm -> list of (ta_text, ta_gloss, ta_source)
    en_ta_map: Dict[str, List[Tuple[str, str, str]]] = {}
    for en_norm, ta_word, ta_source, ta_gloss in en_ta_rows:
        en_norm_lower = normalize_generic(en_norm)
        en_ta_map.setdefault(en_norm_lower, []).append((ta_word, ta_gloss or "", ta_source))
    # Build pivoted candidates
    rows: List[Tuple[str, str, str, str]] = []
    for es_norm in sorted(lemma_set):
        en_list = es_en_map.get(es_norm)
        if not en_list:
            continue
        candidate_map: Dict[str, Tuple[Set[str], Optional[str]]] = {}
        for en_word, en_gloss, en_source in en_list:
            en_norm = normalize_generic(en_word)
            ta_list = en_ta_map.get(en_norm)
            if not ta_list:
                continue
            for ta_word, ta_gloss, ta_source in ta_list:
                ta = ta_word.strip()
                if not ta:
                    continue
                # determine provenance tag
                # combine sources from both edges but mark pivot explicitly
                sources = {src for src in (en_source, ta_source) if src}
                source_tag = "{};pivot".format(";".join(sorted(sources))) if sources else "pivot"
                # choose gloss: prefer Spanish→English gloss, then English→Tamil
                gloss = en_gloss or ta_gloss
                # accumulate
                if ta not in candidate_map:
                    candidate_map[ta] = (set([source_tag]), gloss)
                else:
                    existing_sources, existing_gloss = candidate_map[ta]
                    existing_sources.add(source_tag)
                    # prefer earlier gloss if exists
                    if not existing_gloss and gloss:
                        candidate_map[ta] = (existing_sources, gloss)
        for ta_word, (source_set, gloss) in candidate_map.items():
            rows.append((es_norm, ta_word, ";".join(sorted(source_set)), gloss or ""))
    if rows:
        write_tsv(es_ta_pivoted_out, rows, ["es_norm", "ta", "source", "gloss"])
    else:
        write_tsv(es_ta_pivoted_out, [], ["es_norm", "ta", "source", "gloss"])


def phase_m4(cfg: dict, config_path: Path) -> None:
    """Main entry point for Phase M4.

    This function orchestrates reading configuration, loading lemmas,
    extracting direct es→ta translations from Wiktionary, reading
    dictionary TSVs from Phase M3, building pivoted translations and
    writing TSV outputs.  It handles missing files gracefully by
    emitting empty outputs with headers.

    Parameters
    ----------
    cfg : dict
        Parsed configuration mapping from YAML.
    config_path : Path
        Path to the configuration file (used to resolve relative paths).
    """
    # Determine output paths
    outputs_cfg = cfg.get("outputs", {}) if isinstance(cfg.get("outputs"), dict) else {}

    def output_path(key: str, default: str) -> Path:
        if key in outputs_cfg:
            return resolve_path(config_path, outputs_cfg[key])
        return resolve_path(config_path, default)

    # Input files from previous phases
    es_en_path = output_path("es_en_tsv", "data_intermediate/05_es_en.tsv")
    en_ta_path = output_path("en_ta_tsv", "data_intermediate/05_en_ta.tsv")
    es_ta_path = output_path("es_ta_tsv", "data_intermediate/05_es_ta.tsv")
    es_ta_pivoted_path = output_path("es_ta_pivoted_tsv", "data_intermediate/05_es_ta_pivoted.tsv")
    # Lemma list path
    lemma_path_cfg = cfg.get("wiktionary", {}).get("lemma_tsv")
    lemma_path = resolve_path(
        config_path,
        lemma_path_cfg if lemma_path_cfg is not None else "data_intermediate/01_lemmas.tsv",
    )
    trailing_markers = get_trailing_markers(cfg)

    def normalize_spanish(text: str) -> str:
        return normalize_es(text, trailing_markers)

    lemmas = read_lemmas(lemma_path, normalize_spanish)
    lemma_set = set(lemmas.keys())
    if not lemmas:
        logging.warning("No lemmas loaded from %s", lemma_path)
    # Wiktionary dump path (optional)
    wikt_cfg = cfg.get("wiktionary", {}) if isinstance(cfg.get("wiktionary"), dict) else {}
    wikt_dump_val = wikt_cfg.get("dump_path")
    wikt_path = resolve_path(config_path, wikt_dump_val) if wikt_dump_val else None
    # Build direct translations
    build_direct_es_ta(lemma_set, wikt_path, es_ta_path, normalize_spanish)
    # Read dictionary TSVs
    es_en_rows = read_translation_tsv(es_en_path, "es_norm", "en", "source", "gloss")
    en_ta_rows = read_translation_tsv(en_ta_path, "en_norm", "ta", "source", "gloss")
    extra_tamil_rows = load_tamil_dictionary_rows(cfg, config_path)
    if extra_tamil_rows:
        logging.info(
            "Loaded %d en-ta rows from %s",
            len(extra_tamil_rows),
            extra_tamil_rows[0][2],
        )
        merge_translation_rows(en_ta_rows, extra_tamil_rows)
    # Build pivot translations
    build_pivot_es_ta(lemma_set, es_en_rows, en_ta_rows, es_ta_pivoted_path)
    logging.info(
        "Phase M4 complete: direct es→ta written to %s; pivoted es→ta written to %s",
        es_ta_path,
        es_ta_pivoted_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build direct and pivoted Spanish→Tamil translations")
    parser.add_argument("--config", default="config.yml", help="Path to YAML configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    cfg = load_config(Path(args.config))
    phase_m4(cfg, Path(args.config))


if __name__ == "__main__":
    main()
