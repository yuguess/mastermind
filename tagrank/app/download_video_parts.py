"""Download one video segment from an exported playlist CSV."""

from __future__ import annotations

import argparse
import csv
import http.client
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from tagrank.app.download_tag import PROJECT_ROOT
from tagrank.app.export_video_segment_list import (
    DEFAULT_PLAYLIST_OUTPUT_DIR,
    safe_filename,
)
from tagrank.base_adt import Strs


DEFAULT_PLAYLIST_DIR = DEFAULT_PLAYLIST_OUTPUT_DIR
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "video"
DEFAULT_TIMEOUT = 20.0
DEFAULT_CONCURRENCY = 1
DEFAULT_RETRY_DELAY = 1.0
MAX_RETRIES = 5


@dataclass(frozen=True, slots=True)
class VideoPart:
    index: int
    resolution: str
    referer: str
    segment_url: str


@dataclass(frozen=True, slots=True)
class DownloadedPart:
    index: int
    path: Path


def parse_args(opt_argv: Optional[Strs] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--resolution", required=True)
    parser.add_argument("--index-start", type=int, required=True)
    parser.add_argument("--index-end", type=int, required=True)
    parser.add_argument("--playlist-dir", type=Path, default=DEFAULT_PLAYLIST_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY)
    return parser.parse_args(opt_argv)


def playlist_csv_path(playlist_dir: Path, video_id: str, resolution: str) -> Path:
    return playlist_dir / safe_filename(video_id) / f"{safe_filename(resolution)}.csv"


def output_dir_for_part(output_dir: Path, video_id: str, resolution: str) -> Path:
    return output_dir / safe_filename(video_id) / safe_filename(resolution)


def segment_filename(segment_url: str, index: int) -> str:
    parsed = urlparse(segment_url)
    filename = "" if parsed.path.endswith("/") else unquote(Path(parsed.path).name)
    return safe_filename(filename or f"part_{index}")


def output_path_for_part(output_dir: Path, video_id: str, part: VideoPart) -> Path:
    return output_dir_for_part(output_dir, video_id, part.resolution) / segment_filename(part.segment_url, part.index)


def int_value(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def video_part_from_row(row: dict[str, str]) -> Optional[VideoPart]:
    opt_index = int_value(row.get("index", ""))
    if opt_index is None:
        return None
    return VideoPart(
        index=opt_index,
        resolution=row.get("resolution", ""),
        referer=row.get("referer", ""),
        segment_url=row.get("segment_url", ""),
    )


def read_video_parts_SE(csv_path: Path) -> list[VideoPart]:
    with csv_path.open(newline="", encoding="utf-8") as file:
        opt_parts = [video_part_from_row(row) for row in csv.DictReader(file)]
    return [part for part in opt_parts if part is not None]


def index_range(index_start: int, index_end: int) -> range:
    if index_start > index_end:
        raise ValueError(f"index-start must be <= index-end: {index_start} > {index_end}")
    return range(index_start, index_end + 1)


def select_video_parts(parts: list[VideoPart], index_start: int, index_end: int) -> list[VideoPart]:
    indexes = set(index_range(index_start, index_end))
    part_map = {part.index: part for part in parts if part.index in indexes}
    missing_indexes = [index for index in index_range(index_start, index_end) if index not in part_map]
    if missing_indexes:
        raise RuntimeError(f"parts not found: indexes={missing_indexes}")
    return [part_map[index] for index in index_range(index_start, index_end)]


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


def open_url_SE(url: str, referer: str, timeout: float):
    request = Request(url, headers=request_headers(referer))
    return urlopen(request, timeout=timeout)


def read_response_chunk(response, chunk_size: int) -> bytes:
    try:
        return response.read(chunk_size)
    except http.client.IncompleteRead as exc:
        return exc.partial


def copy_response_SE(response, output_file: BinaryIO, chunk_size: int = 1024 * 1024) -> None:
    opt_chunk = read_response_chunk(response, chunk_size)
    while opt_chunk:
        output_file.write(opt_chunk)
        opt_chunk = read_response_chunk(response, chunk_size)


def temp_output_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".part")


def retry_numbers(max_retries: int = MAX_RETRIES) -> range:
    return range(1, max_retries + 1)


def retry_delay_seconds(attempt: int, retry_delay: float) -> float:
    return attempt * retry_delay


def sleep_before_retry_SE(attempt: int, retry_delay: float) -> None:
    time.sleep(retry_delay_seconds(attempt, retry_delay))


def download_segment_SE(
    segment_url: str,
    output_file: BinaryIO,
    referer: str,
    timeout: float,
    retry_delay: float,
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
            if attempt < MAX_RETRIES:
                sleep_before_retry_SE(attempt, retry_delay)
    raise RuntimeError(f"segment failed after {MAX_RETRIES} attempts: {segment_url}, err:{opt_error}")


def download_part_file_SE(part: VideoPart, output_path: Path, timeout: float, retry_delay: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_output_path(output_path)
    with temp_path.open("wb") as file:
        download_segment_SE(part.segment_url, file, part.referer, timeout, retry_delay)
    temp_path.replace(output_path)
    return output_path


def try_download_part_file_SE(part: VideoPart, output_path: Path, timeout: float, retry_delay: float) -> Optional[DownloadedPart]:
    try:
        return DownloadedPart(
            index=part.index,
            path=download_part_file_SE(part, output_path, timeout, retry_delay),
        )
    except Exception as exc:
        print(f"skip part index={part.index}: {part.segment_url}, err:{exc}")
        return None


def merged_output_path(output_dir: Path, video_id: str, resolution: str, index_start: int, index_end: int) -> Path:
    filename = "_".join(
        [
            safe_filename(video_id),
            safe_filename(resolution),
            str(index_start),
            str(index_end),
        ]
    )
    return output_dir_for_part(output_dir, video_id, resolution) / f"{filename}.ts"


def merge_part_files_SE(part_paths: list[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_output_path(output_path)
    with temp_path.open("wb") as output_file:
        for part_path in part_paths:
            with part_path.open("rb") as input_file:
                copy_response_SE(input_file, output_file)
    temp_path.replace(output_path)
    return output_path


def delete_files_SE(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def normalized_concurrency(concurrency: int) -> int:
    return max(1, concurrency)


def download_part_task_SE(
    output_dir: Path,
    video_id: str,
    timeout: float,
    retry_delay: float,
    part: VideoPart,
) -> Optional[DownloadedPart]:
    return try_download_part_file_SE(
        part,
        output_path_for_part(output_dir, video_id, part),
        timeout,
        retry_delay,
    )


def download_parts_sequential_SE(
    parts: list[VideoPart],
    output_dir: Path,
    video_id: str,
    timeout: float,
    retry_delay: float,
) -> list[DownloadedPart]:
    opt_downloaded_parts = [
        download_part_task_SE(output_dir, video_id, timeout, retry_delay, part)
        for part in parts
    ]
    return [part for part in opt_downloaded_parts if part is not None]


def download_parts_concurrent_SE(
    parts: list[VideoPart],
    output_dir: Path,
    video_id: str,
    timeout: float,
    retry_delay: float,
    concurrency: int,
) -> list[DownloadedPart]:
    with ThreadPoolExecutor(max_workers=normalized_concurrency(concurrency)) as executor:
        opt_downloaded_parts = list(
            executor.map(
                lambda part: download_part_task_SE(output_dir, video_id, timeout, retry_delay, part),
                parts,
            )
        )
    return [part for part in opt_downloaded_parts if part is not None]


def download_parts_with_concurrency_SE(
    parts: list[VideoPart],
    output_dir: Path,
    video_id: str,
    timeout: float,
    retry_delay: float,
    concurrency: int,
) -> list[DownloadedPart]:
    return (
        download_parts_sequential_SE(parts, output_dir, video_id, timeout, retry_delay)
        if normalized_concurrency(concurrency) == 1
        else download_parts_concurrent_SE(parts, output_dir, video_id, timeout, retry_delay, concurrency)
    )


def download_video_parts_SE(
    video_id: str,
    resolution: str,
    index_start: int,
    index_end: int,
    playlist_dir: Path,
    output_dir: Path,
    timeout: float,
    concurrency: int,
    retry_delay: float,
) -> Path:
    csv_path = playlist_csv_path(playlist_dir, video_id, resolution)
    parts = select_video_parts(read_video_parts_SE(csv_path), index_start, index_end)
    downloaded_parts = download_parts_with_concurrency_SE(
        parts,
        output_dir,
        video_id,
        timeout,
        retry_delay,
        concurrency,
    )
    part_paths = [part.path for part in sorted(downloaded_parts, key=lambda part: part.index)]
    if not part_paths:
        raise RuntimeError(f"no parts downloaded: video_id={video_id} resolution={resolution}")
    output_path = merged_output_path(output_dir, video_id, resolution, index_start, index_end)
    merged_path = merge_part_files_SE(part_paths, output_path)
    delete_files_SE(part_paths)
    return merged_path


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    args = parse_args(opt_argv)
    try:
        output_path = download_video_parts_SE(
            args.video_id,
            args.resolution,
            args.index_start,
            args.index_end,
            args.playlist_dir,
            args.output_dir,
            args.timeout,
            args.concurrency,
            args.retry_delay,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"saved video parts to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_SE())
