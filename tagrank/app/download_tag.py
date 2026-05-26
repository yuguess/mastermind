"""Download movie tags from missav.ws into data/tags.csv."""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "https://missav.ws"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "tags.csv"
DEFAULT_TAG_PAGES = (
    "/en/genres",
)
TAG_PATH_RE = re.compile(
    r"^/(?:dm\d+/)?(?:[a-z]{2}/)?genres?/([^/?#]+)/*$", re.IGNORECASE
)
GENRE_PAGE_RE = re.compile(r"^/(?:[a-z]{2}/)?genres?/*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Tag:
    """A normalized tag discovered on the source site."""

    name: str
    slug: str
    url: str


class AnchorParser(HTMLParser):
    """Collect anchors and their visible text from a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._opt_href: str | None = None
        self._text_parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        opt_href = dict(attrs).get("href") if tag.lower() == "a" else None
        if opt_href:
            self._opt_href = opt_href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._opt_href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._opt_href:
            text = " ".join("".join(self._text_parts).split())
            self.anchors.append((self._opt_href, text))
            self._opt_href = None
            self._text_parts = []


def decode_response(raw: bytes, opt_content_type: str | None) -> str:
    charset = "utf-8"
    if opt_content_type:
        opt_match = re.search(r"charset=([\w.-]+)", opt_content_type, flags=re.IGNORECASE)
        if opt_match:
            charset = opt_match.group(1)
    return raw.decode(charset, errors="replace")


def fetch_html(url: str, timeout: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return decode_response(response.read(), response.headers.get("Content-Type"))


def normalize_name(text: str, slug: str) -> str:
    text = html.unescape(text).strip()
    if text:
        return text
    return unquote(slug).replace("-", " ").replace("_", " ").strip()


def parse_tag_anchor(href: str, text: str, page_url: str) -> tuple[str, Tag] | None:
    absolute_url = urljoin(page_url, href)
    parsed = urlparse(absolute_url)
    opt_match = TAG_PATH_RE.match(parsed.path)
    if not opt_match:
        return None

    slug = unquote(opt_match.group(1)).strip()
    name = normalize_name(text, slug)
    if not slug or slug.lower() in {"all", "random"} or not name:
        return None

    parsed_url = parsed._replace(query="", fragment="").geturl()
    return slug.lower(), Tag(name=name, slug=slug, url=parsed_url)


def keep_first_tag_by_slug(tag_items: Iterable[tuple[str, Tag]]) -> dict[str, Tag]:
    reversed_items = reversed(list(tag_items))
    return dict(reversed({slug: tag for slug, tag in reversed_items}.items()))


def extract_tags(page_html: str, page_url: str) -> list[Tag]:
    parser = AnchorParser()
    parser.feed(page_html)

    tag_items = [
        opt_tag_item
        for href, text in parser.anchors
        if (opt_tag_item := parse_tag_anchor(href, text, page_url))
    ]
    tags = keep_first_tag_by_slug(tag_items)

    return sorted(tags.values(), key=lambda tag: tag.name.casefold())


def parse_genre_page_anchor(href: str, page_url: str) -> tuple[int, str] | None:
    absolute_url = urljoin(page_url, href)
    parsed = urlparse(absolute_url)
    if not GENRE_PAGE_RE.match(parsed.path):
        return None

    opt_page_values = parse_qs(parsed.query).get("page")
    if not opt_page_values:
        return 1, parsed._replace(fragment="").geturl()

    opt_page_number = parse_positive_int(opt_page_values[0])
    if opt_page_number is None:
        return None

    return opt_page_number, parsed._replace(fragment="").geturl()


def parse_positive_int(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 1 else None


def extract_genre_page_urls(page_html: str, page_url: str) -> list[str]:
    parser = AnchorParser()
    parser.feed(page_html)

    page_anchors = [
        opt_page_anchor
        for href, _text in parser.anchors
        if (opt_page_anchor := parse_genre_page_anchor(href, page_url))
    ]
    pages = {page_number: url for page_number, url in page_anchors}

    pages.setdefault(1, page_url)
    return [pages[page] for page in sorted(pages)]


def candidate_urls(base_url: str, paths: Iterable[str] = DEFAULT_TAG_PAGES) -> list[str]:
    base = base_url.rstrip("/") + "/"
    return [urljoin(base, path.lstrip("/")) for path in paths]


def fetch_html_or_error(url: str, timeout: float) -> tuple[str | None, str | None]:
    try:
        return fetch_html(url, timeout), None
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return None, f"{url}: {exc}"


def flatten_tag_groups(tag_groups: Iterable[Iterable[Tag]]) -> Iterator[Tag]:
    return (tag for tags in tag_groups for tag in tags)


def sort_tags(tags: Iterable[Tag]) -> list[Tag]:
    return sorted(tags, key=lambda tag: tag.name.casefold())


def deduplicate_tags(tags: Iterable[Tag]) -> list[Tag]:
    tag_items = [(tag.slug.lower(), tag) for tag in tags]
    return sort_tags(keep_first_tag_by_slug(tag_items).values())


def fetch_page_tags(page_url: str, timeout: float) -> tuple[list[Tag], str | None]:
    opt_html, opt_error = fetch_html_or_error(page_url, timeout)
    return (
        (extract_tags(opt_html, page_url), None)
        if opt_html is not None
        else ([], opt_error)
    )


def fetch_paginated_tags(first_page_html: str, first_page_url: str, timeout: float) -> tuple[list[Tag], list[str]]:
    page_urls = [
        page_url
        for page_url in extract_genre_page_urls(first_page_html, first_page_url)
        if page_url != first_page_url
    ]
    page_results = [fetch_page_tags(page_url, timeout) for page_url in page_urls]
    tag_groups = [
        extract_tags(first_page_html, first_page_url),
        *(tags for tags, _opt_error in page_results),
    ]
    errors = [opt_error for _tags, opt_error in page_results if opt_error]
    return deduplicate_tags(flatten_tag_groups(tag_groups)), errors


def download_tags_from_url(url: str, timeout: float) -> tuple[list[Tag], list[str]]:
    opt_first_page_html, opt_error = fetch_html_or_error(url, timeout)
    if opt_error:
        return [], [opt_error]
    if opt_first_page_html is None:
        return [], [f"{url}: empty response"]
    return fetch_paginated_tags(opt_first_page_html, url, timeout)


def download_tags(base_url: str, timeout: float) -> list[Tag]:
    errors: list[str] = []
    for url in candidate_urls(base_url):
        tags, url_errors = download_tags_from_url(url, timeout)
        errors.extend(url_errors)
        if tags:
            return tags
        errors.append(f"{url}: no tag links found")

    message = f"Could not download tags from {base_url}."
    if errors:
        message += "\nTried:\n- " + "\n- ".join(errors)
    raise RuntimeError(message)


def write_tags_csv(tags: Iterable[Tag], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=("name", "slug", "url"))
        writer.writeheader()
        for tag in tags:
            writer.writerow({"name": tag.name, "slug": tag.slug, "url": tag.url})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download missav.ws movie tags into data/tags.csv."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        tags = download_tags(args.base_url, args.timeout)
        write_tags_csv(tags, args.output)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"downloaded {len(tags)} tags to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
