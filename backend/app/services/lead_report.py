"""무료 진단 블러 리포트 (PRD F5 · 설계 §6).

## 가림은 렌더 옵션이 아니라 구조다

CSS blur와 PDF 오버레이는 텍스트 레이어에서 복원된다(F5-3). "가리세요"라고 문서에
적어두면 지켜지지 않으므로 **함수 시그니처로 막는다**:

    build_lead_report_payload(diagnosis, results) -> LeadReportPayload
    render_lead_report_html(payload)              -> str
    render_lead_report_pdf(payload)               -> bytes

렌더러는 `results`를 인자로 받지 않는다. `LeadReportPayload`에는 `raw_response`도
경쟁 병원명도 개선 액션도 **담을 필드 자체가 없다.** 실수로 샐 경로가 없다.

## 파는 숫자의 정의

기술통계다(PRD §2-2 rev4). "이 3개 질문에 3번씩 물어 9번 중 N번" 형태이며,
모집단 추정·신뢰구간을 쓰지 않는다 — 질문 세트는 우리가 만든 템플릿일 뿐이라
"환자 질문 전체를 대표한다"고 주장할 근거가 없다.

플랫폼 표기는 **API·모델명**으로 한다(F5-1). "ChatGPT 9번 중 0번"이라고 쓰면
§2-2에서 철회한 "환자가 보는 화면" 주장을 라벨로 되살리는 셈이 된다.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.models.lead_diagnosis import LeadDiagnosis, LeadDiagnosisResult, MentionVerdict
from app.services import sov_engine

# 리포트가 칸을 인쇄하는 순서. 측정 엔진의 PLATFORMS와 같은 집합이어야 한다.
_PLATFORMS = ("chatgpt", "gemini")

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "lead_report.html"
PRETENDARD_FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "PretendardVariable.woff2"
)

# 템플릿이 바뀌면 같은 데이터라도 다른 리포트가 나온다. artifact에 기록해
# "같은 병원인데 숫자가 왜 다르냐"에 답할 수 있게 한다.
TEMPLATE_VERSION = "lead-v8"

# 공급자 표기 — 사람이 읽는 이름이지 "ChatGPT 화면"이 아니다.
_VENDOR_LABELS = {"chatgpt": "OpenAI API", "gemini": "Google Gemini API"}


@dataclass(frozen=True, slots=True)
class PlatformSegment:
    """플랫폼 1개의 기술통계. 분모·분자·실패·보류 수를 따로 표기한다(F3-5)."""

    platform: str
    vendor_label: str
    model: str
    planned: int          # 계획 측정 수 (설계값 — 실제 행 수가 아니다)
    measured: int         # 확정 판정 수 = 분모
    mentioned: int        # 언급 횟수 = 분자
    failed: int
    ambiguous: int = 0    # 판정 보류 — 분모·분자 어디에도 들어가지 않는다 (F3-7)
    # 답변을 받은 측정 중 모델이 실제로 웹 검색을 한 건수. 검색 도구는 제공하되
    # 강제하지 않으므로(측정 정책 v2), 이 값이 답변의 성격을 크게 가른다.
    # None은 계측 이전 측정이라 '모름'이라는 뜻이다 — 0(안 씀)과 다르다.
    searched: int | None = None

    @property
    def label(self) -> str:
        return f"{self.vendor_label} · {self.model}"

    @property
    def mention_rate(self) -> float | None:
        """확정 판정이 0이면 None. '측정 안 됨'과 '실제 0%'는 다른 사실이다."""
        if self.measured <= 0:
            return None
        return round(self.mentioned / self.measured * 100, 1)

    @property
    def rate_ceiling(self) -> float | None:
        """미확정 건이 전부 언급이었다고 가정한 상한.

        신뢰구간이 아니다 — 미확정 셀의 가능한 결과를 그대로 편 결정론적 범위다.
        `mention_rate`만 크게 인쇄하면 결측이 있었다는 사실이 시각적으로 사라진다.
        """
        unconfirmed = self.failed + self.ambiguous
        if self.measured <= 0 or unconfirmed <= 0:
            return None
        return round((self.mentioned + unconfirmed) / (self.measured + unconfirmed) * 100, 1)


@dataclass(frozen=True, slots=True)
class QueryDisclosure:
    """질의 원문과 안전한 집계 결과. 원문 답변은 의도적으로 포함하지 않는다."""

    slot: int
    kind: str
    text: str
    measured_at: datetime | None   # 캐시 적중 시 **원본 측정 시각**이지 오늘이 아니다
    planned: int
    measured: int
    mentioned: int
    failed: int
    ambiguous: int = 0


@dataclass(frozen=True, slots=True)
class LeadReportContact:
    """무료 진단 결과를 설명할 MotionLabs 담당자."""

    name: str
    role: str
    email: str
    phone: str


@dataclass(frozen=True, slots=True)
class LeadReportPayload:
    """리포트에 실리는 것 **전부**.

    여기 없는 것은 리포트에 존재할 수 없다. 특히 `raw_response`, 경쟁 병원명,
    개선 액션 목록을 담을 필드가 없다 — 그것이 F5-3의 구현이다.
    """

    hospital_name: str
    region: str
    generated_at: datetime
    repeat_count: int
    system_prompt: str
    judge_model: str
    segments: tuple[PlatformSegment, ...] = ()
    queries: tuple[QueryDisclosure, ...] = ()
    notices: tuple[str, ...] = ()
    contact: LeadReportContact = LeadReportContact(name="", role="", email="", phone="")

    # 합산값은 **헤드라인이 아니라 존재 여부 판정에만** 쓴다("확정 0건인가?").
    # 경로별 결측률이 다르면 합산 비율은 결측이 적은 경로에 가중치를 주는데,
    # 그 가중치는 문서 어디에도 표기되지 않는다.
    @property
    def total_measured(self) -> int:
        return sum(s.measured for s in self.segments)

    @property
    def total_mentioned(self) -> int:
        return sum(s.mentioned for s in self.segments)

    @property
    def total_unconfirmed(self) -> int:
        """응답 실패 + 판정 보류. 분모에서 빠진 것이 몇 건인지 독자가 알아야 한다."""
        return sum(s.failed + s.ambiguous for s in self.segments)


# F5-5 고지 2종. 문구만으로 의료법 제56조 위험이 통제되지는 않지만, 빠지면 확실히 문제다.
_NOTICES = (
    "본 자료는 귀 병원 내부 참고용 진단 자료이며 광고물이 아닙니다.",
    "측정과 리포트 생성에 인공지능이 사용되었습니다.",
)

# F3-6 — 무료 진단은 기술통계다. 표본 한계를 숫자 옆에 붙여 고지한다.
_SAMPLE_CAVEAT = (
    "이 숫자는 아래 공개된 질문 세트 기준이며, 다른 질문에서는 다르게 나올 수 있습니다. "
    "질문·모델·지시문을 그대로 공개하므로 직접 재현하실 수 있습니다."
)


def build_lead_report_payload(
    diagnosis: LeadDiagnosis,
    results: list[LeadDiagnosisResult],
    *,
    generated_at: datetime,
) -> LeadReportPayload:
    """측정 결과 → 공개 대상만 담은 allowlist.

    실패 측정은 분모에서 뺀다 — 도구 장애를 병원 성과로 계상하지 않기 위해서다.
    판정 보류(AMBIGUOUS)도 뺀다 — 확정하지 못한 것을 언급으로도 미언급으로도 세지
    않는다(F3-7). 대신 실패·보류 건수를 그대로 표기한다(F3-5). 감추면 "9번 중 0번"과
    "1번 중 0번"이 같은 숫자로 보인다.

    **계획 건수는 행 수가 아니라 설계값에서 계산한다.** 행 수로 세면 재시도로 누적된
    행이 그대로 "계획 18건"이 되어, 리포트가 공개하는 측정 설계(질의 3 × 플랫폼 2 ×
    반복 3)와 인쇄된 값이 어긋난다.
    """
    models = diagnosis.requested_models or {}
    segments: list[PlatformSegment] = []
    query_count = len(diagnosis.queries or [])
    planned_per_platform = query_count * diagnosis.repeat_count

    for platform in _PLATFORMS:
        rows = [r for r in results if r.platform == platform]
        if not rows:
            continue
        succeeded = [r for r in rows if r.measurement_status == "SUCCESS"]
        confirmed = [r for r in succeeded if r.mention_verdict != MentionVerdict.AMBIGUOUS.value]
        # 한 건이라도 계측된 진단만 검색 사용을 표기한다. 전부 NULL(계측 이전)이면
        # 0으로 인쇄해 "검색을 한 번도 안 썼다"는 없는 사실을 만들지 않는다.
        instrumented = [r for r in succeeded if r.search_calls is not None]
        segments.append(
            PlatformSegment(
                platform=platform,
                vendor_label=_VENDOR_LABELS.get(platform, platform),
                model=models.get("openai" if platform == "chatgpt" else "gemini") or "",
                planned=planned_per_platform,
                measured=len(confirmed),
                mentioned=sum(1 for r in confirmed if r.is_mentioned),
                failed=len(rows) - len(succeeded),
                ambiguous=len(succeeded) - len(confirmed),
                searched=(
                    sum(1 for r in instrumented if (r.search_calls or 0) > 0)
                    if instrumented
                    else None
                ),
            )
        )

    # 질의별 측정 일시는 가장 이른 측정을 쓴다 — 캐시 적중이 섞이면 서로 다를 수 있고,
    # 가장 오래된 것을 보여야 "언제 잰 숫자인가"를 과장하지 않는다.
    measured_at_by_slot: dict[int, datetime] = {}
    for row in results:
        if row.measurement_status != "SUCCESS" or row.measured_at is None:
            continue
        current = measured_at_by_slot.get(row.query_slot)
        if current is None or row.measured_at < current:
            measured_at_by_slot[row.query_slot] = row.measured_at

    queries: list[QueryDisclosure] = []
    for query in diagnosis.queries or []:
        slot = int(query["slot"])
        rows = [row for row in results if row.query_slot == slot]
        succeeded = [row for row in rows if row.measurement_status == "SUCCESS"]
        confirmed = [
            row for row in succeeded if row.mention_verdict != MentionVerdict.AMBIGUOUS.value
        ]
        queries.append(
            QueryDisclosure(
                slot=slot,
                kind=query.get("kind", ""),
                text=query["text"],
                measured_at=measured_at_by_slot.get(slot),
                planned=diagnosis.repeat_count * len(_PLATFORMS),
                measured=len(confirmed),
                mentioned=sum(1 for row in confirmed if row.is_mentioned),
                failed=len(rows) - len(succeeded),
                ambiguous=len(succeeded) - len(confirmed),
            )
        )

    # 리포트가 공개하는 지시문은 **측정 당시의 스냅샷**이다. 렌더 시점의 전역 상수를
    # 읽으면 프롬프트 변경 후 재생성한 리포트가 실제 측정과 다른 조건을 공개하게 된다.
    # 스냅샷 도입 이전 진단(NULL)만 전역값으로 폴백한다.
    snapshot = diagnosis.measurement_config or {}

    return LeadReportPayload(
        hospital_name=diagnosis.subject_hospital_name,
        region=diagnosis.subject_region,
        generated_at=generated_at,
        repeat_count=diagnosis.repeat_count,
        system_prompt=snapshot.get("system_prompt") or sov_engine.SYSTEM_PROMPT_SOV,
        judge_model=models.get("judge") or "",
        segments=tuple(segments),
        queries=tuple(queries),
        notices=_NOTICES,
        contact=LeadReportContact(
            name=settings.LEAD_REPORT_CONTACT_NAME,
            role=settings.LEAD_REPORT_CONTACT_ROLE,
            email=settings.LEAD_REPORT_CONTACT_EMAIL,
            phone=settings.LEAD_REPORT_CONTACT_PHONE,
        ),
    )


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )


def render_lead_report_html(payload: LeadReportPayload) -> str:
    """**payload 외의 인자를 받지 않는다.** 렌더러는 원자료에 접근할 수 없다."""
    template = _environment().get_template(TEMPLATE_NAME)
    return template.render(payload=payload, sample_caveat=_SAMPLE_CAVEAT)


def render_lead_report_pdf(payload: LeadReportPayload) -> bytes:
    """HTML을 그대로 PDF로. 가림 대상은 HTML에 없으므로 텍스트 레이어에도 없다."""
    from weasyprint import HTML  # 네이티브 의존성 — 임포트를 지연시킨다.

    return HTML(
        string=render_lead_report_html(payload),
        base_url=str(TEMPLATE_DIR),
    ).write_pdf()


def artifact_storage_uri(diagnosis_id, version: int) -> str:
    bucket = settings.GCS_REPORTS_BUCKET
    return f"gs://{bucket}/lead-diagnoses/{diagnosis_id}/v{version}.pdf"


def content_hash(data: bytes) -> str:
    """GCS 객체와 DB 기록의 불일치(업로드 절단)를 탐지하기 위한 지문."""
    return hashlib.sha256(data).hexdigest()


def _blob_name(diagnosis_id, version: int) -> str:
    return f"lead-diagnoses/{diagnosis_id}/v{version}.pdf"


def store_report_pdf(diagnosis_id, version: int, data: bytes) -> str:
    """PDF를 GCS에 올리고 `gs://` 경로를 돌려준다.

    프로덕션에서 업로드가 실패하면 **예외를 올린다** — 조용히 로컬 경로를 돌려주면
    `report_status=READY`인데 파일이 어디에도 없는 상태가 만들어지고, 메일은 나간다.
    """
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(settings.GCS_REPORTS_BUCKET)
        blob = bucket.blob(_blob_name(diagnosis_id, version))
        blob.upload_from_string(data, content_type="application/pdf")
        return artifact_storage_uri(diagnosis_id, version)
    except Exception as exc:  # noqa: BLE001
        logger.error("lead report upload failed: %s", exc)
        if settings.APP_ENV == "production":
            raise RuntimeError(f"lead report GCS upload failed: {exc}") from exc
        # 개발 환경은 로컬 파일로 떨어뜨려 전체 플로우를 돌려볼 수 있게 한다.
        output_dir = Path(settings.REPORT_OUTPUT_DIR) / "lead-diagnoses" / str(diagnosis_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        local_path = output_dir / f"v{version}.pdf"
        local_path.write_bytes(data)
        return str(local_path)


def delete_report_pdf(storage_uri: str) -> None:
    """파기 — **DB 커밋보다 먼저 부른다.**

    반대 순서로 하면 `purged_at`은 찍혔는데 객체는 살아 있는, 가장 나쁜 상태가 된다
    (파기 기록이 거짓말이 됨). 실패하면 예외를 올려 커밋을 막는다.

    **이미 없는 객체는 성공으로 본다.** 산출물 A는 지웠는데 B에서 실패해 트랜잭션이
    롤백되면 A의 `purged_at`도 되돌아간다. 재시도가 A에서 `NotFound`로 또 멈추면
    파기가 영구히 좌초한다 — 부재는 우리가 원한 결과이므로 전진해야 한다.
    """
    if not storage_uri:
        return
    if not storage_uri.startswith("gs://"):
        path = Path(storage_uri)
        if path.exists():
            path.unlink()
        return

    from google.api_core import exceptions as gcloud_exceptions
    from google.cloud import storage

    _, _, rest = storage_uri.partition("gs://")
    bucket_name, _, blob_name = rest.partition("/")
    client = storage.Client()
    try:
        client.bucket(bucket_name).blob(blob_name).delete()
    except gcloud_exceptions.NotFound:
        logger.info("lead report already absent, treating as purged: %s", storage_uri)
