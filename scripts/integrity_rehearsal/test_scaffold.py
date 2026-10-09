"""Fast component tests for the phase 16A rehearsal scaffold."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from harnesslib import (
    ImageIdentity,
    assert_distinct,
    count_matching_revalidation_callbacks,
    matching_revalidation_callback_indexes,
    read_compatible_checkpoint,
    revalidation_effect_digest,
)


SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent


class ScaffoldContractTests(unittest.TestCase):
    def test_revalidation_callback_attempts_are_bound_to_exact_effect(self) -> None:
        expected = ["/", "/rehearsal-bareun-clinic", "/rehearsal-bareun-clinic/contents/abc"]
        reordered = list(reversed(expected))
        raw_callbacks = [
            json.dumps({"paths": expected}),
            json.dumps({"paths": expected}, separators=(",", ":")),
            json.dumps({"paths": reordered}),
            json.dumps({"paths": ["/unrelated"]}),
            "not-json",
        ]

        self.assertEqual(count_matching_revalidation_callbacks(raw_callbacks, expected), 2)
        self.assertEqual(matching_revalidation_callback_indexes(raw_callbacks, expected), [0, 1])
        self.assertNotEqual(
            revalidation_effect_digest(expected),
            revalidation_effect_digest(reordered),
        )

    def test_compatible_checkpoint_requires_exact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "compatible.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "verifiedTasks": "1-14",
                        "createdBeforeTask15": False,
                        "originalCheckpointCreatedBeforeTask15": True,
                        "readerContract": "purpose-first-compatible-public-read-v1",
                        "sourceSha": "a" * 40,
                        "originalCheckpointSha": "b" * 40,
                        "verificationScope": (
                            "Read-only compatible reader hotfix; final global runtime verification remains pending"
                        ),
                        "hotfix": {
                            "parentSha": "b" * 40,
                            "createdAfterTask15Started": True,
                            "paths": [
                                "backend/app/api/admin/hospital_overview.py",
                                "backend/app/models/content.py",
                                "backend/app/services/content_visibility.py",
                            ],
                            "evidence": ["evidence.json"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            identity = read_compatible_checkpoint(checkpoint)
            self.assertEqual(identity.source_sha, "a" * 40)
            bad = json.loads(checkpoint.read_text(encoding="utf-8"))
            bad["createdBeforeTask15"] = True
            checkpoint.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "createdBeforeTask15"):
                read_compatible_checkpoint(checkpoint)

    def test_all_image_identity_dimensions_must_be_distinct(self) -> None:
        first = ImageIdentity("old", "a" * 40, "old:a", "sha256:" + "b" * 64, "sha256:" + "b" * 64)
        second = ImageIdentity("new", "c" * 40, "new:c", "sha256:" + "d" * 64, "sha256:" + "d" * 64)
        third = ImageIdentity("compatible", "e" * 40, "compatible:e", "sha256:" + "f" * 64, "sha256:" + "f" * 64)
        assert_distinct([first, second, third])
        with self.assertRaisesRegex(ValueError, "distinct source_sha"):
            assert_distinct([first, second, ImageIdentity("bad", first.source_sha, "bad:e", "sha256:" + "0" * 64, "sha256:" + "0" * 64)])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ScaffoldContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
