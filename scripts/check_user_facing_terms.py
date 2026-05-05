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
    ("Exposure Action", re.compile(r"Exposure\s+Action", re.I)),
    ("Content Essence", re.compile(r"Content\s+Essence", re.I)),
    ("AI Visibility", re.compile(r"AI\s+Visibility", re.I)),
    ("Source Signal", re.compile(r"Source\s+Signal", re.I)),
    ("홈페이지 빌드", re.compile(r"홈페이지\s*빌드")),
    ("사이트 빌드", re.compile(r"사이트\s*빌드")),
    ("AI 검색 최적화", re.compile(r"AI\s*검색\s*최적화")),
    ("Brief 상태", re.compile(r"Brief\s*상태")),
    ("Brief JSON", re.compile(r"\bBrief\s+JSON\b", re.I)),
    ("Content brief", re.compile(r"Content\s+brief", re.I)),
    ("draft brief", re.compile(r"draft\s+brief", re.I)),
    ("콘텐츠 철학", re.compile(r"콘텐츠\s*철학")),
    ("승인된 철학", re.compile(r"승인된\s+v?\{?\w*\}?\s*철학")),
    ("승인 철학", re.compile(r"승인\s*철학")),
    ("Positioning Statement", re.compile(r"Positioning\s+Statement", re.I)),
    ("Doctor Voice", re.compile(r"Doctor\s+Voice", re.I)),
    ("Patient Promise", re.compile(r"Patient\s+Promise", re.I)),
    ("Evidence Map", re.compile(r"Evidence\s+Map", re.I)),
    ("Unsupported Gaps", re.compile(r"Unsupported\s+Gaps", re.I)),
    ("Essence 검수", re.compile(r"Essence\s*검수")),
    ("Essence 재검수", re.compile(r"Essence\s*재검수")),
    ("Essence source", re.compile(r"Essence\s+source", re.I)),
    ("웹블로그 IA", re.compile(r"웹블로그\s*IA", re.I)),
    ("타깃 질의", re.compile(r"타깃\s*질의")),
    ("타깃 질문", re.compile(r"타깃\s*질문")),
    ("질의 변형", re.compile(r"질의\s*변형")),
    ("질의 세트", re.compile(r"질의\s*세트")),
    ("질문 변형", re.compile(r"질문\s*변형")),
    ("AI Exposure Strategy", re.compile(r"AI\s+Exposure\s+Strategy", re.I)),
    ("측정 매트릭스", re.compile(r"측정\s*매트릭스")),
    ("측정 대상 AI", re.compile(r"측정\s*대상\s*AI", re.I)),
    ("baseline", re.compile(r"(?<!-)\bbaseline\b(?!-)", re.I)),
    ("Google Business Profile", re.compile(r"Google\s+Business\s+Profile", re.I)),
    ("출처 신호", re.compile(r"출처\s*신호")),
    ("근거 신호", re.compile(r"근거\s*신호")),
    ("크롤링/색인", re.compile(r"크롤링\s*/\s*색인")),
    ("url/raw_text required", re.compile(r"url\s+또는\s+raw_text", re.I)),
    ("URL-only source", re.compile(r"URL-only\s+source", re.I)),
    ("Excluded source", re.compile(r"Excluded\s+source", re.I)),
    ("처리된 source", re.compile(r"처리된\s+source", re.I)),
    ("evidence note", re.compile(r"evidence\s+note", re.I)),
    ("status philosophy", re.compile(r"\b(?:APPROVED|ARCHIVED|DRAFT)\s+philosophy\b", re.I)),
    ("source_asset_ids 형식", re.compile(r"source_asset_ids\s+형식", re.I)),
    ("raw evidence map JSON", re.compile(r"JSON\.stringify\(selectedDraft\.(?:evidence_map|unsupported_gaps)", re.I)),
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
        in_triple_quoted_comment = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(('"""', "'''")):
                if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                    in_triple_quoted_comment = not in_triple_quoted_comment
                continue
            if in_triple_quoted_comment:
                if stripped.endswith(('"""', "'''")):
                    in_triple_quoted_comment = False
                continue
            if stripped.startswith("#"):
                continue
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
