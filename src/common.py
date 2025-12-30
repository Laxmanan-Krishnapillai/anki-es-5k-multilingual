from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


def load_config(config_path: Path) -> dict:
    if yaml is None:
        raise SystemExit("PyYAML is required. Install with: pip install pyyaml")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit("config.yml must parse to a mapping")
    return data


def resolve_path(config_path: Path, path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def path_from_config(cfg: dict, config_path: Path, *keys: str) -> Path:
    cur: Any = cfg
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            raise SystemExit(f"Missing config key: {'/'.join(keys)}")
        cur = cur[key]
    if not isinstance(cur, str):
        raise SystemExit(f"Config path is not a string: {'/'.join(keys)}")
    return resolve_path(config_path, cur)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_tsv_header(path: Path, headers: list[str]) -> None:
    ensure_parent_dir(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(headers)


def write_csv_header(path: Path, headers: list[str]) -> None:
    ensure_parent_dir(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)


def write_json(path: Path, data: dict) -> None:
    ensure_parent_dir(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_PUNCT_TRIM_RE = re.compile(r"^[\s\W_]+|[\s\W_]+$")


def normalize_es(text: str, trailing_markers: list[str] | None = None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.strip().lower()
    text = _PUNCT_TRIM_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    if trailing_markers:
        for marker in trailing_markers:
            marker = marker.strip().lower()
            if not marker:
                continue
            if text.endswith(marker):
                text = text[: -len(marker)].strip()
    return text
