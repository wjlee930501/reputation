# Re:putation 코드 리뷰 — 최근 웨이브 (#27–#37, main @ 8ad0f2b)

- 리뷰어: Claude Fable 5 (읽기 전용 리뷰 — 제품 코드 변경 없음)
- 대상: origin/main `8ad0f2b` (#37 머지 직후), 범위 `07a8273..8ad0f2b`
- 요청: MotionLabs 김실장

---

## Verdict: **BLOCK**

제품 잠금 6개 중 5개는 지켜졌고 alembic 체인 복구(#37) 자체는 견고하지만,
**프로덕션 스키마(0052의 provenance CHECK 제약) 기준으로 공개 사진 저장 경로 전체가
IntegrityError(500)로 죽는 P0**가 main에 남아 있다. #29·#30·#36이 내세운 "사진
업로드 → 즉시 공개" 기능이 프로덕션에서 동작하지 않고, 0052가 기존 공개 사진을
전부 비공개로 돌려놓은 상태라 **복구할 API 경로 자체가 없다.** 이 한 건이 해소되기
전에는 이 웨이브를 "완료"로 볼 수 없다.

(사진은 온보딩 필수 게이트가 아니므로 잠금 2 위반은 아니다. 온보딩 완주는 가능하다.
깨진 것은 이 웨이브가 출하했다고 주장한 기능과 기존 공개 데이터의 노출이다.)

---

## Top 5 findings (심각도 순)

### [P0-1] 공개 사진 저장이 프로덕션 CHECK 제약과 충돌 — 업로드·재공개 전부 500

- 파일:
  - `backend/alembic/versions/0052_add_photo_asset_provenance.py` (41–50행 제약 생성, 37–40행 기존 공개 사진 전부 비공개화)
  - `backend/app/api/admin/essence.py` — `upload_source_file` (719–761행: `is_public=True` 기본, provenance 미기록), `toggle_source_public` (1021–1070행: 동일)
  - `backend/app/models/essence.py` 116–120행 (컬럼 매핑만 존재)
- 내용: 프로덕션은 alembic `0054`에 스탬프되어 있어(= #37 커밋 메시지가 명시) 0052의
  `ck_public_photo_requires_provenance` 제약이 **이미 살아 있다.** 이 제약은 공개
  PHOTO_* 행에 `photo_source_owner`·`photo_rights_basis(LICENSE|OWNER_CONSENT)`·
  `photo_evidence_reference`·`photo_verified_by`·`photo_verified_at` 5개를 요구한다.
  그런데 main에는 이 5개 필드를 **쓰는 코드가 한 줄도 없다** (provenance 입력 UI를
  가진 `fix/visual-media-hardening` 브랜치의 앱 코드는 main 조상이 아님). 사진 업로드는
  기본 공개(`resolve_upload_is_public` → True)이므로 INSERT가 제약 위반 → 500. 공개
  토글 PATCH도 동일. 0052가 배포 시점에 기존 공개 사진을 전부 `is_public=false`로
  뒤집어 놓았으므로, 현재 프로덕션 공개 표면(히어로·갤러리·원장 사진)은 비어 있고
  운영자가 되돌릴 방법이 없다.
- CI가 못 잡는 이유: 업로드 API는 순수 함수 단위 테스트(`test_essence_upload_public.py`,
  DB 미사용)만 있고, 마이그레이션 적용된 Postgres에서 업로드/토글을 실제로 커밋하는
  테스트가 없다. 크로스테넌트 통합 테스트는 픽스처가 provenance를 **수동으로 채워서**
  제약을 우회한다(#37에서 그렇게 고쳐야 통과했다는 사실 자체가 이 버그의 증거다).

### [P1-2] V0 워커의 세션 advisory 락이 오류 경로에서 풀 커넥션에 영구 잔류

- 파일: `backend/app/workers/tasks.py` 1153–1157행, `backend/app/core/database.py` (sync 엔진: QueuePool, `pool_recycle` 없음)
- 내용: `trigger_v0_report`의 `finally`가 **rollback 없이** `pg_advisory_unlock`을
  실행한다. 락 구간 안에서 SQLAlchemy 오류(커밋 실패 등)가 나면 세션이
  pending-rollback 상태라 unlock 쿼리 자체가 실패하고 `except`가 삼킨다. 세션 락은
  트랜잭션 롤백으로 해제되지 않으므로 커넥션이 풀로 반환된 뒤에도 락이 살아남고,
  `pool_recycle`이 없어 **무기한** 유지된다. 같은 advisory 키를 쓰는 그 병원의 모든
  쓰기(프로파일 저장, 자료 업로드/제외, V0 재시도)가 알림 없이 무한 대기한다.
  수정은 unlock 직전 `db.rollback()` 한 줄(또는 새 커넥션에서 unlock).

### [P1-3] 온보딩 시각 요소 폼 입력이 처리 폴링에 5초마다 초기화됨

- 파일: `admin/app/hospitals/[id]/onboarding/page.tsx`
  - 669–675행: `ClinicVisualForm`의 `useEffect([hospital])`가 폼 상태를 무조건 리셋
  - 266–290행: `refresh()`가 매번 새 `hospital` 객체를 set → 참조 변경으로 이펙트 재실행
  - 1425–1435행: 자료 일괄 처리 추적 중 `setInterval(onChanged, 5000)` — 5초마다 `refresh()`
- 내용: 자료 처리(단계 6, 건당 1–2분)를 돌려놓고 AE가 단계 2의 시각 요소 폼(로고 URL·
  대표색·첫 화면 카피)을 입력하는 것이 자연스러운 동선인데, 폴링이 돌 때마다 저장 전
  입력이 서버 값으로 되돌아간다. 업로드·제외·공개 토글 등 다른 자식 폼의 성공 콜백도
  같은 `refresh`를 부르므로 단발 소실도 발생한다. 리뷰 질문 (d)의 시나리오 그대로이며,
  dirty-state 가드(입력 중이면 리셋 생략)나 저장 전 값 보존이 필요하다. #35/#36 어느
  쪽에서도 수정되지 않았다.

### [P1-4] 인증서 재시도 예산이 실제 발급 시간보다 짧고, 영구 실패 시 Slack이 없음

- 파일: `backend/app/workers/domain_certificate_tasks.py` (84행 `max_retries=20`, 110행 `countdown=30`; 57–77행 FAILED 처리에 notifier 부재)
- 내용: 20회 × 30초 ≈ 10분 폴링 후 FAILED 확정인데, GCP Certificate Manager의 LB 인증서는
  DNS 전파 후 15–30분+ 걸리는 경우가 흔하다. 결국 **성공했을 작업이 FAILED로 찍히고**,
  운영자가 재검증 버튼을 다시 눌러야 풀린다 — 잠금 5가 금지한 "사람 확인 루프"를
  시스템이 만들어낸다. 반대로 진짜 영구 실패(커스텀 도메인이 HTTPS 없이 서비스되는
  상황 = 사람이 지금 개입해야 하는 상황)에는 Slack이 전혀 발송되지 않는다(잠금 3의
  취지 미충족). 부수: ISSUING 클레임에 만료/리컨실이 없어 commit-후-dispatch-전 크래시
  시 영구 고착(409 반복, UI 버튼 비활성)하는 P2 창구도 있다
  (`domain_certificate_jobs.py` 142–149행, `domain_verification.py` 150–194행).
- 참고: DNS 성공 = 단계 5 완료, `site_live` 보존, 클레임 자체의 멱등성(row lock +
  토큰 리스)은 모두 올바르게 구현됐다(잠금 6 통과).

### [P2-5] COST_BLOCKED·PROVIDER_* 공통 원인 장애가 아이템당 Slack으로 최대 50건 버스트

- 파일: `backend/app/workers/generation_incident_control.py` (34–40행 immediate 코드 셋, 151–158행 인시던트 identity가 content_item 단위)
- 내용: 야간 생성 중 일일 비용 한도 도달이나 Claude/Imagen 장애는 배치 전체에 동시에
  걸리는 **하나의 원인**인데, 인시던트 식별자가 콘텐츠 아이템 단위라 같은 메시지가
  아이템 수만큼(캡 50건) 발송된다. PROVIDER_*의 next_action이 "지금은 기다리세요"인
  메시지를 즉시 알림으로 50번 보내는 셈 — #27이 잡으려던 Slack 홍수의 잔재다.
  COST_BLOCKED·PROVIDER_*는 병원 또는 플릿 단위 인시던트로 묶어야 한다
  (`MISSING_APPROVED_ESSENCE` 다이제스트가 이미 그 패턴이다). 관련 잠재 결함: 알 수
  없는 실패 코드는 immediate에도 expected-pending에도 없어 **어떤 경로로도 Slack에
  도달하지 못하며**, 주석이 약속한 "08시 소진 차단 요약"은 구현되어 있지 않다
  (`tasks.py` 1940–1941행 주석 vs 2357–2368행 실제).

---

## 잘된 것 (짧게)

- **alembic 0051→0055 선형 체인 복구(#37)**: 리비전 id·부모 보존, 0055 백필의
  `jsonb ||` 비객체 멱등성 처리와 실제 Postgres 이중 실행 테스트, 체인 선형성 고정
  테스트(`test_migration_chain_linearity.py`)까지 — 스탬프 0054 DB에서는 0055만,
  새 DB에서는 전체가 순서대로 적용된다. 데이터 손실 없음.
- **KEEP-8 준수**: 정확히 8단계(인수→기본정보→V0→허브→도메인공개→자료처리→운영기준→
  스케줄) + 후속 성과 2단계 분리, 자료 단계(6) 상시 펼침, 시각 요소는 9번째 단계가
  아니라 기존 프로파일 단계 카드 내부 — 그리고 이 계약을 소스 레벨로 고정하는
  테스트(`onboarding-visual-step.test.ts`)까지 있다.
- **사진 선택 원칙**: 시각 체크리스트에서 사진은 `blocksApproval: false`로 명시
  (`clinic-visual-readiness.ts`), 온보딩 완료를 막지 않는다.
- **브랜드색 1개 + 시스템 램프(잠금 4)**: `clinic-theme.ts`가 대표색 하나에서 WCAG
  대비 안전 단계 전체를 파생하고, hex 재검증 + CSS 변수로만 주입해 CSS 주입이 불가능.
  admin 쪽도 `#RRGGBB` 정규식·`Literal`·의료광고 필터로 저장 시점 검증.
- **보안**: 공개 API 전 쿼리의 테넌트 스코핑(실 Postgres 크로스테넌트 통합 테스트 포함),
  admin 라우터 전체 레벨 인증, 업로드 스트리밍 크기 제한·파일명 새니타이즈,
  `ReactDevTools`는 개발 환경 이중 게이트로 프로덕션 미실행.
- **V0 중복 방지(#32)**: API 409 + 워커 advisory 락 + 40분 클레임 TTL의 3중 구조,
  쿼리 매트릭스 재사용으로 재시도 시 측정 비용 미증가.
- **도메인 인증서 클레임 멱등성**: row lock + 리스 토큰 + 결정적 GCP 리소스 id,
  동시 클레임 통합 테스트(200/409, 태스크 1회 디스패치) 확보.

---

## P0/P1 해소를 위한 다음 수정 제안 (drive-by 리팩터 없음)

1. **[P0-1] 공개 사진 provenance 경로 복구** — 둘 중 하나를 의식적으로 선택:
   (a) 업로드/공개 토글에 provenance 캡처를 추가(운영자 identity로 `photo_verified_by`,
   권리 근거 입력 폼 — hardening 브랜치가 의도했던 형태), 또는
   (b) 새 마이그레이션으로 제약을 완화(예: `needs_operator_review` 기반 소프트 게이트)하고
   0052가 비공개화한 사진의 복구 절차를 마련.
   어느 쪽이든 **마이그레이션 적용된 Postgres에서 `upload_source_file`·
   `toggle_source_public`을 end-to-end로 커밋하는 통합 테스트**를 반드시 추가.
2. **[P1-2]** `tasks.py` 1153행 `finally`에서 unlock 전에 `db.rollback()` 추가
   (+ 실패 시 커넥션 폐기). 회귀 테스트: 락 구간에서 커밋 실패를 주입해 후속
   xact 락 획득이 블록되지 않는지.
3. **[P1-3]** `ClinicVisualForm`에 dirty 가드 추가 — 사용자가 입력을 시작한 뒤에는
   `hospital` 참조 변경으로 리셋하지 않기(저장 성공 시에만 동기화).
4. **[P1-4]** 인증서 재시도 예산을 실제 발급 시간에 맞게 상향(예: 60회 × 60초 or
   지수 백오프로 ~40분) + 영구 FAILED 시 Slack 1건(사람이 지금 행동해야 하는 케이스).
   ISSUING 리스에 만료(예: 30분) 또는 리컨실 태스크 추가.

---

## 리뷰 질문별 답변

### (a) 프로덕션 경로 회귀/버그

- **P0-1** (공개 사진 저장 500 — admin 온보딩·공개 사이트 양쪽 타격), **P1-2** (V0 워커
  → 병원 단위 쓰기 정지), **P1-4** (인증서 false-FAILED), 그리고 도메인 변경 직후 admin
  UI의 낙관적 패치가 이전 인증서 상태를 지우지 않아 "연결 완료"를 오표시하고 검증
  버튼을 숨기는 P2(`DomainSetupPanel.tsx` 231–238행 vs `domain_connect.py` 125–130행,
  새로고침으로 복구 가능).
- 발행 다이제스트가 morning 태스크 재시도를 가로지르면 건수를 과소 집계하는 P2
  (`tasks.py` 2404–2412행: 재시도 시 이미 발행된 아이템이 outcomes에서 빠지거나
  날짜 dedupe에 걸림).

### (b) 데이터 무결성

- **asset_kind**: 0055 백필과 앱의 `photo_assets.py` 복구 로직이 같은 보수적 규칙
  (원장 사진은 절대 identity로 자동 승격 안 함, `LEGACY_BACKFILL` + 재확인 플래그)을
  공유 — 일관성 좋음. 비객체 `source_metadata`의 `jsonb ||` 함정도 0055에서 올바르게
  처리·테스트됨.
- **provenance 제약**: 제약 자체는 "근거 없는 사진은 공개 불가"라는 올바른 계약이지만,
  main에는 이를 만족시킬 쓰기 경로가 없다 → P0-1. 또한 제약이 마이그레이션에만 있고
  모델 `__table_args__`에 없어 단위 테스트 스키마에는 존재하지 않는다(커버리지 사각).
- **stamp 0054→0055**: `iterate_revisions("heads", "0054")`가 0055 하나만 반환함을
  테스트로 고정. 어느 단계에서도 컬럼 드랍 없음. 0049/0050은 nullable ADD COLUMN만이라
  온라인 안전. 통과.

### (c) 보안

- 크로스테넌트: 공개 site.py 전 쿼리가 slug→ACTIVE+site_live 해석 후 `hospital_id`
  스코핑, 타 테넌트 philosophy를 가리키는 적대적 케이스까지 실 Postgres 테스트. 통과.
- 공개 GET: `director_philosophy` 강제 null, `license_number` 제거, 사진 직렬화는
  안전 필드만. 단 **`image_style_direction`(운영자용 아트 디렉션 프롬프트, 의료광고
  필터 미적용)이 공개 페이로드에 실리는데 사이트는 렌더링하지 않는다** — 노출 이유가
  없으므로 제거 권장(P2, `site.py` 454행; `8e52b4d`의 "operator-facing 유지" 취지와도
  모순).
- admin 인증: 신규 엔드포인트 포함 전 admin 라우터가 `main.py` 레벨 공통 의존성
  (rate limit + constant-time 키 비교) 적용. 통과. 부수 P2/P3: 업로드가 클라이언트
  content-type/확장자를 신뢰(매직바이트 미검사)하고 저장된 mime을 공개 자산 응답에
  그대로 사용 — 어드민 한정 행위자라 실질 위험 낮음.

### (d) UX

- **P1-3** (시각 입력 폴링 소실)이 핵심. 그 외에는 좋다: 단계 카드가 "지금 해야 할 일"
  헤드라인·잠김 사유·차단 사유를 항상 문장으로 제시하고, 사진 분류 드롭다운이 허용
  용도를 한국어로 설명하며, 업로드 실패 시 파일 단위 성공/실패 카운트를 보여준다.
  V0 실패 시 "추가 비용을 막기 위해 중단했으며 사람 확인이 필요합니다"류의 문구도
  학습 없이 이해 가능.

### (e) 운영 비용

- 중복 잡: V0(3중 방어)·인증서(리스 토큰)·야간 생성(row claim + skip_locked) 모두
  멱등. 야간 캡 50, 비용 가드 선차감, 재작성 1회 한도, 쿼리 매트릭스 재사용. 통과.
- 재시도 폭풍: 없음(전 태스크 max_retries 유한). 단 인증서는 반대로 예산 부족(P1-4).
- Slack 노이즈: 발행 성공은 일일 다이제스트 1건으로 수렴(#27 목표 달성)이지만
  **P2-5의 아이템당 버스트**가 잔존. 잠재적 침묵: 미지 실패 코드 무알림, 발행일 아침
  차단 무알림(48h 후 에스컬레이션만), 인증서 영구 실패 무알림.
- 사람 확인 루프: 자동 경로에는 없음. 단 인증서 false-FAILED가 사실상의 수동 재시도
  루프를 만든다(P1-4).

### (f) 고위험 경로의 누락 테스트

1. **마이그레이션 적용된 DB에서 사진 업로드/공개 토글 API end-to-end** — P0-1을 잡을
   유일한 테스트. 현재 업로드는 순수 함수 테스트만 존재.
2. **V0 락 구간 오류 주입 후 세션 락 해제 검증** — P1-2 회귀 방지.
3. **시각 폼 dirty-state 보존** — 폴링 중 입력 유지에 대한 컴포넌트/락 테스트
   (현재 `onboarding-visual-step.test.ts`는 소스 문자열 검사만).
4. **인증서 ISSUING 고착 시나리오** — commit-후-dispatch-전 실패에서 재검증 가능해야
   한다는 계약(현재는 반대 방향, 즉 "만료 없음"이 테스트로 고정되어 있음 —
   `test_cert_status_never_calls_certificate_manager_or_expires_worker_job`).
5. **공통 원인(비용 한도/프로바이더 장애) 시 Slack 발송 총량 상한** — P2-5 회귀 방지.
6. **공개 페이로드 필드 스냅샷** — `image_style_direction` 같은 내부 필드가 공개
   계약에 추가될 때 리뷰 없이 통과하지 못하도록.

---

## 부록: 잠금 준수 표

| 잠금 | 판정 | 근거 |
|---|---|---|
| 1. KEEP-8 (8단계·병합/9단계 금지·자료 단계 6 상시 펼침) | ✅ | `onboarding-lifecycle.ts` 온보딩 phase 8개, `page.tsx` `open={key==='processing'}`, 소스 고정 테스트 |
| 2. 사진 선택(하드 게이트 금지·Drive 없음) | ✅ | `clinic-visual-readiness.ts` `blocksApproval:false`; Drive 의존성 없음. (단 P0-1로 기능 자체가 프로덕션 불능) |
| 3. 성공 Slack 금지·사람 행동 필요 시에만 | ⚠️ 대체로 ✅ | 발행 성공은 일일 다이제스트 1건(요약 성격). 반면 "사람이 지금 행동해야 하는" 인증서 영구 실패·발행일 차단은 무알림, PROVIDER_* 는 과알림(P2-5) — 방향 양쪽 모두 보정 필요 |
| 4. 병원별 부분 시각 커스텀(admin+온보딩 동일 필드, 브랜드색 1+램프) | ✅ | 온보딩 `ClinicVisualForm`과 profile 페이지가 동일 PATCH/필드 사용, `clinic-theme.ts` 단일색 램프 파생 |
| 5. 자율 운영이 API 낭비·확인 루프 금지 | ⚠️ | 생성·V0·발행은 준수. 인증서 false-FAILED(P1-4)가 수동 재검증 루프 유발 |
| 6. DNS 성공=단계5 완료·인증서 비동기·커스텀 저장 시 site_live 유지 | ✅ | `domain_verification.py` 87–117행, `domain_connect.py` 123–130행(site_live 불변), 백엔드에 `site_live=False` 경로 없음(QA 유틸 제외) |

메모(스펙 문서 정합): CLAUDE.md의 "자동 발행 완료 per-item Slack"과 "자료 추가 시
승인 stale" 두 조항은 #27이 의도적으로 대체했다(다이제스트 1건, 기존 승인 baseline
유지 + 신규 자료만 pending). 제품 결정이 맞다면 CLAUDE.md를 갱신해야 다음 웨이브가
스펙과 코드 사이에서 흔들리지 않는다.
