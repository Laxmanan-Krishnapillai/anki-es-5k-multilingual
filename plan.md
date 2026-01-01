# Technical implementation plan (with your decisions)

## 1) Build outputs

### Primary deliverable

* `deck_es_5k_multilingual.csv` (single import into Anki)

### QA + transparency

* `qa_report.html` (coverage + error buckets)
* `missing_fields.tsv` (lemma + what’s missing)
* `sense_conflicts.tsv` (where cues diverge)
* `build_info.json` (source versions, counts, timestamps)

---

## 2) Data sources (curated only)

### 2.1 Spanish lemma frequency (base list)

* SUBTLEX-ES lemmas (top 5,000) + POS + rank + Zipf/frequency

Outputs:

* `01_lemmas.tsv`

### 2.2 IPA (curated)

* **Wiktionary** IPA for Spanish entries (dump extraction)

Outputs:

* `02_ipa.tsv` (lemma → IPA)
* `02_wikt_meta.tsv` (pageid/revision/date for provenance)

### 2.3 Human-curated example sentences

Use one curated example corpus (or two, if licensing allows) **and keep the best match**.

Recommended options (choose based on what you can legally download/use):

* **Tatoeba** Spanish sentences (community-curated)
* **Wiktionary usage examples** (often short and clean)
* (Optional) **OpenSubtitles** is curated-ish but noisy; since you said “only human curated,” treat it as optional only if you personally deem it acceptable.

Outputs:

* `03_examples.tsv` (lemma → example sentence + source)

### 2.4 Dictionaries for FR/DE (no MT)

* **Primary:** downloadable bilingual dictionary datasets (e.g., TEI/XML such as FreeDict es–fr, es–de)
* **Secondary/fallback:** Wiktionary translations (human edited)

Outputs:

* `04_es_fr.tsv`
* `04_es_de.tsv`
* `04_wikt_fr_de.tsv` (fallback candidates)

### 2.5 Tamil via dictionary pivot (curated only)

Pipeline:

* Direct: **es→ta** from Wiktionary translations (when present)
* Pivot: **es→en** (dictionary/Wiktionary) + **en→ta** (Tamil lexicon/dictionary dataset)

Key requirement: every edge must be dictionary-derived.

Outputs:

* `05_es_ta.tsv` (direct)
* `05_es_en.tsv`
* `05_en_ta.tsv`
* `05_es_ta_pivoted.tsv`

---

## 3) Internal data model (record per lemma)

**LemmaRecord**

* `id` (1..5000)
* `es` (lemma)
* `pos`, `rank`, `zipf`
* `ipa`
* `example_es` (+ `example_source`)
* `fr_primary`, `de_primary`, `ta_primary`
* `fr_alt[]`, `de_alt[]`, `ta_alt[]`
* `sources` (compact provenance string)
* `flags[]` (missing_fr, missing_example, sense_conflict, low_confidence_ta, etc.)
* `notes` (formatted alt translations + comments)

Storage: **SQLite** recommended (debuggable, reproducible joins).

---

## 4) Normalization rules (critical for joins)

Apply the same normalization to all Spanish headwords from all sources:

* Unicode NFC
* lowercase
* keep diacritics (ñ, á, etc.)
* strip surrounding punctuation
* normalize whitespace
* remove trailing markers like “(m)”, “(f)”, “v.”, etc. from dictionary entries

Maintain both:

* `es_norm` (for joins)
* `es_display` (original lemma)

---

## 5) Candidate collection & scoring

### 5.1 Candidate pools per language

For each lemma, gather candidates:

* FR: from es–fr dictionary + Wiktionary translations
* DE: from es–de dictionary + Wiktionary translations
* TA: direct es→ta, else pivot via es→en + en→ta

Keep candidates with metadata:

* `text`, `source`, `sense/gloss if available`, `confidence_base`

### 5.2 Scoring (simple, deterministic)

Score each candidate using transparent rules:

* +2 if appears in **primary dictionary dataset**
* +1 if appears in **Wiktionary**
* +1 if candidate appears in multiple sources
* −1 if marked archaic/regional (when metadata exists)
* −1 if very long (e.g., > 25 chars) unless it’s a standard phrase
* For Tamil pivot:

  * direct es→ta starts higher
  * pivoted candidates start lower and get flag `ta_pivot`

Pick:

* **Primary** translation = highest score
* **Alt translations** = next best up to N (suggest N=3)

Put alts into `Notes` in a consistent format:

* `FR alt: … | …`
* `DE alt: … | …`
* `TA alt: … | …`

---

## 6) Example selection (human curated)

### 6.1 Matching

For each lemma:

* First try exact lemma match in sentence (good for nouns/adjectives)
* For verbs, allow inflected forms using a **curated morphology list** if available; otherwise:

  * prefer Wiktionary’s own usage examples for verbs
  * or accept a sentence containing a common inflection if you have a reliable lemma→forms list (must be sourced, not generated)

### 6.2 Filters

* length ≤ 15 words (configurable)
* avoid proper names when possible
* avoid duplicates across lemmas (keep variety)

If no good example:

* leave blank + flag `missing_example`

---

## 7) Polysemy / sense conflicts handling

### Default: one note per lemma

Use the example sentence to anchor sense.

Flag `sense_conflict` when:

* the top FR/DE/TA candidates point to clearly different senses (heuristic: different gloss buckets if provided, or disjoint synonym sets)

Resolution order:

1. prefer candidates whose gloss aligns with example (if gloss exists)
2. prefer candidates supported by multiple sources
3. otherwise keep best primary + put competing candidate into Notes

Only later (optional) split into multiple sense-notes.

---

## 8) CSV export for AnkiMobile

### 8.1 Final CSV columns

1. `ID`
2. `Spanish`
3. `IPA`
4. `Example`
5. `Tamil`
6. `German`
7. `French`
8. `POS`
9. `FrequencyRank`
10. `Zipf`
11. `Sources`
12. `Flags`
13. `Notes`

### 8.2 Card templates (HTML-only)

Front:

* `TA:` line
* `DE:` line
* `FR:` line

Back:

* Spanish big
* IPA
* Example
* Notes smaller

No JS. No remote assets.

---

## 9) QA gates (must pass before full build)

### Gate A — coverage report

Target minimums (realistic with curated-only constraint):

* IPA coverage: ≥ 85–95%
* FR/DE coverage: ≥ 90%+
* TA coverage: direct+ pivot ≥ 70–85% (depends on en→ta dataset quality)
* Example coverage: ≥ 70–90% (depends on corpus)

### Gate B — spot check sample

Random 200 lemmas:

* 1. translation sense matches example reasonably
* 2. Tamil script renders correctly
* 3. no obvious garbage tokens

### Gate C — AnkiMobile import test (100-card pilot)

Import first 100 rows:

* field mapping correct
* formatting correct
* review experience sane

---

## 10) Folder structure (concrete)

```
anki-es-5k-multilingual/
  config.yml
  data_raw/
    subtlex_es/
    wiktionary_dump/
    dictionaries/
    examples/
  data_intermediate/
    01_lemmas.tsv
    02_ipa.tsv
    03_examples.tsv
    04_es_fr.tsv
    04_es_de.tsv
    05_es_ta.tsv
    05_es_ta_pivoted.tsv
  build/
    deck_es_5k_multilingual.csv
    qa_report.html
    build_info.json
  src/
    ingest_subtlex.py
    parse_wiktionary.py
    parse_dictionaries.py
    select_examples.py
    merge_score_select.py
    export_csv.py
    qa.py
  deck.sqlite
```

---

## 11) Execution sequence (deterministic)

1. `ingest_subtlex` → `01_lemmas.tsv`
2. `parse_wiktionary` → IPA + translations tables
3. `parse_dictionaries` → es–fr + es–de + en–ta datasets
4. `select_examples` → `03_examples.tsv`
5. `merge_score_select` → final LemmaRecords (SQLite)
6. `qa` → reports + missing lists
7. `export_csv` → Anki import CSV

---

## 12) Implementation milestones

* **M1:** Lemmas ingested + normalized + 5k list frozen
* **M2:** Wiktionary IPA extraction working
* **M3:** es–fr + es–de dictionaries parsed & merged
* **M4:** Tamil direct + pivot pipeline working with provenance tags
* **M5:** Curated examples integra  ted + selection heuristics stable
* **M6:** QA gates pass + AnkiMobile pilot import
* **M7:** Full 5k export + documentation

---