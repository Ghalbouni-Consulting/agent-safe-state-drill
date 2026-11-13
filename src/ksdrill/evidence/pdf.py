# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""HTML is the evidence card. PDF is a one-page print of that HTML, or a warning."""

from __future__ import annotations

import os
from pathlib import Path


class PdfUnavailable(RuntimeError):
    pass


def write_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PdfUnavailable("playwright is not installed") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=_chrome() or None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri())
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                prefer_css_page_size=True,
            )
        finally:
            browser.close()


def page_count(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def _chrome() -> str | None:
    env = os.environ.get("KSD_CHROME")
    if env and Path(env).exists():
        return env
    for candidate in (
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if Path(candidate).exists():
            return candidate
    return None
