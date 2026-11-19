"""원장 사전 설문의 문항 정의 — 한 곳의 정본.

Site 설문 화면은 이 정의를 API로 받아 그린다(`GET /public/doctor-survey/{token}`). 서버는 같은 정의로
답을 검사하고(`validate_answers`), 진행률을 세고(`progress`), 근거 자료 본문을 만든다
(`doctor_survey_source.render_interview_text`). 문항 문구는 마케팅팀 설문 미리보기(2026-11-19)를 따른다.

답은 평평한 사전이다. 모든 입력 칸은 설문 전체에서 겹치지 않는 ``key``를 가진다. 펼침(reveal)·
스위치 아래 칸도 같은 층에 저장한다. 줄 목록(``list``)은 줄마다 사전 하나다. 주력 진료마다 반복되는
3부 답은 ``answers["focus_answers"][진료 이름][key]``다.

칸 종류(``t``):

- 글: ``text`` ``tel`` ``area`` ``address`` → 문자열. ``number`` → 숫자 문자열.
- 고르기: ``select`` ``single`` → 문자열. ``multi`` ``tags`` → 문자열 목록. ``custom``이 있으면 보기에
  없는 값도 받는다.
- ``toggle`` → 참/거짓. ``toggles`` → 스위치 여러 개(각 항목이 ``toggle``).
- ``list`` → 줄 목록(``fields``가 한 줄의 칸).
- ``hours`` → 진료시간 사전. ``doctors`` → 의료진 목록.
- ``files`` → 올린 파일 목록(``[{id, name}]``, `doctor_survey_files`).
- 표시만: ``info`` ``sh``(소제목) ``sec``(묶음 제목).

``essence``가 있는 칸만 근거 자료(설문 자료)에 실린다. 값은 근거 노트 종류다. 필수 문구(모든 글에 원문
그대로 들어가는 원장 문장)는 진료 철학·핵심 메시지 노트에서만 고르므로(`essence_engine._select_must_use`),
그 종류는 '첫 진료 원칙'·'그 이유'·'한 줄 소개'에만 준다 — 개원 이야기처럼 긴 일화가 모든 글 끝에 붙지 않게
개원 계기는 지역·맥락 노트다. 병원 사실(이름·의료진·
주소·전화·진료시간·채널·인증·가격)과 측정 기준(검색 키워드·AI 질문·비교 병원)은 싣지 않는다 — 사실은
AE가 보고 병원 정보에 반영하고, 측정 기준은 바꾸면 지난달과 비교할 수 없게 되기 때문이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

SCHEMA_VERSION = "2026-11-19.1"
CONSENT_VERSION = "2026-11-19.1"

NOTE_PHILOSOPHY = "DOCTOR_PHILOSOPHY"
NOTE_KEY_MESSAGE = "KEY_MESSAGE"
NOTE_TONE = "TONE_SIGNAL"
NOTE_TREATMENT = "TREATMENT_SIGNAL"
NOTE_LOCAL = "LOCAL_CONTEXT"
NOTE_RISK = "RISK_SIGNAL"

RESPONDENTS = ("DIRECTOR", "STAFF")

TIMES = [f"{minute // 60:02d}:{minute % 60:02d}" for minute in range(360, 1441, 30)]
YEARS = [f"{year}년" for year in range(2026, 1989, -1)]
SPEC_GROUPS = [
    [
        "의과",
        [
            "가정의학과", "결핵과", "내과", "마취통증의학과", "방사선종양학과", "병리과",
            "비뇨의학과", "산부인과", "성형외과", "소아청소년과", "신경과", "신경외과", "안과",
            "영상의학과", "예방의학과", "외과", "응급의학과", "이비인후과", "재활의학과",
            "정신건강의학과", "정형외과", "직업환경의학과", "진단검사의학과", "피부과",
            "핵의학과", "흉부외과",
        ],
    ],
    [
        "치과",
        [
            "구강내과", "구강병리과", "구강악안면외과", "소아치과", "영상치의학과", "예방치과",
            "치과교정과", "치과보존과", "치과보철과", "치주과", "통합치의학과",
        ],
    ],
    [
        "한방",
        [
            "사상체질과", "침구과", "한방내과", "한방부인과", "한방소아과", "한방신경정신과",
            "한방안이비인후피부과", "한방재활의학과",
        ],
    ],
    ["기타", ["일반의 (전문의 자격 없음)"]],
]
DEPTS = [
    "내과", "외과", "정형외과", "신경외과", "재활의학과", "마취통증의학과", "가정의학과",
    "소아청소년과", "산부인과", "비뇨의학과", "피부과", "성형외과", "안과", "이비인후과", "신경과",
    "정신건강의학과", "영상의학과", "치과", "한의원",
]
CHANNELS = [
    ("homepage", "홈페이지"),
    ("blog", "블로그"),
    ("kakao", "카카오 채널"),
    ("instagram", "인스타그램"),
    ("youtube", "유튜브"),
    ("other", "기타"),
]
BIO_KINDS = ["학력", "수련", "이전 근무", "현재 직책", "학회 활동", "기타"]
DOCTOR_TITLES = ["대표원장", "원장", "부원장", "진료원장"]
FAQ_COMMON = [
    "수술해야 하나요?",
    "얼마나 다녀야 하나요?",
    "실비 보험이 되나요?",
    "운동해도 되나요?",
    "다른 병원에서 수술하라고 하던데요?",
]
# AI 질문 보기 — Site가 동네·진료과·주력 진료로 채운다. 비어 있으면 '우리 동네'·'진료과'·'이 진료'.
AI_QUESTION_TEMPLATES = [
    "{town}에서 주말에 진료하는 {dept} 알려줘",
    "{town} 근처에서 {focus} 하는 병원 있어?",
    "{town}에서 {focus} 잘하는 병원 비교해줘",
    "{focus} 진료비는 얼마 정도야?",
    "부모님 모시고 갈 만한 {town} 근처 병원 알려줘",
]
AI_QUESTION_FALLBACKS = {"town": "우리 동네", "dept": "진료과", "focus": "이 진료"}

FOCUS_ANSWERS_KEY = "focus_answers"
FOCUS_MAX = 5
# 진료 이름은 다른 칸(진료비의 진료 선택·주력 진료)의 보기 값이 된다 — 보기 값 한도(200자)와 맞춘다.
MAX_OPTION_INPUT = 200

# ── 문항 ────────────────────────────────────────────────────────────────


def _channel_reveal() -> dict[str, list[dict[str, Any]]]:
    return {
        label: [{"t": "text", "key": f"channel_url_{key}", "label": f"{label} 주소", "ph": "https://"}]
        for key, label in CHANNELS
    }


PARTS: list[dict[str, Any]] = [
    {
        "name": "기본 정보",
        "sum": "병원·의료진, 진료시간, 연락처",
        "qs": [
            {
                "id": "clinic",
                "n": "",
                "t": "병원명·진료과",
                "d": "진료과는 여러 개 고르셔도 됩니다.",
                "b": [
                    {"t": "text", "key": "clinic_name", "label": "병원명", "ph": "예) ○○정형외과의원"},
                    {"t": "multi", "key": "departments", "label": "진료과", "opts": DEPTS, "custom": True, "role": "dept"},
                ],
            },
            {
                "id": "doctors",
                "n": "1-4",
                "t": "의료진 정보",
                "d": "원장님이 여러 분이시면 아래 \"의료진 추가\"를 눌러 주시면 됩니다. 대표원장 외 의료진은 이름과 직함만 적어 주셔도 됩니다.",
                "b": [{"t": "doctors", "key": "doctors"}],
            },
            {
                "id": "treatments",
                "n": "5",
                "t": "진료 항목",
                "d": "진료마다 한 줄 설명을 붙여 주시면 됩니다.",
                "b": [
                    {
                        "t": "list",
                        "key": "treatments",
                        "row_label": "진료",
                        "add": "진료 추가",
                        "rows": 1,
                        "essence": NOTE_TREATMENT,
                        "fields": [
                            {"t": "text", "key": "name", "label": "진료 이름", "ph": "예) 체외충격파 치료", "role": "treat", "max": MAX_OPTION_INPUT},
                            {"t": "text", "key": "summary", "label": "한 줄 설명", "ph": "예) 통증 부위에 충격파를 쏘는 비수술 치료"},
                        ],
                    }
                ],
            },
            {
                "id": "contact",
                "n": "6",
                "t": "병원 주소·대표 전화번호",
                "b": [
                    {"t": "address", "key": "address", "label": "도로명 주소", "ph": "예) 서울 강남구 ○○로 123"},
                    {"t": "text", "key": "address_detail", "label": "층·호수", "ph": "예) 3층 301호"},
                    {"t": "tel", "key": "phone", "label": "대표 전화번호", "ph": "02-000-0000"},
                ],
            },
            {
                "id": "hours",
                "n": "7",
                "t": "진료시간",
                "d": "진료를 하지 않는 날은 스위치를 꺼 주시면 됩니다.",
                "b": [
                    {"t": "hours", "key": "hours"},
                    {
                        "t": "single",
                        "key": "holiday",
                        "label": "공휴일 진료",
                        "opts": ["휴진", "진료", "공휴일마다 다름"],
                        "reveal": {
                            "진료": [
                                {"t": "select", "key": "holiday_start", "label": "시작", "opts": TIMES},
                                {"t": "select", "key": "holiday_end", "label": "끝", "opts": TIMES},
                            ]
                        },
                    },
                    {
                        "t": "single",
                        "key": "biweekly_off",
                        "label": "격주 휴진",
                        "opts": ["없음", "있음"],
                        "reveal": {
                            "있음": [
                                {"t": "single", "key": "biweekly_day", "label": "쉬는 요일", "opts": ["월", "화", "수", "목", "금", "토"]}
                            ]
                        },
                    },
                ],
            },
            {
                "id": "prices",
                "n": "8",
                "t": "공개 가능한 비급여 진료비",
                "why": "환자가 AI에게 진료비를 물을 때 답변에 반영하겠습니다. 적어 주신 가격은 제출일 기준으로 안내합니다.",
                "b": [
                    {
                        "t": "single",
                        "key": "prices_public",
                        "opts": ["있음", "공개 안 함"],
                        "reveal": {
                            "있음": [
                                {
                                    "t": "list",
                                    "key": "prices",
                                    "row_label": "진료비",
                                    "add": "진료비 추가",
                                    "rows": 1,
                                    "fields": [
                                        {"t": "select", "key": "treatment", "label": "진료", "src": "treat", "custom": True},
                                        {
                                            "t": "toggle",
                                            "key": "price_range",
                                            "label": "범위 가격",
                                            "children_off": [
                                                {"t": "number", "key": "price", "label": "가격", "unit": "원", "ph": "50000"}
                                            ],
                                            "children": [
                                                {"t": "number", "key": "price_min", "label": "최소", "unit": "원"},
                                                {"t": "number", "key": "price_max", "label": "최대", "unit": "원"},
                                            ],
                                        },
                                    ],
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "id": "google",
                "n": "9",
                "t": "구글 지도 등록 여부",
                "why": "Gemini는 구글 지도의 병원 정보를 참고해 답변합니다.",
                "b": [
                    {
                        "t": "single",
                        "key": "google_status",
                        "col": True,
                        "opts": ["병원에서 관리 중", "등록만 되어 있음", "등록 안 됨", "모름"],
                        "reveal": {
                            "병원에서 관리 중": [
                                {
                                    "t": "toggle",
                                    "key": "google_share",
                                    "label": "관리 권한 공유",
                                    "children": [
                                        {
                                            "t": "info",
                                            "text": "구글 비즈니스 프로필 > 사용자 > 사용자 추가에서 담당자가 알려 드리는 이메일을 넣어 주시면 됩니다.",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "id": "channels",
                "n": "11",
                "t": "운영 중인 병원 채널",
                "b": [
                    {"t": "multi", "key": "channels", "opts": [label for _key, label in CHANNELS], "reveal": _channel_reveal()},
                    {"t": "tags", "key": "stopped_channels", "label": "운영을 멈춘 채널", "ph": "예) 예전 블로그 주소"},
                ],
            },
            {
                "id": "certifications",
                "n": "13",
                "t": "병원 인증·지정",
                "d": "받으신 인증이나 지정이 없으면 건너뛰셔도 됩니다.",
                "b": [
                    {
                        "t": "list",
                        "key": "certifications",
                        "row_label": "인증",
                        "add": "인증 추가",
                        "rows": 1,
                        "fields": [
                            {"t": "text", "key": "name", "label": "이름", "ph": "예) 보건복지부 지정 ○○전문병원, 의료기관 인증"},
                            {"t": "select", "key": "year", "label": "받은 해", "opts": YEARS},
                            {"t": "files", "key": "files", "label": "지정서·인증서 사진", "accept": "image/*,.pdf", "kind": "CERTIFICATE", "max": 5},
                        ],
                    }
                ],
            },
            {
                "id": "media",
                "n": "14·15",
                "t": "사진·로고",
                "b": [
                    {
                        "t": "sec",
                        "title": "병원 페이지용 사진",
                        "d": "원장님 사진과 병원 외관·내부·진료실 사진을 한 번에 올려 주시면 됩니다. 환자 얼굴이 나온 사진과 전후 사진은 사용하지 않겠습니다.",
                    },
                    {"t": "files", "key": "photos", "label": "사진 올리기", "accept": "image/*", "kind": "PHOTO", "sub": "여러 장을 한 번에 고를 수 있음 · 30장까지", "max": 30},
                    {"t": "toggle", "key": "photo_consent", "label": "병원 페이지에 사진 사용 동의"},
                    {"t": "sec", "title": "병원 로고", "d": "선호하시는 색이 있으면 함께 적어 주시면 됩니다."},
                    {"t": "files", "key": "logo_files", "label": "로고 파일", "accept": "image/png,image/jpeg,image/webp", "kind": "LOGO", "sub": "PNG·JPG 파일 (AI·SVG 파일은 담당자에게 따로 보내 주세요)", "max": 5},
                    {"t": "text", "key": "logo_color", "label": "선호하시는 색", "ph": "예) 로고에 쓰인 남색, 초록 계열"},
                ],
            },
        ],
    },
    {
        "name": "환자 질문",
        "sum": "환자가 AI에게 하는 질문",
        "qs": [
            {
                "id": "focus",
                "n": "16",
                "t": "늘리고 싶은 진료",
                "d": "최대 5개까지 적으실 수 있습니다.",
                "b": [
                    {"t": "tags", "key": "focus_treatments", "max": FOCUS_MAX, "ph": "예) 체외충격파, 대장내시경, 임플란트", "role": "focus", "essence": NOTE_TREATMENT, "render_label": "늘리고 싶은 진료"}
                ],
            },
            {
                "id": "patients",
                "n": "20·28·29",
                "t": "환자 정보",
                "b": [
                    {"t": "sec", "title": "환자들이 오는 동네"},
                    {"t": "tags", "key": "towns", "ph": "예) 도곡동, 개포동", "role": "town", "essence": NOTE_LOCAL, "render_label": "환자들이 오는 동네"},
                    {"t": "sec", "title": "주요 환자층"},
                    {"t": "multi", "key": "patient_groups", "custom": True, "opts": ["학생", "직장인", "주부", "어르신", "관광객·외지인", "외국인"], "essence": NOTE_LOCAL, "render_label": "주요 환자층"},
                    {"t": "sec", "title": "동네 환자들에게 많은 증상·생활 습관"},
                    {"t": "area", "key": "local_symptoms", "ph": "예) 농사일을 하는 어르신이 많아서 어깨·무릎 통증 환자가 많음", "essence": NOTE_LOCAL, "render_label": "동네 환자들에게 많은 증상·생활 습관"},
                ],
            },
            {
                "id": "keywords",
                "n": "21",
                "t": "우리 병원이 검색됐으면 하는 키워드",
                "d": "환자가 이 단어로 찾았을 때 우리 병원이 보이도록 글과 페이지에 반영하겠습니다. 지역 이름은 빼고 적어 주시면 됩니다.",
                "why": "\"명의\", \"최고\", \"1등\", \"전문\" 같은 표현은 의료광고에 쓸 수 없어 반영하지 않습니다.",
                "b": [{"t": "tags", "key": "search_keywords", "ph": "예) 주말 진료, 영유아 검진, 어깨 통증"}],
            },
            {
                "id": "ai_questions",
                "n": "22",
                "t": "우리 병원이 나왔으면 하는 AI 질문",
                "d": "환자가 AI에게 하는 질문입니다. 원하시는 질문은 개수 제한 없이 추가하실 수 있습니다.",
                "b": [{"t": "multi", "key": "ai_questions", "src": "aiq", "col": True, "custom": True, "custom_ph": "원하시는 질문 입력"}],
            },
            {
                "id": "competitors",
                "n": "23",
                "t": "환자들이 비교하는 근처 병원",
                "d": "AI 답변 분석에만 활용하겠습니다.",
                "b": [{"t": "tags", "key": "competitors", "ph": "병원 이름"}],
            },
            {
                "id": "intro",
                "n": "24·26",
                "t": "병원 소개",
                "b": [
                    {"t": "sec", "title": "병원 한 줄 소개", "d": "환자가 \"○○동에 ○○ 잘하는 병원 있어?\"라고 물을 때 AI가 쓸 소개 문장입니다."},
                    {"t": "text", "key": "one_line_intro", "max": 50, "ph": "예) 초음파로 원인을 확인하고 비수술 치료부터 권하는 정형외과", "essence": NOTE_KEY_MESSAGE, "render_label": "병원 한 줄 소개"},
                    {"t": "sec", "title": "AI 답변에 넣을 병원 특징", "d": "여러 개 고르셔도 됩니다."},
                    {
                        "t": "multi",
                        "key": "features",
                        "custom": True,
                        "opts": ["전문의 진료·시술", "당일 검사·결과 안내", "비수술 치료부터 시작", "자세한 설명", "짧은 대기 시간", "야간·주말 진료", "편리한 주차", "여성 의료진"],
                        "essence": NOTE_TONE,
                        "render_label": "AI 답변에 넣을 병원 특징",
                    },
                ],
            },
            {
                "id": "environment",
                "n": "27",
                "t": "진료 환경",
                "b": [
                    {
                        "t": "multi",
                        "key": "environment",
                        "opts": ["예약 없이 당일 진료", "주차 가능", "엘리베이터·휠체어 이용", "보호자 대기 공간", "외국어 진료"],
                        "reveal": {
                            "주차 가능": [{"t": "number", "key": "parking_spaces", "label": "주차 대수", "unit": "대"}],
                            "외국어 진료": [{"t": "multi", "key": "languages", "label": "언어", "opts": ["영어", "중국어", "일본어", "기타"]}],
                        },
                        "essence": NOTE_LOCAL,
                        "render_label": "진료 환경",
                    },
                    {"t": "single", "key": "wait_time", "label": "평균 대기 시간", "opts": ["10분 이내", "10~30분", "30분 이상"]},
                ],
            },
        ],
    },
    {"name": "진료별 답변", "sum": "주력 진료마다 질문 3개", "focus": True, "qs": []},
    {
        "name": "자주 묻는 질문",
        "sum": "진료실에서 자주 듣는 질문",
        "qs": [
            {
                "id": "faq",
                "n": "35",
                "t": "진료실에서 자주 받는 질문",
                "d": "질문을 누르고 평소 답변을 한 줄로 적어 주시면 됩니다. 환자들은 AI에게도 같은 질문을 합니다.",
                "b": [
                    {
                        "t": "toggles",
                        "items": [
                            {
                                "t": "toggle",
                                "key": f"faq_{index}",
                                "label": question,
                                "children": [
                                    {"t": "text", "key": f"faq_{index}_answer", "ph": "평소 답변 한 줄", "essence": NOTE_TREATMENT, "render_label": f"자주 받는 질문 '{question}'에 대한 답"}
                                ],
                            }
                            for index, question in enumerate(FAQ_COMMON, start=1)
                        ],
                    },
                    {
                        "t": "list",
                        "key": "faq_custom",
                        "row_label": "질문",
                        "add": "다른 질문 추가",
                        "rows": 0,
                        "essence": NOTE_TREATMENT,
                        "fields": [
                            {"t": "text", "key": "question", "label": "질문"},
                            {"t": "text", "key": "answer", "label": "답변"},
                        ],
                    },
                ],
            }
        ],
    },
    {
        "name": "원장님 이야기",
        "sum": "진료 원칙, 설명 방식, 글 말투",
        "qs": [
            {
                "id": "opening",
                "n": "37",
                "new_clinic_only": True,
                "t": "개원 계기와 지역 선택 이유",
                "b": [
                    {"t": "area", "key": "opening_story", "ph": "예) 대학병원에서 수술 대기만 몇 달씩 하는 환자들을 보며…", "essence": NOTE_LOCAL, "render_label": "개원 계기와 지역 선택 이유"}
                ],
            },
            {
                "id": "approach",
                "n": "38·39",
                "t": "진료·설명 방식",
                "b": [
                    {"t": "sec", "title": "첫 진료 원칙", "d": "원칙 하나와 그 이유를 적어 주시면 됩니다."},
                    {"t": "text", "key": "principle", "label": "원칙", "ph": "예) 검사 화면을 같이 보며 설명한 다음 치료를 정함", "essence": NOTE_PHILOSOPHY, "render_label": "첫 진료 원칙"},
                    {"t": "text", "key": "principle_reason", "label": "이유", "ph": "예) 환자가 이해하지 못한 치료는 오래가기 어려움", "essence": NOTE_PHILOSOPHY, "render_label": "그 원칙의 이유"},
                    {"t": "sec", "title": "환자 설명 방법"},
                    {"t": "multi", "key": "explain_methods", "custom": True, "opts": ["검사 화면 함께 보기", "그림·모형", "설명 자료 제공"], "essence": NOTE_TONE, "render_label": "환자 설명 방법"},
                ],
            },
            {
                "id": "tone",
                "n": "40",
                "t": "병원 글 말투",
                "b": [
                    {
                        "t": "single",
                        "key": "tone",
                        "col": True,
                        "opts": [
                            ["차분한 전문가", "\"검사 결과를 확인한 뒤 치료 방향을 정합니다.\""],
                            ["친근한 동네 의사", "\"어깨가 아프면 참지 말고 들러 주세요.\""],
                            ["쉬운 비유", "\"연골은 무릎 사이의 쿠션 역할을 합니다.\""],
                            "직접 입력",
                        ],
                        "reveal": {"직접 입력": [{"t": "text", "key": "tone_custom", "ph": "원하는 말투", "essence": NOTE_TONE, "render_label": "원하는 글 말투(직접 입력)"}]},
                        "essence": NOTE_TONE,
                        "render_label": "원하는 글 말투",
                    }
                ],
            },
        ],
    },
    {
        "name": "주의 사항",
        "sum": "글에 쓰지 않을 표현·진료",
        "qs": [
            {
                "id": "avoid",
                "n": "41",
                "t": "글에 쓰면 안 되는 표현·정보",
                "d": "알리고 싶지 않은 진료도 여기에 적어 주시면 됩니다.",
                "b": [
                    {
                        "t": "single",
                        "key": "avoid_any",
                        "opts": ["있음", "없음"],
                        "reveal": {
                            "있음": [
                                {"t": "multi", "key": "avoid_kinds", "label": "종류", "opts": ["가격", "장비 이름", "특정 진료", "경쟁 병원 언급", "기타"], "essence": NOTE_RISK, "render_label": "글에 쓰지 말아야 할 정보의 종류"},
                                {"t": "text", "key": "avoid_detail", "label": "내용", "ph": "예) \"통증 완치\" 같은 표현은 쓰지 않기", "essence": NOTE_RISK, "render_label": "글에 쓰지 말아야 할 표현·정보"},
                            ]
                        },
                    }
                ],
            }
        ],
    },
]

# 주력 진료마다 반복되는 3부 문항. ``{nm}``은 진료 이름이다.
FOCUS_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "recommend",
        "n": "30",
        "t": "{nm} 권하는 경우와 권하지 않는 경우",
        "d": "환자가 \"이 증상이면 {nm} 받아야 하나요?\"라고 물을 때 답변에 반영하겠습니다.",
        "b": [
            {"t": "text", "key": "recommend_when", "label": "권하는 경우", "ph": "예) 통증이 6주 넘게 이어질 때", "essence": NOTE_TREATMENT, "render_label": "권하는 경우"},
            {"t": "text", "key": "not_recommend_when", "label": "권하지 않는 경우", "ph": "예) 다친 지 일주일이 안 됐을 때", "essence": NOTE_TREATMENT, "render_label": "권하지 않는 경우"},
        ],
    },
    {
        "id": "process",
        "n": "31",
        "t": "{nm} 진행 과정",
        "d": "환자가 \"아픈가요? 얼마나 걸리나요?\"라고 물을 때 답변에 반영하겠습니다.",
        "b": [
            {"t": "multi", "key": "pre_tests", "label": "사전 검사", "none": "없음", "opts": ["없음", "X-ray", "초음파", "MRI", "CT", "혈액검사", "내시경", "기타"], "essence": NOTE_TREATMENT, "render_label": "사전 검사"},
            {"t": "single", "key": "duration", "label": "치료 시간", "opts": ["10분 이내", "10~30분", "30분~1시간", "1시간 이상"], "essence": NOTE_TREATMENT, "render_label": "치료 시간"},
            {"t": "single", "key": "anesthesia", "label": "마취", "opts": ["없음", "국소", "수면", "전신"], "essence": NOTE_TREATMENT, "render_label": "마취"},
            {
                "t": "single",
                "key": "admission",
                "label": "입원 여부",
                "opts": ["당일 귀가", "입원 필요"],
                "reveal": {"입원 필요": [{"t": "number", "key": "admission_days", "label": "입원 기간", "unit": "일", "essence": NOTE_TREATMENT, "render_label": "입원 기간(일)"}]},
                "essence": NOTE_TREATMENT,
                "render_label": "입원 여부",
            },
            {"t": "number", "key": "visits", "label": "내원 횟수", "unit": "회", "essence": NOTE_TREATMENT, "render_label": "내원 횟수(회)"},
            {"t": "single", "key": "recovery", "label": "일상 복귀까지", "opts": ["당일", "1~3일", "1주", "2주 이상"], "essence": NOTE_TREATMENT, "render_label": "일상 복귀까지"},
        ],
    },
    {
        "id": "care",
        "n": "33·34",
        "t": "응급 기준·치료 후 당부",
        "b": [
            {"t": "sec", "title": "응급 기준", "d": "병원에 와야 하는 증상과 큰 병원으로 가야 하는 경우입니다."},
            {"t": "text", "key": "emergency_visit", "label": "병원에 와야 하는 증상", "ph": "예) 저림과 함께 힘이 빠질 때", "essence": NOTE_TREATMENT, "render_label": "병원에 와야 하는 증상"},
            {"t": "text", "key": "emergency_refer", "label": "큰 병원으로 가야 하는 경우", "ph": "예) 다친 뒤 관절 모양이 변해 보일 때", "essence": NOTE_TREATMENT, "render_label": "큰 병원으로 가야 하는 경우"},
            {"t": "sec", "title": "치료 후 당부", "d": "한 줄이면 충분합니다."},
            {"t": "text", "key": "aftercare", "ph": "예) 치료 당일에는 무리한 운동 피하기", "essence": NOTE_TREATMENT, "render_label": "치료 후 당부"},
        ],
    },
]

INTRO = {
    "title": "사전 설문",
    "desc": "적어주신 내용을 기반으로, AI 병원 페이지에 들어가는 것들을 구성하겠습니다.",
    "badges": ["15~20분 소요", "자동 저장", "건너뛰기 가능"],
    "notice": "환자 치료 경험담, 치료 전후 사진, 기준일 없는 수치는 받지 않습니다.",
    "respondent_label": "작성하시는 분",
    "respondents": [["DIRECTOR", "원장님"], ["STAFF", "병원 직원(대리 작성)"]],
    # 동의 문구는 사용자 확정(2026-11-19). 바꾸면 `CONSENT_VERSION`을 올려 원장에게 다시 동의를 받는다.
    "consent": {
        "version": CONSENT_VERSION,
        "title": "개인정보 수집·이용 동의",
        "items": [
            "수집 항목: 원장님·의료진 성함, 학력·경력·자격, 병원 정보, 설문 답변, 올려 주신 사진·파일",
            "이용 목적: 병원 AI 페이지와 콘텐츠 제작, AI 답변 분석",
            "보유 기간: 서비스 계약 기간 동안 보관하며, 삭제를 요청하시면 지체 없이 파기합니다.",
            "답변에 적힌 휴대전화번호·이메일·주민등록번호는 저장할 때 자동으로 가립니다. 병원 대표 전화번호 칸은 그대로 저장합니다.",
        ],
        "label": "위 내용에 동의합니다.",
    },
}
REVIEW = {"title": "제출 전 확인", "desc": "비어 있는 문항을 눌러 채우시거나 그대로 제출하셔도 됩니다."}
DONE = {
    "title": "제출 완료",
    "desc": "담당자가 확인 후 2~3일 안에 연락드리겠습니다.",
    "edit_note": "제출하신 내용은 이 링크에서 수정하실 수 있습니다.",
}
FILES_PENDING_NOTICE = "파일은 담당자에게 따로 보내 주세요. 곧 이 화면에서 바로 올리실 수 있게 됩니다."


def public_schema(*, uploads_enabled: bool = False) -> dict[str, Any]:
    """Site 화면이 그릴 정의 전체."""

    return {
        "version": SCHEMA_VERSION,
        "intro": INTRO,
        "review": REVIEW,
        "done": DONE,
        "parts": PARTS,
        "focus_questions": FOCUS_QUESTIONS,
        "focus_key": FOCUS_ANSWERS_KEY,
        "focus_max": FOCUS_MAX,
        "ai_question_templates": AI_QUESTION_TEMPLATES,
        "ai_question_fallbacks": AI_QUESTION_FALLBACKS,
        "spec_groups": SPEC_GROUPS,
        "times": TIMES,
        "bio_kinds": BIO_KINDS,
        "doctor_titles": DOCTOR_TITLES,
        "uploads_enabled": uploads_enabled,
        "files_pending_notice": FILES_PENDING_NOTICE,
    }


# ── 칸 훑기 ──────────────────────────────────────────────────────────────


def option_value(option: Any) -> str:
    """보기는 문자열이거나 [이름, 설명]이다."""

    if isinstance(option, (list, tuple)):
        return str(option[0])
    return str(option)


def iter_fields(fields: Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    """같은 층(펼침·스위치 아래 포함)의 입력 칸. 줄 목록의 줄 안 칸은 내려가지 않는다."""

    for field in fields:
        kind = field.get("t")
        if kind == "toggles":
            yield from iter_fields(field.get("items") or [])
            continue
        if field.get("key"):
            yield field
        for children in (field.get("reveal") or {}).values():
            yield from iter_fields(children)
        yield from iter_fields(field.get("children") or [])
        yield from iter_fields(field.get("children_off") or [])


def question_fields(question: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(iter_fields(question.get("b") or []))


def active_fields(
    fields: Iterable[Mapping[str, Any]], scope: Mapping[str, Any]
) -> Iterator[Mapping[str, Any]]:
    """지금 화면에 보이는 칸만 — 고른 보기의 펼침, 켠 스위치의 아래 칸.

    원장이 보기를 바꾸거나 스위치를 끄면 그 아래에 적어 둔 답은 저장돼 있어도 쓰지 않는다.
    """

    for spec in fields:
        kind = spec.get("t")
        if kind == "toggles":
            yield from active_fields(spec.get("items") or [], scope)
            continue
        key = spec.get("key")
        if key:
            yield spec
        value = scope.get(str(key)) if key else None
        reveal = spec.get("reveal") or {}
        if reveal:
            chosen = set(value) if isinstance(value, list) else {value}
            for option, children in reveal.items():
                if option in chosen:
                    yield from active_fields(children, scope)
        if kind == "toggle":
            yield from active_fields(spec.get("children" if value is True else "children_off") or [], scope)


def all_questions() -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for part in PARTS:
        for question in part["qs"]:
            yield part, question


def _field_index(fields: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for field in fields:
        key = str(field["key"])
        if key in index:
            raise RuntimeError(f"설문 칸 key가 겹친다: {key}")
        index[key] = field
    return index


TOP_FIELDS = _field_index(
    field for _part, question in all_questions() for field in question_fields(question)
)
FOCUS_FIELDS = _field_index(
    field for question in FOCUS_QUESTIONS for field in question_fields(question)
)
if FOCUS_ANSWERS_KEY in TOP_FIELDS:  # pragma: no cover — 정의 실수
    raise RuntimeError("focus_answers는 예약된 key다")


# ── 답 검사 ──────────────────────────────────────────────────────────────

MAX_TEXT = 500
MAX_AREA = 3000
MAX_OPTION = 200
MAX_ITEMS = 50
MAX_ROWS = 30
MAX_DOCTORS = 20
MAX_BIO_ROWS = 30
MAX_FILES = 30
_NUMBER = re.compile(r"^\d{1,12}$")
_TIME = re.compile(r"^(?:[01]\d|2[0-4]):[0-5]\d$")
_FILE_ID = re.compile(r"^[0-9a-f]{32}$")
_ROW_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HOURS_DAYS = ("weekday", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
HOURS_DAY_FLAGS = ("open", "lunch", "night")
HOURS_DAY_TIMES = ("start", "end", "last", "lunch_start", "lunch_end", "night_end")


class SurveyAnswerError(ValueError):
    """받지 않는 답. 메시지는 화면에 그대로 보여 줄 수 있다."""


def _bad(key: str) -> SurveyAnswerError:
    return SurveyAnswerError(f"'{key}' 답의 형식이 맞지 않습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.")


def _clean_str(value: Any, key: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _bad(key)
    value = value.replace("\x00", "").strip()
    if len(value) > limit:
        raise SurveyAnswerError(f"답이 너무 깁니다. {limit}자 안으로 줄여 주세요.")
    return value


def _clean_str_list(value: Any, key: str, *, limit: int = MAX_ITEMS) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise _bad(key)
    out: list[str] = []
    for item in value:
        text = _clean_str(item, key, MAX_OPTION)
        if text and text not in out:
            out.append(text)
    return out


def _allowed(field: Mapping[str, Any]) -> set[str] | None:
    """보기 밖 값을 받지 않는 칸이면 허용 값 집합. 받으면 None."""

    if field.get("custom") or field.get("src"):
        return None
    opts = field.get("opts")
    if not opts:
        return None
    return {option_value(option) for option in opts}


def _clean_files(value: Any, key: str, limit: int = MAX_FILES) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _bad(key)
    if len(value) > limit:
        raise SurveyAnswerError(f"파일은 이 칸에 {limit}개까지 올릴 수 있습니다. 나머지는 담당자에게 보내 주세요.")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _bad(key)
        file_id = item.get("id")
        if not isinstance(file_id, str) or not _FILE_ID.match(file_id):
            raise _bad(key)
        out.append({"id": file_id, "name": _clean_str(item.get("name"), key, 200)})
    return out


def _clean_hours(value: Any, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _bad(key)
    out: dict[str, Any] = {}
    same = value.get("same")
    if same is not None:
        if not isinstance(same, bool):
            raise _bad(key)
        out["same"] = same
    for day in HOURS_DAYS:
        raw = value.get(day)
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise _bad(key)
        day_out: dict[str, Any] = {}
        for flag in HOURS_DAY_FLAGS:
            if raw.get(flag) is not None:
                if not isinstance(raw.get(flag), bool):
                    raise _bad(key)
                day_out[flag] = raw[flag]
        for name in HOURS_DAY_TIMES:
            time = _clean_str(raw.get(name), key, 5)
            if time:
                if not _TIME.match(time):
                    raise _bad(key)
                day_out[name] = time
        out[day] = day_out
    return out


def _clean_doctors(value: Any, key: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_DOCTORS:
        raise _bad(key)
    out: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise _bad(key)
        doctor: dict[str, Any] = {
            "name": _clean_str(raw.get("name"), key, 100),
            "title": _clean_str(raw.get("title"), key, 50),
            "school": _clean_str(raw.get("school"), key, 200),
            "specialty": _clean_str(raw.get("specialty"), key, 100),
            "societies": _clean_str_list(raw.get("societies"), key),
        }
        bio_raw = raw.get("bio")
        if bio_raw is not None and (not isinstance(bio_raw, list) or len(bio_raw) > MAX_BIO_ROWS):
            raise _bad(key)
        bio: list[dict[str, Any]] = []
        for row in bio_raw or []:
            if not isinstance(row, Mapping):
                raise _bad(key)
            kind = _clean_str(row.get("kind"), key, 20)
            if kind and kind not in BIO_KINDS:
                raise _bad(key)
            star = row.get("star", False)
            if not isinstance(star, bool):
                raise _bad(key)
            bio.append({"kind": kind, "text": _clean_str(row.get("text"), key, 300), "star": star})
        if sum(1 for row in bio if row["star"]) > 3:
            raise SurveyAnswerError("대표 경력은 3개까지 고르실 수 있습니다.")
        doctor["bio"] = bio
        out.append(doctor)
    return out


def _clean_value(field: Mapping[str, Any], value: Any) -> Any:
    key = str(field["key"])
    kind = field.get("t")
    if kind in {"text", "tel", "address"}:
        limit = int(field.get("max") or MAX_TEXT)
        return _clean_str(value, key, max(limit, 1))
    if kind == "area":
        return _clean_str(value, key, MAX_AREA)
    if kind == "number":
        text = _clean_str(value if not isinstance(value, int) else str(value), key, 12)
        if text and not _NUMBER.match(text):
            raise SurveyAnswerError("숫자만 적어 주세요.")
        return text
    if kind in {"select", "single"}:
        text = _clean_str(value, key, MAX_OPTION)
        allowed = _allowed(field)
        if text and allowed is not None and text not in allowed:
            raise _bad(key)
        return text
    if kind in {"multi", "tags"}:
        values = _clean_str_list(value, key)
        allowed = _allowed(field)
        if allowed is not None and any(item not in allowed for item in values):
            raise _bad(key)
        if field.get("max") and len(values) > int(field["max"]):
            raise SurveyAnswerError(f"{field['max']}개까지 적으실 수 있습니다.")
        none = field.get("none")
        if none and none in values and len(values) > 1:
            values = [none]
        return values
    if kind == "toggle":
        if value is None:
            return None
        if not isinstance(value, bool):
            raise _bad(key)
        return value
    if kind == "list":
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > MAX_ROWS:
            raise _bad(key)
        row_fields = _field_index(iter_fields(field.get("fields") or []))
        rows: list[dict[str, Any]] = []
        for row in value:
            if not isinstance(row, Mapping):
                raise _bad(key)
            cleaned_row = _clean_mapping(row, row_fields)
            # 화면이 붙이는 줄 id — 늦게 끝난 파일 올리기가 원래 줄을 찾게 한다. 근거 자료·제안에는 쓰지 않는다.
            row_id = row.get("_rid")
            if isinstance(row_id, str) and _ROW_ID.match(row_id):
                cleaned_row["_rid"] = row_id
            rows.append(cleaned_row)
        return rows
    if kind == "hours":
        return _clean_hours(value, key)
    if kind == "doctors":
        return _clean_doctors(value, key)
    if kind == "files":
        return _clean_files(value, key, int(field.get("max") or MAX_FILES))
    raise _bad(key)  # pragma: no cover — 정의에 없는 종류


def _clean_mapping(raw: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """알려진 key만 남긴다(모르는 key는 버린다 — 옛 화면이 보낸 칸)."""

    out: dict[str, Any] = {}
    for key, value in raw.items():
        field = fields.get(str(key))
        if field is None:
            continue
        cleaned = _clean_value(field, value)
        if cleaned in (None, "", [], {}):
            continue
        out[str(key)] = cleaned
    return out


def validate_answers(raw: Any) -> dict[str, Any]:
    """화면이 보낸 답을 검사해 저장할 모양으로 돌려준다. 받지 않는 답은 :class:`SurveyAnswerError`."""

    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise SurveyAnswerError("답의 형식이 맞지 않습니다.")
    answers = _clean_mapping(raw, TOP_FIELDS)
    focus_raw = raw.get(FOCUS_ANSWERS_KEY)
    if focus_raw is not None:
        if not isinstance(focus_raw, Mapping) or len(focus_raw) > MAX_ITEMS:
            raise _bad(FOCUS_ANSWERS_KEY)
        focus: dict[str, Any] = {}
        for name, block in focus_raw.items():
            treatment = _clean_str(name, FOCUS_ANSWERS_KEY, MAX_OPTION)
            if not treatment or not isinstance(block, Mapping):
                continue
            cleaned = _clean_mapping(block, FOCUS_FIELDS)
            if cleaned:
                focus[treatment] = cleaned
        if focus:
            answers[FOCUS_ANSWERS_KEY] = focus
    return answers


# ── 개인정보 가림 ────────────────────────────────────────────────────────

#: 가리지 않는 칸 — 병원 대표 전화번호(원장 휴대전화가 아니라 병원 사실)와 숫자 칸.
UNMASKED_KEYS = frozenset(
    {"phone"}
    | {
        str(spec["key"])
        for spec in [*TOP_FIELDS.values(), *FOCUS_FIELDS.values()]
        for spec in [spec, *iter_fields(spec.get("fields") or [])]
        if spec.get("t") == "number"
    }
)


def mask_answers(value: Any, key: str | None = None) -> Any:
    """저장 전에 답 안의 휴대전화·이메일·주민등록번호를 가린다(동의 화면의 약속, E6과 같은 규칙).

    작성 중인 답(`doctor_surveys.answers`)도, 제출한 답도, 근거 자료도 모두 가린 값만 저장한다.
    """

    from app.services.survey_sources import mask_personal_info

    if key in UNMASKED_KEYS:
        return value
    if isinstance(value, str):
        return mask_personal_info(value)
    if isinstance(value, list):
        return [mask_answers(item, key) for item in value]
    if isinstance(value, dict):
        # 진료별 답의 key는 원장이 적은 진료 이름이다 — `focus_treatments`와 같은 값으로 가려야 서로 맞는다.
        if key == FOCUS_ANSWERS_KEY:
            return {mask_personal_info(str(name)): mask_answers(item) for name, item in value.items()}
        return {name: mask_answers(item, str(name)) for name, item in value.items()}
    return value


# ── 진행률 ───────────────────────────────────────────────────────────────


def _has_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        # 줄 id(`_rid`)는 답이 아니다.
        return any(_has_value(item) for key, item in value.items() if key != "_rid")
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return True


def visible_questions(answers: Mapping[str, Any], *, new_clinic: bool) -> list[tuple[str, Mapping[str, Any], str | None]]:
    """(화면 key, 문항, 주력 진료 이름). Site 화면의 순서와 같다."""

    focus = [str(item) for item in (answers.get("focus_treatments") or [])][:FOCUS_MAX]
    out: list[tuple[str, Mapping[str, Any], str | None]] = []
    for part in PARTS:
        if part.get("focus"):
            for name in focus:
                for question in FOCUS_QUESTIONS:
                    out.append((f"focus|{name}|{question['id']}", question, name))
            continue
        for question in part["qs"]:
            if question.get("new_clinic_only") and not new_clinic:
                continue
            out.append((f"q|{question['id']}", question, None))
    return out


def question_answered(answers: Mapping[str, Any], question: Mapping[str, Any], focus: str | None) -> bool:
    scope: Mapping[str, Any] = answers
    if focus is not None:
        scope = (answers.get(FOCUS_ANSWERS_KEY) or {}).get(focus) or {}
    return any(_has_value(scope.get(str(field["key"]))) for field in question_fields(question))


def progress(answers: Mapping[str, Any] | None, *, new_clinic: bool) -> dict[str, int]:
    answers = answers or {}
    questions = visible_questions(answers, new_clinic=new_clinic)
    done = sum(1 for _key, question, focus in questions if question_answered(answers, question, focus))
    return {"answered": done, "total": len(questions)}


__all__ = (
    "AI_QUESTION_TEMPLATES",
    "CONSENT_VERSION",
    "FOCUS_ANSWERS_KEY",
    "FOCUS_FIELDS",
    "FOCUS_QUESTIONS",
    "PARTS",
    "RESPONDENTS",
    "SCHEMA_VERSION",
    "SurveyAnswerError",
    "TOP_FIELDS",
    "iter_fields",
    "mask_answers",
    "option_value",
    "progress",
    "public_schema",
    "question_answered",
    "question_fields",
    "validate_answers",
    "visible_questions",
)
