"""Common persistence and policy helpers."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

RULE = Path(__file__).resolve().parents[1] / "references" / "policies" / "投研系统阶段1.1_市场范围与排除规则_v1.6_20261109.md"


class EvidenceGap(ValueError):
    pass


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_bytes(payload):
    return (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def write(path, payload):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(text):
    return re.sub(r"\s+", "", text)
