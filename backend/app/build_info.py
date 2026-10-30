"""Content identity of the backend source, reported by health and V2 audits."""
import hashlib
import json
from pathlib import Path


def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


BUILD_ID = digest({p.relative_to(Path(__file__).parent).as_posix(): p.read_text()
                   for p in sorted(Path(__file__).parent.rglob('*.py'))})[:16]
