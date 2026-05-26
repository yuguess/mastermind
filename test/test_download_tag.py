from tagrank.app.download_tag import Tag, extract_genre_page_urls, extract_tags, write_tags_csv


def test_extract_tags_from_genre_links():
    html = """
    <html>
      <body>
        <a href="/genres/big-tits">Big Tits</a>
        <a href="https://missav.com/en/genres/cosplay">Cosplay</a>
        <a href="/genres/big-tits">Duplicate</a>
        <a href="/actresses/example">Not a tag</a>
      </body>
    </html>
    """

    tags = extract_tags(html, "https://missav.com/genres")

    assert tags == [
        Tag(name="Big Tits", slug="big-tits", url="https://missav.com/genres/big-tits"),
        Tag(name="Cosplay", slug="cosplay", url="https://missav.com/en/genres/cosplay"),
    ]


def test_extract_tags_from_missav_ws_prefixed_links():
    html = """
    <a href="/dm96/en/genres/Hd">Hd</a>
    <a href="/dm96/en/genres/Hd">276601 videos</a>
    <a href="/dm139/en/genres/Exclusive">Exclusive</a>
    """

    tags = extract_tags(html, "https://missav.ws/en/genres")

    assert tags == [
        Tag(
            name="Exclusive",
            slug="Exclusive",
            url="https://missav.ws/dm139/en/genres/Exclusive",
        ),
        Tag(name="Hd", slug="Hd", url="https://missav.ws/dm96/en/genres/Hd"),
    ]


def test_extract_tags_uses_slug_when_anchor_text_is_empty():
    html = '<a href="/genres/married-woman"><span></span></a>'

    tags = extract_tags(html, "https://missav.com/genres")

    assert tags == [
        Tag(
            name="married woman",
            slug="married-woman",
            url="https://missav.com/genres/married-woman",
        )
    ]


def test_extract_genre_page_urls():
    html = """
    <a href="https://missav.ws/en/genres?page=3">3</a>
    <a href="/en/genres?page=2">2</a>
    <a href="/en/genres">Genre</a>
    <a href="/en/actresses?page=9">Not genre</a>
    <a href="/en/genres?page=bad">Bad page</a>
    """

    assert extract_genre_page_urls(html, "https://missav.ws/en/genres") == [
        "https://missav.ws/en/genres",
        "https://missav.ws/en/genres?page=2",
        "https://missav.ws/en/genres?page=3",
    ]


def test_write_tags_csv(tmp_path):
    output = tmp_path / "tags.csv"
    write_tags_csv(
        [Tag(name="Cosplay", slug="cosplay", url="https://missav.com/genres/cosplay")],
        output,
    )

    assert output.read_text(encoding="utf-8") == (
        "id,name,slug,url\n"
        "1,Cosplay,cosplay,https://missav.com/genres/cosplay\n"
    )
