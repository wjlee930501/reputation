"""Deterministic Slack language; technical findings remain in authenticated Admin.

This module cannot change incident state, publication gates, audience or delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.incident_safety import sanitize_operator_text

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class ActionCopy:
    title: str
    action: str
    button: str


_CATALOG = {
    "DOMAIN_UNHEALTHY": ActionCopy(
        "병원 주소 접속 확인",
        "병원 주소를 열어 보세요. 접속이 안 되면 병원 정보에서 공개 주소 상태를 확인해 주세요. DNS는 오류가 확인된 경우에만 변경하세요.",
        "공개 주소 상태 보기",
    ),
    "PUBLIC_SITE_UNAVAILABLE": ActionCopy(
        "여러 병원 공개 페이지 응답 오류",
        "개발 담당자는 공개 사이트 서비스와 로드밸런서 상태를 확인해 주세요. 병원별 DNS는 변경하지 마세요.",
        "운영 센터 보기",
    ),
    "DOMAIN_CERTIFICATE_FAILED": ActionCopy(
        "병원 주소 연결 미완료",
        "병원 정보에서 DNS와 인증서 상태를 확인해 주세요. 설정이 맞는데도 연결되지 않으면 오류 정보를 개발 담당자에게 전달해 주세요.",
        "공개 주소 상태 보기",
    ),
    "CONTENT_GENERATION_FAILED": ActionCopy(
        "예정 글 발행 보류",
        "콘텐츠에서 해당 글의 차단 사유와 근거 자료를 확인해 주세요. 병원 자료 수정이 필요한 항목은 확인된 사실로 보완해 주세요.",
        "멈춘 글 확인",
    ),
    "ESSENCE_AUTO_REVIEW_ESCALATED": ActionCopy(
        "콘텐츠 운영 기준 확인 필요",
        "병원 정보에서 운영 기준의 확인 요청 항목을 보완해 주세요. 확인되지 않은 내용은 승인하지 마세요.",
        "운영 기준 확인",
    ),
    "ESSENCE_MUST_USE_EXCLUDED": ActionCopy(
        "필수 문구 금지 표현 확인 필요",
        "병원 정보에서 운영 기준의 해당 필수 문구를 금지 표현 없이 고쳐 다시 승인해 주세요. 그 전까지 이 문구는 새 글에 들어가지 않습니다.",
        "운영 기준 확인",
    ),
    "SOURCE_PROCESSING_FAILED": ActionCopy(
        "병원 자료 처리 중단",
        "병원 정보에서 처리되지 않은 자료를 확인하고 읽을 수 있는 원문으로 다시 등록해 주세요.",
        "병원 자료 확인",
    ),
    "NAVER_SOURCE_FETCH_FAILED": ActionCopy(
        "공식 채널 자료 확인 필요",
        "병원 정보에서 등록한 공식 채널 주소와 접근 가능 여부를 확인해 주세요.",
        "공식 채널 확인",
    ),
    "MONTHLY_SLOT_GENERATION_FAILED": ActionCopy(
        "월간 발행 일정 미완료",
        "병원 콘텐츠에서 이번 달 계약 편수와 배정된 일정을 확인해 주세요. 일정 저장이 실패하면 오류 정보를 개발 담당자에게 전달해 주세요.",
        "발행 일정 확인",
    ),
    "MONTHLY_DOCTOR_PDF_BLOCKED": ActionCopy(
        "고객용 레포트 파일 생성 실패",
        "개발 담당자는 보고서 오류 기록을 확인해 주세요. 검증이 끝나지 않은 파일을 고객에게 보내지 마세요.",
        "레포트 오류 보기",
    ),
    "BACKGROUND_TASK_FAILED": ActionCopy(
        "백그라운드 작업 중단",
        "개발 담당자는 작업 오류와 재시도 상태를 확인해 주세요. 운영 담당자가 같은 작업을 반복 실행할 필요는 없습니다.",
        "작업 오류 보기",
    ),
    "BROKER_UNAVAILABLE": ActionCopy(
        "자동 작업 대기열 연결 실패",
        "개발 담당자는 Redis 연결과 Worker 상태를 확인해 주세요. 연결 복구 전에는 반복 실행을 요청하지 마세요.",
        "대기열 오류 보기",
    ),
    "CACHE_REVALIDATION_FAILED": ActionCopy(
        "공개 페이지 갱신 지연",
        "개발 담당자는 페이지 갱신 오류를 확인해 주세요. 글을 다시 발행하지 마세요.",
        "페이지 갱신 오류 보기",
    ),
    "NOTIFICATION_DELIVERY_FAILED": ActionCopy(
        "Slack 알림 전송 실패",
        "개발 담당자는 웹훅 설정과 전송 오류를 확인한 뒤 해당 알림만 재시도해 주세요.",
        "알림 전송 오류 보기",
    ),
    "NOTIFICATION_DELIVERY_UNKNOWN": ActionCopy(
        "Slack 수신 여부 확인 필요",
        "채널에서 같은 알림이 도착했는지 먼저 확인해 주세요. 미수신이 확인된 경우에만 재전송하세요.",
        "알림 전송 기록 보기",
    ),
    "COST_GUARD_LIMIT_REACHED": ActionCopy(
        "자동 작업 비용 한도 도달",
        "운영센터에서 한도에 도달한 작업과 재개 예정 시각을 확인해 주세요. 한도 변경은 비용 담당자와 결정해 주세요.",
        "비용 차단 확인",
    ),
    "LEAD_DIAGNOSIS_FAILED": ActionCopy(
        "무료 진단 생성 중단",
        "신청 내역에서 진단 실패 사유를 확인해 주세요. 재시도가 끝난 건만 후속 조치해 주세요.",
        "진단 신청 확인",
    ),
    "LEAD_DIAGNOSIS_RETRIES_EXHAUSTED": ActionCopy(
        "무료 진단 자동 재시도 종료",
        "신청 내역의 오류를 확인하고 진단 재진행 또는 고객 안내 여부를 결정해 주세요.",
        "진단 신청 확인",
    ),
    "LEAD_DELIVERY_ABANDONED": ActionCopy(
        "진단 메일 전송 중단",
        "신청 내역에서 받는 주소와 전송 결과를 확인한 뒤 필요한 경우에만 다시 보내 주세요.",
        "메일 전송 기록 보기",
    ),
}


# These aliases share actions only; they never change the audience registry.
for _kind in (
    "CONTENT_DISPATCH_FAILED",
    "OPERATION_TERMINAL_FAILED",
    "SITE_BUILD_DISPATCH_FAILED",
    "UNSAFE_STORED_DISPATCH",
):
    _CATALOG[_kind] = _CATALOG["BACKGROUND_TASK_FAILED"]
for _kind in ("ESSENCE_AUTO_REVIEW_COST_BLOCKED", "LEAD_DIAGNOSIS_COST_BLOCKED"):
    _CATALOG[_kind] = _CATALOG["COST_GUARD_LIMIT_REACHED"]
_CATALOG["ESSENCE_AUTO_REVIEW_FAILED"] = _CATALOG["ESSENCE_AUTO_REVIEW_ESCALATED"]
_CATALOG["PUBLISH_NOTIFICATION_FAILED"] = _CATALOG["NOTIFICATION_DELIVERY_FAILED"]


def incident_copy(incident_type: str) -> ActionCopy:
    return _CATALOG.get(
        incident_type,
        ActionCopy(
            "운영 항목 확인 필요",
            "연결된 화면에서 원인과 가능한 조치를 확인해 주세요. 원인이 불명확하면 오류 정보를 개발 담당자에게 전달해 주세요.",
            "상세 원인 보기",
        ),
    )


def readable_detail(value: str, *, fallback: str, limit: int = 200) -> str:
    """Do not relay raw model findings or internal field/JSON names into Slack."""
    cleaned = sanitize_operator_text(value, limit=limit) or ""
    if (
        not cleaned
        or cleaned == "자동 작업이 완료되지 않았습니다."
        or re.search(r"[a-zA-Z]+_[a-zA-Z_]+|[{}]|```", cleaned)
    ):
        return fallback
    return re.sub(r"\s+", " ", cleaned).strip()


def display_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware Slack timestamp is required")
    return value.astimezone(KST).strftime("%m/%d %H:%M KST")


# 아침 요약(07:45·08:00)의 진료비·병원 선택 글 참고자료 보류 줄만 쓰는 문구 키. 차단 코드는 그대로
# MISSING_REFERENCES이고(인시던트·요약 식별자), 주간 요약의 평범한 참고자료 보류 문구는 바꾸지 않는다.
REFERENCES_OPERATOR_DECIDES_COPY_CODE = "MISSING_REFERENCES_OPERATOR_DECIDES"
# 같은 보류의 아직 쓰이지 않은 슬롯(생성이 작가의 제목으로 판정해 남긴 결정) — 고칠 제목·본문이
# 없으므로 '새로 쓰기'를 말한다.
REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE = "MISSING_REFERENCES_OPERATOR_DECIDES_UNWRITTEN"
REFERENCES_OPERATOR_DECIDES_COPY_CODES = frozenset(
    {REFERENCES_OPERATOR_DECIDES_COPY_CODE, REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE}
)
# 두 모양이 함께 싣는 경고. 진료비·병원 선택 글(저장될 제목 기준)에 검증된 문서 목록의 문서를 담은
# 저장은 422 `CURATED_REFERENCE_NOT_ALLOWED`로 거절된다. 자동 복구가 다시 쓰지 않는 것은 생성
# 조건이 그대로인 동안이다 — 새 운영 기준 승인은 옛 기준의 본문을, 생성 지문(운영 기준·유형·측정
# 질문 등, `tasks._generation_attempt_context`) 변화는 쓰이지 않은 슬롯을 다시 쓴다.
_OPERATOR_DECIDES_CURATED_WARNING = (
    "진료비·병원 선택 글에는 검증된 문서 목록의 문서를 넣을 수 없어 저장이 거절됩니다."
)
_OPERATOR_DECIDES_OUTCOME = (
    "참고 자료 없이는 발행되지 않고, 자동 복구는 운영 기준이 새로 승인되는 등 생성 조건이 "
    "바뀌기 전에는 이 글을 다시 쓰지 않습니다."
)
# 아직 쓰이지 않은 슬롯의 결과. 질환·검사 안내 글로 새로 쓰면 더 이상 진료비·병원 선택 글이 아니라
# 자동 복구가 참고 자료를 찾는다 — 그 예외를 먼저 말한다. 인시던트 조치
# (`generation_incident_control.REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION`)의 끝 두 문장과 같다.
_OPERATOR_DECIDES_UNWRITTEN_OUTCOME = (
    "질환·검사 안내 글로 쓰면 참고 자료 없이 저장해도 자동 복구가 참고 자료를 찾습니다. 그대로 두면 "
    "생성 조건이 바뀌기 전에는 자동 복구가 이 글을 쓰지 않고, 참고 자료 없이는 발행되지 않습니다."
)
# 콘텐츠 생성 서비스의 일시 장애. 독립 검수 라벨과 섞지 않는다. 이 코드의 빈 슬롯은 “작업 다시
# 시도”가 환경 실패 기록의 억제를 풀어 바로 원고를 만든다(`operator_retry_releases`).
_PROVIDER_OUTAGE_CODES = frozenset({"PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"})
# 분류되지 않은 생성 작업 오류. 공급자 장애처럼 환경 실패(ENVIRONMENT_RECOVERABLE)라 예산 안에서
# 자동 재시도하고, 빈 슬롯은 “작업 다시 시도”가 억제를 풀어 바로 다시 시도한다. 본문·근거 확인이
# 아니다.
_GENERATION_ERROR_CODES = frozenset({"GENERATION_FAILED"})
# 독립 검수가 끝나지 않은 코드. 부분 문자열이 아니라 코드 그대로 맞춘다 — "UNAVAILABLE"을
# 포함한다는 이유로 생성 서비스 장애까지 검수 미완료로 부르면 운영자가 엉뚱한 곳을 본다.
_REVIEW_PENDING_CODES = frozenset({"CONTENT_AI_REVIEW_UNAVAILABLE"})


def references_operator_decides_copy_code(*, written: bool) -> str:
    """사람이 정하는 참고자료 보류 줄의 문구 키 — 작성된 글은 '고쳐 저장', 빈 슬롯은 '새로 쓰기'."""

    return (
        REFERENCES_OPERATOR_DECIDES_COPY_CODE
        if written
        else REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE
    )


def blocker_copy(code: object) -> ActionCopy:
    value = str(code or "")
    if value == "REFERENCE_SITE_UNREACHABLE":
        # 문서가 없다는 판정이 아니다 — 기관 사이트가 열리지 않아 확인을 미뤘다.
        return ActionCopy(
            "기관 사이트 접속 불가로 발행 대기",
            "참고 자료 기관 사이트에 접속하지 못해 발행을 미뤘습니다. 다음 발행 시간대에 "
            "자동으로 다시 확인하며, 사이트가 계속 열리지 않으면 콘텐츠에서 참고 자료 주소를 바꿔 주세요.",
            "참고 자료 확인",
        )
    if value == "MISSING_APPROVED_ESSENCE":
        return ActionCopy(
            "운영 기준 미승인",
            "병원 정보에서 콘텐츠 운영 기준을 확인하고 승인해 주세요.",
            "운영 기준 확인",
        )
    if value == "CONTENT_NOT_GENERATED":
        return ActionCopy(
            "발행용 원고 미생성",
            "운영센터에서 해당 글의 생성 상태를 확인하고, 자동 재시도 중이 아니면 “작업 다시 시도”를 눌러 주세요.",
            "생성 상태 확인",
        )
    if value == "TOPIC_SWAPPED":
        return ActionCopy(
            "주제 자동 교체",
            "같은 주제로 자동 생성이 소진되어 다른 주제로 바꿨습니다. 콘텐츠에서 새 주제를 확인해 주세요. 새 주제 생성은 자동으로 진행되니 다시 실행하지 마세요.",
            "새 주제 확인",
        )
    if "IMAGE" in value:
        return ActionCopy(
            "발행용 이미지 준비 실패",
            "콘텐츠에서 이미지 오류를 확인해 주세요. 자동 재시도가 남은 글은 다시 실행하지 마세요.",
            "이미지 오류 확인",
        )
    if "COST" in value:
        return incident_copy("COST_GUARD_LIMIT_REACHED")
    if value in _PROVIDER_OUTAGE_CODES:
        return ActionCopy(
            "생성 서비스 일시 장애",
            "콘텐츠 생성 서비스의 일시 장애로 원고를 만들지 못했습니다. 운영 센터에서 해당 글의 생성 "
            "상태를 확인하고, 서비스가 복구됐으면 “작업 다시 시도”를 눌러 주세요.",
            "생성 상태 확인",
        )
    if value in _GENERATION_ERROR_CODES:
        return ActionCopy(
            "생성 서비스 오류",
            "콘텐츠 생성 작업이 오류로 중단돼 원고를 만들지 못했습니다. 운영 센터에서 해당 글의 생성 "
            "상태를 확인하고, 오류가 풀렸으면 “작업 다시 시도”를 눌러 주세요.",
            "생성 상태 확인",
        )
    if value in _REVIEW_PENDING_CODES:
        return ActionCopy(
            "자동 검수 미완료",
            "콘텐츠에서 검수 상태와 자동 재시도 여부를 확인해 주세요.",
            "검수 상태 확인",
        )
    if value == REFERENCES_OPERATOR_DECIDES_COPY_CODE:
        # 인시던트 조치(`REFERENCES_OPERATOR_DECIDES_ACTION`)의 요약 한 줄 — 콘텐츠 화면에 실제로 있는
        # 조작(“콘텐츠 수정”·“참고 자료 추가”·제목·본문 저장)만 말한다. 항목 종료·재생성 버튼은 없다.
        return ActionCopy(
            "참고 자료 운영자 판단",
            "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러, 글의 주장을 직접 뒷받침하는 공공·학술 기관 "
            "문서를 “참고 자료 추가”로 넣거나 제목·본문을 질환·검사 안내 글로 고쳐 저장해 주세요. "
            f"{_OPERATOR_DECIDES_CURATED_WARNING} {_OPERATOR_DECIDES_OUTCOME}",
            "참고 자료 확인",
        )
    if value == REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE:
        # 아직 쓰이지 않은 슬롯 — 콘텐츠 수정은 빈 제목·본문 편집을 연다. 고칠 원고가 없다.
        return ActionCopy(
            "참고 자료 운영자 판단",
            "아직 원고가 없는 글입니다. 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러, 질환·검사 안내 "
            "글로 제목·본문을 새로 쓰거나 글의 주장을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 "
            f"추가”로 함께 넣어 새 원고를 저장해 주세요. {_OPERATOR_DECIDES_CURATED_WARNING} "
            f"{_OPERATOR_DECIDES_UNWRITTEN_OUTCOME}",
            "참고 자료 확인",
        )
    return ActionCopy(
        "본문·근거 확인 필요",
        "콘텐츠에서 해당 글의 차단 사유와 병원 근거 자료를 확인해 주세요. 미해결 안전 지적은 승인하지 마세요.",
        "차단 사유 확인",
    )
