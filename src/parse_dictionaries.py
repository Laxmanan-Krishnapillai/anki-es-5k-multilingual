"""
parse_dictionaries
===================

This module implements phase **M3** of the Spanish 5 k multilingual deck
build as described in the accompanying technical plan.  Its job is to
extract bilingual dictionary data for Spanish→French and Spanish→German
pairs (and optionally Spanish→English and English→Tamil to support
later phases) from curated data sources.  Primary translations are
expected to come from downloadable FreeDict TEI/XML dictionaries;
secondary/fallback translations may be derived from a preprocessed
Wiktionary dump produced by ``wiktextract``.  The resulting TSV
files contain one row per translation candidate with simple
provenance and gloss fields suitable for subsequent scoring.

The script reads the frozen lemma list produced in phase M1 to
restrict output to the top 5 000 Spanish lemmas and normalises
headwords consistently.  When dictionary files or dumps are missing
the script emits empty TSVs with only the header row so downstream
steps can proceed deterministically.

Example usage
-------------

Run from the repository root:

```
python3 -m parse_dictionaries --config config.yml
```

The YAML configuration may contain a ``dictionaries`` section with
paths to your downloaded TEI files and a ``wiktionary.dump_path``
entry pointing at a wiktextract JSON/JSONL dump.  Sensible defaults
are used when configuration keys are missing.  See ``plan.md`` for
details.

Outputs
-------

The following TSV files are written under ``data_intermediate`` (or
paths specified in the configuration):

* ``04_es_fr.tsv`` – ``es_norm`` → French translation candidates.
* ``04_es_de.tsv`` – ``es_norm`` → German translation candidates.
* ``05_es_en.tsv`` – ``es_norm`` → English translation candidates (optional).
* ``05_en_ta.tsv`` – ``en_norm`` → Tamil translation candidates (optional).

Each row has the columns ``(source_word, target_word, source, gloss)``.
The ``source`` column records where the translation came from
(e.g. ``freedict``, ``wiktionary`` or a semicolon‑delimited list when
a translation appears in multiple sources).  The ``gloss`` column
currently contains the sense or gloss field from the source when
available; otherwise it is left blank.

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
import xml.etree.ElementTree as ET
import itertools
import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from common import (
    load_config,
    normalize_es,
    resolve_path,
)


def read_lemmas(
    lemma_path: Path, normalize: Optional[Callable[[str], str]] = None
) -> Dict[str, str]:
    """Read the M1 lemma list and return a mapping from ``es_norm`` to
    ``es_display``.

    The TSV is expected to have at least an ``es_norm`` column.  If an
    ``es_display`` column is present its values are stored as the
    display form; otherwise the normalised form is used for display as
    well.

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
                es_norm = normalize(raw) if normalize else normalize_es(raw)
            display = row.get("es_display", es_norm)
            lemmas[es_norm] = display
    return lemmas


def open_text(path: Path) -> Iterable[str]:
    """Open a text file with optional compression based on extension.

    Supports plain text (.txt, .tei, .xml), gzip (.gz), bzip2 (.bz2) and
    xz (.xz).

    Parameters
    ----------
    path : Path
        Path to the file.

    Yields
    ------
    str
        Lines of text from the file.
    """
    import gzip
    import bz2
    import lzma

    suffix = path.suffix.lower()
    if suffix == ".gz":
        opener = gzip.open  # type: ignore
    elif suffix == ".bz2":
        opener = bz2.open  # type: ignore
    elif suffix == ".xz":
        opener = lzma.open  # type: ignore
    else:
        opener = open  # type: ignore
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line


def read_tei_xml(dict_path: Path) -> Optional[str]:
    """Load TEI/XML content from a dictionary file or archive."""
    if not dict_path.exists():
        return None
    try:
        import tarfile

        if tarfile.is_tarfile(dict_path):
            with tarfile.open(dict_path, "r:*") as tf:
                members = [
                    member
                    for member in tf.getmembers()
                    if member.isfile()
                    and member.name.lower().endswith((".tei", ".xml"))
                ]
                if not members:
                    logging.warning("No TEI/XML file found in %s", dict_path)
                    return None
                member = next(
                    (candidate for candidate in members if candidate.name.lower().endswith(".tei")),
                    members[0],
                )
                extracted = tf.extractfile(member)
                if extracted is None:
                    logging.warning("Failed to extract %s from %s", member.name, dict_path)
                    return None
                return extracted.read().decode("utf-8", errors="replace")
    except Exception as e:
        logging.warning("Failed to read dictionary archive %s: %s", dict_path, e)
        return None

    xml_text = "".join(open_text(dict_path))
    return xml_text


OPEN_CORPUS_SPLIT_RE = re.compile(r"\s*[;,/]\s*")


def split_open_corpus_translations(text: str) -> List[str]:
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    parts = OPEN_CORPUS_SPLIT_RE.split(text)
    cleaned: List[str] = []
    for part in parts:
        part = part.strip().strip(".")
        if part:
            cleaned.append(part)
    return cleaned


def expand_english_variants(text: str) -> List[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    variants = {cleaned}
    lowered = cleaned.lower()
    if lowered.startswith("to "):
        stripped = cleaned[3:].strip()
        if stripped:
            variants.add(stripped)
    return sorted(variants)


def clean_open_corpus_gloss(text: Optional[str]) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def parse_open_corpus_es_en(
    dict_path: Path,
    *,
    normalize: Optional[Callable[[str], str]] = None,
    lemma_set: Optional[Set[str]] = None,
) -> Dict[str, List[Tuple[str, Optional[str]]]]:
    """Parse Open Corpus es-en XML and return a mapping of source lemmas
    to lists of ``(translation, gloss)`` tuples.

    The Open Corpus format uses ``<c>`` for the Spanish headword and
    ``<d>`` for the English translation string. Multiple translations are
    split on commas/semicolons/slashes. Verb translations prefixed with
    ``to`` are expanded to include a variant without ``to`` to improve
    matching against English headword dictionaries.
    """
    if not dict_path.exists():
        return {}
    results: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    normalizer = normalize or normalize_es
    try:
        for _event, elem in ET.iterparse(dict_path, events=("end",)):
            if elem.tag != "w":
                continue
            src = elem.findtext("c")
            dst = elem.findtext("d")
            tag = elem.findtext("t")
            if not src or not dst:
                elem.clear()
                continue
            es_norm = normalizer(src)
            if lemma_set is not None and es_norm not in lemma_set:
                elem.clear()
                continue
            gloss = clean_open_corpus_gloss(tag)
            translations: List[str] = []
            for part in split_open_corpus_translations(dst):
                translations.extend(expand_english_variants(part))
            if translations:
                bucket = results.setdefault(es_norm, [])
                for trans in translations:
                    bucket.append((trans, gloss or None))
            elem.clear()
    except Exception as e:
        logging.warning("Failed to parse Open Corpus dictionary %s: %s", dict_path, e)
        return {}
    return results


def parse_apertium_fra_spa_tsv(
    dict_path: Path,
    *,
    normalize: Optional[Callable[[str], str]] = None,
    lemma_set: Optional[Set[str]] = None,
) -> Dict[str, List[Tuple[str, Optional[str]]]]:
    """Parse Apertium fra-spa TSV and return a mapping of Spanish lemmas
    to lists of ``(French, gloss)`` tuples.

    The TSV is expected to have at least ``French`` and ``Spanish`` columns.
    This file is French->Spanish, so we invert it to Spanish->French.
    """
    results: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    if not dict_path.exists():
        return results
    normalizer = normalize or normalize_es
    try:
        with dict_path.open("r", encoding="utf-8", errors="replace") as f:
            header = ""
            for line in f:
                if line.strip():
                    header = line.lstrip("\ufeff")
                    break
            if not header:
                return results
            reader = csv.DictReader(
                itertools.chain([header], f),
                delimiter="\t",
            )
            if not reader.fieldnames:
                return results
            field_map = {name.strip().lower(): name for name in reader.fieldnames if name}
            fr_col = field_map.get("french") or field_map.get("fr")
            es_col = field_map.get("spanish") or field_map.get("es")
            if fr_col is None or es_col is None:
                logging.warning("Apertium TSV missing French/Spanish columns: %s", dict_path)
                return results
            for row in reader:
                fr_word = (row.get(fr_col) or "").strip()
                es_word = (row.get(es_col) or "").strip()
                if not fr_word or not es_word:
                    continue
                es_norm = normalizer(es_word)
                if lemma_set is not None and es_norm not in lemma_set:
                    continue
                results.setdefault(es_norm, []).append((fr_word, None))
    except Exception as e:
        logging.warning("Failed to parse Apertium TSV %s: %s", dict_path, e)
        return {}
    return results


def parse_ding_es_de_tsv(
    dict_path: Path,
    *,
    normalize: Optional[Callable[[str], str]] = None,
    lemma_set: Optional[Set[str]] = None,
) -> Dict[str, List[Tuple[str, Optional[str]]]]:
    """Parse Ding es-de TSV and return a mapping of Spanish lemmas
    to lists of ``(German, gloss)`` tuples."""
    results: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    if not dict_path.exists():
        return results
    normalizer = normalize or normalize_es
    es_split_re = re.compile(r"\s*[;/]\s*")
    es_comma_re = re.compile(r"\s*,\s*")
    es_paren_re = re.compile(r"\s*\([^)]*\)")
    try:
        with dict_path.open("r", encoding="utf-8", errors="replace") as f:
            header = ""
            for line in f:
                if line.strip():
                    header = line.lstrip("\ufeff")
                    break
            if not header:
                return results
            reader = csv.DictReader(
                itertools.chain([header], f),
                delimiter="\t",
            )
            if not reader.fieldnames:
                return results
            field_map = {name.strip().lower(): name for name in reader.fieldnames if name}
            es_col = field_map.get("spanish") or field_map.get("es")
            de_col = field_map.get("german") or field_map.get("de")
            if es_col is None or de_col is None:
                logging.warning("Ding TSV missing Spanish/German columns: %s", dict_path)
                return results
            for row in reader:
                es_word_raw = (row.get(es_col) or "").strip()
                de_word = (row.get(de_col) or "").strip()
                if not es_word_raw or not de_word:
                    continue
                es_word_raw = es_paren_re.sub("", es_word_raw).strip()
                variants = es_split_re.split(es_word_raw) if es_word_raw else []
                for es_group in variants:
                    es_group = es_group.strip()
                    if not es_group:
                        continue
                    comma_alts = [alt for alt in es_comma_re.split(es_group) if alt]
                    if not comma_alts:
                        continue
                    selected_alt = None
                    selected_norm = ""
                    if lemma_set is not None:
                        for alt in comma_alts:
                            alt = alt.strip()
                            if not alt:
                                continue
                            es_norm = normalizer(alt)
                            if es_norm in lemma_set:
                                selected_alt = alt
                                selected_norm = es_norm
                                break
                        if selected_alt is None:
                            continue
                    else:
                        selected_alt = comma_alts[0].strip()
                        if not selected_alt:
                            continue
                        selected_norm = normalizer(selected_alt)
                    results.setdefault(selected_norm, []).append((de_word, None))
    except Exception as e:
        logging.warning("Failed to parse Ding TSV %s: %s", dict_path, e)
        return {}
    return results


def parse_freedict_tei(
    dict_path: Path,
    *,
    source_lang: str,
    target_lang: str,
    normalize: Optional[Callable[[str], str]] = None,
    lemma_set: Optional[Set[str]] = None,
) -> Dict[str, List[Tuple[str, Optional[str]]]]:
    """Parse a FreeDict TEI file and return a mapping of source lemmas
    to lists of ``(translation, gloss)`` tuples.

    The parser is intentionally permissive: it ignores XML namespaces
    and selects any ``cit`` element whose ``type`` attribute contains
    the substring ``trans``.  Each translation's text is taken from
    the contained ``quote`` element.  If a ``cit`` element contains a
    ``gloss`` or ``def`` element, its string content is used as the
    gloss.

    Parameters
    ----------
    dict_path : Path
        Path to the TEI or XML dictionary file.  Compression suffixes
        (.gz, .bz2) are handled automatically.
    source_lang : str
        ISO 2‑letter code of the source language (e.g. ``"es"``).
        Currently unused but reserved for future validation.
    target_lang : str
        ISO 2‑letter code of the target language (e.g. ``"fr"``).
        Currently unused but reserved for future validation.

    Returns
    -------
    dict
        Mapping from source word to a list of translations and glosses.
    """
    results: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    normalizer = normalize or (lambda text: text)
    try:
        # Parse the XML.  Some FreeDict dictionaries wrap the TEI in
        # <TEI> with namespace declarations; ElementTree handles
        # namespaces by exposing the full QName in the tag.  To avoid
        # hard‑coding namespace URIs, we ignore the namespace prefix
        # altogether when searching for elements by stripping it.
        # Load the XML from a string rather than using ET.parse on a
        # file handle: ET.parse does not accept compressed streams.
        xml_text = read_tei_xml(dict_path)
        if not xml_text or not xml_text.strip():
            return results
        root = ET.fromstring(xml_text)
    except Exception as e:
        logging.warning("Failed to parse %s as TEI/XML: %s", dict_path, e)
        return results

    def strip_ns(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    for entry in root.iter():
        if strip_ns(entry.tag) != "entry":
            continue
        # Extract the lemma (first <orth> in a <form>)
        lemma: Optional[str] = None
        for form in entry:
            if strip_ns(form.tag) == "form":
                for child in form:
                    if strip_ns(child.tag) == "orth":
                        text = (child.text or "").strip()
                        if text:
                            lemma = text
                            break
                if lemma:
                    break
        if not lemma:
            continue
        lemma_norm = normalizer(lemma)
        if not lemma_norm:
            continue
        if lemma_set is not None and lemma_norm not in lemma_set:
            continue
        # Extract all translations for this lemma
        translations: List[Tuple[str, Optional[str]]] = []
        for cit in entry.iter():
            if strip_ns(cit.tag) != "cit":
                continue
            cit_type = (cit.get("type") or "").lower()
            if "trans" not in cit_type:
                continue
            # translation text
            trans_text: Optional[str] = None
            gloss_text: Optional[str] = None
            for child in cit:
                tag = strip_ns(child.tag)
                if tag == "quote":
                    txt = (child.text or "").strip()
                    if txt:
                        trans_text = txt
                elif tag in {"gloss", "def"}:
                    gloss_text = (child.text or "").strip()
            if trans_text:
                translations.append((trans_text, gloss_text))
        if translations:
            results.setdefault(lemma_norm, []).extend(translations)
    return results


def open_dump(path: Path) -> Iterable[str]:
    """Open a Wiktextract dump in JSON/JSONL with optional compression.

    Parameters
    ----------
    path : Path
        Path to the dump file (.json, .jsonl, .gz, .bz2).

    Yields
    ------
    str
        Each line containing a JSON object.
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


def parse_wiktionary_translations(
    dump_path: Path,
    lemma_set: Set[str],
    languages: Set[str],
    normalize: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]]:
    """Extract translation candidates from a wiktextract dump.

    This function scans a JSON or JSONL dump produced by
    ``wiktextract`` and collects translation words for the requested
    target languages.  It restricts processing to entries where the
    ``lang_code`` corresponds to Spanish and the normalised lemma
    appears in the provided lemma set.  For each translation, the
    function records the target word and any available sense/gloss
    information.

    Parameters
    ----------
    dump_path : Path
        Path to the wiktextract dump (.json, .jsonl, .gz, .bz2).
    lemma_set : set of str
        Normalised Spanish lemmas to include.
    languages : set of str
        Two‑letter language codes for the desired target languages
        (e.g. {"fr", "de"}).

    Returns
    -------
    dict
        Mapping from ``es_norm`` to a mapping of target language code
        to a list of ``(translation, gloss)`` tuples.
    """
    translations: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]] = {}
    normalizer = normalize or normalize_es
    try:
        for line in open_dump(dump_path):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Accept both language codes and names; normalise to lower‑case
            lang_code = (entry.get("lang_code") or entry.get("lang") or "").lower()
            if lang_code not in {"es", "spa", "spanish", "espanol"}:
                continue
            word = entry.get("word") or entry.get("title") or entry.get("page_title")
            if not word:
                continue
            es_norm = normalizer(str(word))
            if es_norm not in lemma_set:
                continue
            # Gather translations from top‑level 'translations' list
            def add_translation(lang: str, text: str, gloss: Optional[str] = None) -> None:
                if not text:
                    return
                translations.setdefault(es_norm, {}).setdefault(lang, []).append((text, gloss))

            top_trans = entry.get("translations")
            if isinstance(top_trans, list):
                for trans in top_trans:
                    lang = trans.get("lang_code") or trans.get("lang") or ""
                    lang = lang.lower()
                    if lang not in languages:
                        continue
                    text = trans.get("word") or trans.get("text") or trans.get("title")
                    if not text:
                        continue
                    gloss = trans.get("sense") or trans.get("gloss")
                    add_translation(lang, str(text).strip(), gloss)
            # Also iterate through senses
            senses = entry.get("senses")
            if isinstance(senses, list):
                for sense in senses:
                    sense_gloss = sense.get("gloss") or sense.get("raw_gloss")
                    sense_trans = sense.get("translations")
                    if not isinstance(sense_trans, list):
                        continue
                    for trans in sense_trans:
                        lang = trans.get("lang_code") or trans.get("lang") or ""
                        lang = lang.lower()
                        if lang not in languages:
                            continue
                        text = trans.get("word") or trans.get("text") or trans.get("title")
                        if not text:
                            continue
                        gloss = trans.get("sense") or trans.get("gloss") or sense_gloss
                        add_translation(lang, str(text).strip(), gloss)
    except FileNotFoundError:
        logging.warning("Wiktionary dump not found at %s", dump_path)
    return translations


FREEDICT_LANG_CODES = {
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "en": "eng",
    "ta": "tam",
}


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


def find_freedict_path(config_path: Path, source_lang: str, target_lang: str) -> Path:
    dict_dir = resolve_path(config_path, "data_raw/dictionaries")
    src_code = FREEDICT_LANG_CODES.get(source_lang, source_lang)
    tgt_code = FREEDICT_LANG_CODES.get(target_lang, target_lang)
    patterns = [
        f"freedict-{src_code}-{tgt_code}-*.src.tar.*",
        f"{src_code}-{tgt_code}.tei",
        f"{src_code}-{tgt_code}.xml",
        f"{source_lang}-{target_lang}.tei",
        f"{source_lang}-{target_lang}.xml",
    ]
    for pattern in patterns:
        matches = sorted(dict_dir.glob(pattern))
        if matches:
            return matches[0]
    return dict_dir / f"{source_lang}-{target_lang}.tei"


def find_open_corpus_paths(config_path: Path) -> List[Path]:
    dict_dir = resolve_path(config_path, "data_raw/dictionaries/open-corpus-es-en")
    candidates = [
        dict_dir / "es-en.xml",
        dict_dir / "verbs" / "es-en.xml",
    ]
    return [path for path in candidates if path.exists()]


def find_apertium_fra_spa_path(config_path: Path, dict_cfg: Dict[str, object]) -> Path:
    for key in ("apertium_fra_spa", "apertium_fr_spa", "apertium_fr"):
        path_val = dict_cfg.get(key)
        if path_val is not None:
            return resolve_path(config_path, str(path_val))
    return resolve_path(config_path, "data_raw/dictionaries/apertium_fra_spa.tsv")


def find_ding_es_de_path(config_path: Path, dict_cfg: Dict[str, object]) -> Path:
    for key in ("ding_es_de", "ding_de_es", "ding"):
        path_val = dict_cfg.get(key)
        if path_val is not None:
            return resolve_path(config_path, str(path_val))
    return resolve_path(config_path, "data_raw/dictionaries/ding_es_de_spa2ger.tsv")


def normalise_generic(text: str) -> str:
    """Simplistic normalisation for non‑Spanish words.

    Converts text to NFC, lowercases and trims surrounding whitespace
    and punctuation.  This is a fallback for languages where
    ``normalize_es`` is not appropriate (e.g. English headwords in
    en→ta).
    """
    import re
    import unicodedata

    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.strip().lower()
    # remove surrounding punctuation and underscores
    text = re.sub(r"^[\s\W_]+|[\s\W_]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


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


def parse_dictionaries(cfg: dict, config_path: Path) -> None:
    """Main entry point for dictionary parsing (phase M3).

    This function orchestrates reading configuration, loading lemmas,
    parsing FreeDict TEI dictionaries, extracting Wiktionary
    translations, merging candidates and writing TSV outputs.  It
    handles missing files gracefully by emitting empty outputs with
    headers.

    Parameters
    ----------
    cfg : dict
        Parsed configuration mapping from YAML.
    config_path : Path
        Path to the configuration file (used to resolve relative paths).
    """
    # Determine output paths
    outputs_cfg = cfg.get("outputs", {}) if isinstance(cfg.get("outputs"), dict) else {}
    # Provide defaults if not specified
    def output_path(key: str, default: str) -> Path:
        if key in outputs_cfg:
            return resolve_path(config_path, outputs_cfg[key])
        return resolve_path(config_path, default)

    es_fr_out = output_path("es_fr_tsv", "data_intermediate/04_es_fr.tsv")
    es_de_out = output_path("es_de_tsv", "data_intermediate/04_es_de.tsv")
    es_en_out = output_path("es_en_tsv", "data_intermediate/05_es_en.tsv")
    en_ta_out = output_path("en_ta_tsv", "data_intermediate/05_en_ta.tsv")

    # Determine lemma list path
    lemma_path_cfg = cfg.get("wiktionary", {}).get("lemma_tsv")
    lemma_path = resolve_path(
        config_path,
        lemma_path_cfg if lemma_path_cfg is not None else "data_intermediate/01_lemmas.tsv",
    )
    if not lemma_path.exists():
        raise FileNotFoundError(f"Lemma file not found: {lemma_path}")
    trailing_markers = get_trailing_markers(cfg)
    normalize_spanish = lambda text: normalize_es(text, trailing_markers)
    lemmas = read_lemmas(lemma_path, normalize_spanish)
    lemma_set = set(lemmas.keys())
    if not lemmas:
        logging.warning("No lemmas loaded from %s", lemma_path)

    # Load dictionary paths from config
    dict_cfg = cfg.get("dictionaries", {}) if isinstance(cfg.get("dictionaries"), dict) else {}

    def dict_path(key: str, source_lang: str, target_lang: str) -> Optional[Path]:
        path_val = dict_cfg.get(key)
        if path_val is not None:
            return resolve_path(config_path, str(path_val))
        return find_freedict_path(config_path, source_lang, target_lang)

    es_fr_dict = dict_path("es_fr", "es", "fr")
    es_de_dict = dict_path("es_de", "es", "de")
    es_en_dict = dict_path("es_en", "es", "en")
    en_ta_dict = dict_path("en_ta", "en", "ta")

    # Parse FreeDict dictionaries
    def build_translation_map(
        dict_file: Optional[Path],
        src_lang: str,
        tgt_lang: str,
        *,
        normalize: Optional[Callable[[str], str]] = None,
        lemma_set: Optional[Set[str]] = None,
    ) -> Dict[str, List[Tuple[str, Optional[str]]]]:
        if dict_file is None or not dict_file.exists():
            logging.warning("Dictionary file missing for %s-%s: %s", src_lang, tgt_lang, dict_file)
            return {}
        return parse_freedict_tei(
            dict_file,
            source_lang=src_lang,
            target_lang=tgt_lang,
            normalize=normalize,
            lemma_set=lemma_set,
        )

    fr_trans = build_translation_map(
        es_fr_dict, "es", "fr", normalize=normalize_spanish, lemma_set=lemma_set
    )
    de_trans = build_translation_map(
        es_de_dict, "es", "de", normalize=normalize_spanish, lemma_set=lemma_set
    )
    en_trans = build_translation_map(
        es_en_dict, "es", "en", normalize=normalize_spanish, lemma_set=lemma_set
    )
    ta_trans = build_translation_map(en_ta_dict, "en", "ta", normalize=normalise_generic)

    apertium_path = find_apertium_fra_spa_path(config_path, dict_cfg)
    apertium_trans = parse_apertium_fra_spa_tsv(
        apertium_path, normalize=normalize_spanish, lemma_set=lemma_set
    )
    if apertium_trans:
        logging.info("Loaded Apertium FR-ES entries from %s", apertium_path)
    else:
        if apertium_path.exists():
            logging.warning("Apertium FR-ES TSV parsed but produced no rows: %s", apertium_path)
        else:
            logging.warning("Apertium FR-ES TSV not found at %s", apertium_path)

    ding_path = find_ding_es_de_path(config_path, dict_cfg)
    ding_trans = parse_ding_es_de_tsv(
        ding_path, normalize=normalize_spanish, lemma_set=lemma_set
    )
    if ding_trans:
        logging.info("Loaded Ding ES-DE entries from %s", ding_path)
    else:
        if ding_path.exists():
            logging.warning("Ding ES-DE TSV parsed but produced no rows: %s", ding_path)
        else:
            logging.warning("Ding ES-DE TSV not found at %s", ding_path)

    # Extract Wiktionary translations for FR/DE/EN if wiktionary dump provided
    wikt_cfg = cfg.get("wiktionary", {}) if isinstance(cfg.get("wiktionary"), dict) else {}
    wikt_dump_path_val = wikt_cfg.get("dump_path")
    wikt_dump_path = (
        resolve_path(config_path, wikt_dump_path_val)
        if wikt_dump_path_val is not None
        else None
    )
    wikt_translations: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]] = {}
    if wikt_dump_path and wikt_dump_path.exists():
        wikt_translations = parse_wiktionary_translations(
            wikt_dump_path,
            lemma_set,
            languages={"fr", "de", "en"},
            normalize=normalize_spanish,
        )
    else:
        if wikt_dump_path:
            logging.warning("Wiktionary dump not found at %s", wikt_dump_path)

    # Merge FreeDict and Wiktionary translations; record sources
    def merge_translations(
        primary_sources: List[Tuple[str, Dict[str, List[Tuple[str, Optional[str]]]]]],
        secondary: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]],
        target_lang: str,
    ) -> List[Tuple[str, str, str, str]]:
        rows: List[Tuple[str, str, str, str]] = []
        for es_norm in sorted(lemma_set):
            # gather candidate translations
            mapping: Dict[Tuple[str, str], Set[str]] = {}  # (translation, gloss) -> sources
            # primary
            for source_name, primary in primary_sources:
                for trans, gloss in primary.get(es_norm, []):
                    t = trans.strip()
                    if not t:
                        continue
                    g = (gloss or "").strip()
                    mapping.setdefault((t, g), set()).add(source_name)
            # secondary (wiktionary)
            sec_lang_map = secondary.get(es_norm, {}).get(target_lang)
            if sec_lang_map:
                for trans, gloss in sec_lang_map:
                    t = trans.strip()
                    if not t:
                        continue
                    g = (gloss or "").strip()
                    mapping.setdefault((t, g), set()).add("wiktionary")
            # produce rows
            for (trans_text, gloss_text), sources in mapping.items():
                source_str = ";".join(sorted(sources))
                rows.append((es_norm, trans_text, source_str, gloss_text))
        return rows

    es_fr_rows = merge_translations(
        [("freedict", fr_trans), ("apertium", apertium_trans)],
        wikt_translations,
        "fr",
    )
    es_de_rows = merge_translations(
        [("freedict", de_trans), ("ding", ding_trans)],
        wikt_translations,
        "de",
    )

    # For es→en and en→ta dictionaries we do not have Wiktionary fallback
    # Open Corpus ES->EN dictionary
    open_corpus_paths = find_open_corpus_paths(config_path)
    open_corpus_trans: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    if open_corpus_paths:
        for path in open_corpus_paths:
            parsed = parse_open_corpus_es_en(
                path, normalize=normalize_spanish, lemma_set=lemma_set
            )
            if not parsed:
                continue
            for es_norm, entries in parsed.items():
                open_corpus_trans.setdefault(es_norm, []).extend(entries)
        logging.info(
            "Loaded Open Corpus ES->EN entries from %d file(s)",
            len(open_corpus_paths),
        )
    else:
        logging.warning(
            "Open Corpus ES->EN dictionary not found under data_raw/dictionaries/open-corpus-es-en"
        )

    es_en_rows = merge_translations(
        [("freedict", en_trans), ("open_corpus", open_corpus_trans)],
        wikt_translations,
        "en",
    )

    en_ta_rows: List[Tuple[str, str, str, str]] = []
    # For en→ta we cannot restrict by Spanish lemmas; include all entries
    for en_norm, trans_list in ta_trans.items():
        if not en_norm:
            continue
        for trans, gloss in trans_list:
            t = trans.strip()
            if not t:
                continue
            en_ta_rows.append((en_norm, t, "freedict", gloss or ""))

    # Write outputs
    if es_fr_rows:
        write_tsv(es_fr_out, es_fr_rows, ["es_norm", "fr", "source", "gloss"])
    else:
        write_tsv(es_fr_out, [], ["es_norm", "fr", "source", "gloss"])

    if es_de_rows:
        write_tsv(es_de_out, es_de_rows, ["es_norm", "de", "source", "gloss"])
    else:
        write_tsv(es_de_out, [], ["es_norm", "de", "source", "gloss"])

    # Only write es→en if primary translations exist
    if es_en_rows:
        write_tsv(es_en_out, es_en_rows, ["es_norm", "en", "source", "gloss"])
    else:
        write_tsv(es_en_out, [], ["es_norm", "en", "source", "gloss"])

    # Always write en→ta (may be empty if dict missing)
    if en_ta_rows:
        write_tsv(en_ta_out, en_ta_rows, ["en_norm", "ta", "source", "gloss"])
    else:
        write_tsv(en_ta_out, [], ["en_norm", "ta", "source", "gloss"])

    logging.info(
        "Dictionary parsing complete: %d fr, %d de, %d en, %d ta rows",
        len(es_fr_rows), len(es_de_rows), len(es_en_rows), len(en_ta_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse bilingual dictionaries and Wiktionary translations")
    parser.add_argument("--config", default="config.yml", help="Path to YAML configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    cfg = load_config(Path(args.config))
    parse_dictionaries(cfg, Path(args.config))


if __name__ == "__main__":
    main()
