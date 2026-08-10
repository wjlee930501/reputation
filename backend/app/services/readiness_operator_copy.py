"""Actionable operator guidance for hospital readiness checks."""

from __future__ import annotations

from collections.abc import Mapping


def readiness_next_actions() -> Mapping[str, str]:
    """Describe impact, the exact current control, and a support fallback."""
    support = " 화면에 해당 버튼이 없으면 개발팀에 병원명과 현재 화면의 문구를 전달하세요."
    return {
        "core_profile": (
            "필수 병원 정보가 비어 있으면 온보딩과 콘텐츠 준비가 멈춥니다. 병원 기본 정보 탭에서 "
            "주소, 전화, 지역, 진료과목, 키워드, 원장, 진료항목을 입력하고 “저장”을 누르세요."
            + support
        ),
        "local_entity": (
            "지도 정보가 없으면 AI가 병원의 위치를 정확히 이해하기 어렵습니다. 병원 기본 정보 탭에서 "
            "Google 병원 정보 또는 지도 URL을 입력하고 “저장”을 누르세요." + support
        ),
        "external_profiles": (
            "공식 채널이 없으면 병원 정보의 근거를 교차 확인하기 어렵습니다. 병원 기본 정보 탭에서 "
            "홈페이지, 블로그, 카카오 채널, Naver Place 중 하나 이상을 연결하고 “저장”을 누르세요."
            + support
        ),
        "essence_sources": (
            "근거 자료 처리가 끝나지 않으면 콘텐츠 운영 기준을 만들 수 없습니다. 운영 기준 탭에서 "
            "“자료 저장” 후 각 자료의 “근거 추출”을 누르세요." + support
        ),
        "essence_philosophy": (
            "운영 기준이 승인되지 않으면 자동 콘텐츠 발행이 차단됩니다. 운영 기준 탭에서 "
            "“선택 자료로 초안 만들기” 후 내용을 검수하고 “승인”을 누르세요." + support
        ),
        "essence_freshness": (
            "새 병원 자료가 기존 운영 기준에 반영되지 않아 콘텐츠가 오래된 기준으로 멈출 수 있습니다. "
            "운영 기준 탭에서 “선택 자료로 초안 만들기”를 누르고 검수한 뒤 “승인”을 누르세요."
            + support
        ),
        "content_alignment": (
            "현재 운영 기준과 맞지 않는 콘텐츠는 공개할 수 없습니다. 운영 기준 탭에서 최신 기준을 "
            "승인한 뒤 콘텐츠 탭에서 해당 글의 “콘텐츠 수정”을 눌러 검수하세요." + support
        ),
        "v0_report": (
            "초기 AI 노출 진단이 없으면 원장님께 현재 상태를 설명할 수 없습니다. 대시보드의 "
            "“초기 진단 리포트 다시 만들기”를 누르고 완료 결과를 확인하세요." + support
        ),
        "site_built": (
            "콘텐츠 허브 준비가 끝나지 않으면 병원 정보와 콘텐츠를 공개할 수 없습니다. 온보딩의 "
            "콘텐츠 허브 준비 단계에서 공개 정보를 확인한 뒤 대시보드의 “사이트 재빌드”를 누르세요."
            + support
        ),
        "domain": (
            "공개 주소가 확인되지 않으면 환자와 AI가 병원 채널에 접속할 수 없습니다. 도메인 화면에서 "
            "주소를 저장한 뒤 “DNS 확인하고 운영 시작”을 누르세요." + support
        ),
        "schedule": (
            "발행 일정이 없으면 월간 콘텐츠가 자동 준비되지 않습니다. 스케줄 탭에서 운영량, 발행 요일, "
            "시작일을 선택하고 “스케줄 저장 및 슬롯 생성”을 누르세요." + support
        ),
        "published_content": (
            "공개 콘텐츠가 없으면 AI가 참고할 병원 설명이 쌓이지 않습니다. 콘텐츠 탭에서 글의 상태와 "
            "본문을 확인하고 “지금 발행 (운영 복구)”을 누르세요." + support
        ),
        "sov_data": (
            "AI 언급률 측정값이 없으면 병원의 현재 노출 상태와 변화를 설명할 수 없습니다. 환자 질문 "
            "탭에서 질문을 활성화한 뒤 “측정 실행”을 누르세요." + support
        ),
    }


__all__ = ("readiness_next_actions",)
