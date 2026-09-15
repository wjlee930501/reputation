# 도입문의 접수 자동 처리 — 초도 노출 진단과 안내 문자

문서 버전: **1.0** · 갱신일: **2026-09-15 (Asia/Seoul)**
범위: 공개 랜딩의 도입문의 폼(`POST /public/leads`)이 접수된 직후 백엔드가 자동으로 하는 두 가지 일과, 사람이 개입하는 지점.

## 흐름

1. 랜딩 폼이 병원명·주소·원장 성함·연락처·홈페이지에 더해 진료과(`specialty`)·지역 키워드(`region_keyword`)·핵심 키워드(`core_keywords`, 1~4개)를 보낸다. `clinic_type`은 도입문의 표식(`도입문의`)으로 고정되며 진료과가 이 칸을 덮지 않는다.
2. 리드를 저장하고 커밋한다. 이 시점부터 아래 두 부수효과가 실패해도 접수는 성공이다.
3. 진료과·지역·키워드가 모두 있으면 `services/inquiry_diagnosis.create_inquiry_diagnosis`가 INTERNAL 진단 1건을 만들고 `run_lead_diagnosis`에 큐잉한다. Admin의 `POST /admin/leads/{id}/diagnoses/internal`과 같은 함수라 규칙(리드당 1건, 병원명 포함 거절, 무료 진단 자리·잠금·공개 토큰 미소비, 고객 발송 영구 차단)이 두 경로에서 같다. 감사 로그 actor는 `system:public-inquiry`다.
4. Slack 도입문의 알림에 자동 처리 한 줄이 붙는다. `초도 노출 진단 자동 시작` · `진료과·지역·키워드 미입력 — Admin에서 초도 진단 생성` · `초도 노출 진단 자동 생성 거절 — Admin에서 입력값 확인 후 생성` 셋 중 하나이며 사용자 입력은 섞이지 않는다.
5. 원장 연락처가 휴대전화면 `services/inquiry_sms.acknowledge_inquiry`가 NHN Cloud Notification SMS(v3.0 MMS 엔드포인트, 첨부 없는 LMS)로 안내 문자를 보낸다. 발신번호는 `INQUIRY_SMS_SENDER_NO`(기본 010-2492-8543, 마케팅팀 김효진 팀장)이며 콘솔에 사전 등록돼 있어야 한다. 같은 연락처가 `INQUIRY_SMS_DEDUP_HOURS`(기본 6시간) 안에 다시 접수되면 보내지 않는다.
6. 결과는 리드 행에 남는다. `ack_sms_status`(SENT/FAILED/SKIPPED)·`ack_sms_error`·`ack_sms_sent_at`. Admin 리드 목록이 발송 완료·미발송을 표시한다.

## 사람이 하는 일

- 자동 처리 한 줄이 `자동 시작`이 아니면 Admin 리드 화면에서 `진단 생성(내부용)`을 눌러 진료과·지역·키워드를 채운다. 폼 기본값은 리드의 `specialty`·`region_keyword`·`core_keywords`를 그대로 가져온다.
- `원장 안내 문자 미발송`이 표시되면 직접 연락한다. 문자는 재발송 버튼이 없다 — 이미 전화로 이어지는 단계라 자동 재시도를 두지 않는다.

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
- 실제 Postgres: `backend/tests/integration/test_inquiry_internal_diagnosis.py::TestPublicIntakeAutoDiagnosis`
- 마이그레이션: `0077_inquiry_intake_automation` (`sales_leads.specialty`, `ack_sms_*`)
