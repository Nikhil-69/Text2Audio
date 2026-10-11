import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.synth import SAMPLE_RATE


class Fake:
    def __init__(self, voice, speed=1.0):
        pass

    def synth_chunk_with_retry(self, text):
        return np.zeros(int(0.2 * SAMPLE_RATE), dtype=np.float32)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("T2A_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("T2A_BATCH_DIR", str(tmp_path / "batches"))
    import importlib, server
    importlib.reload(server)
    server.SYNTH_FACTORY = lambda v, s: Fake(v)
    with TestClient(server.app) as c:
        yield c


def _wait(c, job):
    for _ in range(200):
        if c.get(f"/api/render/{job}/status").json()["complete"]:
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _create(c):
    files = [("files", (f"dir/ch{i}.txt", f"Hello {w}.".encode(), "text/plain"))
             for i, w in ((10, "ten"), (2, "two"), (1, "one"))]
    files.append(("files", ("dir/.DS_Store", b"x", "application/octet-stream")))
    r = c.post("/api/batches", files=files, data={"title": "Demo"})
    assert r.status_code == 200
    return r.json()


def test_full_flow_with_range_merge_and_crud(client):
    b = _create(client)
    bid = b["id"]
    assert [i["name"] for i in b["items"]] == ["ch1", "ch2", "ch10"]      # natural order, junk skipped

    # nothing is finished yet -> merge refused
    assert client.post(f"/api/batches/{bid}/merge", json={"items": [1, 2]}).status_code == 409

    # generate only files 1-2 (as if the run had stopped before file 3)
    _wait(client, client.post(f"/api/batches/{bid}/synthesize", json={"items": [1, 2]}).json()["jobId"])
    st = client.get(f"/api/batches/{bid}").json()
    assert [i["status"] for i in st["items"]] == ["done", "done", "pending"]

    # merge + video of files 1-2, with a custom name
    _wait(client, client.post(f"/api/batches/{bid}/merge", json={"items": [1, 2], "name": "Part 1"}).json()["jobId"])
    _wait(client, client.post(f"/api/batches/{bid}/video", json={"items": [1, 2]}).json()["jobId"])
    outs = client.get(f"/api/batches/{bid}").json()["outputs"]
    assert [o["kind"] for o in outs] == ["audio", "video"] and outs[0]["name"] == "Part 1"
    for o in outs:
        r = client.get(f"/api/batches/{bid}/outputs/{o['id']}")
        assert r.status_code == 200 and len(r.content) > 0

    # CRUD
    assert client.patch(f"/api/batches/{bid}", json={"title": "New title"}).json()["title"] == "New title"
    r = client.patch(f"/api/batches/{bid}/items/3", json={"name": "Last"})
    assert r.json()["items"][2]["name"] == "Last"
    assert client.get(f"/api/batches/{bid}/items/3/text").json()["text"].strip().endswith("ten.")
    r = client.post(f"/api/batches/{bid}/items/3/move", json={"to": 1})
    assert [i["name"] for i in r.json()["items"]] == ["Last", "ch1", "ch2"]
    r = client.post(f"/api/batches/{bid}/items/delete", json={"items": [1]})
    assert [i["name"] for i in r.json()["items"]] == ["ch1", "ch2"]
    r = client.post(f"/api/batches/{bid}/files", files=[("files", ("x.txt", b"More text.", "text/plain"))])
    assert [i["index"] for i in r.json()["items"]] == [1, 2, 3]
    r = client.post(f"/api/batches/{bid}/items/reset", json={"items": [1]})
    assert r.json()["items"][0]["status"] == "pending"
    r = client.patch(f"/api/batches/{bid}/outputs/{outs[0]['id']}", json={"name": "Renamed"})
    assert r.json()["outputs"][0]["name"] == "Renamed"
    r = client.delete(f"/api/batches/{bid}/outputs/{outs[1]['id']}")
    assert [o["kind"] for o in r.json()["outputs"]] == ["audio"]

    # bad input -> 4xx, not a crash
    assert client.post(f"/api/batches/{bid}/items/9/move", json={"to": 1}).status_code == 400
    assert client.patch(f"/api/batches/{bid}/items/1", json={"text": " "}).status_code == 400
    assert client.get("/api/batches/zzzzzzzz").status_code in (404, 422)
    assert client.delete(f"/api/batches/{bid}").status_code == 200
    assert client.get(f"/api/batches/{bid}").status_code == 404


def _png(w=40, h=60):
    import struct, zlib
    raw = b"".join(b"\x00" + b"\x20\x80\xc0" * w for _ in range(h))
    chunk = lambda t, d: (struct.pack(">I", len(d)) + t + d
                          + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_video_uses_uploaded_image(client):
    bid = _create(client)["id"]
    _wait(client, client.post(f"/api/batches/{bid}/synthesize", json={}).json()["jobId"])

    assert client.get(f"/api/batches/{bid}/cover").status_code == 404
    bad = client.post(f"/api/batches/{bid}/cover", files={"image": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 400
    ok = client.post(f"/api/batches/{bid}/cover", files={"image": ("cover.png", _png(), "image/png")})
    assert ok.status_code == 200
    r = client.get(f"/api/batches/{bid}/cover")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    _wait(client, client.post(f"/api/batches/{bid}/video", json={"items": [1, 2]}).json()["jobId"])
    out = client.get(f"/api/batches/{bid}").json()["outputs"][-1]
    assert out["kind"] == "video" and out["cover"] is True and out["sizeBytes"] > 0

    assert client.delete(f"/api/batches/{bid}/cover").status_code == 200
    _wait(client, client.post(f"/api/batches/{bid}/video", json={"items": [1]}).json()["jobId"])
    assert client.get(f"/api/batches/{bid}").json()["outputs"][-1]["cover"] is False


def test_video_with_several_images(client):
    bid = _create(client)["id"]
    _wait(client, client.post(f"/api/batches/{bid}/synthesize", json={}).json()["jobId"])

    files = [("images", (f"{n}.png", _png(40 + n, 60), "image/png")) for n in range(3)]
    r = client.post(f"/api/batches/{bid}/images", files=files)
    assert r.status_code == 200 and len(r.json()["images"]) == 3
    ids = r.json()["images"]
    assert client.get(f"/api/batches/{bid}/images/{ids[0]}").headers["content-type"] == "image/jpeg"
    bad = client.post(f"/api/batches/{bid}/images", files=[("images", ("x.png", b"nope", "image/png"))])
    assert bad.status_code == 400

    _wait(client, client.post(f"/api/batches/{bid}/video", json={"items": [1, 2]}).json()["jobId"])
    out = client.get(f"/api/batches/{bid}").json()["outputs"][-1]
    assert out["kind"] == "video" and out["imageCount"] == 3 and out["sizeBytes"] > 0

    left = client.delete(f"/api/batches/{bid}/images/{ids[0]}").json()["images"]
    assert left == ids[1:]
    assert client.get(f"/api/batches/{bid}/images/not-an-id").status_code == 404


def test_cross_site_check_allows_same_origin_on_any_port_and_rejects_others(client):
    ok = client.post("/api/batches/zzzzzzzzzz/synthesize", json={},
                     headers={"Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123"})
    assert ok.status_code != 403                                  # reached the app (404/422 for the fake id)
    for host, origin in (("127.0.0.1:8123", "https://evil.example"),
                         ("127.0.0.1:8123", "http://127.0.0.1:9999"),
                         ("evil.example", "http://evil.example")):         # DNS-rebinding style
        r = client.post("/api/batches/zzzzzzzzzz/synthesize", json={},
                        headers={"Host": host, "Origin": origin})
        assert r.status_code == 403, (host, origin)
    assert client.post("/api/batches/zzzzzzzzzz/synthesize", json={},
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
