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


def test_python_scanner_ignores_identifiers_but_keeps_same_line_literals(tmp_path: Path) -> None:
    """A Python identifier is implementation detail, while a literal can reach an operator."""
    guard = _load_guard()
    source = tmp_path / "copy.py"
    source.write_text('baseline = "baseline"\n', encoding="utf-8")

    scannable_lines = guard.iter_scannable_lines(source)

    assert scannable_lines == [(1, '"baseline"')]
    assert guard.banned_labels_for_line(scannable_lines[0][1]) == ["baseline"]


def test_python_scanner_keeps_multiline_and_f_string_display_text(tmp_path: Path) -> None:
    """Only f-string display text is copy; the embedded Python name is not."""
    guard = _load_guard()
    source = tmp_path / "copy.py"
    source.write_text(
        'baseline = f"{baseline}"\n'
        'visible = f"baseline {baseline}"\n'
        'allowed = f"결과: {baseline}"\n'
        'nested = f"{\'baseline\'}"\n'
        'multiline = """first\nbaseline\nthird"""\n'
        'f_multiline = f"""first\nbaseline {baseline}\nthird"""\n',
        encoding="utf-8",
    )

    found = [
        (lineno, guard.banned_labels_for_line(line))
        for lineno, line in guard.iter_scannable_lines(source)
        if guard.banned_labels_for_line(line)
    ]

    assert found == [
        (2, ["baseline"]),
        (4, ["baseline"]),
        (6, ["baseline"]),
        (9, ["baseline"]),
    ]


def test_non_python_scanner_preserves_korean_and_typescript_copy_checks(tmp_path: Path) -> None:
    """The Python-specific parsing must not change TypeScript or Korean term checks."""
    guard = _load_guard()
    source = tmp_path / "copy.tsx"
    source.write_text('const label = "리포트";\n', encoding="utf-8")

    scannable_lines = guard.iter_scannable_lines(source)

    assert scannable_lines == [(1, 'const label = "리포트";')]
    assert guard.banned_labels_for_line(
        scannable_lines[0][1], guard.NEW_SURFACE_BANNED_PATTERNS
    ) == ["리포트 → 보고서"]
