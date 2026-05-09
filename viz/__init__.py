"""Twitter/Substack-ready chart templates.

Each template lives in its own module and exposes one public function,
``render(args, out_dir) -> list[Path]``, that returns the paths of the
PNGs it wrote (typically a 1200x675 widescreen for Twitter cards plus a
1080x1080 square fallback).

Wire-up (CLI):
    python cli.py viz prize-pool-ladder --year 2026 --limit 15
    python cli.py viz tournament-card --id 20710
    python cli.py viz sticker-prices --event-slug pgl-copenhagen-2024
"""
