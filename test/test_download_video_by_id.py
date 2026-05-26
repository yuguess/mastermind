from tagrank.app import download_video_by_id
from tagrank.app.download_video_by_id import (
    VideoPlaylist,
    VideoSource,
    download_segment_SE,
    download_m3u8_SE,
    download_video_by_id_SE,
    export_video_playlists_SE,
    extract_video_sources,
    fetch_text_with_retry_SE,
    leaf_m3u8_text_SE,
    output_path_for_source,
    parse_m3u8_playlists,
    parse_m3u8_segments,
    parse_m3u8_variants,
    select_video_source,
    video_id_from_link,
)


def test_video_id_from_link_uses_last_path_part():
    assert video_id_from_link("https://example.com/en/hhkl-240") == "hhkl-240"


def test_extract_video_sources_reads_script_and_source_attrs():
    html = r"""
    <video>
      <source src="/video/sample.mp4">
    </video>
    <script>
      window.url = "https:\/\/cdn.example.com\/movie.m3u8";
    </script>
    """

    assert extract_video_sources(html, "https://example.com/en/hhkl-240") == [
        VideoSource(url="https://example.com/video/sample.mp4", extension=".mp4"),
        VideoSource(url="https://cdn.example.com/movie.m3u8", extension=".m3u8"),
    ]


def test_extract_video_sources_reads_packed_eval_script():
    html = """
    <script>
    eval(function(p,a,c,k,e,d){e=function(c){return c.toString(36)};if(!''.replace(/^/,String)){while(c--){d[c.toString(a)]=k[c]||c.toString(a)}k=[function(e){return d[e]}];e=function(){return'\\\\w+'};c=1};while(c--){if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}return p}('f=\\'8://7.6/5-4-3-2-1/e.0\\';',16,16,'m3u8|45af610fbc40|8d31|4cb4|4ad9|72a9a5af|com|surrit|https||||||playlist|source'.split('|'),0,{}))
    </script>
    """

    assert extract_video_sources(html, "https://example.com/en/hhkl-240") == [
        VideoSource(
            url="https://surrit.com/72a9a5af-4ad9-4cb4-8d31-45af610fbc40/playlist.m3u8",
            extension=".m3u8",
        )
    ]


def test_select_video_source_prefers_mp4():
    sources = [
        VideoSource(url="https://example.com/movie.m3u8", extension=".m3u8"),
        VideoSource(url="https://example.com/movie.mp4", extension=".mp4"),
    ]

    assert select_video_source(sources) == VideoSource(
        url="https://example.com/movie.mp4",
        extension=".mp4",
    )


def test_parse_m3u8_segments_resolves_relative_urls():
    text = """
    #EXTM3U
    #EXTINF:1,
    part1.ts
    #EXTINF:1,
    nested/part2.jpeg
    """

    assert parse_m3u8_segments(text, "https://cdn.example.com/path/index.m3u8") == [
        "https://cdn.example.com/path/part1.ts",
        "https://cdn.example.com/path/nested/part2.jpeg",
    ]


def test_parse_m3u8_variants_resolves_master_playlist_urls():
    text = """
    #EXTM3U
    #EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360
    360p/video.m3u8
    """

    assert parse_m3u8_variants(text, "https://cdn.example.com/path/playlist.m3u8") == [
        "https://cdn.example.com/path/360p/video.m3u8"
    ]


def test_parse_m3u8_playlists_reads_resolution_labels():
    text = """
    #EXTM3U
    #EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360
    360p/video.m3u8
    #EXT-X-STREAM-INF:BANDWIDTH=2,RESOLUTION=1280x720
    720p/video.m3u8
    """

    assert parse_m3u8_playlists(text, "https://cdn.example.com/path/playlist.m3u8") == [
        VideoPlaylist(
            resolution="360p",
            url="https://cdn.example.com/path/360p/video.m3u8",
        ),
        VideoPlaylist(
            resolution="720p",
            url="https://cdn.example.com/path/720p/video.m3u8",
        ),
    ]


def test_leaf_m3u8_text_reads_first_variant(monkeypatch):
    calls = []

    def fake_fetch_text_SE(url, referer, timeout):
        calls.append((url, referer, timeout))
        return (
            "#EXTM3U\n360p/video.m3u8\n"
            if url.endswith("playlist.m3u8")
            else "#EXTM3U\n#EXTINF:1,\nvideo0.jpeg\n"
        )

    monkeypatch.setattr(download_video_by_id, "fetch_text_SE", fake_fetch_text_SE)

    assert leaf_m3u8_text_SE("https://cdn.example.com/playlist.m3u8", "referer", 10) == (
        "#EXTM3U\n#EXTINF:1,\nvideo0.jpeg\n",
        "https://cdn.example.com/360p/video.m3u8",
    )
    assert calls == [
        ("https://cdn.example.com/playlist.m3u8", "referer", 10),
        ("https://cdn.example.com/360p/video.m3u8", "referer", 10),
    ]


def test_output_path_for_source_uses_video_id_and_extension(tmp_path):
    assert output_path_for_source(
        tmp_path,
        "hhkl-240",
        VideoSource(url="https://example.com/movie.m3u8", extension=".m3u8"),
    ) == tmp_path / "hhkl-240.ts"


def test_fetch_text_with_retry_retries_until_success(monkeypatch):
    attempts = []

    def fake_fetch_text_SE(url, referer, timeout):
        attempts.append((url, referer, timeout))
        if len(attempts) < 3:
            raise OSError("eof")
        return "ok"

    monkeypatch.setattr(download_video_by_id, "fetch_text_SE", fake_fetch_text_SE)

    assert fetch_text_with_retry_SE("https://example.com", "referer", 10) == "ok"
    assert attempts == [
        ("https://example.com", "referer", 10),
        ("https://example.com", "referer", 10),
        ("https://example.com", "referer", 10),
    ]


def test_download_video_by_id_downloads_selected_source(monkeypatch, tmp_path):
    calls = []

    def fake_fetch_text_SE(url, referer, timeout):
        calls.append(("fetch", url, referer, timeout))
        return '<source src="https://cdn.example.com/movie.mp4">'

    def fake_download_source_SE(source, output_path, referer, timeout):
        calls.append(("download", source, output_path, referer, timeout))
        return output_path

    monkeypatch.setattr(download_video_by_id, "fetch_text_SE", fake_fetch_text_SE)
    monkeypatch.setattr(download_video_by_id, "download_source_SE", fake_download_source_SE)

    assert download_video_by_id_SE(
        "https://example.com/en/hhkl-240",
        tmp_path,
        10,
    ) == tmp_path / "hhkl-240.mp4"
    assert calls == [
        ("fetch", "https://example.com/en/hhkl-240", "https://missav.ws/en/genres", 10),
        (
            "download",
            VideoSource(url="https://cdn.example.com/movie.mp4", extension=".mp4"),
            tmp_path / "hhkl-240.mp4",
            "https://example.com/en/hhkl-240",
            10,
        ),
    ]


def test_download_m3u8_uses_page_referer_for_segments(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return None

        def read(self, _chunk_size):
            return b""

    def fake_leaf_m3u8_text_SE(source_url, referer, timeout):
        calls.append(("leaf", source_url, referer, timeout))
        return "#EXTM3U\n#EXTINF:1,\nvideo0.jpeg\n", source_url

    def fake_open_url_SE(url, referer, timeout):
        calls.append(("segment", url, referer, timeout))
        return FakeResponse()

    monkeypatch.setattr(download_video_by_id, "leaf_m3u8_text_SE", fake_leaf_m3u8_text_SE)
    monkeypatch.setattr(download_video_by_id, "open_url_SE", fake_open_url_SE)

    assert download_m3u8_SE(
        "https://cdn.example.com/video.m3u8",
        tmp_path / "out.ts",
        "https://example.com/en/hhkl-240",
        10,
    ) == tmp_path / "out.ts"
    assert calls == [
        ("leaf", "https://cdn.example.com/video.m3u8", "https://example.com/en/hhkl-240", 10),
        (
            "segment",
            "https://cdn.example.com/video0.jpeg",
            "https://example.com/en/hhkl-240",
            10,
        ),
    ]


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

    monkeypatch.setattr(download_video_by_id, "open_url_SE", fake_open_url_SE)

    output_path = tmp_path / "segment.bin"
    with output_path.open("wb") as file:
        download_segment_SE("https://cdn.example.com/video0.jpeg", file, "referer", 10)

    assert attempts == [1, 2, 3]
    assert output_path.read_bytes() == b"ok"


def test_export_video_playlists_writes_url_and_segments(monkeypatch, tmp_path):
    page_html = """
    <script>
    window.url = "https://cdn.example.com/playlist.m3u8";
    </script>
    """
    master_m3u8 = """
    #EXTM3U
    #EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360
    360p/video.m3u8
    #EXT-X-STREAM-INF:BANDWIDTH=2,RESOLUTION=1280x720
    720p/video.m3u8
    """
    leaf_m3u8 = """
    #EXTM3U
    #EXTINF:1,
    video0.jpeg
    #EXTINF:1,
    video1.jpeg
    """

    def fake_fetch_text_with_retry_SE(url, _referer, _timeout):
        if url == "https://example.com/en/hhkl-240":
            return page_html
        if url == "https://cdn.example.com/playlist.m3u8":
            return master_m3u8
        return leaf_m3u8

    monkeypatch.setattr(
        download_video_by_id,
        "fetch_text_with_retry_SE",
        fake_fetch_text_with_retry_SE,
    )

    output_paths = export_video_playlists_SE(
        "https://example.com/en/hhkl-240",
        tmp_path,
        10,
    )

    assert output_paths == [
        tmp_path / "hhkl-240" / "360p.m3u8.txt",
        tmp_path / "hhkl-240" / "360p.csv",
        tmp_path / "hhkl-240" / "720p.m3u8.txt",
        tmp_path / "hhkl-240" / "720p.csv",
    ]
    assert (tmp_path / "hhkl-240" / "360p.m3u8.txt").read_text(encoding="utf-8") == (
        "https://cdn.example.com/360p/video.m3u8\n"
    )
    assert (tmp_path / "hhkl-240" / "360p.csv").read_text(encoding="utf-8") == (
        "index,resolution,referer,segment_url\n"
        "0,360p,https://example.com/en/hhkl-240,https://cdn.example.com/360p/video0.jpeg\n"
        "1,360p,https://example.com/en/hhkl-240,https://cdn.example.com/360p/video1.jpeg\n"
    )


def test_export_video_playlists_deduplicates_direct_variant_sources(monkeypatch, tmp_path):
    page_html = """
    <script>
    window.master = "https://cdn.example.com/playlist.m3u8";
    window.direct = "https://cdn.example.com/720p/video.m3u8";
    </script>
    """
    master_m3u8 = """
    #EXTM3U
    #EXT-X-STREAM-INF:BANDWIDTH=2,RESOLUTION=1280x720
    720p/video.m3u8
    """
    leaf_m3u8 = "#EXTM3U\n#EXTINF:1,\nvideo0.jpeg\n"

    def fake_fetch_text_with_retry_SE(url, _referer, _timeout):
        if url == "https://example.com/en/hhkl-240":
            return page_html
        if url == "https://cdn.example.com/playlist.m3u8":
            return master_m3u8
        return leaf_m3u8

    monkeypatch.setattr(
        download_video_by_id,
        "fetch_text_with_retry_SE",
        fake_fetch_text_with_retry_SE,
    )

    assert export_video_playlists_SE(
        "https://example.com/en/hhkl-240",
        tmp_path,
        10,
    ) == [
        tmp_path / "hhkl-240" / "720p.m3u8.txt",
        tmp_path / "hhkl-240" / "720p.csv",
    ]
