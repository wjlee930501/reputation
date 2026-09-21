# 2026-09-21 — main 통합과 브랜치 정리

## 소스와 범위

원격 main `9495837319c1640ac8840bcb817e58f380aa8a93`에 로컬 Slack 작업
`5e3cf2871470ca50549a52ec424c738be5f0f4d8`을 통합한다. 기존 main은 먼저
fast-forward pull했고, 별도 worktree에서 충돌을 해결했다. 이번 작업은 소스 통합이며
운영 배포·운영 DB 변경·Slack 시험 발송·콘텐츠 생성 구매를 포함하지 않는다.

## 통합 결정

- PR #134의 정확한 Lead / Error / Report 라벨, 이벤트별 분류와 모든 메시지 표본
  검증을 유지한다. 라벨은 fallback과 header 또는 첫 section 앞에 한 번 붙는다.
- 9월 17일의 행동 중심 문구, 정상 알림 억제, 성공한 이미지 대체 발행과 실제 차단의
  분리, 최신 보고서 버전만 알림, KST 표시, 안전한 Admin 링크를 함께 적용한다.
- 기존 라벨 테스트 중 과거 문구·성공 알림을 고정하던 부분만 통합 정책에 맞춘다.
  종류별 first-token 검사, 보안·중복·수신 대상·outbox 회귀 검사는 유지한다.
- main의 OpenRouter 전환, 최신 생성·재작성·재시도·발행 게이트 수정은 유지한다.
  `tasks.py`의 추가 차이는 주간 알림에 안전 코드와 사람의 조치 여부를 넘기는 부분뿐이다.
- Slack 전송 재시도·CAS·lease, 발행 안전 게이트, 서비스 배포 설정은 변경하지 않는다.

## 브랜치 검토 결과

정리 전 로컬 20개·원격 25개 ref를 확인했다(main 각각 포함, origin/HEAD 제외).
26개는 main 조상, 9개는 현재 tip과 동일한 HEAD의 병합 PR, 3개는 patch-equivalent다.
별도 로컬 Slack ref 1개를 이 통합에서 반영한다. main ref 2개는 유지한다.
아래 대체안 4개는 코드를 다시 병합하지 않고 전체 Git 이력 백업에 보존한다.

| 보존할 대체안 | main에 다시 적용하지 않는 이유 |
|---|---|
| `fix/visual-media-hardening` · `43b647e` | 8월 22일의 별도 구현. 이후 main이 복구한 사진 근거·선형 마이그레이션·현재 화면과 충돌한다. 과거 UI·공급자 코드를 되살리지 않는다. |
| `claude/reputation-website-layout-913ee8` · `1b119c8` | 9월 15일의 미병합 전면 재설계안. 이후 선택된 PR #118 레이아웃과 #119 이미지 전달 개선을 유지한다. |
| `cursor/force-hard-finding-rewrite-recovery-a1b2` · `ba3d714` | PR #125가 #124의 자동 복구로 대체됐다고 명시적으로 종료됐다. 예산 외 강제 실행 경로를 추가하지 않는다. |
| `cursor/fix-hard-finding-rewrite-crash-f32a` · `5f8f1a7` | 소유자가 PR #127을 #126으로 대체했다고 명시적으로 종료했다. 중복 RCA 구현을 되살리지 않는다. |

`fix/visual-media-hardening`의 과거 문제와 후속 선택은
`2026-08-22-wave-27-37-code-review.md` 및 이후 main의 사진 근거 수정에 기록돼 있다.
현재 레이아웃의 근거는 `2026-09-16-clinic-layout-system.md`와 병합 PR #118이다.
따라서 ‘모든 과거 코드를 무조건 병합’하지 않고 main을 단일 작업 기준으로 정리한다.

## 보존·삭제 안전 조건

정리 전 `git bundle --all`을 생성하고 검증했다. 브랜치 tip, PR 상태, patch 비교 결과,
기존 worktree 목록과 `aside-test.js` 사본을 저장소 밖 `.reputation-backups/20260921-141719`
디렉터리에 보존한다. 브랜치 삭제 직전에 원격 tip을 다시 확인해 다른 작업의 새 커밋을
삭제하지 않는다. 현재 통합 브랜치도 병합 확인 후 정리한다.

기존 worktree의 미추적·무시 파일과 root의 `aside-test.js`는 삭제하거나 커밋하지 않는다.
테스트는 이번 작업 전용 loopback PostgreSQL 3개·Redis 1개만 사용한다. Python 테스트의
외부 연결과 운영 dotenv를 차단하며 PDF 검사도 skip하지 않는다.

## 검증

실행 결과는 같은 이름의 `-validation.json`에 기록한다. 이 문서의 테스트 결과나
브랜치 병합은 실제 Slack 수신·운영 배포 완료를 뜻하지 않는다.
