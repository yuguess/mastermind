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
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse
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
        self._href: str | None = None
        self._text_parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return

        text = " ".join("".join(self._text_parts).split())
        self.anchors.append((self._href, text))
        self._href = None
        self._text_parts = []


def decode_response(raw: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type:
        match = re.search(r"charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
        if match:
            charset = match.group(1)
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


def extract_tags(page_html: str, page_url: str) -> list[Tag]:
    parser = AnchorParser()
    parser.feed(page_html)

    tags: dict[str, Tag] = {}
    for href, text in parser.anchors:
        absolute_url = urljoin(page_url, href)
        parsed = urlparse(absolute_url)
        match = TAG_PATH_RE.match(parsed.path)
        if not match:
            continue

        slug = unquote(match.group(1)).strip()
        if not slug or slug.lower() in {"all", "random"}:
            continue

        name = normalize_name(text, slug)
        if not name:
            continue

        parsed_url = parsed._replace(query="", fragment="").geturl()
        tags.setdefault(slug.lower(), Tag(name=name, slug=slug, url=parsed_url))

    return sorted(tags.values(), key=lambda tag: tag.name.casefold())


def candidate_urls(base_url: str, paths: Iterable[str] = DEFAULT_TAG_PAGES) -> list[str]:
    base = base_url.rstrip("/") + "/"
    return [urljoin(base, path.lstrip("/")) for path in paths]


def download_tags(base_url: str, timeout: float) -> list[Tag]:
    errors: list[str] = []
    for url in candidate_urls(base_url):
        try:
            tags = extract_tags(fetch_html(url, timeout), url)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"{url}: {exc}")
            continue

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
