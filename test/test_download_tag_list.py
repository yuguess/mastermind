from tagrank.app import download_tag_list
from tagrank.app.download_tag_list import (
    TagSource,
    VideoLink,
    extract_videos,
    fetch_page_html_with_retry_SE,
    merge_incremental_videos,
    page_url,
    parse_tag_ids,
    safe_filename,
    select_tag_sources,
)


def test_page_url_adds_page_query():
    assert page_url("https://example.com/en/genres/Foo", 1) == (
        "https://example.com/en/genres/Foo"
    )
    assert page_url("https://example.com/en/genres/Foo", 3) == (
        "https://example.com/en/genres/Foo?page=3"
    )


def test_extract_videos_deduplicates_video_links():
    html = """
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890"></a>
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890">2:17:34</a>
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890">HEZ-890 title</a>
    <a href="https://missav.ws/en/genres/VR">Not a video</a>
    """

    assert extract_videos(html, "https://missav.ws/en/genres/Foo") == [
        VideoLink(
            url="https://missav.ws/dm2/en/hez-890",
            code="hez-890",
            title="HEZ-890 title",
            image_description="",
            duration="2:17:34",
        )
    ]


def test_extract_videos_reads_image_description():
    html = """
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890">
        <img alt="Image description">
    </a>
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890">2:17:34</a>
    <a href="https://missav.ws/dm2/en/hez-890" alt="hez-890">HEZ-890 title</a>
    """

    assert extract_videos(html, "https://missav.ws/en/genres/Foo") == [
        VideoLink(
            url="https://missav.ws/dm2/en/hez-890",
            code="hez-890",
            title="HEZ-890 title",
            image_description="Image description",
            duration="2:17:34",
        )
    ]


def test_safe_filename():
    assert safe_filename("3P, 4P") == "3P_4P"


def test_parse_tag_ids_reads_comma_separated_numbers():
    assert parse_tag_ids("1, 2,3") == [1, 2, 3]


def test_parse_tag_ids_ignores_empty_and_invalid_items():
    assert parse_tag_ids("1, abc, , 4") == [1, 4]


def test_select_tag_sources_uses_requested_ids():
    tags = [
        TagSource(id=1, name="A", url="https://example.com/a"),
        TagSource(id=2, name="B", url="https://example.com/b"),
        TagSource(id=3, name="C", url="https://example.com/c"),
    ]

    assert select_tag_sources(tags, [3, 1]) == [
        TagSource(id=1, name="A", url="https://example.com/a"),
        TagSource(id=3, name="C", url="https://example.com/c"),
    ]


def test_select_tag_sources_returns_all_tags_without_ids():
    tags = [
        TagSource(id=1, name="A", url="https://example.com/a"),
        TagSource(id=2, name="B", url="https://example.com/b"),
        TagSource(id=3, name="C", url="https://example.com/c"),
    ]

    assert select_tag_sources(tags, []) == tags


def test_fetch_page_html_with_retry_retries_until_success(monkeypatch):
    attempts = []
    sleeps = []

    def fake_fetch_once_SE(url):
        attempts.append(url)
        return ("<html></html>", None) if len(attempts) == 3 else (None, "timeout")

    monkeypatch.setattr(download_tag_list, "fetch_page_html_once_SE", fake_fetch_once_SE)
    monkeypatch.setattr(download_tag_list, "retry_delay_SE", sleeps.append)

    assert fetch_page_html_with_retry_SE("https://example.com", 3, 1) == (
        "<html></html>",
        None,
    )
    assert attempts == ["https://example.com"] * 3
    assert sleeps == [1, 1]


def test_merge_incremental_videos_uses_code_as_id():
    existing = [
        VideoLink(
            url="https://example.com/old-a",
            code="aaa-001",
            title="Old title",
            image_description="Old image",
            duration="1:00",
        )
    ]
    new_videos = [
        VideoLink(
            url="https://example.com/new-a",
            code="aaa-001",
            title="New title",
            image_description="",
            duration="",
        ),
        VideoLink(
            url="https://example.com/b",
            code="bbb-002",
            title="B title",
            image_description="B image",
            duration="2:00",
        ),
    ]

    assert merge_incremental_videos(existing, new_videos) == [
        VideoLink(
            url="https://example.com/new-a",
            code="aaa-001",
            title="New title",
            image_description="Old image",
            duration="1:00",
        ),
        VideoLink(
            url="https://example.com/b",
            code="bbb-002",
            title="B title",
            image_description="B image",
            duration="2:00",
        ),
    ]
