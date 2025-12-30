# anki-es-5k-multilingual

Scaffold for building a curated Spanish 5k multilingual Anki deck (FR/DE/TA).

## Layout
- config.yml: pipeline configuration
- data_raw/: input datasets (not tracked)
- data_intermediate/: normalized TSV outputs
- build/: final CSV and QA reports
- src/: pipeline scripts
- deck.sqlite: generated SQLite database

## Pipeline
1. ingest_subtlex -> 01_lemmas.tsv
2. parse_wiktionary -> 02_ipa.tsv, 02_wikt_meta.tsv, 04_wikt_fr_de.tsv, 05_es_ta.tsv
3. parse_dictionaries -> 04_es_fr.tsv, 04_es_de.tsv, 05_es_en.tsv, 05_en_ta.tsv
4. select_examples -> 03_examples.tsv
5. merge_score_select -> deck.sqlite, 05_es_ta_pivoted.tsv
6. qa -> qa_report.html, missing_fields.tsv, sense_conflicts.tsv, build_info.json
7. export_csv -> deck_es_5k_multilingual.csv

## Usage (scaffold)
python src/ingest_subtlex.py --config config.yml
python src/parse_wiktionary.py --config config.yml
python src/parse_dictionaries.py --config config.yml
python src/select_examples.py --config config.yml
python src/merge_score_select.py --config config.yml
python src/qa.py --config config.yml
python src/export_csv.py --config config.yml

## Notes
- Scripts are stubs and write headers/placeholders to make the pipeline shape concrete.
- TSV outputs include a header row.
