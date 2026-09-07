from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from manual_generator.security import (
    ArchiveLimits,
    UnsafeArchiveError,
    redact_secrets,
    safe_extract_zip,
)


def make_zip(path: Path, entries: dict[str, str]) -> None:
    with ZipFile(path, "w") as bundle:
        for name, value in entries.items():
            bundle.writestr(name, value)


def test_safe_extract(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    make_zip(archive, {"app/main.py": "print('ok')"})
    output = tmp_path / "out"
    safe_extract_zip(archive, output, ArchiveLimits())
    assert (output / "app" / "main.py").read_text() == "print('ok')"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "C:/escape"])
def test_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.zip"
    make_zip(archive, {name: "bad"})
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(archive, tmp_path / "out", ArchiveLimits())


def test_rejects_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    with ZipFile(archive, "w") as bundle:
        info = ZipInfo("link")
        info.external_attr = 0o120777 << 16
        bundle.writestr(info, "target")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(archive, tmp_path / "out", ArchiveLimits())


def test_rejects_special_file(tmp_path: Path) -> None:
    archive = tmp_path / "special.zip"
    with ZipFile(archive, "w") as bundle:
        info = ZipInfo("fifo")
        info.external_attr = 0o010644 << 16
        bundle.writestr(info, "payload")

    with pytest.raises(UnsafeArchiveError, match="特殊文件"):
        safe_extract_zip(archive, tmp_path / "out", ArchiveLimits())


@pytest.mark.parametrize("ignored", ["node_modules", ".venv", "venv", ".git", "__pycache__", "__MACOSX", "._metadata"])
def test_ignores_rebuildable_directories_including_symlinks(
    tmp_path: Path, ignored: str
) -> None:
    archive = tmp_path / f"{ignored}.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("bank/frontend/src/main.ts", "console.log('ok')")
        bundle.writestr(f"bank/frontend/{ignored}/dependency", "dependency")
        info = ZipInfo(f"bank/frontend/{ignored}/bin/tool")
        info.external_attr = 0o120777 << 16
        bundle.writestr(info, "../dependency")

    output = tmp_path / "out"
    safe_extract_zip(archive, output, ArchiveLimits())

    assert (output / "bank" / "frontend" / "src" / "main.ts").is_file()
    assert not (output / "bank" / "frontend" / ignored).exists()


def test_ignored_entries_do_not_count_toward_file_limit(tmp_path: Path) -> None:
    archive = tmp_path / "dependencies.zip"
    make_zip(
        archive,
        {
            "app/node_modules/a.js": "a",
            "app/node_modules/b.js": "b",
            "app/src/main.js": "main",
        },
    )

    output = tmp_path / "out"
    safe_extract_zip(archive, output, ArchiveLimits(max_files=1))

    assert (output / "app" / "src" / "main.js").is_file()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("app/Foo.ts", "app/foo.ts"),
        ("app/config", "app/config/settings.json"),
        ("app/config/settings.json", "app/config"),
    ],
)
def test_rejects_case_and_file_directory_conflicts(
    tmp_path: Path, first: str, second: str
) -> None:
    archive = tmp_path / "conflict.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(first, "first")
        bundle.writestr(second, "second")

    with pytest.raises(UnsafeArchiveError, match="冲突"):
        safe_extract_zip(archive, tmp_path / "out", ArchiveLimits())


def test_rejects_invalid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"not a zip")

    with pytest.raises(UnsafeArchiveError, match="损坏"):
        safe_extract_zip(archive, tmp_path / "out", ArchiveLimits())


def test_redacts_explicit_and_named_secrets() -> None:
    text = "password=my-pass Authorization: Bearer-token username=demo"
    redacted = redact_secrets(text, ("demo",))
    assert "my-pass" not in redacted
    assert "Bearer-token" not in redacted
    assert "demo" not in redacted
