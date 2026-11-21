# 측정 키워드 30개 운영 반영 절차 (2026-11-21)

기준: [원 요청과 기준](../plans/2026-11-21-measurement-30-requirements.md), [설계](../plans/2026-11-21-measurement-30-design.md).
PR ① #378 측정 계약 → ② #379 A그룹 정확도 → ③ #380 30개 초안·어드민 → ④ #381 원장 보고서 → ⑤ #382 이 문서·한도 — 2026-11-21 통합 PR #391로 main `c0ae1adf`에 병합.
측정 당일(12/1) 점검은 [12/1 새벽 첫 실행 점검 목록](measurement-30-dawn-checklist.md).

**마감**: 12/1 00:15 KST 월간 측정(11월분) 전에 운영에 나가 있어야 한다. 매월 1일 00:15~09:00은 배포 금지이고, 그 밖에도 배치 시간대(22:15~02:00, 04·07·08·12·18·22시 앞뒤 45분)는 `deploy.sh`가 막는다. **11/27~28 낮**에 배포한다.

## 0. 한 쪽 체크리스트(12/1 전 운영 실행 순서)

| # | 언제 | 할 일 | 확인 |
|---|---|---|---|
| 1 | 배포 날(배치 시간대 밖) | env `COST_GUARD_MONTHLY_SOV_QUERIES=13500`(일 9,000 그대로) → `deploy.sh backend --accept-env-diff`·`deploy.sh admin` | readiness 통과, 마이그레이션 head가 `0101_add_query_target_set_roles` 이후(#391 병합 뒤 main head는 `0102_add_hospital_contract_ended_on`) |
| 2 | 배포 직후 | `measurement_set_rollout precheck` | 코호트 병원마다 `action=apply`, 고정 20·교체 10, `problems=[]` |
| 3 | 2 직후 | (필요하면) `show --ids=<id>`로 문장 확인 | 어색한 문장은 4 뒤 어드민에서 고친다 |
| 4 | 3 직후 | `measurement_set_rollout apply --confirm` | 병원마다 `action=applied`, `summary.fixed=20` |
| 5 | 4 직후 | `measurement_set_rollout precheck` 다시 | 전부 `skip_curated`, `curated_now=true`, `legacy_tracked=0` |
| 6 | 5 직후 | `ops_sql_report --only=q17` | 코호트 병원 모두 `curated=true`, 그룹 A5·B10·C8·D7 |
| 6b | 11/23(월) 아침(주간 등록 02:00 뒤) | `ops_sql_report --only=q17` | 코호트 밖이던 세 곳(유내과 `d6f6e0b0`·다올연합 `2438bb4f`·다대으뜸 `dfb24909`)이 `monthly_sov_cohort=true`이고 여전히 `curated=true`·고정 20·교체 10(등록은 30개 방식 병원의 역할을 다시 고르지 않는다). 아직 false면 q1의 `MONTHLY_SOV_COHORT_GAP` 사유를 본다 — 12/1 첫 틱(00:15)의 등록이 한 번 더 편입을 시도하지만, 그때 편입되면 11월 서비스 구간이 있는 병원만 11월분을 잰다 |
| 7 | 배포 다음 날~11/30 | 마케터: 콘텐츠 > 측정 키워드에서 문구 고치기·교체 10개 고르기(시즌 강조 키워드) | **운영 약속: 11/30 23:00 편집 마감.** 실제 동결은 1일 00:15 뒤 병원마다 첫 측정 실행이 측정표를 얼리는 그때다 — 그 전 저장은 이번 측정에, 그 뒤 저장은 다음 달에 쓰인다(병원마다 몇 분~수십 분 차이). 마감을 지켜야 어느 쪽인지 헷갈리지 않는다 |
| 8 | 11/30 18:45~21:30 | 워커 최소 인스턴스 1 → 2(`gcloud run services update reputation-worker --min-instances=2 …`) | 인스턴스 2대 |
| 9 | 11/30 저녁 | §4 확인 목록, 코호트 20곳 넘으면 일 한도 상향을 대표에게 묻기(올린다면 env는 11/30 22:15 전 배포, 아니면 12/1 00:00~00:15 운영센터 '오늘 일일 상한') | q17 마지막 확인 |
| 10 | 12/1 00:15~06:45 | [새벽 점검 목록](measurement-30-dawn-checklist.md) §1 — q17b(새벽 진행) | `answers_received` 합이 30분마다 약 900~1,000 늘고 `frozen_set30=true` |
| 11 | 12/1 07:15~09:00 | 보고서 확인(점검 목록 §1·§2) | 1면 B~D, P3 A행·정확도 줄, 부록 A 30개 |
| 12 | 12/1 09:00 뒤 | 워커 최소 인스턴스 2 → 1 | 인스턴스 1대 |

**로컬 리허설(2026-11-21)**: 로컬 테스트 DB에 운영과 같은 모양의 병원 3곳(내과·치과·한의원, 옛 15개 질문 11~14개, 코호트)을 만들어 2→3→4→5→6을 실제로 돌렸다. precheck 3곳 `apply`·문제 0 → `apply --confirm` 3곳 `applied`(곳마다 새 질문 39개 = 세트 30 + 후보 9, 옛 질문 11~14개는 측정에서만 빠짐) → 다시 precheck 3곳 `skip_curated`·`legacy_tracked=0` → 다시 apply 3곳 `skipped_curated` → q17 3곳 `curated=true`·고정 20·교체 10·A5·B10·C8·D7 → 월간 세트 등록(`register_tracking_set`) 3곳 유효·30개·바뀐 것 0. 옛 질문 문구("○○ 내과 병원 추천해줘")는 지침 3-1 문형과 달라 그대로 이어 쓴 질문은 0개였다 — 11월분은 어차피 질문 세트가 바뀐 첫 달이라 지난달과 비교하지 않는다.

## 1. 배포 전

1. PR ①~⑤를 순서대로 병합한다(②~⑤는 앞 PR 위에 쌓은 PR — 앞이 병합되면 base를 main으로 바꾼다). 병합 직전 `origin/main`의 마이그레이션 head를 확인한다 — ①은 `0101_add_query_target_set_roles`를 더한다. 다른 PR이 먼저 `0101`을 쓰면 번호를 올리고 `scripts/integrity_rehearsal/check.py`·`backend/tests/test_migration_chain_linearity.py`의 head도 함께 올린다.
2. 운영 env(정본은 운영 `reputation-api` 서비스 env — `deploy.sh`가 로컬 `.env.production`과 다르면 멈춘다)를 바꾼다.
   - `COST_GUARD_MONTHLY_SOV_QUERIES=13500` (9,000 → 13,500, 2026-11-21 사용자 승인)
   - `COST_GUARD_DAILY_SOV_QUERIES=9000` (그대로. 코호트가 20곳을 넘으면 올릴지 정한다 — 새벽 하루가 이 한도에 묶인다)
   - 선택: `SOV_MONTHLY_ANSWER_CONCURRENCY=6`(코드 기본값과 같다. 1이면 동시 답변을 끈다 — 롤백 스위치)
   - 로컬 `.env.production`을 운영 env로 다시 만든 뒤 위 값을 고치고 `--accept-env-diff`로 배포한다(`removed` 키가 없는지 확인).
3. 워커 최소 인스턴스는 **12/1 새벽 창에만 2로** 올린다(2026-11-21 결정 — §4의 11/30 저녁에 올리고 12/1 09:00 뒤 1로 되돌린다). 12/1은 새벽 창의 첫 실제 실행이다(11/1은 10월 측정표가 이미 끝나 바로 끝났다).

   **부하 계산**(10/30~31 월말 측정 실측: 칸당 약 22초, 답변 + 판정). 병원 16곳 × 30문항 × 2플랫폼 × 5반복 = 4,800칸(ChatGPT 2,400 · Gemini 2,400).
   - ChatGPT: 프로세스당 동시 6(`SOV_MONTHLY_ANSWER_CONCURRENCY`, 그 안의 공급자 풀 한도는 10). 칸당 약 30초로 보면 2,400 × 30 ÷ (6 × 프로세스 수).
   - Gemini: 같은 키의 짧은 burst가 429를 내서 **프로세스당 1개로 직렬화**한다(`sov_engine._pool_limit`, 기존 계약). 칸당 약 12초로 보면 2,400 × 12 ÷ 프로세스 수 — 이쪽이 창 길이를 정한다.
   - **정정(2026-11-21 저녁)**: 병합된 코드는 병원마다 한 실행이 미완료 30칸(한 페이지)만 사고 다음 페이지를 **다음 틱(:15·:45)**까지 기다렸다 — 병원당 틱 10번, 가장 빨라도 04:45. #396이 '실패 없이 끝난 페이지 뒤 남은 칸은 120초 뒤 잇기'로 고쳤다(실패·비용 보류 페이지는 여전히 다음 틱). 아래는 #396 기준이다.
   - 한 페이지(30칸) 약 3~5분(Gemini 15칸 직렬 × 약 12초가 정한다). 16곳이면 틱마다 약 64 프로세스·분.
   - 워커 1대(프로세스 2): 페이지 160개 × 약 4분 ÷ 2 ≈ 5시간 → 05:30 안팎, 마감 여유 적음.
   - 워커 2대(프로세스 4): ≈ 2.5~3시간 → 03:00 안팎. 01·04시 콘텐츠 스윕이 같은 프로세스를 쓰는 몫과 재시도의 여유다.
   - #396 없이 12/1을 맞으면 위 '틱 10번' 제약이 되살아난다 — 11/30 저녁 배포 리비전을 확인한다(§4).
   - 순수 공급자 시간(참고): 워커 1대 Gemini 14,400초 ≈ 4시간, 2대 약 2시간. 동시 답변 없이 차례로 사면(옛 코드) 워커 1대로 약 14.7시간이었다.

## 2. 배포

```bash
./scripts/deploy.sh backend --accept-env-diff --override-reason="측정 키워드 30개"
```

```bash
./scripts/deploy.sh admin --override-reason="측정 키워드 30개"
```

- `backend`가 마이그레이션 `0101`(추가형, 칼럼 2개 + 검사 제약)을 먼저 돌린다.
- Site는 바뀌지 않는다.

## 3. 배포 직후 — 병원별 30개 등록

모두 migrate Job에서 돌린다(운영 DB는 사설 IP). gcloud `--args`는 쉼표로 인자를 나누므로 인자마다 따로 넘긴다.

1. **읽기 전용 확인** — 지금 상태와 초안 요약(병원마다 JSON 한 줄):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.measurement_set_rollout,precheck
   ```

   확인할 것: `curated_now=false`, `draft.A.fixed=5`, 고정 합 20·교체 합 10, `problems=[]`, `action=apply`. 문제가 있는 병원은 `show --ids <id>`로 문장을 보고 어드민에서 고치거나 그 병원만 빼고 적용한다.

2. **적용** — 코호트 병원 전부(이미 30개 방식인 병원은 건너뛴다):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.measurement_set_rollout,apply,--confirm
   ```

   - 대상: 코호트 병원 + 아직 코호트는 아니지만 측정 기록이 있어 다음 등록(월요일·1일)에 자동 편입될 ACTIVE 병원(`cohort=false`로 표시). 빼면 다음 등록이 그 병원을 옛 15개로 편입한다.
   - 같은 문구의 옛 질문은 다시 쓴다(측정 이력 보존). 세트에서 빠진 옛 질문은 측정에서만 빠지고 글 주제로 남는다.
   - 병원마다 커밋하고 감사 로그(`apply_measurement_set`, actor `system:measure-30-draft`)를 남긴다. 거절된 병원은 `action=rejected`와 사유가 나온다.
   - 특정 병원만: `--ids=a,b`(gcloud에서는 `--ids=<uuid>` 한 토큰). 초안으로 다시 만들기: `--replace`(마케터가 고친 세트를 덮어쓴다 — 쓰지 않는다).

3. **확인** — 세트 준비 상태(q17):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.ops_sql_report,--only=q17
   ```

   코호트 병원(`monthly_sov_cohort=true`)과 측정 기록이 있어 다음 등록에 편입될 병원이 모두 `curated=true`, `fixed`가 15~20(초안은 20), `rotating`이 0~10(초안은 10), `legacy_tracked=0`이어야 한다.

4. **마케터 안내** — 어드민 병원 > 콘텐츠 > '측정 키워드 30개' 카드 > 측정 키워드 화면에서 그룹별 질문을 보고 고치고 교체 10개를 고른다. 운영 약속은 11/30 23:00까지 저장이다. 실제로는 12/1 00:15 뒤 병원마다 첫 측정 실행이 그 병원 측정표를 얼리고, 얼린 뒤 저장은 다음 측정부터 쓰인다(그 병원 측정표가 얼었으면 화면이 알려 준다). 시즌 강조 키워드(예: 연세속시원·서울W 11~12월 내시경)는 교체 질문으로 넣는다.

## 4. 11/30 저녁 최종 확인(측정 전날)

- [ ] q17: 코호트 병원 모두 `curated=true`, 모양 정상. 마케터가 고친 병원도 고정 15~20·교체 ≤10.
- [ ] 운영 env: `COST_GUARD_MONTHLY_SOV_QUERIES=13500`, `COST_GUARD_DAILY_SOV_QUERIES=9000`(또는 결정한 값). readiness Job 통과.
- [ ] 비용 사용량: 운영센터 > 비용 패널에서 SoV 일·월 한도가 12/1 새벽 소요(병원 수 × 약 330)를 덮는지 본다(readiness `facts`에는 SoV 사용량이 없다 — content lane만 있다). 일·월 카운터는 KST 자정에 새로 시작하므로 12/1은 둘 다 0부터다.
- [ ] Beat 스케줄에 `monthly-sov-measurement`(1일 00:15~06:45 30분마다)가 있다(`backend/app/core/celery_app.py`).
- [ ] 워커 리비전이 ①(`SOV_MONTHLY_ANSWER_CONCURRENCY`)과 #396(페이지 뒤 120초 이어가기)을 포함한다.
- [ ] **워커 최소 인스턴스 1 → 2**(배치 시간대 밖, 11/30 18:45~21:30 사이). `gcloud run services update reputation-worker --min-instances=2 --project mso-platform-481505 --region asia-northeast3`. 12/1 09:00 뒤 1로 되돌린다(`--min-instances=1`).
- [ ] **일 한도 경고**: 코호트(q17의 `monthly_sov_cohort=true`)가 **20곳을 넘으면** 일 한도 9,000을 올릴지 대표에게 묻는다 — 새벽 하루가 일 한도에 묶이고 30문항 병원 1곳이 약 330단위다(24곳 안팎에서 막힌다). 올리는 방법은 둘이다: env `COST_GUARD_DAILY_SOV_QUERIES`(배포 — 11/30 22:15 전에), 또는 12/1 00:00~00:15에 운영센터 '오늘 일일 상한'(그날 하루만, 기본값의 2배까지). 운영센터 상향은 그날 날짜 키에 걸리므로 11/30에 누르면 12/1에 적용되지 않는다.
- [ ] 12/1 00:15~09:00 배포·env 변경 금지.

## 5. 12/1 확인

시각별 조회·정상 기준·막힘별 원인은 [12/1 새벽 첫 실행 점검 목록](measurement-30-dawn-checklist.md)에 있다. 요약:

- 00:00~00:15 비용 패널(필요하면 오늘 상한 상향) → 00:45부터 q17b로 진행(워커 2대면 30분마다 합 약 900~1,000칸, 03:00 안팎 끝) → 07:00 마감.
- 비용 보류는 사고 종류가 아니라 `MONTHLY_SOV_MEASUREMENT_FAILED` 사고·실행의 오류 코드 `MONTHLY_SOV_COST_GUARD_BLOCKED`다(q15b). 상한을 올리면 다음 틱이 그 병원을 다시 연다.
- 07:15~08:45 보고서 최종화(q15), 원장 보고서 실물 확인, 09:00 최종 판정.

## 6. 되돌리기

- **동시 답변만 끄기**: `SOV_MONTHLY_ANSWER_CONCURRENCY=1`(env만, 코드 롤백 없음).
- **30개 세트를 옛 방식으로**: 그 병원 질문의 `set_role`을 모두 지우면(어드민 저장으로는 할 수 없다 — DB 작업) 다음 등록에서 옛 10~15개 자동 선택으로 돌아간다. 이미 얼린 이번 달 측정표는 그대로다.
- **코드 롤백**: 마이그레이션 `0101`은 추가형이라 옛 코드가 칼럼을 무시하고 읽는다. 다만 옛 코드는 30개 세트를 모르고 월간·주간 등록이 `in_tracking_set`을 15개로 다시 고른다(마케터가 고른 세트의 측정 플래그가 바뀐다 — 칼럼은 남는다). 옛 코드는 A그룹 질문을 LOCAL로 대표 노출률에 넣는다.
