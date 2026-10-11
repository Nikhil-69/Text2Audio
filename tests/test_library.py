import os

from backend.library import Library, new_id


def test_new_id_is_unique_hex():
    a, b = new_id(), new_id()
    assert a != b and len(a) >= 6 and a.isalnum()


def test_save_and_get_and_list(tmp_path):
    lib = Library(str(tmp_path))
    lib.new_dir("book1")
    manifest = {"id": "book1", "title": "T", "author": "A", "created": "2026-01-01T00:00:00",
                "chapters": [{"title": "C1", "startMs": 0, "endMs": 1000}]}
    lib.save_manifest("book1", manifest)
    assert lib.get("book1")["title"] == "T"
    listed = lib.list()
    assert len(listed) == 1 and listed[0]["id"] == "book1"


def test_list_sorted_newest_first(tmp_path):
    lib = Library(str(tmp_path))
    for i, ts in [("old", "2026-01-01T00:00:00"), ("new", "2026-02-01T00:00:00")]:
        lib.new_dir(i); lib.save_manifest(i, {"id": i, "created": ts})
    assert [m["id"] for m in lib.list()] == ["new", "old"]


def test_get_missing_returns_none(tmp_path):
    assert Library(str(tmp_path)).get("nope") is None


def test_delete_removes_dir(tmp_path):
    lib = Library(str(tmp_path)); lib.new_dir("x"); lib.save_manifest("x", {"id": "x", "created": "2026"})
    lib.delete("x")
    assert lib.get("x") is None


def test_audio_and_cover_paths(tmp_path):
    lib = Library(str(tmp_path)); d = lib.new_dir("y")
    assert lib.audio_path("y").replace("\\", "/").endswith("y/book.mp4")
    # cover_path returns None until a cover.jpg exists
    assert lib.cover_path("y") is None
    open(lib.audio_path("y"), "wb").close()  # touch to ensure dir works


def test_corrupt_manifest_isolated_from_library_list(tmp_path):
    lib = Library(str(tmp_path))
    good = lib.new_dir("good")
    lib.save_manifest("good", {"id": "good", "created": "2026"})
    bad = lib.new_dir("bad")
    with open(os.path.join(bad, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{broken")
    assert lib.get("bad") is None
    assert [m["id"] for m in lib.list()] == ["good"]
    assert not os.path.exists(os.path.join(good, "manifest.json.tmp"))
