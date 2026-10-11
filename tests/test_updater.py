import io
import os
import zipfile

from backend import updater


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return buf.getvalue()


def test_version_compare():
    assert updater.is_newer("1.10.0", "1.9.2")
    assert updater.is_newer("v2", "1.9")
    assert not updater.is_newer("1.4.0", "1.4.0")
    assert not updater.is_newer("1.3.9", "1.4.0")


def test_apply_zip_updates_code_and_keeps_user_data(tmp_path):
    (tmp_path / "server.py").write_text("old")
    (tmp_path / "requirements.txt").write_text("a\n")
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "book.txt").write_text("mine")
    data = _zip({"Text2Audio-abc/server.py": "new", "Text2Audio-abc/pyproject.toml": "",
                 "Text2Audio-abc/requirements.txt": "a\nb\n",
                 "Text2Audio-abc/library/book.txt": "OVERWRITE", "Text2Audio-abc/web/x.js": "js"})
    assert updater.apply_zip(data, str(tmp_path)) is True        # requirements changed
    assert (tmp_path / "server.py").read_text() == "new"
    assert (tmp_path / "web" / "x.js").read_text() == "js"
    assert (tmp_path / "library" / "book.txt").read_text() == "mine"


def test_apply_zip_rejects_non_app_and_traversal(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        updater.apply_zip(_zip({"readme.txt": "x"}), str(tmp_path))
    with pytest.raises(ValueError):
        updater.apply_zip(_zip({"../evil.py": "x"}), str(tmp_path))


def test_auto_update_skips_git_checkouts_and_offline(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(updater, "latest_release", lambda *a, **k: pytest_fail())
    assert updater.auto_update(str(tmp_path)) is False
    os.rmdir(tmp_path / ".git")
    monkeypatch.setattr(updater, "latest_release", lambda *a, **k: None)
    assert updater.auto_update(str(tmp_path)) is False


def pytest_fail():
    raise AssertionError("must not check for updates in a git checkout")
