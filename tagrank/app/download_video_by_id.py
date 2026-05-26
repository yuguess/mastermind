"""Download one video from its detail page."""

from __future__ import annotations

import argparse
import csv
import html
import http.client
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from tagrank.app.download_tag import PROJECT_ROOT, decode_response
from tagrank.base_adt import OptStr, Strs


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "video_list"
DEFAULT_PLAYLIST_OUTPUT_DIR = PROJECT_ROOT / "data" / "video_playlist_exports"
DEFAULT_REFERER = "https://missav.ws/en/genres"
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 3
VIDEO_SOURCE_RE = re.compile(
    r"https?://[^\"'\\\s<>]+?\.(?:mp4|m3u8)(?:\?[^\"'\\\s<>]*)?",
    re.IGNORECASE,
)
PACKED_EVAL_RE = re.compile(
    r"eval\(function\(p,a,c,k,e,d\).*?\}\('(?P<p>.*?)',(?P<a>\d+),(?P<c>\d+),'(?P<k>.*?)'\.split\('\|'\),0,\{\}\)\)",
    re.DOTALL,
)
PACKER_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
M3U8_SEGMENT_EXTENSIONS = (".ts", ".m4s", ".aac", ".mp4", ".jpeg", ".jpg")


@dataclass(frozen=True, slots=True)
class VideoSource:
    url: str
    extension: str


@dataclass(frozen=True, slots=True)
class VideoPlaylist:
    resolution: str
    url: str


class SourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: Strs = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, OptStr]]) -> None:
        attr_map = dict(attrs)
        values = [
            opt_value
            for key, opt_value in attr_map.items()
            if key.lower() in {"src", "data-src"} and opt_value
        ]
        self.urls.extend(values)


def parse_args(opt_argv: Optional[Strs] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--export-playlists", action="store_true")
    return parser.parse_args(opt_argv)


def request_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }


def fetch_text_SE(url: str, referer: str, timeout: float) -> str:
    request = Request(url, headers=request_headers(referer))
    with urlopen(request, timeout=timeout) as response:
        return decode_response(response.read(), response.headers.get("Content-Type"))


def fetch_text_with_retry_SE(url: str, referer: str, timeout: float) -> str:
    opt_error = None
    for attempt in retry_numbers():
        try:
            return fetch_text_SE(url, referer, timeout)
        except Exception as exc:
            opt_error = exc
            print(f"text retry {attempt}/{MAX_RETRIES}: {url}, err:{exc}")
    raise RuntimeError(f"text request failed after {MAX_RETRIES} attempts: {url}, err:{opt_error}")


def open_url_SE(url: str, referer: str, timeout: float):
    request = Request(url, headers=request_headers(referer))
    return urlopen(request, timeout=timeout)


def video_id_from_link(link: str) -> str:
    parsed = urlparse(link)
    slug = unquote(parsed.path.rstrip("/").split("/")[-1])
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", slug).strip("_") or "video"


def normalize_source_url(page_url: str, source_url: str) -> str:
    cleaned = html.unescape(source_url).replace("\\/", "/")
    return urljoin(page_url, cleaned)


def source_extension(source_url: str) -> str:
    path = urlparse(source_url).path.lower()
    return ".m3u8" if path.endswith(".m3u8") else ".mp4"


def source_from_url(page_url: str, source_url: str) -> Optional[VideoSource]:
    normalized_url = normalize_source_url(page_url, source_url)
    path = urlparse(normalized_url).path.lower()
    if not path.endswith((".mp4", ".m3u8")):
        return None
    return VideoSource(url=normalized_url, extension=source_extension(normalized_url))


def parse_attr_sources(page_html: str, page_url: str) -> list[VideoSource]:
    parser = SourceParser()
    parser.feed(page_html)
    opt_sources = [source_from_url(page_url, url) for url in parser.urls]
    return [source for source in opt_sources if source is not None]


def parse_text_sources(page_html: str, page_url: str) -> list[VideoSource]:
    normalized_html = unpack_packed_scripts(page_html.replace("\\/", "/"))
    urls = [match.group(0) for match in VIDEO_SOURCE_RE.finditer(normalized_html)]
    opt_sources = [source_from_url(page_url, url) for url in urls]
    return [source for source in opt_sources if source is not None]


def packer_token(number: int, base: int) -> str:
    if number < base:
        return PACKER_ALPHABET[number]
    return packer_token(number // base, base) + PACKER_ALPHABET[number % base]


def unpack_packed_match(match: re.Match[str]) -> str:
    payload = match.group("p")
    base = int(match.group("a"))
    count = int(match.group("c"))
    words = match.group("k").split("|")
    decoded = payload
    replacements = [
        (packer_token(index, base), words[index])
        for index in range(count - 1, -1, -1)
        if index < len(words) and words[index]
    ]
    for token, word in replacements:
        decoded = re.sub(r"\b" + re.escape(token) + r"\b", word, decoded)
    return decoded


def unpack_packed_scripts(page_html: str) -> str:
    decoded_scripts = [unpack_packed_match(match) for match in PACKED_EVAL_RE.finditer(page_html)]
    return "\n".join([page_html, *decoded_scripts])


def unique_sources(sources: list[VideoSource]) -> list[VideoSource]:
    source_map = {source.url: source for source in reversed(sources)}
    return list(reversed(source_map.values()))


def extract_video_sources(page_html: str, page_url: str) -> list[VideoSource]:
    return unique_sources([*parse_attr_sources(page_html, page_url), *parse_text_sources(page_html, page_url)])


def source_rank(source: VideoSource) -> int:
    return 0 if source.extension == ".mp4" else 1


def select_video_source(sources: list[VideoSource]) -> Optional[VideoSource]:
    return min(sources, key=source_rank) if sources else None


def output_path_for_source(output_dir: Path, video_id: str, source: VideoSource) -> Path:
    extension = ".ts" if source.extension == ".m3u8" else source.extension
    return output_dir / f"{video_id}{extension}"


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "video"


def video_playlist_dir(output_dir: Path, video_id: str) -> Path:
    return output_dir / safe_filename(video_id)


def playlist_url_path(output_dir: Path, video_id: str, playlist: VideoPlaylist) -> Path:
    return video_playlist_dir(output_dir, video_id) / f"{safe_filename(playlist.resolution)}.m3u8.txt"


def playlist_segments_path(output_dir: Path, video_id: str, playlist: VideoPlaylist) -> Path:
    return video_playlist_dir(output_dir, video_id) / f"{safe_filename(playlist.resolution)}.csv"


def copy_response_SE(response, output_file: BinaryIO, chunk_size: int = 1024 * 1024) -> None:
    opt_chunk = read_response_chunk(response, chunk_size)
    while opt_chunk:
        output_file.write(opt_chunk)
        opt_chunk = read_response_chunk(response, chunk_size)


def read_response_chunk(response, chunk_size: int) -> bytes:
    try:
        return response.read(chunk_size)
    except http.client.IncompleteRead as exc:
        return exc.partial


def download_file_SE(source_url: str, output_path: Path, referer: str, timeout: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open_url_SE(source_url, referer, timeout) as response:
        with output_path.open("wb") as file:
            copy_response_SE(response, file)
    return output_path


def temp_output_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".part")


def retry_numbers(max_retries: int = MAX_RETRIES) -> range:
    return range(1, max_retries + 1)


def download_segment_SE(
    segment_url: str,
    output_file: BinaryIO,
    referer: str,
    timeout: float,
) -> None:
    opt_error = None
    for attempt in retry_numbers():
        try:
            with open_url_SE(segment_url, referer, timeout) as response:
                copy_response_SE(response, output_file)
            return
        except Exception as exc:
            opt_error = exc
            print(f"segment retry {attempt}/{MAX_RETRIES}: {segment_url}, err:{exc}")
    raise RuntimeError(f"segment failed after {MAX_RETRIES} attempts: {segment_url}, err:{opt_error}")


def is_m3u8_segment(line: str) -> bool:
    clean_line = line.strip()
    path = urlparse(clean_line).path.lower()
    return bool(clean_line and not clean_line.startswith("#") and path.endswith(M3U8_SEGMENT_EXTENSIONS))


def is_m3u8_variant(line: str) -> bool:
    clean_line = line.strip()
    path = urlparse(clean_line).path.lower()
    return bool(clean_line and not clean_line.startswith("#") and path.endswith(".m3u8"))


def parse_m3u8_segments(m3u8_text: str, m3u8_url: str) -> Strs:
    lines = [line.strip() for line in m3u8_text.splitlines()]
    return [urljoin(m3u8_url, line) for line in lines if is_m3u8_segment(line)]


def parse_m3u8_variants(m3u8_text: str, m3u8_url: str) -> Strs:
    lines = [line.strip() for line in m3u8_text.splitlines()]
    return [urljoin(m3u8_url, line) for line in lines if is_m3u8_variant(line)]


def resolution_from_stream_inf(line: str) -> str:
    opt_match = re.search(r"RESOLUTION=(\d+x\d+)", line, flags=re.IGNORECASE)
    return opt_match.group(1) if opt_match else "unknown"


def resolution_label(value: str) -> str:
    opt_match = re.fullmatch(r"\d+x(\d+)", value)
    return f"{opt_match.group(1)}p" if opt_match else value


def resolution_from_playlist_url(url: str) -> str:
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    opt_resolution = next(
        (part for part in reversed(path_parts) if re.fullmatch(r"\d+p", part)),
        None,
    )
    return opt_resolution or "source"


def parse_m3u8_playlists(m3u8_text: str, m3u8_url: str) -> list[VideoPlaylist]:
    lines = [line.strip() for line in m3u8_text.splitlines()]
    playlists: list[VideoPlaylist] = []
    opt_resolution: OptStr = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            opt_resolution = resolution_label(resolution_from_stream_inf(line))
        elif is_m3u8_variant(line):
            playlists.append(
                VideoPlaylist(
                    resolution=opt_resolution or "unknown",
                    url=urljoin(m3u8_url, line),
                )
            )
            opt_resolution = None
    return playlists


def leaf_m3u8_text_SE(source_url: str, referer: str, timeout: float) -> tuple[str, str]:
    m3u8_text = fetch_text_with_retry_SE(source_url, referer, timeout)
    variant_urls = parse_m3u8_variants(m3u8_text, source_url)
    if not variant_urls:
        return m3u8_text, source_url
    variant_url = variant_urls[0]
    return fetch_text_with_retry_SE(variant_url, referer, timeout), variant_url


def source_playlists_SE(source: VideoSource, referer: str, timeout: float) -> list[VideoPlaylist]:
    if source.extension != ".m3u8":
        return [VideoPlaylist(resolution="file", url=source.url)]
    m3u8_text = fetch_text_with_retry_SE(source.url, referer, timeout)
    playlists = parse_m3u8_playlists(m3u8_text, source.url)
    return playlists if playlists else [VideoPlaylist(resolution=resolution_from_playlist_url(source.url), url=source.url)]


def unique_playlists(playlists: list[VideoPlaylist]) -> list[VideoPlaylist]:
    playlist_map = {playlist.url: playlist for playlist in reversed(playlists)}
    return list(reversed(playlist_map.values()))


def playlist_segment_rows_SE(playlist: VideoPlaylist, referer: str, timeout: float) -> list[dict[str, str]]:
    m3u8_text = fetch_text_with_retry_SE(playlist.url, referer, timeout)
    segments = parse_m3u8_segments(m3u8_text, playlist.url)
    return [
        {
            "index": str(index),
            "resolution": playlist.resolution,
            "referer": referer,
            "segment_url": segment_url,
        }
        for index, segment_url in enumerate(segments)
    ]


def write_playlist_url_SE(output_dir: Path, video_id: str, playlist: VideoPlaylist) -> Path:
    output_path = playlist_url_path(output_dir, video_id, playlist)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(playlist.url + "\n", encoding="utf-8")
    return output_path


def write_playlist_segments_csv_SE(
    output_dir: Path,
    video_id: str,
    playlist: VideoPlaylist,
    rows: list[dict[str, str]],
) -> Path:
    output_path = playlist_segments_path(output_dir, video_id, playlist)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=("index", "resolution", "referer", "segment_url"))
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def export_video_playlists_SE(link: str, output_dir: Path, timeout: float) -> list[Path]:
    page_html = fetch_text_with_retry_SE(link, DEFAULT_REFERER, timeout)
    video_id = video_id_from_link(link)
    sources = extract_video_sources(page_html, link)
    playlists = unique_playlists([
        playlist
        for source in sources
        for playlist in source_playlists_SE(source, link, timeout)
    ])
    return [
        output_path
        for playlist in playlists
        for output_path in [
            write_playlist_url_SE(output_dir, video_id, playlist),
            write_playlist_segments_csv_SE(
                output_dir,
                video_id,
                playlist,
                playlist_segment_rows_SE(playlist, link, timeout),
            ),
        ]
    ]


def download_m3u8_SE(source_url: str, output_path: Path, referer: str, timeout: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_output_path(output_path)
    m3u8_text, m3u8_url = leaf_m3u8_text_SE(source_url, referer, timeout)
    segment_urls = parse_m3u8_segments(m3u8_text, m3u8_url)
    with temp_path.open("wb") as file:
        for segment_url in segment_urls:
            download_segment_SE(segment_url, file, referer, timeout)
    temp_path.replace(output_path)
    return output_path


def download_source_SE(source: VideoSource, output_path: Path, referer: str, timeout: float) -> Path:
    return (
        download_m3u8_SE(source.url, output_path, referer, timeout)
        if source.extension == ".m3u8"
        else download_file_SE(source.url, output_path, referer, timeout)
    )


def download_video_by_id_SE(link: str, output_dir: Path, timeout: float) -> Path:
    page_html = fetch_text_with_retry_SE(link, DEFAULT_REFERER, timeout)
    opt_source = select_video_source(extract_video_sources(page_html, link))
    if opt_source is None:
        raise RuntimeError(f"no video source found: {link}")
    output_path = output_path_for_source(output_dir, video_id_from_link(link), opt_source)
    return download_source_SE(opt_source, output_path, link, timeout)


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    args = parse_args(opt_argv)
    try:
        if args.export_playlists:
            output_dir = args.output_dir or DEFAULT_PLAYLIST_OUTPUT_DIR
            output_paths = export_video_playlists_SE(args.link, output_dir, args.timeout)
            print(f"saved {len(output_paths)} playlist files to {output_dir}")
            return 0
        output_dir = args.output_dir or DEFAULT_OUTPUT_DIR
        output_path = download_video_by_id_SE(args.link, output_dir, args.timeout)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"saved video to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_SE())
