"""Diagnose whether requests can log in to missav.ws."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from curl_cffi import requests

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from tagrank.base_adt import OptStr, Strs


DEFAULT_BASE_URL = "https://missav.ws"
DEFAULT_LOGIN_PAGE = "https://missav.ws/en/genres"
DEFAULT_LOGIN_API = "https://missav.ws/en/api/login"
DEFAULT_SAVED_URL = "https://missav.ws/saved"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_IMPERSONATE = "chrome"
DEFAULT_OUTPUT = Path("data/requests_login_diagnosis.json")
DEFAULT_COOKIE_FILE = Path("data/cookies.json")
DEFAULT_CURL_FILE = Path("data/chrome_saved_curl(bash)")
USERNAME_ENV = "MS_USER"
PASSWORD_ENV = "MS_PASS"
SKIPPED_CURL_HEADERS = {"cookie", "content-length", "host"}


@dataclass(frozen=True, slots=True)
class LoginConfig:
    opt_username: OptStr
    opt_password: OptStr
    login_page: str
    login_api: str
    saved_url: str
    timeout: float
    retries: int
    retry_delay: float
    impersonate: str
    output: Path
    cookie_file: Path
    curl_file: Path


@dataclass(frozen=True, slots=True)
class RequestResult:
    label: str
    url: str
    method: str
    request_headers: dict[str, str]
    opt_request_body: Optional[str]
    opt_status_code: Optional[int]
    response_url: OptStr
    response_headers: dict[str, str]
    response_text: str
    is_cloudflare: bool
    opt_title: OptStr
    opt_error_type: OptStr
    opt_error_repr: OptStr


def parse_args(opt_argv: Optional[Strs] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=os.environ.get(USERNAME_ENV))
    parser.add_argument("--password", default=os.environ.get(PASSWORD_ENV))
    parser.add_argument("--login-page", default=DEFAULT_LOGIN_PAGE)
    parser.add_argument("--login-api", default=DEFAULT_LOGIN_API)
    parser.add_argument("--saved-url", default=DEFAULT_SAVED_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY)
    parser.add_argument("--impersonate", default=DEFAULT_IMPERSONATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument("--curl-file", type=Path, default=DEFAULT_CURL_FILE)
    return parser.parse_args(opt_argv)


def config_from_args(args: argparse.Namespace) -> LoginConfig:
    return LoginConfig(
        opt_username=args.username,
        opt_password=args.password,
        login_page=args.login_page,
        login_api=args.login_api,
        saved_url=args.saved_url,
        timeout=args.timeout,
        retries=max(args.retries, 1),
        retry_delay=max(args.retry_delay, 0.0),
        impersonate=args.impersonate,
        output=args.output,
        cookie_file=args.cookie_file,
        curl_file=args.curl_file,
    )


def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }


def chrome_document_headers() -> dict[str, str]:
    return {
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "accept-language": "en,en-US;q=0.9,zh-CN;q=0.8,zh-TW;q=0.7,zh;q=0.6,ja;q=0.5",
        "cache-control": "max-age=0",
        "priority": "u=0, i",
        "referer": DEFAULT_SAVED_URL,
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"148.0.7778.179"',
        "sec-ch-ua-full-version-list": (
            '"Chromium";v="148.0.7778.179", "Google Chrome";v="148.0.7778.179", '
            '"Not/A)Brand";v="99.0.0.0"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
    }


def json_headers(referer: str, opt_xsrf: OptStr) -> dict[str, str]:
    xsrf_header = {"X-XSRF-TOKEN": opt_xsrf} if opt_xsrf else {}
    return {
        "User-Agent": browser_headers()["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": DEFAULT_BASE_URL,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        **xsrf_header,
    }


def title_from_html(text: str) -> OptStr:
    lower_text = text.lower()
    start = lower_text.find("<title")
    end = lower_text.find("</title>")
    opt_title = text[start : end + len("</title>")] if start >= 0 and end >= 0 else None
    return " ".join(opt_title.split()) if opt_title else None


def is_cloudflare_html(text: str) -> bool:
    lower_text = text.lower()
    return "just a moment" in lower_text and "challenge-platform" in lower_text


def request_result_from_response(
    label: str,
    method: str,
    url: str,
    request_headers: dict[str, str],
    opt_request_body: Optional[str],
    response: requests.Response,
) -> RequestResult:
    return RequestResult(
        label=label,
        url=url,
        method=method,
        request_headers=request_headers,
        opt_request_body=opt_request_body,
        opt_status_code=response.status_code,
        response_url=response.url,
        response_headers=dict(response.headers),
        response_text=response.text,
        is_cloudflare=is_cloudflare_html(response.text),
        opt_title=title_from_html(response.text),
        opt_error_type=None,
        opt_error_repr=None,
    )


def request_result_from_exception(
    label: str,
    method: str,
    url: str,
    request_headers: dict[str, str],
    opt_request_body: Optional[str],
    exc: Exception,
) -> RequestResult:
    return RequestResult(
        label=label,
        url=url,
        method=method,
        request_headers=request_headers,
        opt_request_body=opt_request_body,
        opt_status_code=None,
        response_url=None,
        response_headers={},
        response_text="",
        is_cloudflare=False,
        opt_title=None,
        opt_error_type=type(exc).__name__,
        opt_error_repr=repr(exc),
    )


def cookie_names(session: requests.Session) -> Strs:
    return sorted([str(name) for name in session.cookies.get_dict().keys()])


def cookie_dicts(session: requests.Session) -> list[dict[str, object]]:
    return [
        {
            "name": str(name),
            "value": str(value),
        }
        for name, value in session.cookies.get_dict().items()
    ]


def cookie_items(cookie_data: object) -> list[dict[str, object]]:
    if isinstance(cookie_data, list):
        items = cookie_data
    elif isinstance(cookie_data, dict):
        items = cookie_data.get("cookies", [])
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def cookie_name_value(cookie: dict[str, object]) -> tuple[OptStr, OptStr]:
    opt_name = cookie.get("name")
    opt_value = cookie.get("value")
    return (
        opt_name if isinstance(opt_name, str) else None,
        opt_value if isinstance(opt_value, str) else None,
    )


def cookie_domain(cookie: dict[str, object]) -> OptStr:
    opt_domain = cookie.get("domain")
    return opt_domain if isinstance(opt_domain, str) and opt_domain else None


def cookie_path(cookie: dict[str, object]) -> str:
    opt_path = cookie.get("path")
    return opt_path if isinstance(opt_path, str) and opt_path else "/"


def load_cookie_file_SE(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        cookies = []
    else:
        cookies = cookie_items(json.loads(path.read_text(encoding="utf-8")))
    return cookies


def split_curl_commands(text: str) -> Strs:
    parts = text.strip().split("\ncurl ")
    return [parts[0], *[f"curl {part}" for part in parts[1:]]] if parts and parts[0] else []


def curl_args(command: str) -> Strs:
    try:
        args = shlex.split(command)
    except ValueError:
        args = []
    return args


def curl_url(args: Strs) -> OptStr:
    return next((arg for arg in args if arg.startswith(("http://", "https://"))), None)


def is_target_curl(args: Strs, target_url: str) -> bool:
    return curl_url(args) == target_url


def header_arg_at(args: Strs, index: int) -> OptStr:
    arg = args[index]
    opt_next = args[index + 1] if index + 1 < len(args) else None
    if arg in {"-H", "--header"} and opt_next:
        opt_header = opt_next
    elif arg.startswith("-H") and len(arg) > 2:
        opt_header = arg[2:].strip()
    else:
        opt_header = None
    return opt_header


def header_lines_from_args(args: Strs) -> Strs:
    return [header for header in map(lambda index: header_arg_at(args, index), range(len(args))) if header]


def header_pair(line: str) -> tuple[OptStr, OptStr]:
    parts = line.split(":", 1)
    return (
        parts[0].strip() if len(parts) == 2 else None,
        parts[1].strip() if len(parts) == 2 else None,
    )


def request_headers_from_curl_args(args: Strs) -> dict[str, str]:
    pairs = [header_pair(line) for line in header_lines_from_args(args)]
    return {
        name: value
        for name, value in pairs
        if name and value is not None and name.lower() not in SKIPPED_CURL_HEADERS
    }


def opt_curl_headers_SE(path: Path, target_url: str) -> Optional[dict[str, str]]:
    if path.exists():
        args_list = [curl_args(command) for command in split_curl_commands(path.read_text(encoding="utf-8"))]
        opt_args = next((args for args in args_list if is_target_curl(args, target_url)), None)
        opt_headers = request_headers_from_curl_args(opt_args) if opt_args else None
    else:
        opt_headers = None
    return opt_headers


def set_browser_cookies_SE(session: requests.Session, cookies: list[dict[str, object]]) -> None:
    valid_cookies = [
        (cookie, *cookie_name_value(cookie))
        for cookie in cookies
        if all(cookie_name_value(cookie))
    ]
    for cookie, name, value in valid_cookies:
        session.cookies.set(
            name,
            value,
            domain=cookie_domain(cookie),
            path=cookie_path(cookie),
        )


def xsrf_token(session: requests.Session) -> OptStr:
    opt_value = session.cookies.get("XSRF-TOKEN")
    return unquote(opt_value) if opt_value else None


def has_credentials(config: LoginConfig) -> bool:
    return bool(config.opt_username and config.opt_password)


def login_payload(config: LoginConfig) -> dict[str, object]:
    return {"email": config.opt_username, "password": config.opt_password, "remember": True}


def request_body(data: Optional[dict[str, object]]) -> Optional[str]:
    return json.dumps(data, ensure_ascii=False) if data is not None else None


def retry_sleep_seconds(attempt: int, delay: float) -> float:
    return attempt * delay


def should_retry_result(result: RequestResult, attempt: int, retries: int) -> bool:
    status = result.opt_status_code
    retriable_status = status is None or status >= 500 or status in {408, 429}
    return attempt < retries and retriable_status


def perform_request_SE(
    session: requests.Session,
    method: str,
    url: str,
    headers: dict[str, str],
    opt_body: Optional[str],
    timeout: float,
    impersonate: str,
):
    return session.request(
        method,
        url,
        data=opt_body,
        headers=headers,
        timeout=timeout,
        impersonate=impersonate,
    )


def send_request_SE(
    session: requests.Session,
    label: str,
    method: str,
    url: str,
    headers: dict[str, str],
    opt_data: Optional[dict[str, object]],
    timeout: float,
    retries: int,
    retry_delay: float,
    impersonate: str,
) -> RequestResult:
    opt_body = request_body(opt_data)
    result = request_result_from_exception(
        label,
        method,
        url,
        headers,
        opt_body,
        RuntimeError("request was not attempted"),
    )
    for attempt in range(1, retries + 1):
        try:
            response = perform_request_SE(session, method, url, headers, opt_body, timeout, impersonate)
            result = request_result_from_response(label, method, url, headers, opt_body, response)
        except Exception as exc:
            result = request_result_from_exception(label, method, url, headers, opt_body, exc)
        if should_retry_result(result, attempt, retries):
            time.sleep(retry_sleep_seconds(attempt, retry_delay))
    return result


def get_page_result_SE(
    session: requests.Session,
    label: str,
    url: str,
    timeout: float,
    retries: int,
    retry_delay: float,
    impersonate: str,
    opt_headers: Optional[dict[str, str]] = None,
) -> RequestResult:
    headers = opt_headers if opt_headers is not None else browser_headers()
    return send_request_SE(
        session,
        label,
        "GET",
        url,
        headers,
        None,
        timeout,
        retries,
        retry_delay,
        impersonate,
    )


def post_login_result_SE(session: requests.Session, config: LoginConfig) -> RequestResult:
    return send_request_SE(
        session,
        "login_api",
        "POST",
        config.login_api,
        json_headers(config.login_page, xsrf_token(session)),
        login_payload(config),
        config.timeout,
        config.retries,
        config.retry_delay,
        config.impersonate,
    )


def safe_json_body(text: str) -> object:
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    return body if isinstance(body, dict) else None


def request_succeeded(result: RequestResult) -> bool:
    return result.opt_status_code is not None and result.opt_error_type is None


def login_succeeded(result: RequestResult) -> bool:
    return result.opt_status_code == 200 and result.opt_error_type is None


def likely_reason(login_result: RequestResult, opt_saved_result: Optional[RequestResult]) -> str:
    status = login_result.opt_status_code
    if status == 200:
        reason = "login api returned 200; inspect saved_status to confirm authenticated access"
    elif status == 403:
        reason = "login api returned 403; likely blocked by Cloudflare/WAF or missing browser challenge state"
    elif status == 419:
        reason = "login api returned 419; likely CSRF token/session mismatch"
    elif status == 422:
        reason = "login api returned 422; likely validation error or invalid credentials"
    elif status == 401:
        reason = "login api returned 401; authentication failed"
    elif status is None:
        reason = f"login api did not return a response; {login_result.opt_error_type}: {login_result.opt_error_repr}"
    else:
        reason = f"login api returned unexpected status {status}"
    saved_note = " saved page also Cloudflare challenge" if opt_saved_result and opt_saved_result.is_cloudflare else ""
    return f"{reason}.{saved_note}".strip()


def diagnosis_dict(
    results: list[RequestResult],
    session: requests.Session,
    login_result: RequestResult,
    opt_saved_result: Optional[RequestResult],
) -> dict[str, object]:
    return {
        "requests": [asdict(result) for result in results],
        "cookie_names": cookie_names(session),
        "cookies": cookie_dicts(session),
        "has_xsrf_token": xsrf_token(session) is not None,
        "xsrf_token": xsrf_token(session),
        "login_json_body": safe_json_body(login_result.response_text),
        "reason": likely_reason(login_result, opt_saved_result),
    }


def write_json_SE(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def print_diagnosis_SE(data: dict[str, object], output: Path) -> None:
    requests_data = data.get("requests", [])
    statuses = [
        f"{item.get('label')}={item.get('opt_status_code') or item.get('opt_error_type')}"
        for item in requests_data
        if isinstance(item, dict)
    ]
    print("diagnosis:", ", ".join(statuses))
    print(f"reason: {data.get('reason')}")
    print(f"wrote diagnosis to {output}")


def maybe_saved_result_SE(
    session: requests.Session,
    config: LoginConfig,
    login_page_result: RequestResult,
    saved_headers: dict[str, str],
) -> Optional[RequestResult]:
    return get_page_result_SE(
        session,
        "saved",
        config.saved_url,
        config.timeout,
        config.retries,
        config.retry_delay,
        config.impersonate,
        saved_headers,
    ) if request_succeeded(login_page_result) else None


def run_diagnosis_SE(config: LoginConfig) -> dict[str, object]:
    session = requests.Session(impersonate=config.impersonate)
    browser_cookies = load_cookie_file_SE(config.cookie_file)
    set_browser_cookies_SE(session, browser_cookies)
    opt_curl_headers = opt_curl_headers_SE(config.curl_file, config.saved_url)
    saved_headers = opt_curl_headers if opt_curl_headers is not None else chrome_document_headers()
    login_page_result = get_page_result_SE(
        session,
        "login_page",
        config.login_page,
        config.timeout,
        config.retries,
        config.retry_delay,
        config.impersonate,
    )
    login_result = post_login_result_SE(session, config) if request_succeeded(login_page_result) and has_credentials(config) else request_result_from_exception(
        "login_api",
        "POST",
        config.login_api,
        json_headers(config.login_page, xsrf_token(session)),
        request_body(login_payload(config)) if has_credentials(config) else None,
        RuntimeError("skipped because login_page request failed or credentials are missing"),
    )
    opt_saved_result = maybe_saved_result_SE(session, config, login_page_result, saved_headers)
    results = [
        login_page_result,
        login_result,
        *([opt_saved_result] if opt_saved_result is not None else []),
    ]
    diagnosis = diagnosis_dict(results, session, login_result, opt_saved_result)
    return {
        **diagnosis,
        "cookie_file": str(config.cookie_file),
        "curl_file": str(config.curl_file),
        "curl_headers_loaded": opt_curl_headers is not None,
        "http_client": "curl_cffi",
        "impersonate": config.impersonate,
        "retries": config.retries,
        "retry_delay": config.retry_delay,
        "saved_request_header_names": sorted(saved_headers.keys()),
        "browser_cookie_count": len(browser_cookies),
        "loaded_browser_cookie_names": sorted(
            [str(cookie.get("name")) for cookie in browser_cookies if cookie.get("name")]
        ),
    }


def main_SE(opt_argv: Optional[Strs] = None) -> int:
    try:
        config = config_from_args(parse_args(opt_argv))
        diagnosis = run_diagnosis_SE(config)
        write_json_SE(config.output, diagnosis)
        print_diagnosis_SE(diagnosis, config.output)
        status = 0
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main_SE())
