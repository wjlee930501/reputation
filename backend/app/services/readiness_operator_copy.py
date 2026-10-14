"""Actionable operator guidance for hospital readiness checks."""

from __future__ import annotations

from collections.abc import Mapping


def readiness_next_actions(
    *,
    has_content_slots: bool = False,
    withheld_content_count: int = 0,
    processing_source_count: int = 0,
) -> Mapping[str, str]:
    """Describe impact and controls that exist in the current Admin state."""
    # 자료 처리는 저장과 함께 자동으로 시작된다 — 운영자가 누를 버튼이 없다(ADM-06).
    # 남은 건수를 알면 "기다리면 되는 상태"인지 바로 알 수 있으므로 함께 말한다.
    essence_sources_progress = (
        f" 처리 중 {processing_source_count}건." if processing_source_count > 0 else ""
    )
    return {
        "core_profile": (
            "필수 병원 정보가 비어 있으면 온보딩과 콘텐츠 준비가 멈춥니다. 병원 정보 탭에서 "
            "주소, 전화, 지역, 진료과목, 키워드, 원장, 진료항목을 입력하고 “저장”을 누르세요."
        ),
        "local_entity": (
            "Google 병원 정보는 선택 항목이라 없어도 공개는 진행됩니다. 있으면 AI가 병원의 위치를 "
            "더 정확히 이해하므로, 병원 정보 탭에서 Google 병원 정보 또는 지도 URL을 입력하고 "
            "“저장”을 누르세요."
        ),
        "external_profiles": (
            "공식 채널이 없으면 병원 정보의 근거를 교차 확인하기 어렵습니다. 병원 정보 탭에서 "
            "홈페이지, 블로그, 카카오 채널, Naver Place 중 하나 이상을 연결하고 “저장”을 누르세요."
        ),
        "essence_sources": (
            "근거 자료 처리가 끝나지 않으면 콘텐츠 운영 기준을 만들 수 없습니다. 병원 정보 탭의 "
            "근거 자료를 저장하면 자동으로 처리됩니다. 처리가 끝나면 발행 일정을 저장할 수 "
            f"있습니다.{essence_sources_progress}"
        ),
        "essence_philosophy": (
            "콘텐츠 운영 기준은 병원 정보·연결 채널·원장 인터뷰 설문으로 시스템이 자동으로 만들어 "
            "바로 적용합니다. 병원 정보 탭에서 원장·진료 정보를 입력하거나 설문을 올리면 곧 만들어집니다."
        ),
        "essence_freshness": (
            "운영 기준은 원장 인터뷰 설문이나 병원 정보가 바뀌면 자동으로 다시 만들어 바로 적용합니다. "
            "홈페이지·블로그의 일반 변경은 다음 판에서 반영되며, 따로 할 일은 없습니다."
        ),
        "content_alignment": (
            "현재 운영 기준과 맞지 않는 공개 전 콘텐츠는 시스템이 새 기준으로 다시 시도합니다. "
            "콘텐츠 탭에서 해당 글의 보류 사유를 확인할 수 있습니다."
        ),
        "v0_report": (
            "초기 AI 노출 진단은 시스템이 백그라운드에서 자동으로 이어서 처리합니다. 완료를 "
            "기다리지 않고 병원 공개 페이지, 도메인, 콘텐츠 설정을 계속 진행하세요."
        ),
        "site_built": (
            "병원 공개 페이지 준비가 끝나지 않으면 병원 정보와 콘텐츠를 공개할 수 없습니다. 병원 정보 "
            "탭에서 공개 정보를 확인하세요 — 준비는 시스템이 이어서 진행하고, 자동 재시도까지 "
            "실패하면 운영 센터에 사고로 올라옵니다."
        ),
        "domain": (
            "공개 주소가 확인되지 않으면 환자와 AI가 병원 채널에 접속할 수 없습니다. 병원 정보 탭의 "
            "공개 주소에서 주소를 저장한 뒤 “DNS 확인하고 운영 시작”을 누르세요."
        ),
        "schedule": (
            "발행 일정이 없으면 월간 콘텐츠가 자동 준비되지 않습니다. 콘텐츠 탭의 발행 일정에서 월 발행 "
            "편수, 발행 요일, 시작일을 선택하고 “발행 일정 저장 및 항목 만들기”를 누르세요."
        ),
        # 발행했는데 공개 페이지가 숨기고 있으면 할 일은 새 글이 아니라 보류 사유 해소다.
        "published_content": (
            f"공개 보류 {withheld_content_count}편 — 콘텐츠 목록에서 보류 사유를 확인하세요."
            if withheld_content_count > 0
            else "공개 콘텐츠가 없으면 AI가 참고할 병원 설명이 쌓이지 않습니다. "
            + (
                "콘텐츠 탭에서 준비된 글의 상태와 예약일을 확인하세요. 예약 콘텐츠는 발행일에 자동 검증·공개됩니다."
                if has_content_slots
                else "콘텐츠 탭의 발행 일정에서 이번 달 편성과 항목 생성 상태를 확인하세요."
            )
        ),
        "sov_data": (
            "AI 언급률 측정값이 없으면 병원의 현재 노출 상태와 변화를 설명할 수 없습니다. 콘텐츠 "
            "탭의 환자 질문 신호(읽기 전용)에서 이번 달 질문과 측정 상태를 확인하세요."
        ),
    }


__all__ = ("readiness_next_actions",)
