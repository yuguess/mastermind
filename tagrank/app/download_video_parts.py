"""Download one video segment from an exported playlist CSV."""

from __future__ import annotations

import argparse
import csv
import http.client
import sys
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
MAX_RETRIES = 3


@dataclass(frozen=True, slots=True)
class VideoPart:
    index: int
    resolution: str
    referer: str
    segment_url: str


def parse_args(opt_argv: Optional[Strs] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    # Locate one exported playlist row by video id, resolution, and part index.
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--resolution", required=True)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--playlist-dir", type=Path, default=DEFAULT_PLAYLIST_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
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


def select_video_part(parts: list[VideoPart], index: int) -> Optional[VideoPart]:
    matches = [part for part in parts if part.index == index]
    return matches[0] if matches else None


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


def download_part_file_SE(part: VideoPart, output_path: Path, timeout: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_output_path(output_path)
    with temp_path.open("wb") as file:
        download_segment_SE(part.segment_url, file, part.referer, timeout)
    temp_path.replace(output_path)
    return output_path


def download_video_part_SE(
    video_id: str,
    resolution: str,
    index: int,
    playlist_dir: Path,
    output_dir: Path,
    timeout: float,
) -> Path:
    csv_path = playlist_csv_path(playlist_dir, video_id, resolution)
    opt_part = select_video_part(read_video_parts_SE(csv_path), index)
    if opt_part is None:
        raise RuntimeError(f"part not found: video_id={video_id} resolution={resolution} index={index}")
    output_path = output_path_for_part(output_dir, video_id, opt_part)
    return download_part_file_SE(opt_part, output_path, timeout)


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    args = parse_args(opt_argv)
    try:
        output_path = download_video_part_SE(
            args.video_id,
            args.resolution,
            args.index,
            args.playlist_dir,
            args.output_dir,
            args.timeout,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"saved video part to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_SE())
