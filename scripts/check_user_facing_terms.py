#!/usr/bin/env python3
"""Fail if hard AI/tech terms leak into user-facing Re:putation copy.

This is intentionally conservative: it scans UI/admin strings and backend
operator-facing messages, while allowing internal identifiers such as `brief_id`,
`query_target_id`, or lower-case API route names.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_PATHS = [
    ROOT / "admin" / "app",
    ROOT / "site" / "app",
    ROOT / "backend" / "app" / "api",
    ROOT / "backend" / "app" / "services",
    ROOT / "backend" / "app" / "workers",
]
EXTENSIONS = {".tsx", ".ts", ".py"}
BANNED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AEO", re.compile(r"\bAEO\b")),
    ("SoV", re.compile(r"\bSoV\b")),
    ("Query Target", re.compile(r"Query\s+Target", re.I)),
    ("Content Essence", re.compile(r"Content\s+Essence", re.I)),
    ("AI Visibility", re.compile(r"AI\s+Visibility", re.I)),
    ("Source Signal", re.compile(r"Source\s+Signal", re.I)),
    ("홈페이지 빌드", re.compile(r"홈페이지\s*빌드")),
    ("사이트 빌드", re.compile(r"사이트\s*빌드")),
    ("AI 검색 최적화", re.compile(r"AI\s*검색\s*최적화")),
    ("Brief 상태", re.compile(r"Brief\s*상태")),
    ("콘텐츠 철학", re.compile(r"콘텐츠\s*철학")),
    ("Essence 재검수", re.compile(r"Essence\s*재검수")),
    ("Essence source", re.compile(r"Essence\s+source", re.I)),
]

# Internal docs/comments that are not shown to operators can be allowed by path.
ALLOW_PATH_FRAGMENTS = {
    "alembic/",
    "models/",
    "schemas/",
    "tests/",
}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_PATHS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in EXTENSIONS:
                rel = path.relative_to(ROOT).as_posix()
                if any(fragment in rel for fragment in ALLOW_PATH_FRAGMENTS):
                    continue
                files.append(path)
    return sorted(files)


def is_probably_non_user_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    # Allow import/type/interface/function identifiers; scan actual copy strings/comments below.
    if stripped.startswith(("import ", "export type", "export interface", "interface ", "type ", "class ")):
        return True
    return False


def main() -> int:
    violations: list[str] = []
    for path in iter_files():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if is_probably_non_user_line(line):
                continue
            for label, pattern in BANNED_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: {label}: {line.strip()}")
    if violations:
        print("User-facing hard terms found. Replace with marketer/operator language:\n")
        print("\n".join(violations))
        return 1
    print("OK: no banned user-facing Re:putation terms found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
