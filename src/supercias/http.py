"""HTTP downloads for the public Supercias files."""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DIRECTORIO_URL = (
    "https://mercadodevalores.supercias.gob.ec/reportes/excel/directorio_companias.xlsx"
)
RANKING_URL = "https://appscvsmovil.supercias.gob.ec/ranking/recursos/bi_ranking.csv"


def session() -> requests.Session:
    client = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    client.mount("https://", adapter)
    client.mount("http://", adapter)
    client.headers.update(
        {
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
            "Accept-Language": "es-EC,es;q=0.9,en;q=0.8",
        }
    )
    return client


def download_file(url: str, dest: Path, timeout: tuple[float, float]) -> None:
    """Stream a GET response to ``dest``. ``timeout`` is ``(connect, read)`` seconds."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s", url)
    client = session()
    try:
        with client.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            next_report = 50 * 1024 * 1024
            with dest.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if written >= next_report:
                        if total:
                            logger.info(
                                "  %.0f / %.0f MB",
                                written / 1_000_000,
                                total / 1_000_000,
                            )
                        else:
                            logger.info("  %.0f MB", written / 1_000_000)
                        next_report += 50 * 1024 * 1024
            logger.info("Saved %s (%.1f MB)", dest.name, written / 1_000_000)
    finally:
        client.close()


def download_text(url: str, timeout: tuple[float, float]) -> str:
    """Download a text body and decode it. The caller parses this string, not a local CSV path."""
    logger.info("Downloading %s", url)
    client = session()
    try:
        with client.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            written = 0
            next_report = 50 * 1024 * 1024
            total = int(response.headers.get("Content-Length") or 0)
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                chunks.append(chunk)
                written += len(chunk)
                if written >= next_report:
                    if total:
                        logger.info(
                            "  %.0f / %.0f MB",
                            written / 1_000_000,
                            total / 1_000_000,
                        )
                    else:
                        logger.info("  %.0f MB", written / 1_000_000)
                    next_report += 50 * 1024 * 1024
    finally:
        client.close()
    payload = b"".join(chunks)
    del chunks
    logger.info("Downloaded %.1f MB", len(payload) / 1_000_000)
    text = decode_body(payload)
    del payload
    return text


def decode_body(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1")
