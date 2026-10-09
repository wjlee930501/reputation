"""Pure-stdlib validation helpers for the Task 16 rehearsal launcher."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPATIBLE_HOTFIX_PATHS = frozenset(
    {
        "backend/app/api/admin/hospital_overview.py",
        "backend/app/models/content.py",
        "backend/app/services/content_visibility.py",
    }
)
COMPATIBLE_VERIFICATION_SCOPE = (
    "Read-only compatible reader hotfix; final global runtime verification remains pending"
)


@dataclass(frozen=True)
class ImageIdentity:
    role: str
    source_sha: str
    tag: str
    image_id: str
    digest: str

    @classmethod
    def from_mapping(cls, role: str, value: dict[str, Any]) -> ImageIdentity:
        image = value.get("image") if isinstance(value.get("image"), dict) else value
        identity = cls(
            role=role,
            source_sha=str(value.get("sourceSha", "")),
            tag=str(image.get("tag", image.get("name", ""))),
            image_id=str(image.get("imageId", image.get("id", ""))),
            digest=str(image.get("digest", "")),
        )
        identity.validate()
        return identity

    def validate(self) -> None:
        if not FULL_SHA.fullmatch(self.source_sha):
            raise ValueError(f"{self.role}: sourceSha must be a full lowercase Git SHA")
        if not self.tag.strip():
            raise ValueError(f"{self.role}: image tag is required")
        if not DIGEST.fullmatch(self.image_id):
            raise ValueError(f"{self.role}: imageId must be sha256:<64 hex>")
        if not DIGEST.fullmatch(self.digest):
            raise ValueError(f"{self.role}: digest must be sha256:<64 hex>")


@dataclass(frozen=True)
class CompatibleCheckpoint:
    source_sha: str
    original_checkpoint_sha: str
    hotfix_paths: frozenset[str]


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"required non-empty JSON is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def read_single_resource(path: Path, role: str) -> ImageIdentity:
    value = read_json(path)
    resources = value.get("resources")
    if not isinstance(resources, list) or len(resources) != 1 or not isinstance(resources[0], dict):
        raise ValueError(f"{role}: registry must contain exactly one resource")
    return ImageIdentity.from_mapping(role, resources[0])


def read_compatible_checkpoint(
    path: Path, *, repo_root: Path | None = None
) -> CompatibleCheckpoint:
    value = read_json(path)
    if value.get("schemaVersion") != 2:
        raise ValueError("compatible: schemaVersion must be 2")
    if value.get("verifiedTasks") != "1-14":
        raise ValueError("compatible: verifiedTasks must be exactly '1-14'")
    if value.get("createdBeforeTask15") is not False:
        raise ValueError("compatible: hotfix createdBeforeTask15 must be false")
    if value.get("originalCheckpointCreatedBeforeTask15") is not True:
        raise ValueError("compatible: original checkpoint must predate Task15")
    if value.get("readerContract") != "purpose-first-compatible-public-read-v1":
        raise ValueError("compatible: unexpected readerContract")
    if value.get("verificationScope") != COMPATIBLE_VERIFICATION_SCOPE:
        raise ValueError("compatible: verificationScope must remain read-only and pending global verification")
    source_sha = str(value.get("sourceSha", ""))
    if not FULL_SHA.fullmatch(source_sha):
        raise ValueError("compatible: sourceSha must be a full lowercase Git SHA")
    original_sha = str(value.get("originalCheckpointSha", ""))
    if not FULL_SHA.fullmatch(original_sha):
        raise ValueError("compatible: originalCheckpointSha must be a full lowercase Git SHA")
    hotfix = value.get("hotfix")
    if not isinstance(hotfix, dict):
        raise ValueError("compatible: hotfix metadata is required")
    if hotfix.get("parentSha") != original_sha or hotfix.get("createdAfterTask15Started") is not True:
        raise ValueError("compatible: hotfix parent/timing metadata is invalid")
    paths = hotfix.get("paths")
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ValueError("compatible: hotfix paths must be strings")
    hotfix_paths = frozenset(paths)
    if hotfix_paths != COMPATIBLE_HOTFIX_PATHS or len(paths) != len(hotfix_paths):
        raise ValueError("compatible: hotfix paths do not match the exact read-only allowlist")
    evidence = hotfix.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) for item in evidence):
        raise ValueError("compatible: hotfix evidence paths are required")
    checkpoint = CompatibleCheckpoint(source_sha, original_sha, hotfix_paths)
    if repo_root is not None:
        verify_compatible_hotfix(repo_root, checkpoint, evidence)
    return checkpoint


def verify_compatible_hotfix(
    repo_root: Path, checkpoint: CompatibleCheckpoint, evidence: list[str]
) -> None:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    parent = git("rev-parse", f"{checkpoint.source_sha}^")
    if parent != checkpoint.original_checkpoint_sha:
        raise ValueError("compatible: hotfix must be a direct child of the original checkpoint")
    changed = frozenset(
        line for line in git("diff-tree", "--no-commit-id", "--name-only", "-r", checkpoint.source_sha).splitlines() if line
    )
    if changed != checkpoint.hotfix_paths:
        raise ValueError(f"compatible: Git hotfix paths differ from manifest allowlist: {sorted(changed)}")
    for relative in evidence:
        candidate = repo_root / relative
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise ValueError(f"compatible: hotfix evidence missing or empty: {relative}")


def assert_distinct(identities: list[ImageIdentity]) -> None:
    for field in ("source_sha", "tag", "image_id", "digest"):
        values = [getattr(identity, field) for identity in identities]
        if len(values) != len(set(values)):
            raise ValueError(f"image identities must have distinct {field}: {values}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def revalidation_effect_digest(paths: list[str]) -> str:
    """Identify the ordered public-cache effect represented by a callback body."""

    if not paths or not all(isinstance(path, str) and path.startswith("/") for path in paths):
        raise ValueError("revalidation paths must be a non-empty list of absolute paths")
    canonical = json.dumps({"paths": paths}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def count_matching_revalidation_callbacks(raw_callbacks: list[str], expected_paths: list[str]) -> int:
    """Count raw HTTP attempts that exactly match one ordered revalidation effect."""

    return len(matching_revalidation_callback_indexes(raw_callbacks, expected_paths))


def matching_revalidation_callback_indexes(
    raw_callbacks: list[str], expected_paths: list[str]
) -> list[int]:
    """Locate exact callback attempts relative to a caller-recorded Redis list cursor."""

    expected = revalidation_effect_digest(expected_paths)
    matches: list[int] = []
    for index, raw in enumerate(raw_callbacks):
        try:
            payload = json.loads(raw)
            paths = payload.get("paths") if isinstance(payload, dict) else None
            if isinstance(paths, list) and revalidation_effect_digest(paths) == expected:
                matches.append(index)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return matches
