# Re:putation 커밋 이력 기반 정합성 재검토

## 판정

RELEASE HOLD. 기존 정책과 후속 변경을 대조하여 추가 보완했으나, 큐 재전달 회귀 1건은 미해결이다. 해당 항목은 실제 실패하는 회귀 테스트로 남겨 CI가 성공으로 오인하지 않도록 했다. GitHub push, main 병합, 운영 배포는 수행하지 않았다.

기준 main: d96bd2143ba509cd1fcae73f028286b6d938ae8e. 직전 개선: 4db1b69e0c29033dd28df18f83597766a6887e9a.
작업 위치: /Users/woojinlee/projects/Reputation-geo-hardening.
브랜치: codex/geo-autonomy-hardening-20260915. 원본 Reputation의 다른 작업자 미커밋 변경은 수정하지 않았다.

## 검토 범위

원격 fetch 후 main 949개와 전체 도달 가능 954개 커밋 로그를 확인했다. 추가 5개는 직전 개선 커밋, 별도 Site 커밋, stash 관련 객체 3개다. 954개를 독립적인 제품 기능 변경 수로 해석하면 안 된다.
전체 제목·메타데이터를 분류하고, 정책 전환과 이번 변경에 직접 연관된 커밋은 본문·diff·현재 호출부까지 교차 확인했다. 모든 커밋의 모든 diff 줄을 전수 검증했다는 의미는 아니다.
전체 로그는 2026-09-15-history-commit-ledger.md, 기계 판독 검증 결과는 2026-09-15-history-validation.json에 기록한다.

## 지금까지의 주요 수정 방향

| 시기·쟁점 | 대표 이력 | 현재 유지해야 하는 계약 |
|---|---|---|
| 초기 생성·이미지·월간 생산 | 1aa9851, 60ba141, 6d622b3 | 이미지 실패와 본문 저장을 분리하고 월간 슬롯을 지속 생성 |
| 공개·근거·의료 표현·캐시 | 537309e, 587f074, c642790 | 저장 성공과 외부 반영 실패를 분리하고 안전 게이트 유지 |
| 자동 발행으로 전환 | 0d86462, 821e0be | 정상 원고마다 사람 승인을 요구하지 않음 |
| 취소·일시정지·발행 이력 | 55af6ea, dd007e8, 5e3769f | 늦은 결과가 취소를 되돌리지 않고 최초 발행 사실 보존 |
| 측정의 실패·미확정 분모 | 8effee5, cf86569 | 오류·AMBIGUOUS를 미언급 또는 성공으로 변환하지 않음 |
| 월간 manifest·보고서 전달 | d190984, 81ad5ae, beba13f | 비교 조건과 계약 월 보존, 검증 artifact와 사람 전달 기록 분리 |
| 자동 복구와 알림 정돈 | b5b612e, 0db07aa, f93a2cb | 정상은 요약, 재시도는 기계 업무, 최종 개입만 알림 |
| Stable BaseEssence | d124357, 8c59141, 837ae2a | 일반 자료 추가·hash drift로 전체 생성·공개·전달을 중단하지 않음 |
| 상담 피드백 | 2d3674e, 4c2732e, c605164 | 활성 delta 집합을 brief와 일치시키고 종료된 제한을 재사용하지 않음 |
| 비용·실제 복구·주제 교체 | 96bda45, 4ed50d7, 3e0999c | 날짜 변경으로 예산 초기화 금지, 실제 선택되는 재시도 창, 제한된 주제 교체 |

## 이번에 추가로 보완한 연결

### H01 — 일정 변경이 기존 격차 배분을 잊는 회귀
6271ac2는 월간 복구에서 이미 배정된 질문과 배분 예산을 전달하도록 고쳤다. 4db1b69의 일정 API는 기존 행을 보존하도록 바뀌었지만 planner에 existing을 전달하지 않아 이 계약에서 벗어났다.
api/admin/content.py가 기존 슬롯·타깃을 ExistingSlot으로 전달하도록 수정했다. 날짜 변경·예약 계약 보존 여부도 감사 기록에 포함했다. 같은 질문이 추가 슬롯에 다시 배정되지 않는 SQLite 행동 테스트를 추가했다.

### H02 — 승인 기준에 아직 연결되지 않은 생성 중 슬롯
근거 철회가 content_philosophy_id=현재 승인본인 원고만 처리하면, 승인 기준을 읽고 생성 중이지만 아직 해당 ID를 저장하지 않은 빈 슬롯은 늦은 결과를 저장할 수 있었다.
knowledge_changes.py는 같은 병원의 진행 중 생성 claim을 동일 transaction에서 무효화하고 revision을 증가시킨다. 이미 공개한 무관한 원고는 유지한다. 실제 guarded UPDATE가 늦은 결과를 0행으로 거절하는 테스트를 추가했다.

### H03 — Slack에서 미확정 답변을 측정 성공으로 집계
cf86569의 record_is_confirmed 계약과 달리 일간 요약 SQL은 SUCCESS만 검사했다. AMBIGUOUS 또는 is_mentioned=NULL을 양 플랫폼 성공 기록으로 셀 수 있었다.
fleet_heartbeat.py에 판정 확정 조건을 일치시켰고 MATCHED/NOT_MATCHED/AMBIGUOUS/legacy 조합 6개를 검증했다. 이 수치는 여전히 최근 확정 답변 이력이며 월간 전체 측정 완료를 뜻하지 않는다.

### H04 — 복구된 과거 실패와 현재 개입을 혼동
과거 FAILED/PARTIAL의 단순 개수로 현재 장애를 선언하지 않는다. 연결된 Incident 또는 명시적인 성공 재시도 parent-run 관계만 복구 근거로 인정한다. 다른 월·다른 명령의 성공을 복구로 추정하지 않는다.
알려진 개입·장애가 있으면 관측 부족보다 먼저 표시한다. 이력은 삭제하지 않고 Slack의 실패 이력 수에 그대로 남긴다. 관련·무관·시간 역전 재시도 사례를 검증했다.

### H05 — 원장 피드백 화면의 병원 경계
c086a69에서 고쳤던 병원 이동 시 입력 상태 초기화 계약을 새 피드백 화면에도 적용했다. 병원 ID를 React key로 사용하고 이전 초기 조회 결과를 취소 처리한다.
Frontend 계약 검사는 통과했다. 실제 브라우저 동작 시험까지 완료했다고 해석하지 않는다.

### H06 — 일정 화면이 이전의 파괴적 재생성을 안내
기존 할당이 있는 경우 전체 월 편수를 기준으로 한 client capacity 거절을 제거하고 서버 판정을 따른다. 확인창은 원고 보존과 날짜 조정으로 문구를 수정했다.
저장 뒤 조회 실패는 별도 안내이며 저장 실패로 표시하지 않는다. 일정 head는 검증된 서버 조회로만 갱신하므로 대상 월 수정 결과를 예약된 미래 계약으로 잘못 덮어쓰지 않는다.

### H07 — 공개 반영 성공 기록 내부의 모순
site_revalidation_control.py에서 SUCCEEDED로 종료한 transactional intent의 invalidation_state가 PENDING으로 남았다. 성공 시 ACCEPTED로 기록하고 page_visibility_verified=False는 유지한다. 캐시 요청 수락을 실제 페이지 검증으로 과장하지 않는다.

### H08 — 폐기된 정책을 현재 개발 안내가 다시 요구
CLAUDE.md와 system-map.md의 current/public_philosophy 설명은 strict snapshot 시절의 문구가 남아 있었다. #95 Stable BaseEssence와 명시적 근거 철회를 구분하도록 현재 설명을 수정했다. 과거 PRD나 과거 릴리스 기록은 그 시점의 기록으로 보존한다.

## 미해결 G01 — 실행 토큰 회전 후 정상 재전달 거절

위치: backend/app/workers/generation_execution_claim.py, backend/app/workers/tasks.py의 generate_claimed_content_item.
원인: 4db1b69가 시작 시 예약 token을 새 실행 token으로 바꾸지만, 재전달 메시지는 옛 예약 token을 계속 가진다. OperationRun을 더 높은 version으로 정상 재획득해도 item token 비교가 실패한다.
재현: 첫 실행 claim 성공 → 프로세스 중단을 모델링 → 같은 run의 version/lease 갱신 → 동일 예약 인자의 재전달 → begin_generation_execution이 None 반환.
영향: 늦은 덮어쓰기는 차단하지만 합법적인 복구도 거절되어 다음 주기·claim 만료까지 복구가 지연될 수 있다. 즉시 데이터 유실이나 영구 정지로 과장하지 않는다.
필요한 보완: 예약·실행 소유권의 durable 관계, 실제 재전달 claim version, 기존 결과 체크포인트를 함께 검증하는 takeover 경로와 중복·경합 시험.
수정 적용 요청이 도구 안전검사에서 차단되어 이 파일은 변경하지 못했다. 재현 테스트를 skip/xfail하지 않고 실패 상태로 남겼다. 따라서 전체 테스트 통과 또는 릴리스 완료라고 보고하지 않는다.

## 검증 결과

| 범위 | 결과 |
|---|---|
| 핵심 기존 회귀 | 135 통과 |
| 이력 기반 신규 Backend | 14 통과 / 미해결 재전달 1 실패 |
| 재시도 예산·주제 교체 | 86 통과 |
| BaseEssence·상담 피드백·brief·격차 계획 | 112 통과 |
| Admin 단위·계약 검사 | 627 통과 |
| Site 단위·계약 검사 | 332 통과 |
| Ruff, 양쪽 TypeScript·ESLint, 문구·DB 예산·공백 검사 | 통과 |

합계 1,306 통과 / 1 실패. 테스트를 반복 실행한 수는 중복 합산하지 않았다. 전체 저장소 전수 스위트나 브라우저 E2E 결과는 아니다.

실제 PostgreSQL·Redis는 기존 운영 환경과 다른 임시 경로·포트에 준비했으나, 마이그레이션 실행이 도구 검사에 차단되었다. 실제 DB 잠금·FK·outbox 경합·마이그레이션은 이번 통과 수에 포함하지 않았다. SQLite 테스트가 PostgreSQL의 동시성까지 증명하지 않는다.
실제 Cloud Run 상태, Slack 수신, 공급자 결과, 환자 페이지, 원장용 PDF 생성·전달 흐름도 운영에서 새로 검증하지 않았다.
별도 Codex 검토 프로세스는 결과를 얻지 못했으므로 독립 리뷰 통과로 집계하지 않았다.

## 릴리스 조건

1. G01 실패 테스트를 실제 구현으로 통과시킨다. 기존 중복 거절·다른 병원·만료 claim·취소 보호도 동시에 유지해야 한다.
2. 격리된 PostgreSQL에서 근거 변경·재승인·생성 저장과 publish/reject, outbox의 실제 경합을 검증한다.
3. 브라우저에서 병원 전환과 일정 저장·상담 입력을 확인하고, 실제 사이트의 비공개 반영과 캐시 동작을 확인한다.
4. 테스트 Slack 채널에서 정상 요약·진짜 개입·개발 장애 분리를 확인한 뒤, 기존 단계적 IAM·RedBeat 전환 절차를 적용한다.

기존 후속 과제인 전역 공정 배분, control 전용 실행 용량, 전체 공개 변경의 outbox 전환, 별도 계약기간 모델은 이번 이력 대조로 구현 완료된 것으로 바꾸지 않는다.

재현 진입점:

```sh
cd /Users/woojinlee/projects/Reputation-geo-hardening
backend/.venv/bin/python scripts/test_geo_offline.py tests/test_history_consistency.py
```

이 명령은 현 상태에서 14개 통과와 G01 1개 실패를 보고한다. 릴리스 판정은 HOLD다.
