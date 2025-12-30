from __future__ import annotations

import argparse
from pathlib import Path

from common import load_config, now_utc_iso, path_from_config, write_json, write_tsv_header


def run_qa(cfg: dict, config_path: Path) -> None:
    report_out = path_from_config(cfg, config_path, "outputs", "qa_report_html")
    missing_out = path_from_config(cfg, config_path, "outputs", "missing_fields_tsv")
    conflicts_out = path_from_config(cfg, config_path, "outputs", "sense_conflicts_tsv")
    build_info_out = path_from_config(cfg, config_path, "outputs", "build_info_json")

    write_tsv_header(missing_out, ["es_norm", "missing_fields"])
    write_tsv_header(conflicts_out, ["es_norm", "conflict_notes"])

    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(
        "<!doctype html>\n"
        "<html><head><meta charset='utf-8'><title>QA Report</title></head>\n"
        "<body><h1>QA Report</h1><p>Placeholder report.</p></body></html>\n",
        encoding="utf-8",
    )

    build_info = {
        "timestamp": now_utc_iso(),
        "note": "placeholder build info",
        "counts": {
            "lemmas": 0,
            "ipa": 0,
            "examples": 0,
            "fr": 0,
            "de": 0,
            "ta": 0,
        },
    }
    write_json(build_info_out, build_info)

    print(f"[stub] wrote {report_out}")
    print(f"[stub] wrote {missing_out}")
    print(f"[stub] wrote {conflicts_out}")
    print(f"[stub] wrote {build_info_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate QA reports")
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    run_qa(cfg, config_path)


if __name__ == "__main__":
    main()
