"""Download video lists for selected tags."""

from __future__ import annotations

import argparse
import csv
import http.client
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from tagrank.app.download_tag import PROJECT_ROOT, decode_response
from tagrank.base_adt import Ints, OptStr, Strs


DEFAULT_INPUT = PROJECT_ROOT / "data" / "tags.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "tag_list"
DEFAULT_REFERER = "https://missav.ws/en/genres"
DEFAULT_PAGES = 2
VIDEO_PATH_RE = re.compile(r"^/(?:dm\d+/)?en/[\w.-]+$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TagSource:
    id: int
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class VideoLink:
    url: str
    code: str
    title: str
    image_description: str
    duration: str


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._opt_href: OptStr = None
        self._opt_alt: OptStr = None
        self._opt_image_alt: OptStr = None
        self._text_parts: Strs = []
        self.anchors: list[tuple[str, OptStr, OptStr, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, OptStr]]) -> None:
        attr_map = dict(attrs)
        opt_href = attr_map.get("href") if tag.lower() == "a" else None
        if opt_href:
            self._opt_href = opt_href
            self._opt_alt = attr_map.get("alt")
            self._opt_image_alt = None
            self._text_parts = []
        if tag.lower() == "img" and self._opt_href:
            self._opt_image_alt = attr_map.get("alt")

    def handle_data(self, data: str) -> None:
        if self._opt_href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._opt_href:
            text = " ".join("".join(self._text_parts).split())
            self.anchors.append((self._opt_href, self._opt_alt, self._opt_image_alt, text))
            self._opt_href = None
            self._opt_alt = None
            self._opt_image_alt = None
            self._text_parts = []


def parse_args(opt_argv: Optional[Strs] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tag-ids", default="")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    return parser.parse_args(opt_argv)


def parse_optional_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def parse_tag_ids(tag_ids_text: str) -> Ints:
    items = [item.strip() for item in tag_ids_text.split(",")]
    opt_numbers = [parse_optional_int(item) for item in items if item]
    return [number for number in opt_numbers if number is not None]


def tag_source_from_row(row: dict[str, str]) -> TagSource:
    return TagSource(id=int(row["id"]), name=row["name"], url=row["url"])


def select_tag_sources(tags: list[TagSource], tag_ids: Ints) -> list[TagSource]:
    selected_ids = set(tag_ids)
    selected_tags = [tag for tag in tags if tag.id in selected_ids] if tag_ids else tags
    return selected_tags


def read_tag_sources_SE(input_path: Path, tag_ids: Ints) -> list[TagSource]:
    with input_path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return select_tag_sources([tag_source_from_row(row) for row in rows], tag_ids)


def fetch_html_with_referer_SE(url: str, referer: str, timeout: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
            "Upgrade-Insecure-Requests": "1",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return decode_response(response.read(), response.headers.get("Content-Type"))


def page_url(base_url: str, page_number: int) -> str:
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    if page_number > 1:
        query["page"] = [str(page_number)]
    clean_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=clean_query, fragment=""))


def parse_links(page_html: str) -> list[tuple[str, OptStr, OptStr, str]]:
    parser = AnchorParser()
    parser.feed(page_html)
    return parser.anchors


def is_video_anchor(page_url_value: str, anchor: tuple[str, OptStr, OptStr, str]) -> bool:
    href, opt_alt, _opt_image_alt, _text = anchor
    path = urlparse(urljoin(page_url_value, href)).path
    return opt_alt is not None and VIDEO_PATH_RE.match(path) is not None


def duration_text(text: str) -> str:
    return text if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text) else ""


def title_text(text: str) -> str:
    return "" if duration_text(text) else text


def video_from_anchor(page_url_value: str, anchor: tuple[str, OptStr, OptStr, str]) -> VideoLink:
    href, opt_alt, opt_image_alt, text = anchor
    absolute_url = urljoin(page_url_value, href)
    return VideoLink(
        url=absolute_url,
        code=opt_alt or "",
        title=title_text(text),
        image_description=opt_image_alt or "",
        duration=duration_text(text),
    )


def merge_video(current: VideoLink, new: VideoLink) -> VideoLink:
    return VideoLink(
        url=current.url,
        code=current.code or new.code,
        title=current.title or new.title,
        image_description=current.image_description or new.image_description,
        duration=current.duration or new.duration,
    )


def deduplicate_videos(videos: Iterable[VideoLink]) -> list[VideoLink]:
    merged: dict[str, VideoLink] = {}
    for video in videos:
        merged[video.url] = merge_video(merged[video.url], video) if video.url in merged else video
    return list(merged.values())


def extract_videos(page_html: str, page_url_value: str) -> list[VideoLink]:
    anchors = parse_links(page_html)
    videos = [
        video_from_anchor(page_url_value, anchor)
        for anchor in anchors
        if is_video_anchor(page_url_value, anchor)
    ]
    return deduplicate_videos(videos)


def fetch_page_videos_SE(tag_url: str, page_number: int) -> list[VideoLink]:
    current_url = page_url(tag_url, page_number)
    try:
        page_html = fetch_html_with_referer_SE(current_url, DEFAULT_REFERER, 20)
    except (HTTPError, URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
        print(f"ERR {current_url}: {exc}")
        return []
    return extract_videos(page_html, current_url)


def fetch_tag_videos_SE(tag: TagSource, pages: int) -> list[VideoLink]:
    page_numbers = range(1, pages + 1)
    videos = [
        video
        for page_videos in map(lambda page: fetch_page_videos_SE(tag.url, page), page_numbers)
        for video in page_videos
    ]
    return deduplicate_videos(videos)


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "tag"


def write_video_csv_SE(output_dir: Path, tag: TagSource, videos: list[VideoLink]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename(tag.name)}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=("url", "code", "title", "duration", "image_description"),
        )
        writer.writeheader()
        writer.writerows(
            {
                "url": video.url,
                "code": video.code,
                "title": video.title,
                "duration": video.duration,
                "image_description": video.image_description,
            }
            for video in videos
        )
    return output_path


def download_tag_list_SE(
    input_path: Path,
    output_dir: Path,
    tag_ids: Ints,
    pages: int,
) -> list[Path]:
    tags = read_tag_sources_SE(input_path, tag_ids)
    rows = [(tag, fetch_tag_videos_SE(tag, pages)) for tag in tags]
    return [write_video_csv_SE(output_dir, tag, videos) for tag, videos in rows]


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    args = parse_args(opt_argv)
    tag_ids = parse_tag_ids(args.tag_ids)
    output_paths = download_tag_list_SE(
        args.input,
        args.output_dir,
        tag_ids,
        args.pages,
    )
    print(f"saved {len(output_paths)} tag video lists to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_SE())
