"""Download video lists for selected tags."""

from __future__ import annotations

import argparse
import csv
import http.client
import re
import sys
import time
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
DEFAULT_PAGE_INDEX = 1
DEFAULT_PAGES = 2
WRITE_BATCH_PAGES = 5
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 3
VIDEO_PATH_RE = re.compile(r"^/(?:dm\d+/)?en/[\w.-]+$", re.IGNORECASE)
VIDEO_CSV_FIELDS = ("id", "url", "code", "title", "duration", "image_description")


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
    parser.add_argument("--page-index", type=int, default=DEFAULT_PAGE_INDEX)
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


def is_last_attempt(attempt: int, max_retries: int) -> bool:
    return attempt >= max_retries


def fetch_page_html_once_SE(url: str) -> tuple[OptStr, OptStr]:
    try:
        return fetch_html_with_referer_SE(url, DEFAULT_REFERER, 20), None
    except (HTTPError, URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
        return None, str(exc)


def retry_delay_SE(delay_seconds: int) -> None:
    time.sleep(delay_seconds)


def fetch_page_html_with_retry_SE(
    url: str,
    max_retries: int = MAX_RETRIES,
    delay_seconds: int = RETRY_DELAY_SECONDS,
) -> tuple[OptStr, OptStr]:
    opt_html = None
    opt_error = None
    for attempt in range(1, max_retries + 1):
        opt_html, opt_error = fetch_page_html_once_SE(url)
        if opt_html is not None:
            return opt_html, None
        rtry_aft_sec = attempt * delay_seconds
        print(f"retry {attempt}/{max_retries} aft {rtry_aft_sec}s: {url}, err:{opt_error}")
        if not is_last_attempt(attempt, max_retries):
            retry_delay_SE(rtry_aft_sec)
    return opt_html, opt_error


def fetch_page_videos_SE(tag_url: str, page_number: int) -> list[VideoLink]:
    current_url = page_url(tag_url, page_number)
    opt_page_html, opt_error = fetch_page_html_with_retry_SE(current_url)
    if opt_page_html is None:
        print(f"ERR {current_url}: {opt_error}")
        return []
    return extract_videos(opt_page_html, current_url)


def page_numbers(page_index: int, pages: int) -> range:
    return range(page_index, page_index + pages)


def page_batches(page_index: int, pages: int, batch_size: int = WRITE_BATCH_PAGES) -> list[range]:
    starts = range(page_index, page_index + pages, batch_size)
    return [
        range(start, min(start + batch_size, page_index + pages))
        for start in starts
    ]


def fetch_tag_videos_SE(tag: TagSource, page_index: int, pages: int) -> list[VideoLink]:
    videos = [
        video
        for page_videos in map(
            lambda page: fetch_page_videos_SE(tag.url, page),
            page_numbers(page_index, pages),
        )
        for video in page_videos
    ]
    return deduplicate_videos(videos)


def fetch_pages_videos_SE(tag: TagSource, pages: range) -> list[VideoLink]:
    videos = [
        video
        for page_videos in map(lambda page: fetch_page_videos_SE(tag.url, page), pages)
        for video in page_videos
    ]
    return deduplicate_videos(videos)


def download_tag_batches_SE(
    output_dir: Path,
    tag: TagSource,
    page_index: int,
    pages: int,
) -> Path:
    output_path = output_dir / f"{safe_filename(tag.name)}.csv"
    [
        write_video_csv_SE(output_dir, tag, fetch_pages_videos_SE(tag, batch))
        for batch in page_batches(page_index, pages)
    ]
    return output_path


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "tag"


def row_value(row: dict[str, str], key: str) -> str:
    opt_value = row.get(key)
    return opt_value or ""


def video_id(video: VideoLink) -> str:
    return video.code or video.url


def video_from_csv_row(row: dict[str, str]) -> VideoLink:
    return VideoLink(
        url=row_value(row, "url"),
        code=row_value(row, "code") or row_value(row, "id"),
        title=row_value(row, "title"),
        duration=row_value(row, "duration"),
        image_description=row_value(row, "image_description"),
    )


def read_existing_videos_SE(output_path: Path) -> list[VideoLink]:
    if not output_path.exists():
        return []
    with output_path.open(newline="", encoding="utf-8") as file:
        return [video_from_csv_row(row) for row in csv.DictReader(file)]


def update_video(existing: VideoLink, new: VideoLink) -> VideoLink:
    return VideoLink(
        url=new.url or existing.url,
        code=new.code or existing.code,
        title=new.title or existing.title,
        image_description=new.image_description or existing.image_description,
        duration=new.duration or existing.duration,
    )


def merge_incremental_videos(existing: list[VideoLink], new_videos: list[VideoLink]) -> list[VideoLink]:
    video_map = {video_id(video): video for video in existing}
    new_ids = [video_id(video) for video in new_videos if video_id(video) not in video_map]
    for video in new_videos:
        current_id = video_id(video)
        video_map[current_id] = (
            update_video(video_map[current_id], video)
            if current_id in video_map
            else video
        )
    return [video_map[current_id] for current_id in [*map(video_id, existing), *new_ids]]


def video_csv_row(video: VideoLink) -> dict[str, str]:
    return {
        "id": video_id(video),
        "url": video.url,
        "code": video.code,
        "title": video.title,
        "duration": video.duration,
        "image_description": video.image_description,
    }


def write_video_csv_SE(output_dir: Path, tag: TagSource, videos: list[VideoLink]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename(tag.name)}.csv"
    merged_videos = merge_incremental_videos(read_existing_videos_SE(output_path), videos)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=VIDEO_CSV_FIELDS,
        )
        writer.writeheader()
        writer.writerows(video_csv_row(video) for video in merged_videos)
    return output_path


def download_tag_list_SE(
    input_path: Path,
    output_dir: Path,
    tag_ids: Ints,
    page_index: int,
    pages: int,
) -> list[Path]:
    tags = read_tag_sources_SE(input_path, tag_ids)
    return [
        download_tag_batches_SE(output_dir, tag, page_index, pages)
        for tag in tags
    ]


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    args = parse_args(opt_argv)
    tag_ids = parse_tag_ids(args.tag_ids)
    output_paths = download_tag_list_SE(
        args.input,
        args.output_dir,
        tag_ids,
        args.page_index,
        args.pages,
    )
    print(f"saved {len(output_paths)} tag video lists to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_SE())
