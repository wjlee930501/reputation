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
    # 화면 문구가 lib의 순수 함수로 빠지는 일이 잦다(예: admin/lib/sov-trend.ts의
    # "아직 측정 전"). app만 스캔하면 리팩터 한 번으로 가드 사각지대가 생긴다.
    ROOT / "admin" / "lib",
    # 상태·요금제 라벨(STATUS_LABELS·PLAN_LABELS)이 여기 산다. 화면은 이 값을 그대로 그린다.
    ROOT / "admin" / "types",
    ROOT / "site" / "app",
    ROOT / "site" / "lib",
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
    ("리포트 스크리닝", re.compile(r"리포트\s*스크리닝")),
    ("internal screening", re.compile(r"internal\s+screening", re.I)),
    ("raw essence_summary", re.compile(r"raw\s+essence_summary|essence_summary\s*JSON", re.I)),
]

# 검토 §4의 통일안(admin/lib/admin-copy.ts)을 벗어난 옛 변형. 옛 8개 화면이 PR-1E에서
# 사라졌으므로 더는 경로 허용 목록을 두지 않고 admin 전체에 적용한다.
NEW_SURFACE_BANNED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("공개 표면 → 병원 공개 페이지", re.compile(r"공개\s*표면")),
    ("정보/콘텐츠 허브 → 병원 공개 페이지", re.compile(r"(?:병원\s*)?(?:정보|콘텐츠)\s*허브")),
    # "초기 진단 리포트"도 이 하나로 걸린다.
    ("리포트 → 보고서", re.compile(r"리포트")),
    ("스케줄 → 발행 일정", re.compile(r"스케줄")),
    ("슬러그/slug → 공개 주소", re.compile(r"슬러그|['\"]slug['\"]")),
    ("월간 운영량 → 월 발행 편수", re.compile(r"월간\s*운영량")),
    ("월간 발행량 → 월 발행 편수", re.compile(r"월간\s*발행량")),
    ("사후검수 → 공개 후 확인", re.compile(r"사후\s*검수")),
    ("후행 확인 → 공개 후 확인", re.compile(r"후행\s*확인")),
    ("AI 진단 분석 중 → 초기 진단 보고서", re.compile(r"AI\s*진단\s*분석\s*중")),
    ("자료 모음 → 근거 자료", re.compile(r"자료\s*모음")),
]

# 위 가드가 도는 범위. 화면 문구는 admin/app·admin/lib·admin/types 어디에나 산다.
UNIFIED_TERM_PATHS = [
    ROOT / "admin" / "app",
    ROOT / "admin" / "lib",
    ROOT / "admin" / "types",
]

INTERNAL_ONLY_MARKER = "# copy-guard: internal-only"

# Internal docs/comments that are not shown to operators can be allowed by path.
# 백엔드 내부 모듈만 제외한다. admin/site 아래에 같은 이름의 디렉터리를 만들어
# 화면 문구를 숨기는 우회를 막기 위해 repo 기준 접두사로만 비교한다.
ALLOW_PATH_FRAGMENTS = {
    "backend/alembic/",
    "backend/app/models/",
    "backend/app/schemas/",
    "backend/tests/",
}


def iter_files(bases: list[Path] | None = None) -> list[Path]:
    files: list[Path] = []
    for base in bases if bases is not None else SCAN_PATHS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in EXTENSIONS:
                rel = path.relative_to(ROOT).as_posix()
                if any(rel.startswith(fragment) for fragment in ALLOW_PATH_FRAGMENTS):
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


def banned_labels_for_line(
    line: str, patterns: list[tuple[str, re.Pattern[str]]] | None = None
) -> list[str]:
    """Return matched labels unless this exact source line declares an internal-only contract."""
    if INTERNAL_ONLY_MARKER in line:
        return []
    return [label for label, pattern in (patterns or BANNED_PATTERNS) if pattern.search(line)]


def iter_scannable_lines(path: Path) -> list[tuple[int, str]]:
    """주석·docstring을 걷어낸, 실제로 화면에 나갈 수 있는 줄만 돌려준다."""
    lines: list[tuple[int, str]] = []
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
        lines.append((lineno, line))
    return lines


def unified_term_violations() -> list[str]:
    """admin 화면 전체에서 통일 전 용어 변형을 막는다.

    화면 문구를 그대로 인용해 고정하는 `*.test.ts(x)`는 제외한다 — "옛 문구가 남지
    않았다"를 검사하려면 그 테스트가 옛 문구를 적어야 하기 때문이다.
    """
    violations: list[str] = []
    for path in iter_files(UNIFIED_TERM_PATHS):
        rel = path.relative_to(ROOT).as_posix()
        if rel.endswith((".test.ts", ".test.tsx")):
            continue
        for lineno, line in iter_scannable_lines(path):
            for label in banned_labels_for_line(line, NEW_SURFACE_BANNED_PATTERNS):
                violations.append(f"{rel}:{lineno}: {label}: {line.strip()}")
    return violations


def main() -> int:
    violations: list[str] = []
    for path in iter_files():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in iter_scannable_lines(path):
            for label in banned_labels_for_line(line):
                violations.append(f"{rel}:{lineno}: {label}: {line.strip()}")
    if violations:
        print("User-facing hard terms found. Replace with marketer/operator language:\n")
        print("\n".join(violations))
        return 1

    unified_violations = unified_term_violations()
    if unified_violations:
        print("admin에 통일 전 용어가 남았다. admin/lib/admin-copy.ts의 키로 바꿀 것:\n")
        print("\n".join(unified_violations))
        return 1

    print("OK: no banned user-facing Re:putation terms found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
