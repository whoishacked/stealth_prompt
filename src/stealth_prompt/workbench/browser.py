"""Launches the workbench browser.

Playwright's bundled Chromium is used with a persistent context, because
Manifest V3 extensions can only be loaded that way. Two defaults are worth
naming:

* the Chromium sandbox stays enabled -- ``--no-sandbox`` is never passed, and
  disabling it requires an explicit setting that the session records;
* TLS is verified. ``ignore_https_errors`` is opt-in and produces a warning.

Playwright is imported lazily so the base install and the offline test suite
never require a browser binary.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import DIR_MODE, _chmod
from .config import ProfileMode, WorkbenchConfig


class BrowserUnavailableError(RuntimeError):
    """Playwright or its Chromium build is not installed."""


@dataclass
class LaunchedBrowser:
    """A running browser context and the resources it owns."""

    context: Any
    page: Any
    playwright: Any
    user_data_dir: Path
    owns_user_data_dir: bool

    async def close(self) -> None:
        """Close everything this object owns, in order, never raising."""
        for closer in (self._close_context, self._stop_playwright):
            try:
                await closer()
            except Exception:  # noqa: BLE001 - shutdown must not mask the real error
                pass
        if self.owns_user_data_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)

    async def _close_context(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None

    async def _stop_playwright(self) -> None:
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None


def chromium_args(extension_dir: Path, config: WorkbenchConfig) -> list[str]:
    """Build the Chromium argument list.

    Note what is absent: ``--no-sandbox``, ``--ignore-certificate-errors``, and
    ``--allow-running-insecure-content``. The legacy Selenium path passed all
    three unconditionally; the workbench does not.
    """
    args = [
        f"--disable-extensions-except={extension_dir}",
        f"--load-extension={extension_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-features=Translate,MediaRouter",
    ]
    if config.browser.headless:
        # Chromium's *new* headless mode. Playwright's own `headless=True` uses
        # a mode that silently loads no MV3 extension -- the browser starts, the
        # page loads, and the dock simply never appears. Requesting the new mode
        # explicitly is the only way to have both.
        args.append("--headless=new")
    if not config.browser.sandbox:
        # Only reachable when the operator explicitly disabled it; the session
        # plan prints a warning and the result records it.
        args.append("--no-sandbox")
    return args


def resolve_user_data_dir(config: WorkbenchConfig) -> tuple[Path, bool]:
    """Return ``(directory, owned)`` for the browser profile."""
    if config.browser.mode is ProfileMode.PERSISTENT:
        directory = config.browser.profile_dir
        assert directory is not None  # guaranteed by ProfileMode.PERSISTENT
        directory.mkdir(parents=True, exist_ok=True)
        _chmod(directory, DIR_MODE)
        return directory, False

    directory = Path(tempfile.mkdtemp(prefix="stealth-prompt-profile-"))
    _chmod(directory, DIR_MODE)
    return directory, True


async def launch_browser(
    config: WorkbenchConfig, extension_dir: Path
) -> LaunchedBrowser:
    """Launch Chromium with the workbench extension loaded."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise BrowserUnavailableError(
            'playwright is not installed; run: pip install "stealth-prompt[workbench]"'
        ) from exc

    user_data_dir, owned = resolve_user_data_dir(config)
    playwright = await async_playwright().start()

    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            # Headlessness is requested through chromium_args() instead, so the
            # extension loads. See the note there.
            headless=False,
            args=chromium_args(extension_dir, config),
            viewport={
                "width": config.browser.viewport_width,
                "height": config.browser.viewport_height,
            },
            ignore_https_errors=config.browser.ignore_https_errors,
            accept_downloads=False,
            permissions=[],
        )
    except Exception as exc:
        await playwright.stop()
        if owned:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise BrowserUnavailableError(
                "Playwright's Chromium is not installed; run: "
                "python -m playwright install chromium"
            ) from None
        raise

    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(config.target_url, wait_until="domcontentloaded")

    return LaunchedBrowser(
        context=context,
        page=page,
        playwright=playwright,
        user_data_dir=user_data_dir,
        owns_user_data_dir=owned,
    )
