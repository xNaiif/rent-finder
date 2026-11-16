"""Command line entry: ``python -m rentfinder``.

Exit codes: 0 ok · 2 invalid search parameters · 3 nothing could be fetched
(usually the site blocked the runner) · 4 unexpected error.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import (DEFAULT_OUTPUT_DIR, ConfigError, Settings, annotate,
                     setup_logging)
from .report import write_outputs
from .search import BrowserFetcher, UrllibFetcher, run_search

log = logging.getLogger("rentfinder")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rentfinder", description="East Riyadh rental search")
    parser.add_argument("--mode", choices=["browser", "urllib"], default=os.getenv("FETCH_MODE", "browser"),
                        help="browser = Chromium via Playwright (default); urllib = lightweight, easier to block")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="output folder (default: site)")
    parser.add_argument("-v", "--verbose", action="store_true", default=os.getenv("RENTFINDER_VERBOSE") == "1")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    log.info("rentfinder %s", __version__)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log.error("invalid input: %s", exc)
        annotate("error", str(exc))
        return 2
    log.info("settings: %s", settings)

    fetcher_cls = BrowserFetcher if args.mode == "browser" else UrllibFetcher
    try:
        with fetcher_cls() as fetcher:
            result = run_search(settings, fetcher)
    except Exception:  # noqa: BLE001 - last-resort guard so the run page shows a clear error
        log.exception("unexpected failure")
        annotate("error", "خطأ غير متوقع، راجع السجل")
        return 4

    if result.stats.seen == 0:
        log.error("no listings could be read; see the debug artifact")
        annotate("error", "لم يُقرأ أي إعلان. غالباً حجب الموقع طلبات GitHub. راجع README (قسم الحجب).")
        return 3

    write_outputs(result, settings, args.output)
    log.info("done: %d results (partial=%s)", len(result.listings), result.stats.partial)
    return 0


if __name__ == "__main__":
    sys.exit(main())
