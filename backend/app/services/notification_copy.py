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
for _kind in ("CONTENT_DISPATCH_FAILED", "SITE_BUILD_DISPATCH_FAILED", "UNSAFE_STORED_DISPATCH"):
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


def blocker_copy(code: object) -> ActionCopy:
    value = str(code or "")
    if value == "MISSING_APPROVED_ESSENCE":
        return ActionCopy(
            "운영 기준 미승인",
            "병원 정보에서 콘텐츠 운영 기준을 확인하고 승인해 주세요.",
            "운영 기준 확인",
        )
    if "IMAGE" in value:
        return ActionCopy(
            "발행용 이미지 준비 실패",
            "콘텐츠에서 이미지 오류를 확인해 주세요. 자동 재시도가 남은 글은 다시 실행하지 마세요.",
            "이미지 오류 확인",
        )
    if "COST" in value:
        return incident_copy("COST_GUARD_LIMIT_REACHED")
    if "UNCERTAIN" in value or "UNAVAILABLE" in value:
        return ActionCopy(
            "자동 검수 미완료",
            "콘텐츠에서 검수 상태와 자동 재시도 여부를 확인해 주세요.",
            "검수 상태 확인",
        )
    return ActionCopy(
        "본문·근거 확인 필요",
        "콘텐츠에서 해당 글의 차단 사유와 병원 근거 자료를 확인해 주세요. 미해결 안전 지적은 승인하지 마세요.",
        "차단 사유 확인",
    )
