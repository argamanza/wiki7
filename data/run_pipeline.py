#!/usr/bin/env python3
"""Master orchestration script for the Wiki7 data pipeline.

Chains together: scrape -> normalize -> merge -> Hebrew enrich -> import to MediaWiki.

Usage:
    python run_pipeline.py                              # Full pipeline, single season (2024)
    python run_pipeline.py --dry-run                    # Preview what would be imported
    python run_pipeline.py --skip-scrape                # Skip scraping, just normalize + import
    python run_pipeline.py --season 2023                # Run for a specific season
    python run_pipeline.py --seasons 2015-2025          # Run for multiple seasons
    python run_pipeline.py --seasons 2015,2020,2024     # Run for specific seasons
    python run_pipeline.py --skip-scrape --dry-run      # Normalize existing data and preview import

    # Two-phase workflow with manual review of Hebrew translations:
    python run_pipeline.py --seasons 2021-2025 --review-mappings     # Phase 1: stop after auto-translate
    # Edit data_pipeline/output/merged/mappings.he.yaml              # Phase 2: review & fix
    python run_pipeline.py --seasons 2021-2025 --skip-scrape --skip-normalize --skip-merge  # Phase 3: apply & import

Phase 3a R2 — idempotency + resume across seasons:

    # An all-time run (1949 -> current). If this dies mid-season — network
    # hiccup, ScraperAPI rate-limit, anything — restart with the SAME command.
    # Spiders whose output already exists on disk are skipped (resume default),
    # so the second run picks up where the first stopped without re-spending
    # credits or hitting TM.
    python run_pipeline.py --seasons 1949-2025

    # Force a full re-scrape when TM's HTML structure has changed or you want
    # fresh data (e.g. after a known spider fix that affects already-scraped
    # seasons).
    python run_pipeline.py --seasons 1949-2025 --force-rescrape

    # The wiki import step is idempotent regardless of resume — every page
    # write does a content-hash compare against the live page text and skips
    # no-op edits. So re-running import after a partial failure is safe and
    # cheap; the bot makes zero edits for unchanged pages.
"""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import click

logger = logging.getLogger("wiki7_pipeline")

# Project directory layout
DATA_DIR = Path(__file__).resolve().parent
SCRAPER_DIR = DATA_DIR / "tmk-scraper"
SCRAPER_OUTPUT_DIR = SCRAPER_DIR / "output"
PIPELINE_DIR = DATA_DIR / "data_pipeline"
PIPELINE_OUTPUT_DIR = PIPELINE_DIR / "output"


def parse_seasons(seasons_arg: str) -> list[str]:
    """Parse --seasons argument into list of season year strings.

    Supports range ('2015-2025') and comma-separated ('2015,2020,2024') formats.
    """
    if "-" in seasons_arg and "," not in seasons_arg:
        parts = seasons_arg.split("-")
        return [str(y) for y in range(int(parts[0]), int(parts[1]) + 1)]
    return [s.strip() for s in seasons_arg.split(",")]


def _has_useful_data(path: Path) -> bool:
    """A scraper output file is "useful" if it contains a non-empty JSON
    list (i.e. at least one record). Empty / `[]` / missing files mean we
    need to re-scrape.
    """
    if not path.exists():
        return False
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return bool(data)
    except (json.JSONDecodeError, OSError):
        return False


def _run_spider(
    spider_name: str,
    season: str,
    output_file: str,
    resume: bool = True,
    allow_empty: bool = False,
) -> bool:
    """Run a Scrapy spider and return True on success.

    Phase 3a R2: when `resume=True` (default for multi-season runs), an
    existing non-empty output file is treated as already-done and the
    spider call is skipped. Pass `resume=False` (--force-rescrape) to
    re-fetch even when output exists. The resume default makes long
    all-time runs restartable when they die mid-season.

    Phase 3a R2: when `allow_empty=True` (default for multi-season runs),
    a spider that returns `[]` is logged as a warning but does NOT abort
    the run — this is the sparse-historical-season case. The downstream
    season-overview rendering handles missing data by emitting a
    placeholder banner. Single-season runs keep allow_empty=False so a
    TM block on the latest season still surfaces as a hard error.
    """
    season_output_dir = SCRAPER_OUTPUT_DIR / season
    season_output_dir.mkdir(parents=True, exist_ok=True)
    output_path = season_output_dir / output_file

    if resume and _has_useful_data(output_path):
        logger.info("Resume: skipping %s for season %s (existing output)", spider_name, season)
        return True

    if output_path.exists():
        output_path.unlink()
        logger.info("Removed stale output: %s", output_path)

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-a", f"season={season}",
        "-o", str(output_path),
    ]

    verbose = logger.isEnabledFor(logging.DEBUG)
    logger.info("Running spider: %s (season=%s)", spider_name, season)

    result = subprocess.run(
        cmd,
        cwd=str(SCRAPER_DIR),
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.PIPE,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        logger.error("Spider '%s' failed (exit code %d)", spider_name, result.returncode)
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-10:]:
                logger.error("  %s", line)
        return False

    if not output_path.exists():
        logger.error("Spider '%s' produced no output at %s", spider_name, output_path)
        return False

    # Check output file has actual data.
    # squad, player, and stats are critical for the latest season — empty
    # typically means Transfermarkt is blocking us. For historical sparse
    # seasons (multi-season runs over the all-time corpus), empty is the
    # legitimate "TM doesn't carry this era" case — `allow_empty=True`
    # downgrades the error to a warning so the run continues.
    # fixtures and match may legitimately return [] (future/incomplete seasons).
    CRITICAL_SPIDERS = {"squad", "player", "stats"}
    with open(output_path, "r") as f:
        data = json.load(f)
    if not data:
        if spider_name in CRITICAL_SPIDERS and not allow_empty:
            logger.error("Spider '%s' returned empty results for season %s", spider_name, season)
            return False
        logger.warning(
            "Spider '%s' returned empty results for season %s (continuing — %s)",
            spider_name, season,
            "sparse historical season" if allow_empty else "non-critical",
        )

    logger.info("Spider '%s' completed -> %s", spider_name, output_path)
    return True


# Per-season spiders (run once per season)
ALL_SPIDERS = [
    ("squad", "squad.json"),
    ("player", "players.json"),
    ("stats", "stats.json"),
    ("fixtures", "fixtures.json"),
    ("match", "matches.json"),
    ("transfers", "transfers.json"),
]

# Club-level spiders (run once, not per-season)
CLUB_SPIDERS = [
    ("coach", "coaches.json"),
    ("honours", "honours.json"),
    ("stadium", "stadium.json"),
    ("records", "records.json"),
    # Phase 3a R2 additions: aggregate club-level data that doesn't change per
    # season. Both spiders ignore the season arg and emit one rows-set per
    # request, covering all seasons TM has data for.
    ("platzierungen", "season_standings.json"),
    ("bilanz", "head_to_head.json"),
]


def run_scrape(
    season: str,
    only: set[str] | None = None,
    resume: bool = True,
    allow_empty: bool = False,
) -> bool:
    """Run per-season spiders in the correct order for a single season.

    If *only* is given, run just those spiders (order is preserved).
    When *resume* is True, existing non-empty output files cause the
    matching spider to be skipped (the default for multi-season runs).
    When *allow_empty* is True, empty results from critical spiders are
    logged as warnings instead of aborting the season — required for
    multi-season runs over sparse historical eras.
    """
    spiders = [(n, f) for n, f in ALL_SPIDERS if only is None or n in only]

    for spider_name, output_file in spiders:
        if not _run_spider(
            spider_name, season, output_file,
            resume=resume, allow_empty=allow_empty,
        ):
            logger.error("Pipeline aborted: spider '%s' failed for season %s", spider_name, season)
            return False

    logger.info("All spiders completed successfully for season %s", season)
    return True


def run_club_scrape(only: set[str] | None = None, resume: bool = True) -> bool:
    """Run club-level spiders (not per-season).

    These are run once and output to the base scraper output directory.
    Resume default applies the same skip-when-output-exists rule.
    """
    spiders = [(n, f) for n, f in CLUB_SPIDERS if only is None or n in only]

    if not spiders:
        return True

    for spider_name, output_file in spiders:
        output_path = SCRAPER_OUTPUT_DIR / output_file
        if resume and _has_useful_data(output_path):
            logger.info("Resume: skipping club spider '%s' (existing output)", spider_name)
            continue
        if output_path.exists():
            output_path.unlink()
            logger.info("Removed stale output: %s", output_path)

        cmd = [
            sys.executable, "-m", "scrapy", "crawl", spider_name,
            "-o", str(output_path),
        ]

        verbose = logger.isEnabledFor(logging.DEBUG)
        logger.info("Running club spider: %s", spider_name)

        result = subprocess.run(
            cmd,
            cwd=str(SCRAPER_DIR),
            stdout=None if verbose else subprocess.DEVNULL,
            stderr=None if verbose else subprocess.PIPE,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            logger.error("Club spider '%s' failed (exit code %d)", spider_name, result.returncode)
            if result.stderr:
                for line in result.stderr.strip().split("\n")[-10:]:
                    logger.error("  %s", line)
            return False

        logger.info("Club spider '%s' completed -> %s", spider_name, output_path)

    return True


def run_normalize(season: str) -> bool:
    """Run the normalization pipeline for a single season.

    Phase 3a R2: sparse seasons (where scrape produced no players.json
    because the squad spider returned empty) get a warning + skip. The
    downstream season-overview rendering handles the missing-data case
    via its placeholder banner.
    """
    logger.info("Running normalization pipeline for season %s...", season)
    try:
        from data_pipeline.normalize_enrich_players import main as normalize_main

        scraper_season_dir = SCRAPER_OUTPUT_DIR / season
        pipeline_season_dir = PIPELINE_OUTPUT_DIR / season

        # Phase 3a R2: sparse-season guard. If players.json doesn't exist or
        # is empty (squad spider returned []), there's nothing to normalize —
        # skip cleanly. import_season_overview will render a placeholder.
        players_path = scraper_season_dir / "players.json"
        if not _has_useful_data(players_path):
            logger.info(
                "Skipping normalize for season %s: no player data on disk "
                "(sparse historical season — placeholder will render downstream).",
                season,
            )
            pipeline_season_dir.mkdir(parents=True, exist_ok=True)
            return True

        normalize_main(
            raw_path=players_path,
            stats_path=scraper_season_dir / "stats.json",
            out_dir=pipeline_season_dir,
        )
        logger.info("Normalization completed for season %s", season)
        return True
    except FileNotFoundError as exc:
        logger.warning("Normalization skipped for season %s (missing input): %s", season, exc)
        return True
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Normalization failed with data error for season %s: %s", season, exc)
        return False


def run_merge(seasons: list[str]) -> bool:
    """Merge normalized data from multiple seasons."""
    logger.info("Merging data from %d seasons...", len(seasons))
    try:
        from data_pipeline.merge_seasons import merge_seasons
        merge_seasons(
            base_dir=PIPELINE_OUTPUT_DIR,
            seasons=seasons,
            output_dir=PIPELINE_OUTPUT_DIR / "merged",
        )
        logger.info("Merge completed")
        return True
    except FileNotFoundError as exc:
        logger.error("Merge failed: %s", exc)
        return False


def run_hebrew_enrichment(data_dir: Path, seasons: list[str] | None = None, review_only: bool = False) -> bool:
    """Generate Hebrew mappings, auto-translate, and apply to data.

    If review_only is True, stops after auto-translate so the user can review
    mappings.he.yaml before applying.
    """
    logger.info("Running Hebrew enrichment pipeline...")
    try:
        from data_pipeline.generate_mapping_stub import generate_stub
        from data_pipeline.auto_translate_hebrew import auto_translate
        from data_pipeline.apply_hebrew_mapping import apply_mappings, apply_hebrew_matches, load_mapping

        players_path = data_dir / "players.jsonl"
        transfers_path = data_dir / "transfers.jsonl"
        mapping_path = data_dir / "mappings.he.yaml"

        logger.info("Generating mapping stub...")
        generate_stub(players_path, transfers_path, mapping_path, SCRAPER_OUTPUT_DIR)

        logger.info("Auto-translating empty mappings...")
        summary = auto_translate(mapping_path)
        if summary:
            logger.info("  Translations: %s", summary)

        if review_only:
            logger.info("=" * 60)
            logger.info("REVIEW MODE: Mappings generated at:")
            logger.info("  %s", mapping_path)
            logger.info("Review and fix translations, then re-run with:")
            logger.info("  --skip-scrape --skip-normalize --skip-merge")
            logger.info("=" * 60)
            return True

        logger.info("Applying Hebrew mappings to data files...")
        apply_mappings(players_path, data_dir / "players.he.jsonl", mapping_path)

        if seasons:
            mapping = load_mapping(mapping_path)
            players_he = data_dir / "players.he.jsonl"
            for season in seasons:
                matches_in = SCRAPER_OUTPUT_DIR / season / "matches.json"
                matches_out = SCRAPER_OUTPUT_DIR / season / "matches.he.json"
                if matches_in.exists():
                    logger.info("Applying Hebrew mappings to matches for season %s...", season)
                    apply_hebrew_matches(matches_in, matches_out, mapping, players_he)

        logger.info("Hebrew enrichment completed")
        return True
    except FileNotFoundError as exc:
        logger.error("Hebrew enrichment failed: %s", exc)
        return False
    except Exception as exc:
        logger.error("Hebrew enrichment failed: %s", exc)
        return False


def run_import(
    seasons: list[str],
    dry_run: bool = False,
    wiki_url: str | None = None,
    data_dir: Path | None = None,
) -> bool:
    """Run the MediaWiki import step."""
    import os

    if not wiki_url:
        if not dry_run:
            logger.warning("No WIKI_URL configured. Forcing dry-run mode.")
        dry_run = True

    site = None
    if not dry_run:
        import mwclient
        from urllib.parse import urlparse
        try:
            parsed = urlparse(wiki_url if "://" in wiki_url else f"http://{wiki_url}")
            host = parsed.hostname or wiki_url
            port = parsed.port
            scheme = parsed.scheme or ("http" if host in ("localhost", "127.0.0.1") else "https")
            host_str = f"{host}:{port}" if port else host
            site = mwclient.Site(host_str, path="/", scheme=scheme)
            wiki_user = os.environ.get("WIKI_BOT_USER", "")
            wiki_pass = os.environ.get("WIKI_BOT_PASS", "")
            if wiki_user and wiki_pass:
                site.login(wiki_user, wiki_pass)
                logger.info("Logged in to %s as %s", wiki_url, wiki_user)
            else:
                logger.warning("WIKI_BOT_USER/WIKI_BOT_PASS not set; proceeding without auth")
        except Exception as exc:
            logger.error("Failed to connect to wiki at %s: %s", wiki_url, exc)
            return False

    from wiki_import.import_players import import_players
    from wiki_import.import_matches import import_matches
    from wiki_import.import_templates import (
        import_mediawiki_templates, import_cargo_templates,
        import_squad_page, import_transfer_page,
        import_coaches_page, import_honours_page, import_stadium_page,
        import_records_page, import_season_overview, import_leaderboards,
        import_attendance, import_competition_pages,
    )

    # Determine data directory (merged or single-season)
    resolved_data_dir = data_dir or PIPELINE_OUTPUT_DIR

    results = {}
    all_ok = True

    # MediaWiki templates (infoboxes, tooltip, etc.) — must exist before content pages
    try:
        logger.info("Importing MediaWiki templates...")
        results["mediawiki_templates"] = import_mediawiki_templates(site=site, dry_run=dry_run)
    except FileNotFoundError as exc:
        logger.error("MediaWiki template import failed: %s", exc)
        all_ok = False

    # Cargo templates (once)
    try:
        logger.info("Importing Cargo templates...")
        results["cargo"] = import_cargo_templates(site=site, dry_run=dry_run)
    except FileNotFoundError as exc:
        logger.error("Cargo template import failed: %s", exc)
        all_ok = False

    # Player pages (from merged/single data dir, prefer Hebrew-enriched versions)
    try:
        logger.info("Importing player pages...")
        players_path = resolved_data_dir / "players.jsonl"
        he_players = resolved_data_dir / "players.he.jsonl"
        if he_players.exists():
            players_path = he_players

        transfers_path = resolved_data_dir / "transfers.jsonl"
        he_transfers = resolved_data_dir / "transfers.he.jsonl"
        if he_transfers.exists():
            transfers_path = he_transfers

        mv_path = resolved_data_dir / "market_values.jsonl"
        he_mv = resolved_data_dir / "market_values.he.jsonl"
        if he_mv.exists():
            mv_path = he_mv

        results["players"] = import_players(
            site=site,
            players_path=players_path,
            transfers_path=transfers_path,
            market_values_path=mv_path,
            stats_path=resolved_data_dir / "stats.jsonl",
            dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        logger.error("Player import failed: %s", exc)
        all_ok = False

    # Match reports (per season, prefer Hebrew-enriched)
    for season in seasons:
        try:
            he_matches = SCRAPER_OUTPUT_DIR / season / "matches.he.json"
            matches_path = he_matches if he_matches.exists() else SCRAPER_OUTPUT_DIR / season / "matches.json"
            if matches_path.exists():
                logger.info("Importing match reports for season %s...", season)
                results[f"matches_{season}"] = import_matches(
                    site=site, matches_path=matches_path, dry_run=dry_run,
                )
        except FileNotFoundError as exc:
            logger.error("Match import failed for season %s: %s", season, exc)
            all_ok = False

    # Squad and transfer pages (per season, prefer Hebrew-enriched)
    squad_players = he_players if he_players.exists() else resolved_data_dir / "players.jsonl"
    squad_transfers = he_transfers if he_transfers.exists() else resolved_data_dir / "transfers.jsonl"

    for season in seasons:
        try:
            logger.info("Importing squad page for season %s...", season)
            results[f"squad_{season}"] = import_squad_page(
                site=site, season=season,
                players_path=squad_players,
                stats_path=resolved_data_dir / "stats.jsonl",
                dry_run=dry_run,
            )
        except FileNotFoundError as exc:
            logger.error("Squad page import failed for season %s: %s", season, exc)
            all_ok = False

        try:
            logger.info("Importing transfer page for season %s...", season)
            results[f"transfers_{season}"] = import_transfer_page(
                site=site, season=season,
                players_path=squad_players,
                transfers_path=squad_transfers,
                dry_run=dry_run,
            )
        except FileNotFoundError as exc:
            logger.error("Transfer page import failed for season %s: %s", season, exc)
            all_ok = False

    # Season overview pages (per season, prefer Hebrew-enriched)
    for season in seasons:
        try:
            logger.info("Importing season overview for %s...", season)
            results[f"season_{season}"] = import_season_overview(
                site=site, season=season,
                players_path=squad_players,
                stats_path=resolved_data_dir / "stats.jsonl",
                dry_run=dry_run,
            )
        except FileNotFoundError as exc:
            logger.error("Season overview import failed for %s: %s", season, exc)
            all_ok = False

    # Club-level pages (once)
    for label, func, kwargs in [
        ("coaches", import_coaches_page, {}),
        ("honours", import_honours_page, {}),
        ("stadium", import_stadium_page, {}),
        ("records", import_records_page, {}),
    ]:
        try:
            logger.info("Importing %s page...", label)
            results[label] = func(site=site, dry_run=dry_run, **kwargs)
        except FileNotFoundError as exc:
            logger.error("%s import failed: %s", label, exc)
            all_ok = False

    # Leaderboards (from merged stats, prefer Hebrew-enriched players)
    try:
        logger.info("Importing leaderboard pages...")
        results["leaderboards"] = import_leaderboards(
            site=site,
            stats_path=resolved_data_dir / "stats.jsonl",
            players_path=squad_players,
            dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        logger.error("Leaderboard import failed: %s", exc)
        all_ok = False

    # Attendance statistics (from all seasons' fixtures)
    try:
        logger.info("Importing attendance statistics...")
        results["attendance"] = import_attendance(
            site=site, seasons=seasons, dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        logger.error("Attendance import failed: %s", exc)
        all_ok = False

    # Competition season pages
    try:
        logger.info("Importing competition pages...")
        results["competitions"] = import_competition_pages(
            site=site, seasons=seasons, dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        logger.error("Competition pages import failed: %s", exc)
        all_ok = False

    # Print summary
    logger.info("=" * 60)
    logger.info("IMPORT SUMMARY%s", " (DRY RUN)" if dry_run else "")
    logger.info("=" * 60)
    total_created = total_updated = total_skipped = total_failed = 0
    for step, result in results.items():
        c, u, s, f = result["created"], result["updated"], result["skipped"], result["failed"]
        total_created += c
        total_updated += u
        total_skipped += s
        total_failed += f
        logger.info(
            "  %-20s: %d created, %d updated, %d skipped, %d failed",
            step, c, u, s, f,
        )

    logger.info("-" * 60)
    logger.info(
        "  TOTAL: %d created, %d updated, %d skipped, %d failed",
        total_created, total_updated, total_skipped, total_failed,
    )

    return all_ok and total_failed == 0


@click.command()
@click.option("--season", default="2024", help="Season year to process (default: 2024)")
@click.option("--seasons", default=None, help="Multi-season range (e.g., '2015-2025') or list (e.g., '2015,2020,2024')")
@click.option("--dry-run", is_flag=True, help="Preview import without writing to wiki")
@click.option("--spiders", default=None, help="Run only these spiders (comma-separated, e.g., 'stats' or 'squad,player')")
@click.option("--skip-scrape", is_flag=True, help="Skip the scraping step")
@click.option("--skip-normalize", is_flag=True, help="Skip the normalization step")
@click.option("--skip-merge", is_flag=True, help="Skip the merge step")
@click.option("--skip-import", is_flag=True, help="Skip the wiki import step")
@click.option("--skip-hebrew", is_flag=True, help="Skip the Hebrew enrichment step")
@click.option("--review-mappings", is_flag=True, help="Stop after generating Hebrew mappings for manual review")
@click.option("--wiki-url", envvar="WIKI_URL", default=None, help="MediaWiki site URL (or set WIKI_URL env var)")
@click.option(
    "--force-rescrape", is_flag=True,
    help="Phase 3a R2: re-fetch every spider output even when a non-empty file "
         "already exists. Default (resume) skips spiders whose output is already "
         "on disk — makes long all-time runs restartable after a partial failure.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(season, seasons, spiders, dry_run, skip_scrape, skip_normalize, skip_merge, skip_import, skip_hebrew, review_mappings, wiki_url, force_rescrape, verbose):
    """Wiki7 data pipeline: scrape -> normalize -> merge -> Hebrew enrich -> import."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Determine season list
    if seasons:
        season_list = parse_seasons(seasons)
        multi_season = True
    else:
        season_list = [season]
        multi_season = False

    # Parse --spiders filter
    spider_filter = None
    if spiders:
        valid_names = {name for name, _ in ALL_SPIDERS} | {name for name, _ in CLUB_SPIDERS}
        spider_filter = {s.strip() for s in spiders.split(",")}
        unknown = spider_filter - valid_names
        if unknown:
            logger.error("Unknown spider(s): %s (valid: %s)", ", ".join(unknown), ", ".join(sorted(valid_names)))
            sys.exit(1)

    start_time = time.time()
    logger.info(
        "Wiki7 pipeline starting (seasons=%s, dry_run=%s, multi_season=%s)",
        season_list, dry_run, multi_season,
    )
    errors = []

    # Step 1: Scrape (per season + club-level). Resume by default; opt out
    # with --force-rescrape for an explicit full re-fetch. allow_empty is
    # automatically enabled for multi-season runs so sparse historical
    # seasons (where TM legitimately has no squad/stats data) don't abort
    # the whole pipeline — they fall through to placeholder season-overview
    # pages downstream.
    resume = not force_rescrape
    allow_empty = multi_season
    if not skip_scrape:
        logger.info("=" * 60)
        logger.info(
            "STEP 1: SCRAPING (%d seasons, %s, empty-tolerant=%s)",
            len(season_list), "resume" if resume else "full re-fetch", allow_empty,
        )
        logger.info("=" * 60)
        for s in season_list:
            logger.info("--- Scraping season %s ---", s)
            if not run_scrape(s, only=spider_filter, resume=resume, allow_empty=allow_empty):
                errors.append(f"Scraping failed for season {s}")
                logger.error("Scraping failed for season %s. Continuing with next...", s)

        # Club-level spiders (run once, not per-season)
        club_filter = spider_filter
        if club_filter:
            club_names = {name for name, _ in CLUB_SPIDERS}
            club_filter = club_filter & club_names
        if club_filter is None or club_filter:
            logger.info("--- Scraping club-level data ---")
            if not run_club_scrape(only=club_filter, resume=resume):
                errors.append("Club-level scraping failed")
                logger.error("Club-level scraping failed")

        # Phase 3a R2: derive coach trophies-won + tenure-seasons by joining
        # honours.json x season_standings.json (platzierungen) + layering current
        # staff (coaches.json) on top. Writes coaches_enriched.json next to the
        # source files. Skipped when none of the inputs exist (e.g. dev runs
        # using --spiders that didn't fetch the prerequisites).
        try:
            from data_pipeline.derive_coach_trophies import write_enriched
            if any((SCRAPER_OUTPUT_DIR / fname).exists()
                   for fname in ("honours.json", "season_standings.json", "coaches.json")):
                logger.info("--- Deriving coach trophies + tenure-seasons ---")
                write_enriched(SCRAPER_OUTPUT_DIR)
        except Exception as exc:  # noqa: BLE001 — non-fatal post-process step
            logger.warning("Coach trophy derivation failed: %s (continuing)", exc)
    else:
        logger.info("Skipping scrape step (--skip-scrape)")

    # Step 2: Normalize (per season)
    if not skip_normalize:
        logger.info("=" * 60)
        logger.info("STEP 2: NORMALIZATION (%d seasons)", len(season_list))
        logger.info("=" * 60)
        for s in season_list:
            logger.info("--- Normalizing season %s ---", s)
            if not run_normalize(s):
                errors.append(f"Normalization failed for season {s}")
                logger.error("Normalization failed for season %s.", s)
    else:
        logger.info("Skipping normalize step (--skip-normalize)")

    # Step 3: Merge (multi-season only)
    data_dir = None
    if multi_season and not skip_merge:
        logger.info("=" * 60)
        logger.info("STEP 3: MERGE (%d seasons)", len(season_list))
        logger.info("=" * 60)
        if not run_merge(season_list):
            errors.append("Merge failed")
            logger.error("Merge failed.")
        else:
            data_dir = PIPELINE_OUTPUT_DIR / "merged"
    elif multi_season:
        logger.info("Skipping merge step (--skip-merge)")
        data_dir = PIPELINE_OUTPUT_DIR / "merged"
    else:
        # Single season: use season-specific dir
        data_dir = PIPELINE_OUTPUT_DIR / season_list[0]

    # Step 4: Hebrew enrichment
    if data_dir and not skip_hebrew:
        step_num = 4 if multi_season else 3
        logger.info("=" * 60)
        logger.info("STEP %d: HEBREW ENRICHMENT%s", step_num, " (REVIEW MODE)" if review_mappings else "")
        logger.info("=" * 60)
        if not run_hebrew_enrichment(data_dir, seasons=season_list, review_only=review_mappings):
            errors.append("Hebrew enrichment failed")
            logger.error("Hebrew enrichment failed.")
        if review_mappings:
            elapsed = time.time() - start_time
            logger.info("Pipeline paused for review (%.1fs). Exiting.", elapsed)
            sys.exit(0)
    elif skip_hebrew:
        logger.info("Skipping Hebrew enrichment step (--skip-hebrew)")

    # Step 5: Import
    if not skip_import:
        step_num = 5 if multi_season else 4
        logger.info("=" * 60)
        logger.info("STEP %d: WIKI IMPORT%s", step_num, " (DRY RUN)" if dry_run else "")
        logger.info("=" * 60)
        if not run_import(season_list, dry_run=dry_run, wiki_url=wiki_url, data_dir=data_dir):
            errors.append("Wiki import had failures")
    else:
        logger.info("Skipping import step (--skip-import)")

    # Final summary
    elapsed = time.time() - start_time
    logger.info("=" * 60)
    if errors:
        logger.error("PIPELINE FINISHED WITH ERRORS (%.1fs):", elapsed)
        for err in errors:
            logger.error("  - %s", err)
        sys.exit(1)
    else:
        logger.info("PIPELINE COMPLETED SUCCESSFULLY (%.1fs)", elapsed)


if __name__ == "__main__":
    main()
