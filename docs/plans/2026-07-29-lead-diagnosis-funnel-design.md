# 리드 진단 퍼널 — 엔지니어링 설계 (2026-07-29 · rev2·rev3 2026-07-30)

> 대상: PRD `REPUTATION-AI-DIAGNOSIS-FUNNEL-PRD-2026-07.md` §12의 엔지니어링 설계 6건
> (12-3 · 12-4 · 12-5 · 12-6 · 12-7 · 12-10). 12-9(성공 판정 계측)는 대표 지시로 보류 — 다루지 않는다.
>
> **rev2 (2026-07-30) — 대표 지시로 규칙 4개가 바뀌었고, 그 결과 설계의 절반이 사라졌다.**
> ①1회 제한 기준을 **전화번호 + 이메일 이중 잠금**으로 ②**하루 20건 선착순**(마케팅 장치 겸 예산 상한)
> ③접수 전 **정보 확인 모달** ④두 단(리드마그넷 / 운영 서비스) 분리 명시.
> 없어진 것: 이메일 확인 링크, 대기열(`DEFERRED`), 30일 캐시, outbox, 별도 원화 비용 가드. 근거는 §2·§3.
>
> **rev3 (2026-07-30) — 원가 절감.** ①**질의 3개 고정**(§2-2) ②**질의 단위 공유 캐시**(§2-6).
> 건당 최악 2,498원 → **1,499원**, 캐시 적중 시 **약 5원**. 하루 최악 52,000원 → **30,000원**.
> **발견: PRD §6 원가표가 `gpt-5-mini` 기준으로 낡아 있었다** — rev4에서 `gpt-5.6-luna`로 이전했으나
> 표를 갱신하지 않아 실제 원가를 8% 과소 계상하고 있었고, 5질의 진단은 상한까지 여유가 4%뿐이었다.

---

## 0. 먼저 — 이 시스템은 두 단이다

지금까지 논의가 계속 엉킨 이유는 두 단의 경계가 문서에 그려진 적이 없어서다. 먼저 긋는다.

| | **1단 — 리드마그넷** | **2단 — 운영 서비스** |
|---|---|---|
| 누구를 위한 것 | 아직 고객이 아닌 원장 | 계약한 병원 |
| 무엇을 하나 | 무료로 한 번 재보고, 일부 가린 리포트를 보낸다 | 매달 재고, 콘텐츠를 만들고, 리포트를 낸다 |
| 목적 | **문의를 만든다** | **AI 노출을 실제로 개선한다** |
| 표면 | 랜딩 + 신청 폼 (`/ai-diagnosis`) | Admin + 병원별 콘텐츠 허브 (`/site`) |
| 데이터 | `sales_leads`, `lead_*` 테이블 | `hospitals`, `query_matrix`, `sov_records`, `content_items` … |
| 큐 | `leadgen` | `sov` · `content` · `reports` |
| 물량 | **하루 20건** | 병원 수 × 월 1회 측정 + 주 1회 모니터링 |
| 사람의 개입 | 없음 (자동) | AE가 승인·검토 (STEP 5·6·7) |

**두 단이 공유하는 것은 딱 하나 — 측정 엔진(`sov_engine`)이다.**
같은 모델·같은 프롬프트·같은 판정 기준으로 재야 무료 진단 숫자와 첫 유료 리포트 숫자가 어긋나지 않는다
(PRD §7-4). 그 외에는 **아무것도 공유하지 않는다** — 데이터 모델도, 상태값도, 예산도, 리포트 템플릿도.

**두 단이 만나는 지점도 딱 하나 — 전환이다.**
`SalesLead.converted_hospital_id`가 채워지는 순간 1단이 끝나고 2단이 시작된다.
이때 진단에 쓴 지역·키워드·진료과를 병원 프로파일 초기값으로 이월한다(PRD F7-3).
그 전에는 1단이 `hospitals` 테이블을 **읽지도 쓰지도 않는다.**

> 이 문서는 **1단만** 설계한다. 2단은 이미 돌아가고 있고 별도 PRD가 있다.

---

## 1. 사람이 보는 순서

```
랜딩 (/ai-diagnosis)
   "오늘 남은 자리 7 / 20"          ← 실제 카운터. 조작하지 않는다
        ↓ 폼 작성
   [정보 확인 모달]                  ← 입력값 전부를 다시 보여주고
   "이 정보가 정확한가요?"             "우리 병원은 딱 한 번만 신청할 수 있습니다"
        ↓ 확인
   접수 완료 → 자리 1칸 소비
        ↓  (사용자는 여기서 기다리지 않는다)
   상태 페이지로 이동 (북마크 가능한 주소)
        ↓  15분 이내
   측정 → 리포트 생성 → 이메일 발송 + 상태 페이지에도 표시
        ↓
   원장이 리포트를 보고 문의 → 2단으로 넘어감
```

---

## 2. 남용·비용 방어와 원가 구조

대표 지시의 핵심은 여기다. 장치마다 막는 것이 다르다.

| | 무엇을 막나 |
|---|---|
| §2-1 하루 20건 선착순 | 예산 폭주 (+ 마케팅 장치) |
| §2-2 질의 3개 고정 | 건당 원가가 상한에 붙는 것 |
| §2-3 전화번호 + 이메일 이중 잠금 | 남의 병원 신청 · 반복 신청 |
| §2-4 AE 해제 | §2-3이 리드 차단 장치가 되는 것 |
| §2-5 확인 모달 | 오타 (이중 잠금 아래에선 치명적) |
| §2-6 질의 단위 공유 캐시 | **중복 지출** — 가장 큰 레버 |
| §2-7 연타 방지 | 중복 행 |

### 2-1. 하루 20건 선착순 — 예산 상한이자 마케팅 장치

| 항목 | 값 |
|---|---|
| 하루 자리 | **20건** (KST 00:00 리셋) |
| 소진 시 | 접수를 **거부**한다. 대기열에 쌓지 않는다 |
| 랜딩 표시 | `오늘 남은 자리 N / 20` — 실제 카운터 |

**대기열(`DEFERRED`)을 만들지 않는 이유**: "선착순 마감"과 "대기하면 언젠가 됩니다"는 양립하지 않는다.
마감이 곧 희소성 메시지다. 대기열을 두면 마케팅 효과가 사라지고 적체 관리 부담만 남는다.

**이 상한 하나가 원화 비용 가드를 대체한다.**
질의 3개 고정(§2-2)에서 진단 1건이 1,499원이므로 **하루 최악 30,000원**으로 수학적으로 묶인다
(캐시가 적중하면 그보다 훨씬 낮다 — §2-6).
따라서 `cost_guard`에 `leadgen` 카테고리를 만들지 않는다 — 자리 수가 곧 예산이다.
Redis 의존도, fail-open/closed 논쟁, 원화 환산 로직이 통째로 필요 없어진다.

**자리 배정은 DB가 한다** (Redis 아님):

```sql
INSERT INTO lead_diagnoses (..., slot_date, slot_no)
SELECT ..., :today, COALESCE(MAX(slot_no), 0) + 1
  FROM lead_diagnoses WHERE slot_date = :today;
-- UNIQUE (slot_date, slot_no) 가 동시 삽입 경합을 잡는다 → 위반 시 최대 3회 재시도
```

`slot_no > 20`이면 롤백하고 마감 응답. 하루 20건에서 경합은 사실상 없고, DB가 진실의 원천이라
카운터와 실제 행 수가 어긋날 수 없다.

**자리는 "신청 1건"에 붙지 "실행 1회"에 붙지 않는다.** 측정이 실패해 AE가 재실행해도 자리를 다시 쓰지 않는다.
반대로 실패했다고 자리를 환불하지도 않는다 — 환불하면 카운터가 흔들리고, AE가 재실행하면 되는 일이다.

### 2-2. 질의 3개 고정 — 무료 진단의 규모를 못 박는다

PRD는 "질의 3~5개(매핑 결과에 따름)"였다. 5개가 걸리면 2,498원으로 상한(2,600원)까지 4%만 남아
재시도 몇 번이면 넘긴다. **무료 진단은 3개로 고정한다.**

| 슬롯 | 질의 | 근거 |
|---|---|---|
| 1 | **진료과형** — `{지역} 근처 {진료과} 병원 추천해줘` | 폼의 진료과를 그대로 쓴다. 조합이 제한적이라 **캐시 적중률이 가장 높다**(§2-6) |
| 2 | 키워드 1로 시술형·증상형 (분류 실패 시 탐색형) | PRD F2 매핑 규칙 |
| 3 | 키워드 2로 동일 | 키워드가 1개뿐이면 키워드 1의 **다른 유형**으로 채운다 |

세 슬롯 모두 `{지역}`을 포함한다(PRD F2-2). 항상 3개이므로 계획 측정 수가 **3 × 2 × 3 = 18콜**로 고정된다.

> 유료 V0는 15질의다(rev4에서 5→15 확대). 무료 3개 / 유료 15개의 차이가 명확해 업셀 논거가 되고,
> **플랫폼별 숫자는 같은 조건이라 그대로 비교된다**(PRD §7-4).

### 2-3. 전화번호 + 이메일 **이중 잠금** — 한 병원 딱 한 번

| 잠금 | 무엇을 식별하나 | 재사용 시 |
|---|---|---|
| **대표번호** | 병원 | 거부 |
| **신청자 이메일** | 사람 | 거부 |

**둘 중 하나라도 이미 쓰였으면 거부한다.** 우회하려면 새 번호와 새 메일이 **동시에** 필요하다.
전화번호만 잠그면 메일만 바꿔 남의 병원을 계속 신청할 수 있고, 이메일만 잠그면
한 사람이 여러 병원을 훑을 수 있다. 둘을 함께 걸어야 둘 다 막힌다.

**기간 제한이 아니라 영구다.** "24시간에 1회"가 아니라 그냥 1회다.

저장은 **해시로만** 한다 — `sha256(정규화값 + pepper)`.

- 원문은 `sales_leads`에 있고 180일 뒤 파기된다(개인정보보호법 제21조).
- 해시는 파기 후에도 남는다. 180일이 지났다고 두 번째 무료 진단을 주는 것이 아니기 때문이다.
- 해시만으로 잠금 판정이 가능하므로 원문 보관 기간을 늘릴 필요가 없다.

정규화:
- 전화번호 — 숫자만 남긴다. `+82`·`82` 국가번호는 `0`으로 치환. `02-123-4567` = `021234567`
- 이메일 — `trim` → `lower` → 로컬파트의 `+태그` 제거

> **원래 PRD F1-6은 "병원당 1회"를 금지했다.** 제3자가 먼저 신청해 원장의 기회를 소진시키는
> 리드 차단 장치가 된다는 이유였다. 대표 지시로 이 판단을 뒤집되, 그 위험은 **AE 해제**로 없앤다.

### 2-4. AE 해제 — 잘못 잠긴 것을 1분 안에 푼다

Admin에서 AE가 잠금을 해제할 수 있다. 이것이 §2-3이 리드 차단 장치가 되지 않게 막는 유일한 장치이므로
**선택 기능이 아니라 §2-3과 한 몸이다.**

```
lock_released_at    timestamptz NULL
lock_released_by    String(100) NULL      -- AE 계정
lock_release_reason String(200) NULL      -- 감사 추적
```

해제하면 부분 유니크 인덱스에서 빠져 같은 번호·메일로 다시 신청할 수 있다.
원장이 "누가 이미 우리 병원으로 신청했다는데요"라고 하면 AE가 즉시 푼다.

### 2-5. 확인 모달 — 오타와 남의 병원을 함께 막는다

제출 버튼을 누르면 바로 접수하지 않고, **입력값 전부를 다시 보여준다.**

```
┌─────────────────────────────────────────┐
│  이 정보가 정확한가요?                    │
│                                         │
│  병원명      장편한외과의원                │
│  지역        수서역                       │
│  대표번호    02-123-4567                  │
│  진료과      외과                         │
│  키워드      대장내시경, 치질              │
│  담당자      홍길동 · 010-0000-0000       │
│  이메일      hong@example.com             │
│                                         │
│  ⚠ 한 병원당 딱 한 번만 신청할 수 있습니다. │
│    대표번호와 이메일 모두 재신청이          │
│    불가능하니 우리 병원 정보가 맞는지        │
│    꼭 확인해 주세요.                       │
│                                         │
│      [ 수정하기 ]  [ 이 정보로 신청 ]      │
└─────────────────────────────────────────┘
```

- **이 모달이 1회 제한 고지의 본체다.** PRD F1-6이 "작게 적으면 효과가 없다"고 한 그 자리다.
  폼 상단 문구는 보조이고, 여기서 마지막으로 못을 박는다.
- 이메일 오타는 흔한 도메인 오탈자를 감지해 교정을 제안한다(`gmail.co` → `gmail.com`).
  이중 잠금 아래에서 오타 하나가 병원의 유일한 기회를 태우므로, 이 작은 보정이 값을 한다.
- 브라우저 `alert()`가 아니라 페이지 안의 모달로 만든다 — 값을 표로 보여줘야 하고,
  `alert()`는 서식이 불가능하며 자동화 도구에서 브라우저를 멈춘다.

### 2-6. 질의 단위 공유 캐시 — 원가를 300배 줄이는 지점

**우리는 질의에 병원 이름을 넣지 않는다**(PRD F1-1). 그래서 이런 일이 성립한다.

> `수서역 근처 내과 병원 추천해줘`
> — 이 질문은 **신청한 병원이 어디든 똑같고, AI 답변도 똑같다.**

병원마다 달라지는 것은 **그 답변에서 이 병원 이름이 나왔는지 보는 판정 단계뿐**이고,
판정은 콜당 0.26원(답변 모델의 1/370, PRD §2-1)이다.

| | 비용 |
|---|---:|
| 첫 번째 병원 (수서역 · 내과) | 1,499원 |
| 같은 질의를 쓰는 두 번째 병원 | **약 5원** (판정 18콜만 다시) |

> **rev6에서 폐기한 "30일 캐시"와 다른 것이다.** 그건 **병원 단위** 캐시였고
> 전화번호 영구 잠금 아래에서 적중 경로가 없어 폐기했다(§5-1).
> 이것은 **질의 단위** 캐시다. 서로 다른 병원 사이에서 적중한다.

#### 캐시 키가 무엇을 포함해야 하는가

```
query_hash = sha256( 정규화된_질의_텍스트 | platform | requested_model | prompt_version )
```

`requested_model`과 `prompt_version`이 키에 **반드시 들어가야 한다.**
모델을 바꾸거나(luna 이전 같은) 프롬프트를 바꾸면 옛 답변이 자동으로 무효가 된다.
이게 없으면 PRD §2-1의 "핀 고정 + 의도적 이전"이 캐시 뒤에서 조용히 깨진다.

반복 회차도 키의 일부다 — 같은 질의를 3회 반복하는 것이 "N번 중 M번"의 근거이므로,
회차별로 서로 다른 답변을 각각 캐시한다(`UNIQUE (query_hash, repeat_no)`).

**질의 텍스트 정규화는 앞뒤 공백 제거와 연속 공백 축소까지만 한다.**
"수서역"과 "수서동"을 같은 키로 묶지 않는다 — 실제로 답변이 다르므로 묶으면 틀린 숫자를 판다.

#### 신선도 — 7일, 그리고 측정 일시를 정직하게 적는다

| 항목 | 값 |
|---|---|
| 캐시 수명 | **7일** (`expires_at`) |
| 만료분 | 배치가 삭제. 되살리지 않는다 |

**캐시된 답변의 측정 일시를 오늘로 적지 않는다.** 결과 행에 원본 `measured_at`을 그대로 복사하고,
리포트는 질의별 측정 일시를 표기한다. PRD F5-1이 이미 측정 일시 공개를 요구하므로 숨길 것이 없고,
"다른 병원도 같은 질문을 받았고 우리는 같은 답변을 본다"는 사실 자체가 방법론 공개 포지셔닝과 맞는다.
테스트로 고정한다(T-15).

#### 캐시는 API 호출만 아낀다 — 데이터는 각자 자기 사본을 갖는다

`lead_diagnosis_results`는 캐시를 **FK로 참조하지 않고 `raw_response`를 복사**한다.

- 참조하면 7일 뒤 캐시가 사라질 때 결과 행이 근거를 잃어 **리포트 재생성이 불가능**해진다.
- 캐시는 여러 병원이 공유하므로 **한 병원의 파기로 지울 수 없다** — 참조 구조에서는 파기가 성립하지 않는다.
- 저장 비용은 무시할 수준이다(하루 20건 × 18행 × 약 10KB ≈ 3.6MB/일).

대신 결과 행에 `answer_source`(`LIVE` | `CACHED`)를 기록한다.
어떤 측정이 캐시에서 왔는지 감사할 수 없으면 원가 검증도, 신선도 검증도 못 한다.

캐시 테이블(`lead_query_answers`) 자체는 **개인정보가 아니다** — 질의에 신청자 이름·연락처가
절대 들어가지 않으므로(PRD §6) 파기 파이프라인의 대상이 아니고, TTL로만 관리한다.

### 2-7. 연타 방지 — 잠금이 곧 디바운스다

같은 사람이 제출 버튼을 두 번 누르면 두 INSERT가 경합하는데, **이메일 잠금 유니크 인덱스가 두 번째를 튕긴다.**
API는 이것을 에러로 처리하지 않고 **기존 행을 찾아 200으로 응답**한다(같은 상태 페이지로 보낸다).

별도의 `request_fingerprint` 컬럼을 두지 않는다 — 영구 잠금이 이미 그 일을 한다.

---

## 3. 12-3 — 이메일 확인 링크를 두지 않는다

원래 PRD F1-4는 "발송된 링크를 클릭하기 전에는 측정을 시작하지 않는다"였다.
이 단계를 **없앤다.** 그것이 하던 두 가지 일을 §2가 더 잘하기 때문이다.

| 확인 링크가 하던 일 | 대신 무엇이 하나 |
|---|---|
| 비용 폭주 방어 (가짜 신청으로 측정 유발) | **하루 20건 상한** — 수학적으로 52,000원에 묶인다 |
| 우리를 스팸 발송 도구로 쓰는 것 방어 | 같은 상한 — 하루 최대 20통, 병원당 평생 1통이면 릴레이가 성립하지 않는다 |
| 신청자 정보의 정확성 | **확인 모달**(§2-5) + 오타 도메인 교정 |

**얻는 것**: 확인 클릭 단계는 통상 리드의 30~50%를 잃는다. 리드를 만드는 것이 이 퍼널의 존재 이유이므로,
그것을 지키지 못하는 방어 장치는 값을 못 한다.

**같이 사라지는 것**: 확인 토큰 테이블, 만료·재발송 정책, 메일 스캐너 오탐 대응(GET/POST 분리),
`verification_status` 축, 그리고 "확인 완료 시점"을 기준으로 삼던 SLA 기점 문제(§7).

**리포트가 잘못된 주소로 갈 위험은 남는다.** 두 가지로 완화한다.
1. 접수 직후 **상태 페이지 주소**를 화면에 보여준다. 메일이 안 와도 결과를 볼 수 있는 경로가 하나 더 있다.
2. 리포트에는 경쟁사 이름도 지역 순위도 없다(PRD F5-2). 남에게 전달돼도 비교 정보가 없다.

> **법무 확인 항목으로 올린다.** PRD F5-5는 의료법 제56조 위험 통제 근거의 하나로
> "F1-4 이메일 소유 확인"을 들고 있었다. 그 항목이 없어졌으므로, 법무 검토 시
> **"확인 모달 + 전화번호 잠금 + 하루 20건 + 비교 콘텐츠 없음"으로 대체 가능한지**를 함께 묻는다.
> 법무가 이메일 소유 확인을 요구하면 §3을 되돌린다 — 그때 필요한 설계는 이 문서의 git 이력에 남아 있다.

---

## 4. 12-4 — 상태 모델 분리

### 4-1. 왜 단일 `status`가 깨지나

PRD F6-4의 나열(`QUEUED / RUNNING / DONE / FAILED / DEFERRED / PARTIAL / REPORT_READY / DELIVERY_FAILED / EXPIRED`)은
**서로 직교하는 축들을 한 컬럼에 눌러 담은 것**이다. `PARTIAL`인 결과로 리포트를 만들면 값이
`REPORT_READY`로 덮여 "일부 측정이 실패했다"는 사실이 사라진다. 그 리포트가 곧 원장에게 간다.

### 4-2. 3축 분리

| 축 | 컬럼 | 값 |
|---|---|---|
| 측정 실행 | `execution_status` | `PENDING` · `RUNNING` · `SUCCEEDED` · `PARTIAL` · `FAILED` |
| 리포트 생성 | `report_status` | `PENDING` · `BUILDING` · `READY` · `BLOCKED` · `PURGED` |
| 전달 | `delivery_status` | `PENDING` · `SENDING` · `SENT` · `FAILED` |

rev1의 4축에서 `verification_status`가 §3으로 사라졌고, `execution_status`에서
`DEFERRED`(대기열 없음, §2-1) · `EXPIRED`(대기가 없으니 만료도 없다) · `CACHED`(§5-3)가 빠졌다.

### 4-3. 불변식

```
INV-1  report_status   ≠ 'PENDING'  ⟹  execution_status ∈ {SUCCEEDED, PARTIAL}
INV-2  delivery_status ≠ 'PENDING'  ⟹  report_status ∈ {READY, PURGED}
INV-3  slot_no BETWEEN 1 AND 20  AND  UNIQUE (slot_date, slot_no)
```

INV-1·2가 "측정 없이 리포트", "리포트 없이 발송"을 스키마 수준에서 불가능하게 만든다.
`delivery_status`가 `PURGED` 이후에도 진행 가능한 이유는 재발송 시도가 파기 뒤에 들어올 수 있어서다(410 응답).

### 4-4. `PARTIAL` / `FAILED`는 개수로 정의한다

"느낌상 일부 실패"로 두면 리포트 생성 게이트가 흔들린다.
`lead_diagnosis_results`의 `measurement_status='SUCCESS'` 개수로만 판정한다.

계획 측정 수 = 질의 수 × 플랫폼 2 × 반복 3.

| 상태 | 조건 |
|---|---|
| `SUCCEEDED` | 모든 (플랫폼 × 질의)가 계획된 반복 횟수만큼 성공 |
| `PARTIAL` | **두 플랫폼 각각** 성공 ≥ 1이지만 계획에 미달 |
| `FAILED` | **어느 한 플랫폼이라도 성공 0** |

효과: 리포트 생성 게이트가 `execution_status ∈ {SUCCEEDED, PARTIAL}` **한 줄**로 끝나고,
플랫폼 하나가 통째로 죽은 진단은 리포트가 만들어지지 않는다.
PRD F3-5는 플랫폼별 분모 표기를 요구하는데, 분모가 0인 칸을 인쇄할 방법이 없기 때문이다.

**부분 실패는 소실되지 않는다.** `execution_status='PARTIAL'`이 남고, 실패 사유는 건별로
`lead_diagnosis_results.failure_reason`에 있으며, 리포트는 플랫폼별 `성공/계획`을 인쇄한다.

### 4-5. 전이표

**execution**

| from | to | 트리거 | 비고 |
|---|---|---|---|
| `PENDING` | `RUNNING` | 폴러의 claim UPDATE 성공 | `execution_attempts += 1` |
| `RUNNING` | `SUCCEEDED`·`PARTIAL`·`FAILED` | 측정 종료 후 §4-4 판정 | |
| `RUNNING` | `PENDING` | 리스 만료 (§6-2) | 워커 사망 회수 |
| `FAILED` | `PENDING` | AE 수동 재시도 | **자리를 다시 쓰지 않는다** |

`execution_attempts ≥ 3`이면 `RUNNING` 재진입을 막고 `FAILED`로 종결 + Slack.

**report**

| from | to | 트리거 |
|---|---|---|
| `PENDING` | `BUILDING` | 렌더 claim |
| `BUILDING` | `READY` | artifact 커밋 (§6) |
| `BUILDING` | `PENDING` | 렌더 실패, 시도 < 3 |
| `BUILDING`·`PENDING` | `BLOCKED` | 렌더 3회 실패 또는 `execution_status='FAILED'` → Slack |
| `READY` | `PURGED` | 파기 (§6-4) |

**delivery**

| from | to | 트리거 |
|---|---|---|
| `PENDING` | `SENDING` | delivery 행 삽입·커밋 (§5-3) |
| `SENDING` | `SENT` | Resend 성공 + 커밋 |
| `SENDING` | `SENDING` | 스윕 재시도 (같은 `Idempotency-Key`) |
| `SENDING` | `FAILED` | 3회 실패 또는 24h 경과 (§5-4) → Slack |

### 4-6. 재시도 정책

| 대상 | 최대 | 간격 | 소진 시 |
|---|---:|---|---|
| 공급자 호출 (질의 1회) | 3 | tenacity 지수 백오프 (기존) | 그 result 행만 `FAILED` |
| 측정 실행 (진단 1건) | 3 | 폴러 재수확, 즉시 | `execution_status='FAILED'` + Slack |
| 리포트 렌더 | 3 | 폴러 재수확, 즉시 | `report_status='BLOCKED'` + Slack |
| 메일 발송 | 3 | 5분 · 30분 · 4시간 | `delivery_status='FAILED'` + Slack |

**DLQ를 별도 큐로 만들지 않는다.** `FAILED`·`BLOCKED` 행 목록이 DLQ다.
하루 20건 규모에서 죽은 편지함은 Admin의 SQL 한 줄이고, AE가 그 화면에서 재시도한다.

---

## 5. 12-5 / 12-6 — 멱등성과 복구

### 5-1. 세 가지 중복을 각각 다르게 막는다

| 무엇 | 장치 | 위치 |
|---|---|---|
| 같은 병원·같은 사람의 재신청 | 전화번호·이메일 **해시 부분 유니크 인덱스** | §2-3 |
| 제출 버튼 연타 | 위와 동일 — 두 번째를 튕기고 기존 행 반환 | §2-7 |
| 태스크 중복 실행 (재배달) | **조건부 UPDATE claim** | §5-2 |

**30일 캐시는 만들지 않는다.** PRD F6-2는 같은 병원이 30일 내 재신청하면 기존 결과를 재발송하도록 했는데,
전화번호 영구 잠금 아래에서는 **같은 병원이 두 번 신청할 수 없다.** 캐시가 적중할 경로가 없다.
`subject_fingerprint`·`source_diagnosis_id`·`CACHED` 상태를 전부 뺀다.

### 5-2. 태스크 멱등성 — 키가 아니라 조건부 UPDATE다

Celery는 `task_id`로 중복 실행을 막아주지 않는다. 이 프로젝트는 `task_acks_late=True`라
재배달이 정상 동작이므로 더욱 그렇다. 실행 소유권은 DB에서 잡는다
(기존 V0 클레임 `_v0_claim_is_alive`와 같은 계열).

```sql
UPDATE lead_diagnoses
   SET execution_status   = 'RUNNING',
       running_since      = now(),
       execution_attempts = execution_attempts + 1
 WHERE id = :id
   AND execution_status = 'PENDING'
   AND execution_attempts < 3
```

`rowcount = 0`이면 다른 워커가 가져갔거나 시도가 소진된 것이므로 태스크는 **조용히 종료**한다.
리포트 렌더·발송도 같은 형태를 쓴다.

### 5-3. dual-write ① — DB 커밋 후 브로커 publish 실패

**dual-write를 없앤다.** 브로커를 진실의 원천으로 쓰지 않는다.

```
[접수] → DB 커밋 (자리 배정 + execution_status='PENDING')
       → celery publish   ← best-effort. 실패해도 삼킨다.

[1분마다] drain_lead_diagnoses (beat)
       → execution_status='PENDING' 행을 created_at ASC로 집어 발행
```

publish가 실패해도 **최대 60초 뒤 폴러가 같은 행을 집는다.**
복구할 outbox가 없는 이유는 잃어버릴 수 있는 쓰기가 하나뿐이기 때문이다.
§5-2의 claim이 폴러와 즉시 발행의 경합을 막는다.

> 규모가 커지면 폴링 주기를 줄이거나 브로커를 진짜 큐로 승격한다.
> claim UPDATE는 어느 쪽에서도 그대로 쓰므로 지금 선택이 앞을 막지 않는다.

### 5-4. dual-write ② — 메일 발송 성공 후 커밋 실패 (중복 발송)

**의도를 부수효과보다 먼저 커밋한다.**

```
1. INSERT lead_deliveries (id=UUID, status='SENDING', event='REPORT')  → COMMIT
2. Resend POST  with  Idempotency-Key: <lead_deliveries.id>
3. UPDATE status='SENT', provider_message_id=…, sent_at=now()          → COMMIT
```

3번이 실패하면 행은 `SENDING`으로 남는다. 스윕이 **같은 키로 재시도**하면 Resend가 원래 응답을
그대로 돌려주고 **메일은 다시 나가지 않는다**(공식 문서 확인).

**Resend는 idempotency key를 24시간만 보관한다.** 그래서:
- 자동 재시도는 최초 시도로부터 **24시간 이내**로 제한한다(§4-6의 5분·30분·4시간은 그 안에 들어간다).
- 24시간을 넘긴 `SENDING`은 자동으로 건드리지 않고 `FAILED` + Slack으로 AE에게 넘긴다.
  "보냈는지 알 수 없는 상태"를 자동 재발송하는 것보다 사람이 한 번 보는 쪽이 옳다.
- Resend는 같은 키에 **다른 payload가 오면 409**를 준다. 재시도 시 본문이 바이트 동일해야 하므로
  본문 입력은 DB에서 오는 값(병원명·리포트 URL·측정일시)만 쓰고, 결정성을 테스트로 고정한다(T-8).

---

## 6. 12-7 — 리포트 산출물

### 6-1. 테이블

```
lead_report_artifacts
  id                UUID PK
  diagnosis_id      UUID FK lead_diagnoses ON DELETE CASCADE
  version           Integer NOT NULL          -- 1부터. 재생성마다 +1
  storage_uri       String(500) NOT NULL      -- gs://{버킷}/lead-diagnoses/{diagnosis_id}/v{n}.pdf
  content_hash      String(64)  NOT NULL      -- sha256(bytes)
  byte_size         Integer     NOT NULL
  template_version  String(40)  NOT NULL      -- 어느 템플릿이 만든 숫자인가
  created_at        timestamptz NOT NULL
  purged_at         timestamptz NULL
  UNIQUE (diagnosis_id, version)
```

`template_version`이 있어야 "같은 병원인데 숫자가 왜 다르냐"에 답할 수 있다.
`content_hash`는 GCS 객체와 DB 기록의 불일치(업로드 절단)를 탐지한다.

### 6-2. 가림은 렌더 옵션이 아니라 구조다

CSS blur·PDF 오버레이는 텍스트 레이어에서 복원된다(PRD F5-3).
문서에 "가리세요"라고 적는 것으로는 지켜지지 않으므로 **함수 시그니처로 막는다.**

```python
def build_lead_report_payload(diagnosis, results) -> LeadReportPayload:
    """공개 대상만 담은 allowlist를 만든다.
    반환 타입에 raw_response·경쟁 병원명·개선 액션 필드가 아예 없다."""

def render_lead_report_pdf(payload: LeadReportPayload) -> bytes:
    """payload 외의 인자를 받지 않는다. 렌더러는 원자료에 접근할 수 없다."""
```

렌더러가 `results`를 받지 않으므로 실수로 샐 경로가 없다.
`LeadReportPayload`에 담기는 것은 PRD F5-1의 공개 목록뿐이다 — 플랫폼별 측정/언급 횟수, 질의 원문,
시스템 프롬프트 전문, 모델명, 반복 횟수, 측정 일시.
**플랫폼 표기는 `OpenAI API · gpt-5.6-luna` 형식**(F5-1 — "ChatGPT"라고 쓰면 철회한 주장을 라벨로 되살린다).

검증은 문서가 아니라 테스트가 한다(T-7).

### 6-3. 열람 링크

`lead_report_tokens` — `token_hash`(원문 저장 안 함), `expires_at`(30일), `revoked_at`,
`last_accessed_at`, `access_count`.

- 전용 서명 키·audience `"lead-report"`. Admin 세션 HMAC과 분리한다(PRD F5-4).
- 리포트 페이지는 `no-store` · `noindex` · `Referrer-Policy: no-referrer`.
- 접수 직후 사용자에게 보여주는 **상태 페이지가 같은 토큰을 쓴다**(§1). 측정 중에는 진행 상태를,
  완료 후에는 리포트를 보여준다. 토큰이 하나라 메일이 안 와도 결과에 도달할 수 있다.
- 재생성 시 토큰은 그대로 두고 artifact `version`만 올린다. 링크가 바뀌면 이미 보낸 메일이 죽는다.

### 6-4. 파기 — `lead_privacy.py` 확장

현재 `anonymize_lead()`는 `sales_leads`의 네 필드만 지운다. 진단 산출물이 그 밖에 있으면
파기가 거짓말이 된다. 같은 함수에서 함께 처리한다.

| 대상 | 처리 |
|---|---|
| `sales_leads.email` · `contact_name` · `contact` | `[purged]` |
| `lead_diagnosis_results.raw_response` | `''` |
| `lead_report_artifacts` | **GCS 객체 삭제** → `purged_at` 기록 (행은 남긴다 — 삭제했다는 증거) |
| `lead_report_tokens` | `revoked_at` |
| `lead_diagnoses.report_status` | `PURGED` |
| `applicant_email_hash` · `subject_phone_hash` | **유지** (§2-3 — 잠금은 파기와 무관하게 영구) |

**GCS 삭제가 DB 커밋보다 먼저다.** 반대로 하면 `purged_at`은 찍혔는데 객체가 살아 있는,
가장 나쁜 상태가 된다. GCS 삭제 실패 시 커밋하지 않고 다음 날 재시도한다(파기는 매일 04:00 반복).

파기 후 리포트 URL은 **410 Gone**을 준다(404가 아니다 — 있었고 지웠다는 사실이 맞다).

---

## 7. 12-10 — SLA

### 7-1. 기점은 접수 시각이다

```
SLA 구간 = created_at (접수)  →  lead_deliveries(event='REPORT').sent_at
목표     = P95 ≤ 15분
```

이메일 확인 단계가 없어져(§3) 전 구간이 우리 통제 안에 들어왔다.
원래 12-10의 문제("사용자의 확인 지연으로 SLA가 실패한다")가 원인 자체와 함께 사라졌다.
대기열도 없으므로(§2-1) `DEFERRED` 제외 조항도, 적체 관리도 필요 없다.

### 7-2. 그런데 20건이 한꺼번에 몰리면 15분을 못 지킨다 — 실측으로 확정해야 한다

**선착순 마케팅은 오픈 직후 신청을 몰리게 만든다.** 그것이 의도다. 그러니 최악을 가정해야 한다.

현재 `sov_engine._get_semaphore()`는 **전역 세마포어 하나**(`SOV_PROVIDER_CONCURRENCY=10`)를 쓴다.
질의 4개 기준 진단 1건 = 24콜, 20건 동시 = 480콜.
luna p50 25초 기준 `480 ÷ 10 × 25s ≈ 20분` — **P95 15분을 넘긴다.**

게다가 세마포어가 전역이라 **유료 측정과 무료 진단이 같은 풀을 두고 경합한다.**
PRD F6-1이 "기존 `sov` 큐와 분리해 유료 고객 측정과 경합하지 않게 한다"고 한 요구가
큐만 나누고 동시성 풀은 그대로 두면 코드 수준에서 지켜지지 않는다.

→ **`leadgen` 전용 세마포어를 둔다.** 기본값 `LEADGEN_PROVIDER_CONCURRENCY=15`로 시작하되,
**PRD §8 게이트 2의 shadow pilot에서 "20건 동시 주입" 부하 시험으로 실측해 확정한다.**
공급자 rate limit이 허용하는 상한을 모르는 채 숫자를 못 박지 않는다.

### 7-3. 출시 게이트에 추가

기존 게이트(신청 → 15분 내 리포트 도착)를 **부하 조건과 함께** 판정한다.

1. **20건을 5분 안에 몰아넣은 상태에서** 접수 → 발송 P95 ≤ 15분
2. 같은 시험 중 **유료 측정 경로의 지연이 늘지 않는다** (세마포어 분리 검증)

이 둘이 없으면 "한가할 때 15분"만 재고 넘어가게 된다.

---

## 8. 스키마 (마이그레이션 `0035`)

### 8-1. `sales_leads` 확장 (PRD §4-2)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `email` | String(320) NULL | 신규 |
| `contact_name` | String(100) NULL | 신규 · 담당자명 (`contact`는 연락처) |
| `region_keyword` | String(100) NULL | 신규 |
| `core_keywords` | JSON NULL | 최대 4개 |
| `clinic_phone` | String(40) NULL | 신규 · **병원 대표번호** (`contact`는 담당자 연락처 — 다른 값이다) |
| `source` | String(40) NOT NULL default `'INQUIRY'` | `AI_DIAGNOSIS` 분기 |
| `question` | Text | **NOT NULL → NULL** |

### 8-2. `lead_diagnoses`

| 컬럼 | 타입 |
|---|---|
| `id` | UUID PK |
| `lead_id` | UUID FK `sales_leads` ON DELETE CASCADE, indexed |
| `applicant_email_hash` | String(64) NOT NULL — 잠금 (§2-3) |
| `subject_phone_hash` | String(64) NOT NULL — 잠금 (§2-3) |
| `subject_hospital_name` | String(200) NOT NULL |
| `subject_region` | String(100) NOT NULL |
| `slot_date` | Date NOT NULL |
| `slot_no` | Integer NOT NULL — 1..20 |
| `queries` | JSON NOT NULL — `[{slot, text, type}]` |
| `requested_models` | JSON NOT NULL — `{openai, gemini, judge}` |
| `repeat_count` | Integer NOT NULL |
| `execution_status` | String(20) NOT NULL default `'PENDING'` |
| `execution_attempts` | Integer NOT NULL default 0 |
| `running_since` · `started_at` · `finished_at` | timestamptz NULL |
| `report_status` | String(20) NOT NULL default `'PENDING'` |
| `report_attempts` | Integer NOT NULL default 0 |
| `delivery_status` | String(20) NOT NULL default `'PENDING'` |
| `lock_released_at` · `lock_released_by` · `lock_release_reason` | AE 해제 (§2-4) |
| `error` | Text NULL |
| `created_at` · `updated_at` | timestamptz |

```sql
CREATE UNIQUE INDEX uq_lead_diagnoses_email_lock
  ON lead_diagnoses (applicant_email_hash) WHERE lock_released_at IS NULL;
CREATE UNIQUE INDEX uq_lead_diagnoses_phone_lock
  ON lead_diagnoses (subject_phone_hash)   WHERE lock_released_at IS NULL;
CREATE UNIQUE INDEX uq_lead_diagnoses_slot
  ON lead_diagnoses (slot_date, slot_no);
CREATE INDEX ix_lead_diagnoses_drain
  ON lead_diagnoses (created_at) WHERE execution_status = 'PENDING';
```

### 8-3. `lead_diagnosis_results` (PRD §4-1)

`id`, `diagnosis_id` FK CASCADE, `platform`, `query_slot`, `repeat_no`, `attempt_no`,
`requested_model`, `answer_model` NULL, `query_text`, `is_mentioned` NULL, `mention_verdict` String(20) NULL,
`measurement_status`, `failure_reason`, `raw_response`, `search_calls`, `input_tokens`, `output_tokens`,
**`answer_source` String(10) NOT NULL** (`LIVE` | `CACHED`), **`measured_at` timestamptz NOT NULL**,
UNIQUE `(diagnosis_id, platform, query_slot, repeat_no, attempt_no)`.

- **`answer_source`·`measured_at`은 캐시(§2-6) 때문에 필수다.** 어떤 측정이 캐시에서 왔는지,
  그 답변이 실제로 언제 측정된 것인지 기록하지 않으면 원가 검증도 신선도 검증도 못 한다.
  `measured_at`은 캐시 적중 시 **원본 측정 시각**이지 오늘이 아니다(T-15).

- `is_mentioned`가 nullable인 이유는 실패 건과 PRD F3-7의 `AMBIGUOUS` 때문이다.
- 판정 3값은 `measurement_status`가 아니라 **별도 `mention_verdict`**에 담는다
  (`MATCHED`/`NOT_MATCHED`/`AMBIGUOUS`) — 측정 성공 여부와 판정 결과는 다른 축이다.
- **`query_intent` 컬럼을 두지 않는다.** PRD F2-2가 모든 리드 진단 질의에 지역을 포함하도록 강제하므로
  전부 `LOCAL`이다. 상수를 컬럼으로 만들지 않고 F2-2를 테스트로 고정한다(T-3).

### 8-4. `lead_query_answers` — 질의 단위 공유 캐시 (§2-6)

```
  id                UUID PK
  query_hash        String(64)  NOT NULL   -- sha256(질의텍스트|platform|requested_model|prompt_version)
  query_text        String(500) NOT NULL   -- 사람이 읽을 수 있게. 판정에는 안 쓴다
  platform          String(20)  NOT NULL
  requested_model   String(100) NOT NULL
  answer_model      String(100) NULL       -- 공급자 응답의 실제 모델
  prompt_version    String(40)  NOT NULL   -- build_sov_prompt 버전
  repeat_no         Integer     NOT NULL
  raw_response      Text        NOT NULL
  source_urls       JSON NULL
  search_calls · input_tokens · output_tokens   Integer NULL
  measured_at       timestamptz NOT NULL
  expires_at        timestamptz NOT NULL   -- measured_at + 7일
  UNIQUE (query_hash, repeat_no)
  INDEX  (expires_at)                      -- 만료 삭제 배치용
```

- **성공한 측정만 캐시한다.** 실패 응답을 캐시하면 한 번의 장애가 7일간 전파된다.
- **개인정보가 아니다** — 질의에 신청자 이름·연락처가 절대 들어가지 않으므로(PRD §6)
  파기 파이프라인의 대상이 아니고 TTL로만 관리한다.
- 만료 삭제는 `drain_lead_diagnoses`(§9)가 함께 처리한다.

### 8-5. 나머지

- `lead_report_tokens` — `id`, `diagnosis_id` FK CASCADE, `token_hash` String(64) UNIQUE,
  `expires_at`, `revoked_at`, `last_accessed_at`, `access_count` Integer default 0, `created_at`
- `lead_report_artifacts` — §6-1
- `lead_deliveries` — `id` UUID PK (= `Idempotency-Key`), `lead_id` FK, `diagnosis_id` FK,
  `channel` String(20), `event` String(40), `status` String(20), `attempt` Integer,
  `provider_message_id` String(200) NULL, `error` Text NULL, `created_at`, `sent_at` NULL
  - `CREATE INDEX ix_lead_deliveries_stuck ON lead_deliveries (created_at) WHERE status = 'SENDING';`
- `sov_records.answer_model` String(100) — **2단** 요구사항(PRD F3-2)이지만 스키마 변경이 1컬럼이고
  기록 규약이 1단과 같아야 하므로 같은 마이그레이션에 넣는다.

---

## 9. 태스크 · 큐 · 배포

| 태스크 | 큐 | 주기/트리거 |
|---|---|---|
| `run_lead_diagnosis(diagnosis_id)` | **`leadgen`** (신규) | 접수 직후 best-effort + 폴러 |
| `build_lead_report(diagnosis_id)` | `leadgen` | 측정 종료 후 + 폴러 |
| `send_lead_email(delivery_id)` | `leadgen` | 리포트 완료 후 + 스윕 |
| `drain_lead_diagnoses` | `default` | **1분마다 (beat)** |

`drain_lead_diagnoses` 하나가 §5-3의 폴러, `RUNNING` 리스 재수확, `SENDING` 스윕을 모두 처리한다.
태스크를 셋으로 쪼갤 물량이 아니다.

배포 시 함께 바뀌어야 하는 것:

- `celery_app.task_routes`에 위 4개 등록 — **누락하면 `celery` 큐로 떨어져 영원히 실행되지 않는다**
  (`tests/test_celery_routing.py`가 막는다)
- `REDBEAT_SCHEDULE_VERSION` 상향
- 워커 인자에 `leadgen` 추가: `-Q default,content,sov,reports,leadgen`
- `RESEND_API_KEY`(Secret Manager에 이미 존재) → `config.py` 추가 + `deploy.sh` 프리플라이트 필수 시크릿 등록
- `LEAD_REPORT_TOKEN_SECRET`, `LEAD_LOCK_HASH_PEPPER` 신규 시크릿 2종
- `LEADGEN_PROVIDER_CONCURRENCY`, `LEADGEN_DAILY_SLOTS` 신규 설정
- `site/lib/host-routing.ts`의 `RESERVED_PREFIXES`에 `/ai-diagnosis` (PRD F1-3)
  + backend slug 발급에 예약어 검사 — 누락하면 커스텀 도메인에서 퍼널이 병원 페이지 밑으로 사라진다

---

## 10. 테스트 계약

핸드오프의 원칙을 따른다 — **자기참조를 피하고, 독립적으로 정해지는 값 사이의 제약을 건다.**
`assert STATUS == "RUNNING"` 같은 테스트는 아무것도 증명하지 않는다.

| # | 무엇을 고정하나 | 뮤테이션 탐지 |
|---|---|---|
| T-1 | INV-1~3 위반 상태 조합이 저장되지 않는다 | 불변식 제거 시 실패 |
| T-2 | 플랫폼 하나가 성공 0이면 `PARTIAL`이 **아니라** `FAILED`이고 리포트 게이트가 막힌다 | 게이트에 `FAILED` 추가 시 실패 |
| T-3 | 생성된 모든 리드 진단 질의에 지역 문자열이 포함된다 (PRD F2-2) | 폴백 템플릿에서 `{지역}` 제거 시 실패 |
| T-4 | 병원명이 질의 텍스트에 등장하지 않는다 (PRD F1-1) | 질의 생성에 병원명 주입 시 실패 |
| T-5 | **21번째 신청은 거부되고, 자정 이후 다시 열린다** | 상한 검사 제거 시 실패 |
| T-6 | 이메일·전화번호 **각각** 재사용이 거부된다 (한쪽만 바꿔도 막힌다) | 인덱스 하나만 남기면 실패 |
| T-7 | **렌더된 PDF 텍스트에 가림 대상 문자열이 0회 등장** — 경쟁 병원명·`raw_response`를 심어두고 추출 검사 | 렌더러가 원자료를 받게 되돌리면 실패 |
| T-8 | `SENDING` 재시도가 **같은** `Idempotency-Key`와 바이트 동일 payload를 보낸다 | 키를 attempt별로 새로 만들면 실패 |
| T-9 | 24시간 초과 `SENDING`은 자동 재시도하지 않고 `FAILED`가 된다 | 창 제한 제거 시 실패 |
| T-10 | 동시 제출 2회 → 행 1개, 응답 2회 모두 200, 자리 1칸만 소비 | 경합 처리 제거 시 실패 |
| T-11 | AE 해제 후 같은 번호·메일로 재신청이 가능해진다 | 부분 인덱스 조건 누락 시 실패 |
| T-12 | 파기 후 GCS 객체가 없고, 리포트 URL이 410이며, **잠금은 유지된다** | cascade 누락 또는 해시 삭제 시 실패 |
| T-13 | `leadgen` 측정이 `sov` 세마포어를 점유하지 않는다 (풀 분리) | 전역 세마포어로 되돌리면 실패 |
| T-14 | **모델명이나 프롬프트가 바뀌면 캐시가 적중하지 않는다** — `requested_model`·`prompt_version`을 바꾼 뒤 같은 질의를 던져 공급자 호출이 실제로 일어나는지 확인 | 캐시 키에서 둘 중 하나를 빼면 실패 |
| T-15 | **캐시 적중 건의 `measured_at`이 오늘이 아니라 원본 측정 시각이고, 리포트에 그 날짜가 인쇄된다** | 적중 시 `now()`를 쓰면 실패 |
| T-16 | 진단 1건의 계획 측정 수가 항상 18(질의 3 × 플랫폼 2 × 반복 3)이다 | 질의 수를 가변으로 되돌리면 실패 |

T-7·T-13·T-15가 가장 값비싼 테스트다. 셋 다 "문서에 적어두면 지켜질 것"이라는 가정이 과거에 깨진 지점
(F5-3의 CSS blur, F6-1의 큐 분리, 그리고 캐시가 만드는 "오래된 답변을 오늘 측정으로 파는" 유혹)을 겨눈다.

---

## 11. 구현 순서

```
1. 마이그레이션 0035 + 모델                    ← 나머지 전부의 전제
2. 정규화·해시 함수 (전화/이메일) + T-6/T-11     ← 순수 함수라 DB 없이 검증된다
3. 접수 API: 자리 배정 + 이중 잠금 + T-5/T-10
4. 랜딩 폼 + 확인 모달 + "남은 자리" 표시
5. leadgen 세마포어 분리 + 폴러 + claim + T-13
6. 측정 실행 (query_mapper 3슬롯 + sov_engine 재사용) + T-2/T-3/T-4/T-16
6-1. **질의 공유 캐시** (`lead_query_answers` 조회·저장·만료) + T-14/T-15
7. 리포트 payload/렌더/artifact + 상태 페이지 + T-7
8. mailer.py (Resend) + lead_deliveries + T-8/T-9
9. 파기 cascade + T-12
10. Admin: 진단 요약 · 잠금 해제 · 실패 목록(DLQ) (PRD F7-1)
```

3·4번이 먼저인 이유: 자리와 잠금이 이 퍼널의 뼈대다. 측정·리포트는 그 다음이다.

---

## 12. 이 설계가 정하지 않은 것

| # | 항목 | 필요한 결정 |
|---|---|---|
| A | 하루 자리 20건 | 마케팅 문구에 노출되는 숫자다. 원가 최악 52,000원/일 (대표) |
| B | 법무 검토 (PRD F5-5) | 인공지능기본법 제31조 적용 항·시행일, 의료법 제56조. **+ §3의 이메일 확인 제거가 수용 가능한지.** 출시 게이트 |
| C | `LEADGEN_PROVIDER_CONCURRENCY` | shadow pilot 부하 시험으로 실측 확정 (§7-2) |
| D | PRD F3-7 (`AMBIGUOUS` + 판정에 지역 주입) | **이 설계는 `mention_verdict` 컬럼 자리만 만들었고 구현하지 않았다.** 동명 의원이 흔해 진단 정확도의 선행 조건일 수 있다 — 착수 순서를 정해야 한다 |
| E | Gemini 무료 구간 해석 | 첫 달 실청구로 확정 (PRD §6 단서) |
