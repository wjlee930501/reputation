# 도입문의 접수 자동 처리 — 초도 노출 진단과 안내 문자

문서 버전: **1.2** · 갱신일: **2026-09-22 (Asia/Seoul)**
범위: 공개 랜딩의 도입문의 폼(`POST /public/leads`)이 접수된 직후 백엔드가 자동으로 하는 두 가지 일과, 사람이 개입하는 지점.

## 랜딩의 자리

랜딩(`site/app/page.tsx`)의 모든 CTA — 헤더·히어로·최종 밴드·모바일 고정 바 — 는 `#lead`,
즉 페이지 하단의 도입문의 폼(`site/app/_components/InquiryForm.tsx`)을 가리킨다.
셀프서브 무료 진단(`/ai-diagnosis`)은 그대로 살아 있지만 랜딩 CTA에서는 빠졌고 푸터 링크로만 간다.

폼은 진료과·지역·핵심 키워드를 **필수**로 받는다. 이 셋이 있어야 접수 즉시 진단이 만들어지고
(`LeadCreate.diagnosis_input`), 없으면 문의만 쌓이고 연락할 근거가 없다. 프록시
(`site/app/api/leads/route.ts`)의 `REQUIRED_FIELDS`가 같은 집합을 강제하며, 셋을 백엔드로
전달하지 않으면 자동 생성이 조용히 `DIAGNOSIS_NOTE_NO_INPUT`으로만 떨어진다.
JSON을 요청한 쪽(폼)은 프록시의 `error` 문자열을 그대로 화면에 띄우므로 그 문구는 우리말이어야 한다.

## 흐름

1. 랜딩 폼이 병원명·주소·원장 성함·연락처·홈페이지에 더해 진료과(`specialty`)·지역 키워드(`region_keyword`)·핵심 키워드(`core_keywords`, 1~4개)를 보낸다. `clinic_type`은 도입문의 표식(`도입문의`)으로 고정되며 진료과가 이 칸을 덮지 않는다.
2. 리드를 저장하고 커밋한다. 이 시점부터 아래 두 부수효과가 실패해도 접수는 성공이다.
3. 진료과·지역·키워드가 모두 있으면 `services/inquiry_diagnosis.create_inquiry_diagnosis`가 INTERNAL 진단 1건을 만들고 `run_lead_diagnosis`에 큐잉한다. Admin의 `POST /admin/leads/{id}/diagnoses/internal`과 같은 함수라 규칙(리드당 1건, 병원명 포함 거절, 무료 진단 자리·잠금·공개 토큰 미소비, 고객 발송 영구 차단)이 두 경로에서 같다. 감사 로그 actor는 `system:public-inquiry`다.
4. Slack 도입문의 알림에 자동 처리 한 줄이 붙는다. `초도 노출 진단 자동 시작` · `진료과·지역·키워드 미입력 — Admin에서 초도 진단 생성` · `초도 노출 진단 자동 생성 거절 — Admin에서 입력값 확인 후 생성` 셋 중 하나이며 사용자 입력은 섞이지 않는다.
5. 원장 연락처가 휴대전화면 `services/inquiry_sms.acknowledge_inquiry`가 NHN Cloud Notification SMS(v3.0 MMS 엔드포인트, 첨부 없는 LMS)로 안내 문자를 보낸다. 발신번호는 `INQUIRY_SMS_SENDER_NO`(기본 010-2492-8543, 마케팅팀 김효진 팀장)이며 콘솔에 사전 등록돼 있어야 한다. 같은 연락처가 `INQUIRY_SMS_DEDUP_HOURS`(기본 6시간) 안에 다시 접수되면 보내지 않는다.
6. 결과는 리드 행에 남는다. `ack_sms_status`(SENT/FAILED/SKIPPED)·`ack_sms_error`·`ack_sms_sent_at`. Admin 리드 목록이 발송 완료·미발송을 표시한다.

## 값이 틀렸을 때

폼이 비어 있으면 자동 생성이 거절돼 Admin에서 채우면 된다. **틀린 값은 다르다** —
자동 생성을 그대로 통과해 잘못된 질의로 측정이 끝나고 콜용 보고서까지 만들어진다.
`다시 측정`은 저장된 그 질의를 다시 묻고 `보고서 다시 만들기`는 같은 측정 결과로 PDF만
다시 만들므로, 둘 다 입력을 고치지 못한다.

상담 요청 화면의 `값 고쳐 다시 만들기`가 그 경로다. 진료과·지역·키워드를 고쳐 새 진단을
만들고 옛 진단은 `superseded_at`·`superseded_by_id`로 갈음해 남긴다. 지우지 않는 이유는
실제로 지출한 공급자 호출과 그때 무엇을 쟀는지가 기록으로 남아야 하기 때문이다.
갈음된 진단은 운영자 큐·복구 버튼·폴러 어디에도 올라오지 않으며, 보고서는 무엇을 잘못
쟀는지 확인할 수 있도록 계속 열린다. 고객에게 나간 진단(`SENDING`·`SENT`·`FAILED`)은
갈음 대상이 아니다 — 공개 토큰 뷰가 최신 버전을 서빙하므로 이미 보낸 링크의 내용이 바뀐다.

문의가 없는 병원은 Admin 네비의 `노출 진단 생성`에서 새로 만든다. 리드 행이 함께
생기며 `privacy`는 False다(원장이 동의한 적이 없다). 이 화면에는 리드 검색이 없다 —
리드 목록 조회는 대량 PII 열람이라 감사 로그를 남기는 표면이고, 고칠 리드는 상담 요청
화면에 이미 떠 있다.

## 사람이 하는 일

- 자동 처리 한 줄이 `자동 시작`이 아니면 Admin 리드 화면에서 `진단 생성(내부용)`을 눌러 진료과·지역·키워드를 채운다. 폼 기본값은 리드의 `specialty`·`region_keyword`·`core_keywords`를 그대로 가져온다.
- `원장 안내 문자 미발송`이 표시되면 직접 연락한다. 문자는 재발송 버튼이 없다 — 이미 전화로 이어지는 단계라 자동 재시도를 두지 않는다.
- 콜용 보고서가 이미 준비됐어도(`READY`) 리드 화면의 `보고서 다시 만들기`로 최신 기준의 새 버전을 만들 수 있다. 이전 버전은 `LeadReportArtifact`로 남는다. 고객에게 나간 리드(`SENT`·`SENDING`·`FAILED`)는 대상이 아니다 — 공개 토큰 뷰가 최신 버전을 서빙하므로 이미 보낸 링크의 내용이 바뀌고, DB 제약 `ck_lead_diagnoses_delivery_requires_report`도 같은 선을 긋는다.

## 설정

| 이름 | 위치 | 비고 |
|---|---|---|
| `INQUIRY_SMS_PROVIDER` | Cloud Run env (`terraform/variables.tf: inquiry_sms_provider`) | `nhn` 또는 빈 값. 비어 있으면 SKIPPED |
| `NHN_SMS_APP_KEY` | Cloud Run env (`nhn_sms_app_key`) | 비밀 아님 |
| `NHN_SMS_SECRET_KEY` | Secret Manager (`secretmanager.tf`, deploy.sh optional) | 비어 있으면 SKIPPED |
| `INQUIRY_SMS_SENDER_NO` | Cloud Run env | 사전 등록 발신번호 |
| `INQUIRY_SMS_DEDUP_HOURS` | config.py 기본 6 | |

문자 본문은 `backend/app/services/inquiry_sms.py`의 `ACK_SMS_BODY` 한 곳에만 있다. 원장의 문의에 답하는 내용이라 정보통신망법 제50조의 광고성 정보 표기 대상이 아니며, 본문을 프로모션으로 바꾸면 (광고) 표기와 수신거부 안내가 필요해진다.

## 검증

- 단위: `backend/tests/test_public_leads.py`, `backend/tests/test_inquiry_sms.py`
- 랜딩 폼·프록시: `site/lib/inquiry-form.test.ts`, `site/lib/inquiry-intake.test.ts`
- 수동 생성·갈음: `backend/tests/integration/test_manual_lead_diagnosis.py`, `admin/lib/manual-diagnosis.test.ts`
- 마이그레이션: `0080_lead_diagnosis_supersede` (`lead_diagnoses.superseded_at`, `superseded_by_id`)
- 실제 Postgres: `backend/tests/integration/test_inquiry_internal_diagnosis.py::TestPublicIntakeAutoDiagnosis`
- 마이그레이션: `0077_inquiry_intake_automation` (`sales_leads.specialty`, `ack_sms_*`)
