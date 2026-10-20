"""The project's discussion skill, frozen at mission creation."""
import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_SKILLS = ('discuss',)


def load_discuss_skill():
    instructions = (PROJECT_ROOT / 'skills' / 'discuss' / 'SKILL.md').read_text(encoding='utf-8')
    if not instructions.strip() or len(instructions.encode('utf-8')) > 64_000:
        raise ValueError('Discussion skill must contain 1–64000 bytes')
    return instructions, hashlib.sha256(instructions.encode('utf-8')).hexdigest()
