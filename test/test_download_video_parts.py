import pytest

from tagrank.app import download_video_parts
from tagrank.app.download_video_parts import (
    VideoPart,
    download_part_file_SE,
    download_segment_SE,
    download_video_parts_SE,
    index_range,
    merge_part_files_SE,
    merged_output_path,
    parse_args,
    output_path_for_part,
    playlist_csv_path,
    read_video_parts_SE,
    segment_filename,
    select_video_parts,
)


def test_playlist_csv_path_uses_video_id_and_resolution(tmp_path):
    assert playlist_csv_path(tmp_path, "jur-586", "360p") == tmp_path / "jur-586" / "360p.csv"


def test_segment_filename_uses_url_basename():
    assert segment_filename("https://cdn.example.com/360p/video0.jpeg?token=abc", 0) == "video0.jpeg"


def test_segment_filename_falls_back_to_index():
    assert segment_filename("https://cdn.example.com/360p/", 7) == "part_7"


def test_read_video_parts_skips_invalid_index_rows(tmp_path):
    csv_path = tmp_path / "360p.csv"
    csv_path.write_text(
        "index,resolution,referer,segment_url\n"
        "0,360p,https://example.com/en/jur-586,https://cdn.example.com/video0.jpeg\n"
        "bad,360p,https://example.com/en/jur-586,https://cdn.example.com/video1.jpeg\n",
        encoding="utf-8",
    )

    assert read_video_parts_SE(csv_path) == [
        VideoPart(
            index=0,
            resolution="360p",
            referer="https://example.com/en/jur-586",
            segment_url="https://cdn.example.com/video0.jpeg",
        )
    ]


def test_index_range_includes_end_index():
    assert list(index_range(2, 4)) == [2, 3, 4]


def test_index_range_rejects_reversed_bounds():
    with pytest.raises(ValueError, match="index-start"):
        index_range(4, 2)


def test_select_video_parts_returns_parts_sorted_by_index():
    parts = [
        VideoPart(index=3, resolution="360p", referer="referer", segment_url="https://cdn.example.com/3.jpeg"),
        VideoPart(index=1, resolution="360p", referer="referer", segment_url="https://cdn.example.com/1.jpeg"),
        VideoPart(index=2, resolution="360p", referer="referer", segment_url="https://cdn.example.com/2.jpeg"),
    ]

    assert select_video_parts(parts, 1, 3) == [
        VideoPart(index=1, resolution="360p", referer="referer", segment_url="https://cdn.example.com/1.jpeg"),
        VideoPart(index=2, resolution="360p", referer="referer", segment_url="https://cdn.example.com/2.jpeg"),
        VideoPart(index=3, resolution="360p", referer="referer", segment_url="https://cdn.example.com/3.jpeg"),
    ]


def test_select_video_parts_reports_missing_indexes():
    parts = [
        VideoPart(index=1, resolution="360p", referer="referer", segment_url="https://cdn.example.com/1.jpeg"),
        VideoPart(index=3, resolution="360p", referer="referer", segment_url="https://cdn.example.com/3.jpeg"),
    ]

    with pytest.raises(RuntimeError, match=r"indexes=\[2\]"):
        select_video_parts(parts, 1, 3)


def test_output_path_for_part_uses_video_resolution_and_filename(tmp_path):
    part = VideoPart(
        index=0,
        resolution="360p",
        referer="https://example.com/en/jur-586",
        segment_url="https://cdn.example.com/path/video0.jpeg",
    )

    assert output_path_for_part(tmp_path, "jur-586", part) == tmp_path / "jur-586" / "360p" / "video0.jpeg"


def test_merged_output_path_uses_ts_suffix(tmp_path):
    assert merged_output_path(tmp_path, "jur-586", "360p", 1, 3) == (
        tmp_path / "jur-586" / "360p" / "jur-586_360p_1_3.ts"
    )


def test_merge_part_files_writes_in_given_order(tmp_path):
    part_paths = [tmp_path / "2.jpeg", tmp_path / "1.jpeg", tmp_path / "3.jpeg"]
    for part_path, content in zip(part_paths, [b"two", b"one", b"three"]):
        part_path.write_bytes(content)

    output_path = tmp_path / "merged.ts"

    assert merge_part_files_SE(part_paths, output_path) == output_path
    assert output_path.read_bytes() == b"twoonethree"
    assert not (tmp_path / "merged.ts.part").exists()


def test_download_part_file_uses_referer_and_temp_file(monkeypatch, tmp_path):
    calls = []
    part = VideoPart(
        index=0,
        resolution="360p",
        referer="https://example.com/en/jur-586",
        segment_url="https://cdn.example.com/path/video0.jpeg",
    )

    def fake_download_segment_SE(segment_url, output_file, referer, timeout):
        calls.append((segment_url, referer, timeout, output_file.name.endswith(".part")))
        output_file.write(b"part-bytes")

    monkeypatch.setattr(download_video_parts, "download_segment_SE", fake_download_segment_SE)

    output_path = tmp_path / "video0.jpeg"
    assert download_part_file_SE(part, output_path, 10) == output_path
    assert output_path.read_bytes() == b"part-bytes"
    assert calls == [
        (
            "https://cdn.example.com/path/video0.jpeg",
            "https://example.com/en/jur-586",
            10,
            True,
        )
    ]
    assert not (tmp_path / "video0.jpeg.part").exists()


def test_download_segment_retries_until_success(monkeypatch, tmp_path):
    attempts = []

    class FakeResponse:
        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return None

        def read(self, _chunk_size):
            self.reads += 1
            return b"ok" if self.reads == 1 else b""

    def fake_open_url_SE(url, referer, timeout):
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise OSError("closed")
        return FakeResponse()

    monkeypatch.setattr(download_video_parts, "open_url_SE", fake_open_url_SE)

    output_path = tmp_path / "segment.bin"
    with output_path.open("wb") as file:
        download_segment_SE("https://cdn.example.com/video0.jpeg", file, "referer", 10)

    assert attempts == [1, 2, 3]
    assert output_path.read_bytes() == b"ok"


def test_download_video_parts_downloads_range_and_merges_by_index(monkeypatch, tmp_path):
    playlist_dir = tmp_path / "playlists"
    output_dir = tmp_path / "video"
    csv_path = playlist_dir / "jur-586" / "360p.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "index,resolution,referer,segment_url\n"
        "0,360p,https://example.com/en/jur-586,https://cdn.example.com/video0.jpeg\n"
        "1,360p,https://example.com/en/jur-586,https://cdn.example.com/video1.jpeg\n"
        "2,360p,https://example.com/en/jur-586,https://cdn.example.com/video2.jpeg\n",
        encoding="utf-8",
    )
    calls = []

    def fake_download_part_file_SE(part, output_path, timeout):
        calls.append((part.index, output_path.name, timeout))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(str(part.index).encode("utf-8"))
        return output_path

    monkeypatch.setattr(download_video_parts, "download_part_file_SE", fake_download_part_file_SE)

    output_path = download_video_parts_SE("jur-586", "360p", 0, 2, playlist_dir, output_dir, 20)

    assert output_path == output_dir / "jur-586" / "360p" / "jur-586_360p_0_2.ts"
    assert output_path.read_bytes() == b"012"
    assert calls == [
        (0, "video0.jpeg", 20),
        (1, "video1.jpeg", 20),
        (2, "video2.jpeg", 20),
    ]


def test_download_video_parts_skips_failed_parts(monkeypatch, tmp_path):
    playlist_dir = tmp_path / "playlists"
    output_dir = tmp_path / "video"
    csv_path = playlist_dir / "jur-586" / "360p.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "index,resolution,referer,segment_url\n"
        "0,360p,https://example.com/en/jur-586,https://cdn.example.com/video0.jpeg\n"
        "1,360p,https://example.com/en/jur-586,https://cdn.example.com/video1.jpeg\n"
        "2,360p,https://example.com/en/jur-586,https://cdn.example.com/video2.jpeg\n",
        encoding="utf-8",
    )

    def fake_download_part_file_SE(part, output_path, _timeout):
        if part.index == 1:
            raise OSError("closed")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(str(part.index).encode("utf-8"))
        return output_path

    monkeypatch.setattr(download_video_parts, "download_part_file_SE", fake_download_part_file_SE)

    output_path = download_video_parts_SE("jur-586", "360p", 0, 2, playlist_dir, output_dir, 20)

    assert output_path.read_bytes() == b"02"


def test_download_video_parts_fails_when_all_parts_fail(monkeypatch, tmp_path):
    playlist_dir = tmp_path / "playlists"
    output_dir = tmp_path / "video"
    csv_path = playlist_dir / "jur-586" / "360p.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "index,resolution,referer,segment_url\n"
        "0,360p,https://example.com/en/jur-586,https://cdn.example.com/video0.jpeg\n",
        encoding="utf-8",
    )

    def fake_download_part_file_SE(_part, _output_path, _timeout):
        raise OSError("closed")

    monkeypatch.setattr(download_video_parts, "download_part_file_SE", fake_download_part_file_SE)

    with pytest.raises(RuntimeError, match="no parts downloaded"):
        download_video_parts_SE("jur-586", "360p", 0, 0, playlist_dir, output_dir, 20)


def test_parse_args_rejects_single_index():
    with pytest.raises(SystemExit):
        parse_args(["--video-id", "jur-586", "--resolution", "360p", "--index", "1"])
