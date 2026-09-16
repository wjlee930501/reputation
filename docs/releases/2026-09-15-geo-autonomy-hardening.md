# GEO 자율 운영 보강 — 1차 구현·로컬 검증

기준: d96bd21 / 브랜치: codex/geo-autonomy-hardening-20260915
작성일: 2026-09-15 KST. 상태: 로컬 구현·선별 검증. 운영 배포·GitHub push 미실행.

## 제품 운영 계약

시스템이 병원별 근거 → 콘텐츠 → 발행 → ChatGPT/Gemini API 검색 답변 관측 → 격차 진단 → 다음 콘텐츠 계획을 운영한다.
사람은 정상 작업을 매번 승인하지 않는다. 운영 상태는 묶음 Slack 보고로 확인하고, 자동 복구 범위를 벗어난 문제를 처리한다.
월간 진단 보고서는 AE와 원장의 대화·방향 조정·관계 형성을 위한 산출물이다. 유료 월간 보고서의 고객 전달은 여전히 사람이 수행하고 기록한다.
새 피드백은 이후 생성할 글의 선호로만 반영한다. 의료 사실·공식 자료·안전 검토를 대체하지 않는다.

## 작업 격리와 보존

원본 /Users/woojinlee/projects/Reputation에 같은 작업 방향의 미커밋 코드와 실행 중인 Codex가 있었다.
원본을 덮어쓰지 않고 별도 worktree /Users/woojinlee/projects/Reputation-geo-hardening에 기존 변경을 복사한 뒤 보완했다.
원본 작업 시작 snapshot: /tmp/reputation-geo-baseline-z5lu6rsb (기존 patch, 30개 파일 hash, 검증 로그).
원본 .env, 인증 정보, 고객 CSV, tmp 데이터는 복사하거나 변경하지 않았다. 로컬 의존성만 심볼릭 링크로 사용했다.
의존성 링크·임시 데이터는 커밋 대상이 아니다. 원본에 돌아갈 때는 다른 작업자의 변경과 충돌 검토가 필요하다.

## 구현

### 1. 운영 상태 Slack 요약

fleet_heartbeat.py / notification_tasks.enqueue_fleet_heartbeat / Celery routing·purpose·Beat 연결.
기본 18시 KST 이후 한 번의 일간 요약을 생성한다. 매시간 tick은 당일 outbox 존재 여부부터 확인하여 놓친 실행을 회수한다.
FLEET_HEARTBEAT:YYYY-MM-DD 키로 하루 중복 생성을 방지하고 기존 outbox가 전달·재시도를 소유한다.
LLM을 사용하지 않는다. 공개 운영 병원 수, 발행 잔여/발행 기록, 자동 복구/개입 건수, 최근 양 플랫폼 측정·보고서 기록을 SQL과 watchdog으로 읽는다.
관측 부족·미완료 항목·관측 지표 양호를 구분한다. DB PUBLISHED나 HTTP invalidation 성공을 실제 페이지 공개 확인으로 말하지 않는다.
정상 글마다 개별 Slack 메시지를 추가하지 않았다. 기존 개발/운영 채널, 중복 억제, 외부 watchdog 경로를 유지한다.

현재 한계: 최근 실패·부분 완료 run 이력은 실제 복구 여부와 완전히 연결되지 않아 요약 상태를 보수적으로 표시할 수 있다.
requires_operator_action의 기존 공통 규칙(OPEN 또는 처리기한이 지난 RETRYING)은 유지했다. 모든 인시던트의 최종 복구 예산과 단일화한 것은 아니다.

### 2. 근거 추가와 명시적 철회·수정 분리

knowledge_changes.py와 Source 제외/수정 API를 연결했다.
일반 추가 자료는 안정된 BaseEssence를 재합성하지 않는다. 기존 승인 근거를 명시적으로 제외하거나 수정할 때만 authority_change_required를 기록한다.
승인 당시 source snapshot은 보존한다. 새 생성은 재승인까지 보류하고, 기존 공개는 의존 원고만 내려 재검토한다.
정확한 원고별 의존성이 없는 legacy는 UNKNOWN_LEGACY로 표시한다. 정확한 근거 연결을 추정하여 만들어내지 않는다.
영향 원고는 본문·최초 발행 이력을 보존하고 claim을 해제하며 revision을 올려 늦은 생성 응답을 차단한다.
동일 철회 재실행은 같은 원고 revision을 반복해서 올리지 않는다. 새 기준은 기존 bounded synthesis/review/backoff 경로를 이용한다.
철회된 사실을 기존 기준의 필수 보존 항목으로 다시 가져오지 않는다.
발행 판정·일반 재검사는 authority_change를 지우거나 새 기준의 승인을 옛 본문에 빌려주지 않는다. 새 원고 writeback이 해결해야 한다.
재승인 대기 중인 원고는 생성 배치 claim 대상에서 제외하여 불필요한 작업과 원고별 중복 예외를 줄인다.

### 3. 월 약정·일정·발행 이력

일정 교체에서 콘텐츠 DELETE를 제거했다. 같은 병원·같은 계약 월의 기존 identity를 합산하여 미할당 편수만 생성한다.
이미 공개한 행, 반려된 행, 생성한 본문, 사람 편집, 이월, claim을 보존한다.
미공개·미편집·미claim 미래 DRAFT/READY 슬롯만 조건부 날짜 UPDATE로 일정을 바꾼다. 원고와 retry fingerprint는 유지한다.
다른 schedule_id라는 이유만으로 같은 달에 약정 전체를 다시 만들지 않는다. 과거 계약 월 집계는 최초 공개 이력과 원 계약 날짜를 유지한다.
계약 plan 변경은 기존 schedule.plan을 덮어쓰지 않고 다음 달부터의 새 일정으로 기록한다. 현재 월 일정 수정은 예약된 다음 달 계약 head를 지우지 않는다.
현재 한계: 별도 ContractPeriod/Entitlement 모델은 추가하지 않았다. 소급 계약 정정, 월중 증액·감액·추가 제공의 상품 정책은 별도 구현이 필요하다.
legacy 중복·CANCELLED allocation은 보수적으로 수량에 포함한다. 데이터 손상을 자동으로 새 유료 생산량으로 보충하지 않는다.

### 4. 콘텐츠 변경 동시성

수동 발행·반려·편집은 병원 advisory lock을 먼저 취한 뒤 콘텐츠 행 잠금·최신 행 확인을 수행한다.
반려는 발행 직전의 오래된 상태가 아니라 잠금 뒤 실제 공개 상태를 보고 캐시 무효화를 결정한다.
공통 expected_revision HTTP 계약을 전 경로에 강제한 것은 아니며 PostgreSQL 다중 세션 경쟁 테스트는 배포 전 필수다.

### 5. 공개 표면 반영 의도와 캐시

public_surface_intents.py는 공개 변경과 같은 transaction에 SITE_REVALIDATION 실행 의도를 추가한다. 외부 호출이나 commit을 수행하지 않는다.
수동 발행·편집·반려, 자동 발행, 병원 일시정지·재개, 근거 변경·재승인에 연결했다.
프로세스가 domain commit 직후 죽어도 기존 자동 reconciliation이 committed intent를 회수한다. rollback이면 업무 변경과 intent 모두 취소된다.
Site fetch에 병원별 cache tag를 붙이고, 비밀 키로 보호된 revalidate route가 해당 병원 데이터 tag와 경로를 무효화한다.
병원 일시정지나 근거 철회가 목록뿐 아니라 관련 상세 글의 데이터 cache에도 반영되게 한다.
성공은 invalidation 요청 수락이지 환자에게 실제 제공되는 페이지의 검증 성공은 아니다.
현재 한계: profile/domain/photo 등 모든 mutation을 이 계약으로 전환한 것은 아니다. fast path 뒤 cheap invalidation이 한 번 더 실행될 수 있다.
Host mapping cache와 다중 Site 인스턴스 공유 cache는 별도 문제다. 단일 Site 인스턴스 보호 제한을 유지한다.

### 6. 생성 실행 lease

generation_execution_claim.py가 인증된 OperationRun의 worker·version·대상·만료와 원고 reservation token을 함께 검사한다.
아직 재할당되지 않은 예약은 큐에서 2시간 넘게 기다렸다는 이유만으로 버리지 않는다. 실제 실행 시 새 token·시각을 받아 시작한다.
같은 큐 메시지의 중복 실행, 다른 병원·worker·version, 만료 run, 정지 병원, 이미 회수된 token은 공급자 호출 전에 거부한다.
전역 공정 배분과 전용 control worker 용량은 이번에 변경하지 않았다.

### 7. 원장 상담 피드백

보고서 탭에 관심 주제·선호 표현·피할 표현·대화 참조 입력과 적용 종료를 연결했다.
OWNER/OPERATOR 활성 계정 확인, 병원 범위, 선택 report_id의 소유 병원 확인, 감사 기록을 유지한다.
동일 입력 중복은 합치고 활성 피드백 총 길이를 6,000자로 제한한다. 기존 제한을 조용히 잘라내지 않고 오래된 선호 종료를 요구한다.
생성 provenance에 적용된 delta ID와 fingerprint를 기록한다. 기존 글의 일괄 재생성은 하지 않는다.
저장은 성공했지만 목록 조회가 실패한 경우 저장 실패로 오인시키지 않는다.

### 8. Site/Admin 인프라 권한

Site와 Admin 서비스 identity·secret 접근을 분리하는 Terraform과 deploy/setup 배선을 반영했다.
legacy_frontend_access_enabled=true를 전환 기본값으로 두어 구 revision 권한을 새 identity 검증 전에 끊지 않는다.
새 Site/Admin을 각각 배포·검증하고 구 revision을 종료한 뒤 false로 바꿔 legacy grants를 회수해야 분리가 완료된다.
실제 IAM 변경, Secret 접근 테스트, terraform apply, Cloud Run 배포는 수행하지 않았다.

## 비용·토큰 정책

운영 요약은 deterministic SQL/watchdog으로 작성한다. 동일 피드백·같은 달 일정·같은 메시지 실행은 중복 작업을 억제한다.
근거가 추가됐다는 이유로 전체 원고를 다시 쓰지 않는다. 명시적으로 변경된 근거의 bounded 재승인·해당 원고 수리만 허용한다.
기존 공급자 usage 원장, 답변/판정 체크포인트, 이미지 재사용·교체, 비용 가드를 보존했다.
비용 절감률은 측정하지 않았다. Redis cost guard의 fail-open 정책은 유지되므로 절대 지출 상한을 보장하지 않는다.

## 검증

선별 Backend 회귀 테스트 539개 통과. 별도 생성 시도 상태·Terraform static 검사 16개 통과.
Admin 단위·계약 테스트 626개, Site 단위·계약 테스트 332개 통과. 총 1,513개이며 전체 저장소 테스트 전수 통과를 의미하지 않는다.
Backend Ruff, Admin/Site ESLint, Admin typegen, 양쪽 TypeScript, 운영 문구 검사, DB 연결 예산 guard, git diff --check 통과.
DB 연결 예산은 선언값 기준 75/80이며 실제 운영 부하 시험은 아니다.
실제 PostgreSQL이 필요한 notification outbox 17개는 네트워크 차단 때문에 fixture 오류였으며 통과 수에 포함하지 않았다.
생성·고객 데이터·Slack·클라우드 자원 변경 없이 SQLite/테스트 더블과 네트워크 차단으로 검증했다.
로그와 JUnit은 /tmp/reputation-geo-baseline-z5lu6rsb에 보관한다. scripts/test_geo_offline.py가 이 격리 테스트 진입점이다.

## 배포 차단 조건 / 후속 검증

1. 실제 테스트 PostgreSQL에서 source 철회→재승인→원고 수리→공개, 발행/반려 경쟁, intent rollback/commit, outbox lease/중복을 검증한다.
2. 브라우저 BFF→API→worker→DB→Site의 병원별 end-to-end와 secret 보호 cache invalidation을 검증한다.
3. 월말 측정→마감→월간 PDF→사람 전달→상담 피드백→다음 신규 원고의 연결을 실제 stage에서 검증한다. 아직 닫히지 않은 월을 억지로 완료 보고하지 않는다.
4. 실제 Slack 테스트 채널에서 하루 한 요약, 자동 복구 중 비개입, 최종 예외·개발 채널 분리를 확인한다.
5. RedBeat schedule version 2026-09-15.2를 배포 이미지와 함께 재조정한다.
6. IAM은 legacy grants 유지→새 identity 채택→구 revision 종료→legacy grants 회수→Site의 Admin secret 접근 거부 순서로 검증한다.
7. 전역 공정 배분, dedicated control worker, 전체 mutation outbox화, 별도 계약기간 모델, 공통 복구 예산 기반 개입 판정은 추가 작업이다.

오류가 없거나 운영 배포가 끝났다는 보장은 하지 않는다. 현재 산출물은 충돌을 피한 별도 브랜치의 구현과 위 범위의 로컬 검증 증거다.
