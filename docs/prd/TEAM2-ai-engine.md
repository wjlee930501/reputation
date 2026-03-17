# PRD: TEAM 2 — AI Engine 개선 (PO 검토 반영 v0.3)

> 버전: v0.2 → v0.3 | 작성일: 2026-03-17 | PO 검토: 완료

---

## 목표

AI 엔진 보안 강화 + 비용 최적화 + 측정 신뢰도 향상 + 경쟁사 비교

---

## 실제 비용 구조 (PO 검토 수정)

병원 100개 기준, Gemini 활성화 시:
- 주간: 10쿼리 × 10반복 × 2플랫폼 = **200 호출/병원** → 20,000 호출/주
- 파싱: 사전 필터 통과분만 → 실제 ~5,000 호출/주 (통과율 ~25%)
- **총: ~25,000 API 호출/주 (100병원)**

---

## 1. 이미지 Signed URL 전환 (P0)

**결정: DB에 GCS 경로 저장 + 서빙 시점에 Signed URL 생성**

DB에 signed URL 자체를 저장하는 것은 안티패턴 (만료 관리 불가).

**변경 사항**:
- `image_engine.py`:
  - `blob.make_public()` 제거
  - 반환값: `gs://bucket/path` 형태의 GCS 경로
  - `ContentItem.image_url`에 `gs://reputation-images/content/{slug}/{uuid}.png` 저장
- 서빙 유틸리티 (`services/gcs_utils.py` 신규):
  ```python
  def get_signed_url(gcs_path: str, expiration_hours: int = 24) -> str:
      """gs:// 경로 → signed URL 변환"""
  ```
- Public API (`site.py`): `_serialize_item`에서 image_url을 signed URL로 변환
- Admin API: 동일 적용
- 기존 public blob 마이그레이션: 스크립트로 기존 `https://storage.googleapis.com/...` URL을 `gs://...` 형태로 DB 업데이트

**수용 기준**:
- [ ] 신규 이미지: `gs://` 경로로 DB 저장
- [ ] API 응답에서 signed URL 반환, 24시간 내 로드 가능
- [ ] `make_public()` 완전 제거
- [ ] 기존 이미지 URL 마이그레이션 스크립트 동작

## 2. SoV 비용 최적화 (P1)

**변경 사항**:

a) **쿼리 우선순위**: `QueryMatrix`에 `priority` 필드 (HIGH/NORMAL/LOW)
- HIGH (멘션 이력 있는 쿼리): 매주
- NORMAL: 격주
- LOW (멘션 0회): 월간
- 강등 조건: 4주 연속 멘션 없으면 HIGH→NORMAL

b) **반복 횟수 조절**:
- V0: 5회 (유지)
- 주간: 5회 (현재 10 → 5)
- 월간 리포트: 10회

c) **파싱 최적화**: 사전 필터 통과 시에만 GPT 호출 (현재 부분 구현됨)

**예상 절감**: ~40% (25K → ~15K 호출/주, 100병원)

**수용 기준**:
- [ ] QueryMatrix priority 필드 동작
- [ ] 주간 모니터링에서 LOW 쿼리 skip 확인
- [ ] Alembic 마이그레이션 포함

## 3. SoV 측정 신뢰도 개선 (P1)

**변경 사항**:
- ChatGPT: `temperature=1.0 → 0.7` (Gemini는 grounding 있으므로 유지)
- 파싱 프롬프트: 병원명 변형 명시 ("장편한외과" = "장편한외과의원" = "장편한 외과")
- 사전 필터 유지: 2글자 (3글자는 false negative 증가하므로 변경 안 함)

**수용 기준**:
- [ ] 변경 전 baseline SoV 측정값 기록 (비교용)
- [ ] temperature 변경 후 동일 쿼리 결과 분산 비교

## 4. 의료광고 필터 강화 (P1)

**변경 사항**:
- `_check_forbidden()`: 단순 문자열 매칭 → 정규식 패턴
- 예: `"최고"` → `r"최고[의의]?"`, `"1등"` → `r"1등|일등|1위"`
- 14개 금지 표현 각각에 변형 패턴 적용

**수용 기준**:
- [ ] "최고의", "최고 수준", "일등" 등 변형 포착
- [ ] 기존 정상 텍스트에서 false positive 없음

## 5. 경쟁사 SoV 비교 (P2)

**결정: 기존 응답 재활용 (추가 API 호출 없음)**

`run_single_query`의 raw_response에서 자사 + 경쟁사를 동시 파싱.

**변경 사항**:
- `_parse_mention` 확장: `hospital_name` 1개 → `target_names: list[str]` (자사 + competitors)
- 파싱 결과에 `target_name` 필드 추가
- `SovRecord`에 `target_name` 필드 추가 (null = 자사)
- 월간 리포트에 경쟁사 비교 테이블 추가 (`report.html`)
- `GET /admin/hospitals/{id}/competitors/sov` API

**수용 기준**:
- [ ] 추가 API 호출 없이 경쟁사 SoV 데이터 수집
- [ ] 월간 리포트에 비교 테이블 (경쟁사 데이터 있을 때만)

## 6. 리포트 PDF 고도화 (P2, TEAM 3에서 이관)

**변경 사항**:
- `report.html`: 경쟁사 비교 섹션, SoV 추이 차트
- matplotlib로 차트 이미지 → PDF 인라인
- "다음 달 추천 액션": 멘션율 낮은 쿼리 TOP 3 기반

**수용 기준**:
- [ ] 경쟁사 비교 테이블 포함
- [ ] SoV 추이 그래프 이미지 포함

---

## 스코프 제외
- Perplexity/Claude SoV 측정
- 콘텐츠 배치 생성 (P2 이하, token 기반 과금이라 절감 미미)
- 콘텐츠 효과 어트리뷰션 (통계적 유의성 부족, 향후 재검토)
