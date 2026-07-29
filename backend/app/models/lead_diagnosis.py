"""리드마그넷 무료 진단 (1단) 엔터티.

설계 정본: `docs/plans/2026-07-29-lead-diagnosis-funnel-design.md`

**2단(운영 서비스)의 SoV 모델을 재사용하지 않는다.** `MeasurementRun.hospital_id`와
`SovRecord.hospital_id`·`query_id`가 nullable=False인데 진단 신청 시점에는 Hospital도
QueryMatrix도 없다. 가짜 Hospital을 만들면 병원 목록·통계·비용 집계가 전부 오염된다.
두 단이 만나는 지점은 전환(`SalesLead.converted_hospital_id`) 하나뿐이다.

상태는 **3축으로 분리**한다(설계 §4). 단일 status로 두면 PARTIAL 결과로 리포트를 만드는
순간 값이 REPORT_READY로 덮여 "일부 측정이 실패했다"는 사실이 사라지고, 그 리포트가 곧
원장에게 간다.
"""
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _jsonb_type():
    return JSON().with_variant(JSONB, "postgresql")


class ExecutionStatus(str, enum.Enum):
    """측정 실행 축. DEFERRED/EXPIRED/CACHED가 없는 것은 의도다 —
    선착순 마감이라 대기열이 없고(설계 §2-1), 병원 단위 캐시는 폐기됐다(§5-1)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"   # 모든 (플랫폼 × 질의)가 계획된 반복만큼 성공
    PARTIAL = "PARTIAL"       # 두 플랫폼 각각 성공 ≥ 1이지만 계획 미달 → 리포트 가능
    FAILED = "FAILED"         # 어느 한 플랫폼이라도 성공 0 → 리포트 불가


class ReportStatus(str, enum.Enum):
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    READY = "READY"
    BLOCKED = "BLOCKED"       # 렌더 3회 실패 또는 execution FAILED. AE 개입
    PURGED = "PURGED"         # 파기 완료. 열람 시 410


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"       # 의도를 부수효과보다 먼저 커밋한 상태 (설계 §5-4)
    SENT = "SENT"
    FAILED = "FAILED"


class AnswerSource(str, enum.Enum):
    LIVE = "LIVE"
    CACHED = "CACHED"


class MentionVerdict(str, enum.Enum):
    """언급 판정 3값. `measurement_status`(측정 성공 여부)와는 다른 축이다."""

    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    AMBIGUOUS = "AMBIGUOUS"   # 분자·분모 모두에서 제외하고 별도 집계 (PRD F3-7)


# 리포트를 만들 수 있는 실행 상태. 게이트가 한 줄인 것은 ExecutionStatus의 정의
# (플랫폼 하나라도 성공 0이면 PARTIAL이 아니라 FAILED) 덕분이다.
REPORTABLE_EXECUTION_STATUSES = frozenset(
    {ExecutionStatus.SUCCEEDED.value, ExecutionStatus.PARTIAL.value}
)


class LeadDiagnosis(Base):
    """무료 진단 1건.

    잠금(applicant_email_hash / subject_phone_hash)은 **부분 유니크 인덱스**로 건다.
    lock_released_at이 채워지면 인덱스에서 빠져 재신청이 가능해진다 — 제3자가 먼저
    신청해 원장의 기회를 소진시킨 경우를 AE가 푸는 유일한 경로다(설계 §2-4).
    """

    __tablename__ = "lead_diagnoses"
    __table_args__ = (
        UniqueConstraint("slot_date", "slot_no", name="uq_lead_diagnoses_slot"),
        Index("ix_lead_diagnoses_lead_id", "lead_id"),
        # 잠금 부분 유니크 인덱스와 폴러용 부분 인덱스는 Postgres 전용 구문이라
        # 마이그레이션 0035에서 raw DDL로 만든다 (SQLite 단위 테스트 호환).
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="CASCADE"), nullable=False
    )

    # ── 잠금 (설계 §2-3). 해시만 저장하며 PII 파기 대상이 아니다 —
    #    보관기간이 지났다고 두 번째 무료 진단을 주는 것이 아니기 때문.
    applicant_email_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── 측정 대상 (판정에만 쓴다. 질의에는 절대 넣지 않는다 — PRD F1-1)
    subject_hospital_name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_region: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── 선착순 자리 (설계 §2-1). DB가 진실의 원천이라 카운터와 행 수가 어긋날 수 없다.
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)

    queries: Mapped[list] = mapped_column(_jsonb_type(), nullable=False, default=list)
    requested_models: Mapped[dict] = mapped_column(_jsonb_type(), nullable=False, default=dict)
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False)

    execution_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExecutionStatus.PENDING.value, server_default="PENDING"
    )
    execution_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    report_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReportStatus.PENDING.value, server_default="PENDING"
    )
    report_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeliveryStatus.PENDING.value, server_default="PENDING"
    )

    # ── AE 잠금 해제 (설계 §2-4). F1-6과 한 몸이다.
    lock_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_released_by: Mapped[str | None] = mapped_column(String(100))
    lock_release_reason: Mapped[str | None] = mapped_column(String(200))

    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    results: Mapped[list["LeadDiagnosisResult"]] = relationship(
        back_populates="diagnosis", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["LeadReportArtifact"]] = relationship(
        back_populates="diagnosis", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["LeadReportToken"]] = relationship(
        back_populates="diagnosis", cascade="all, delete-orphan"
    )


class LeadDiagnosisResult(Base):
    """측정 1회.

    `raw_response`는 공유 캐시를 참조하지 않고 **복사**한다 — 캐시는 7일 뒤 사라지고
    여러 병원이 공유하므로, 참조 구조에서는 리포트 재생성도 개별 파기도 성립하지 않는다
    (설계 §2-6).
    """

    __tablename__ = "lead_diagnosis_results"
    __table_args__ = (
        UniqueConstraint(
            "diagnosis_id",
            "platform",
            "query_slot",
            "repeat_no",
            "attempt_no",
            name="uq_lead_diagnosis_results_measurement",
        ),
        Index("ix_lead_diagnosis_results_diagnosis_id", "diagnosis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_diagnoses.id", ondelete="CASCADE"), nullable=False
    )

    platform: Mapped[str] = mapped_column(String(20), nullable=False)   # chatgpt | gemini
    query_slot: Mapped[int] = mapped_column(Integer, nullable=False)    # 1..3
    repeat_no: Mapped[int] = mapped_column(Integer, nullable=False)     # 1..3
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # 공급자 응답의 실제 모델. 성공 레코드에만 필수다 — 응답을 받기 전에 실패한
    # 측정에는 기록할 모델이 없다 (PRD F3-2).
    answer_model: Mapped[str | None] = mapped_column(String(100))

    is_mentioned: Mapped[bool | None] = mapped_column()
    mention_verdict: Mapped[str | None] = mapped_column(String(20))
    measurement_status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_urls: Mapped[list | None] = mapped_column(_jsonb_type())

    search_calls: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    # 캐시에서 왔는지, 그 답변이 실제로 언제 측정된 것인지. 없으면 원가 검증도
    # 신선도 검증도 못 한다. CACHED면 measured_at은 **원본 측정 시각**이지 오늘이 아니다.
    answer_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default=AnswerSource.LIVE.value, server_default="LIVE"
    )
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    diagnosis: Mapped["LeadDiagnosis"] = relationship(back_populates="results")


class LeadQueryAnswer(Base):
    """질의 단위 공유 캐시 — 원가의 지배 요인을 줄이는 지점 (설계 §2-6).

    질의에 병원 이름이 들어가지 않으므로(PRD F1-1) `수서역 근처 내과 병원 추천해줘`의
    답변은 신청 병원이 누구든 동일하다. 병원마다 달라지는 것은 판정 단계뿐이고
    판정은 콜당 0.26원이다 — 두 번째 병원부터 1,499원이 약 5원이 된다.

    **개인정보가 아니다.** 질의에 신청자 이름·연락처를 절대 넣지 않으므로(PRD §6)
    파기 파이프라인의 대상이 아니고 TTL로만 관리한다.
    """

    __tablename__ = "lead_query_answers"
    __table_args__ = (
        UniqueConstraint("query_hash", "repeat_no", name="uq_lead_query_answers_hash_repeat"),
        Index("ix_lead_query_answers_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # sha256(질의텍스트 | platform | requested_model | prompt_version).
    # requested_model·prompt_version이 키에 없으면 모델·프롬프트를 바꿔도 옛 답변이
    # 살아남아 PRD §2-1의 "핀 고정 + 의도적 이전"이 캐시 뒤에서 조용히 깨진다.
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    repeat_no: Mapped[int] = mapped_column(Integer, nullable=False)

    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(100), nullable=False)
    answer_model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)

    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    source_urls: Mapped[list | None] = mapped_column(_jsonb_type())
    search_calls: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadReportToken(Base):
    """리포트 열람 + 상태 페이지 공용 서명 링크.

    원문이 아니라 sha256만 저장한다. Admin 세션 HMAC과는 서명 키·audience가 다르다
    (그건 accountId/role을 요구하는 관리자 토큰이다 — PRD F5-4).
    """

    __tablename__ = "lead_report_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_lead_report_tokens_token_hash"),
        Index("ix_lead_report_tokens_diagnosis_id", "diagnosis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_diagnoses.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    diagnosis: Mapped["LeadDiagnosis"] = relationship(back_populates="tokens")


class LeadReportArtifact(Base):
    """블러 리포트 산출물 (PRD F5-3).

    가림은 렌더 옵션이 아니라 구조로 강제한다 — 렌더러가 원자료를 인자로 받지 않으므로
    가릴 대상이 PDF 텍스트 레이어에 존재할 경로가 없다(설계 §6-2).

    파기 시 행을 지우지 않고 `purged_at`만 찍는다 — 삭제했다는 사실 자체가 증거다.
    """

    __tablename__ = "lead_report_artifacts"
    __table_args__ = (
        UniqueConstraint("diagnosis_id", "version", name="uq_lead_report_artifacts_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_diagnoses.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # 어느 템플릿이 만든 숫자인가 — "같은 병원인데 왜 다르냐"에 답하려면 필요하다.
    template_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    diagnosis: Mapped["LeadDiagnosis"] = relationship(back_populates="artifacts")


class LeadDelivery(Base):
    """메일 발송 이력.

    `id`가 곧 Resend `Idempotency-Key`다. 의도(SENDING 행)를 부수효과(발송)보다 먼저
    커밋하므로, 발송 후 커밋이 실패해도 같은 키로 재시도하면 Resend가 원래 응답을
    돌려주고 메일은 다시 나가지 않는다.

    **Resend는 키를 24시간만 보관한다** — 그 창을 넘긴 SENDING은 자동 재시도하지 않고
    AE에게 넘긴다(설계 §5-4).

    기존 `SalesLead.notification_status`를 재사용하지 않는다. 그건 리드 생성 Slack
    알림용이다(PRD F5-4).
    """

    __tablename__ = "lead_deliveries"
    __table_args__ = (
        Index("ix_lead_deliveries_diagnosis_id", "diagnosis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_leads.id", ondelete="CASCADE"), nullable=False
    )
    diagnosis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_diagnoses.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="EMAIL")
    event: Mapped[str] = mapped_column(String(40), nullable=False)   # REPORT | ...
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeliveryStatus.SENDING.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
