from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


class CopyGuardImportError(RuntimeError):
    """The source-copy guard could not be loaded for its contract test."""


def _load_guard() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "check_user_facing_terms.py"
    spec = spec_from_file_location("check_user_facing_terms", script)
    if spec is None or spec.loader is None:
        raise CopyGuardImportError("copy guard module could not be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_internal_only_marker_exempts_only_its_exact_source_line() -> None:
    guard = _load_guard()
    internal = 'return f"invalid monthly SoV {field}"  # copy-guard: internal-only'
    visible = 'message = "invalid monthly SoV result"'

    assert guard.banned_labels_for_line(internal) == []
    assert guard.banned_labels_for_line(visible) == ["SoV"]
