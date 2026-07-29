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
from app.models.lead_diagnosis import LeadDiagnosis, LeadDiagnosisResult
from app.services import sov_engine

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "lead_report.html"

# 템플릿이 바뀌면 같은 데이터라도 다른 리포트가 나온다. artifact에 기록해
# "같은 병원인데 숫자가 왜 다르냐"에 답할 수 있게 한다.
TEMPLATE_VERSION = "lead-v1"

# 공급자 표기 — 사람이 읽는 이름이지 "ChatGPT 화면"이 아니다.
_VENDOR_LABELS = {"chatgpt": "OpenAI API", "gemini": "Google Gemini API"}


@dataclass(frozen=True)
class PlatformSegment:
    """플랫폼 1개의 기술통계. 분모·분자·실패 수를 따로 표기한다(F3-5)."""

    platform: str
    vendor_label: str
    model: str
    planned: int          # 계획 측정 수
    measured: int         # 성공 측정 수 = 분모
    mentioned: int        # 언급 횟수 = 분자
    failed: int

    @property
    def label(self) -> str:
        return f"{self.vendor_label} · {self.model}"

    @property
    def mention_rate(self) -> float | None:
        """성공 측정이 0이면 None. '측정 안 됨'과 '실제 0%'는 다른 사실이다."""
        if self.measured <= 0:
            return None
        return round(self.mentioned / self.measured * 100, 1)


@dataclass(frozen=True)
class QueryDisclosure:
    """질의 원문 공개 (§2-2). 원장이 직접 재현할 수 있어야 한다."""

    slot: int
    kind: str
    text: str
    measured_at: datetime | None   # 캐시 적중 시 **원본 측정 시각**이지 오늘이 아니다


@dataclass(frozen=True)
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

    @property
    def total_measured(self) -> int:
        return sum(s.measured for s in self.segments)

    @property
    def total_mentioned(self) -> int:
        return sum(s.mentioned for s in self.segments)


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
    대신 실패 건수를 그대로 표기한다(F3-5). 감추면 "9번 중 0번"과
    "1번 중 0번"이 같은 숫자로 보인다.
    """
    models = diagnosis.requested_models or {}
    segments: list[PlatformSegment] = []

    for platform in ("chatgpt", "gemini"):
        rows = [r for r in results if r.platform == platform]
        if not rows:
            continue
        succeeded = [r for r in rows if r.measurement_status == "SUCCESS"]
        segments.append(
            PlatformSegment(
                platform=platform,
                vendor_label=_VENDOR_LABELS.get(platform, platform),
                model=models.get("openai" if platform == "chatgpt" else "gemini") or "",
                planned=len(rows),
                measured=len(succeeded),
                mentioned=sum(1 for r in succeeded if r.is_mentioned),
                failed=len(rows) - len(succeeded),
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

    queries = tuple(
        QueryDisclosure(
            slot=int(q["slot"]),
            kind=q.get("kind", ""),
            text=q["text"],
            measured_at=measured_at_by_slot.get(int(q["slot"])),
        )
        for q in (diagnosis.queries or [])
    )

    return LeadReportPayload(
        hospital_name=diagnosis.subject_hospital_name,
        region=diagnosis.subject_region,
        generated_at=generated_at,
        repeat_count=diagnosis.repeat_count,
        system_prompt=sov_engine.SYSTEM_PROMPT_SOV,
        judge_model=models.get("judge") or "",
        segments=tuple(segments),
        queries=queries,
        notices=_NOTICES,
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

    return HTML(string=render_lead_report_html(payload)).write_pdf()


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
