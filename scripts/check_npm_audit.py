#!/usr/bin/env python3
"""배포되는 코드의 npm 취약점 게이트.

## 왜 `npm audit --audit-level=high`를 그대로 쓰지 않나

두 가지가 섞여 있어서 게이트가 신호를 잃었다.

1. **dev 의존성** — site의 high 20건 중 18건이 `brace-expansion → minimatch → eslint`
   툴체인 전이다. 린트할 때만 쓰이고 컨테이너에 실리지 않는다. 게이트의 목적이
   "취약한 코드를 배포하지 않는다"이므로 `--omit=dev`로 범위를 배포물에 맞춘다.

2. **앞으로 고칠 수 없는 권고** — `sharp → libvips` CVE는 npm이 제시하는 유일한 "수정"이
   `next@14` **다운그레이드**다. 더 오래된 Next의 자체 권고를 다시 들이는 것이므로 악화다.
   Next 16을 쓰는 admin에도 같은 권고가 남는다(= 버전 올려서 해결되지 않는다).

그래서 **고칠 수 없는 것만 기한을 붙여 명시적으로 예외 처리**하고, 그 외에는 전부 막는다.
예외를 두지 않으면 게이트가 항상 빨간불이 되어 아무도 보지 않게 되고, 예외에 기한이
없으면 upstream이 고친 뒤에도 영원히 열린 채로 남는다.

사용법:
    python3 scripts/check_npm_audit.py site admin
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BLOCKING_SEVERITIES = frozenset({"high", "critical"})


class Exception_:
    """허용된 권고 1건. 기한이 지나면 게이트가 다시 막는다."""

    def __init__(self, advisory: str, *, review_by: str, reason: str):
        self.advisory = advisory
        self.review_by = date.fromisoformat(review_by)
        self.reason = reason


# 고칠 수 없다고 판단해 통과시키는 권고. **여기 추가할 때는 근거와 기한을 함께 쓴다.**
ALLOWED = [
    Exception_(
        "GHSA-f88m-g3jw-g9cj",
        review_by="2026-10-31",
        reason=(
            "sharp → libvips CVE 4건. next가 sharp를 물고 있고 npm의 유일한 수정 제안은 "
            "next@14 다운그레이드(= 더 오래된 Next의 자체 권고를 다시 들임)라 전진 경로가 "
            "없다. Next 16을 쓰는 admin도 동일하게 남는다. 노출 경로는 next/image 최적화이며 "
            "우리 site는 자체 GCS 버킷의 생성 이미지만 최적화하고 사용자 업로드를 받지 않는다. "
            "upstream(sharp/libvips) 수정 대기."
        ),
    ),
]

_ALLOWED_BY_ID = {item.advisory: item for item in ALLOWED}


def _advisory_ids(vuln: dict) -> set[str]:
    """이 항목이 근거로 삼는 GHSA id들. 문자열 via는 다른 패키지 경유이므로 id가 없다."""
    ids: set[str] = set()
    for via in vuln.get("via", []):
        if isinstance(via, dict):
            url = str(via.get("url") or "")
            if "/advisories/" in url:
                ids.add(url.rsplit("/", 1)[-1])
    return ids


def _audit(directory: Path) -> dict:
    result = subprocess.run(
        ["npm", "audit", "--omit=dev", "--json"],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    # npm audit은 취약점이 있으면 비정상 종료코드를 주면서도 JSON을 출력한다.
    if not result.stdout.strip():
        raise SystemExit(f"✗ {directory.name}: npm audit이 출력을 주지 않았습니다.\n{result.stderr}")
    return json.loads(result.stdout)


def _check(directory: Path) -> tuple[list[str], set[str]]:
    """(차단 사유 목록, 실제로 관측된 예외 id 집합)."""
    report = _audit(directory)
    blockers: list[str] = []
    observed: set[str] = set()

    for name, vuln in sorted(report.get("vulnerabilities", {}).items()):
        if vuln.get("severity") not in BLOCKING_SEVERITIES:
            continue

        ids = _advisory_ids(vuln)
        # 자체 권고가 없는 항목(via가 패키지 이름뿐)은 의존하는 쪽에서 이미 판정된다 —
        # 그 부모가 허용됐다면 이 항목도 같은 사유로 허용된 것이다.
        if not ids:
            parents = {str(v) for v in vuln.get("via", []) if isinstance(v, str)}
            if parents and all(
                _is_transitively_allowed(report, parent) for parent in parents
            ):
                continue
            blockers.append(f"{directory.name}: {name} ({vuln['severity']}) — 경유 취약")
            continue

        unallowed = ids - set(_ALLOWED_BY_ID)
        observed |= ids & set(_ALLOWED_BY_ID)
        if unallowed:
            blockers.append(
                f"{directory.name}: {name} ({vuln['severity']}) — {', '.join(sorted(unallowed))}"
            )

    return blockers, observed


def _is_transitively_allowed(report: dict, package: str) -> bool:
    vuln = report.get("vulnerabilities", {}).get(package)
    if vuln is None:
        return False
    ids = _advisory_ids(vuln)
    return bool(ids) and ids <= set(_ALLOWED_BY_ID)


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["site", "admin"]
    today = date.today()

    all_blockers: list[str] = []
    all_observed: set[str] = set()
    for target in targets:
        directory = REPO_ROOT / target
        if not (directory / "package.json").exists():
            print(f"✗ {target}: package.json이 없습니다.")
            return 1
        blockers, observed = _check(directory)
        all_blockers.extend(blockers)
        all_observed |= observed
        status = "차단" if blockers else "통과"
        print(f"  {target:6s} {status}")

    # 기한이 지난 예외는 다시 막는다 — 재검토 없이 영원히 열려 있지 않게.
    expired = [item for item in ALLOWED if item.review_by < today]
    for item in expired:
        all_blockers.append(
            f"예외 기한 만료: {item.advisory} (기한 {item.review_by}) — "
            "upstream 수정 여부를 다시 확인하고 기한을 갱신하거나 예외를 제거하세요."
        )

    # 사라진 예외는 알려준다(실패시키지는 않는다 — 의존성 갱신이 CI를 깨면 안 된다).
    stale = [item.advisory for item in ALLOWED if item.advisory not in all_observed]
    for advisory in stale:
        print(f"  ℹ 예외 {advisory}가 더 이상 관측되지 않습니다 — ALLOWED에서 제거해도 됩니다.")

    if all_blockers:
        print("\n✗ 배포 코드에 허용되지 않은 취약점이 있습니다:")
        for blocker in all_blockers:
            print(f"    - {blocker}")
        print("\n  수정하거나, 고칠 수 없다면 scripts/check_npm_audit.py의 ALLOWED에")
        print("  근거와 기한을 적어 추가하세요.")
        return 1

    print("\n✓ 배포 코드 npm 취약점 게이트 통과")
    if ALLOWED:
        print(f"  (기한부 예외 {len(ALLOWED)}건 — 가장 이른 기한 {min(i.review_by for i in ALLOWED)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
