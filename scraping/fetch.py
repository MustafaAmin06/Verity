from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from .models import FetchResult, PipelineConfig


def is_pdf_url(url: str, content_type: str = "") -> bool:
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    if "application/pdf" in content_type.lower():
        return True
    return False


def is_private_host(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return True
    try:
        resolved = socket.getaddrinfo(parsed.hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return True
        return False
    except (socket.gaierror, ValueError, OSError):
        return True


def classify_403_response(response: httpx.Response) -> str:
    server = response.headers.get("server", "").lower()
    header_names = {name.lower() for name in response.headers.keys()}
    body_sample = (response.text or "")[:600].lower()

    hard_block_markers = (
        "attention required",
        "access denied",
        "forbidden",
        "please enable cookies",
        "checking your browser",
        "security check",
        "request blocked",
        "captcha",
    )

    if (
        "cf-ray" in header_names
        or "cloudflare" in server
        or "akamai" in server
        or "imperva" in server
        or "sucuri" in server
        or "x-sucuri-id" in header_names
        or "x-iinfo" in header_names
        or any(marker in body_sample for marker in hard_block_markers)
    ):
        return "blocked_403_waf"
    return "blocked_403"


async def fetch_url(url: str, config: PipelineConfig, logger) -> FetchResult:
    if is_private_host(url):
        return FetchResult(kind="private_ip_blocked", url=url)

    try:
        async with httpx.AsyncClient(
            timeout=config.request_timeout_seconds,
            headers=config.browser_headers,
            follow_redirects=True,
            max_redirects=config.max_redirects,
            http2=True,
        ) as client:
            try:
                response = await client.get(url)
            except httpx.TimeoutException:
                return FetchResult(kind="timeout", url=url)
            except Exception as exc:
                logger.warning("  GET failed   %-40s  %s: %s", url, type(exc).__name__, exc)
                return FetchResult(kind="scrape_failed", url=url, error=str(exc))
    except Exception as exc:
        logger.warning("  CLIENT fail  %-40s  %s: %s", url, type(exc).__name__, exc)
        return FetchResult(kind="scrape_failed", url=url, error=str(exc))

    http_status = response.status_code
    content_type = response.headers.get("content-type", "")
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.max_response_bytes:
                return FetchResult(
                    kind="response_too_large",
                    url=url,
                    http_status=http_status,
                    content_type=content_type,
                    final_url=str(response.url),
                )
        except ValueError:
            pass

    if http_status == 403:
        return FetchResult(
            kind=classify_403_response(response),
            url=url,
            http_status=http_status,
            content_type=content_type,
            final_url=str(response.url),
        )
    if http_status >= 400:
        return FetchResult(
            kind="url_dead",
            url=url,
            http_status=http_status,
            content_type=content_type,
            final_url=str(response.url),
        )
    if is_pdf_url(url, content_type):
        return FetchResult(
            kind="pdf_skipped",
            url=url,
            http_status=http_status,
            content_type=content_type,
            final_url=str(response.url),
        )

    return FetchResult(
        kind="ok",
        url=url,
        http_status=http_status,
        html=response.text,
        content_type=content_type,
        final_url=str(response.url),
    )
