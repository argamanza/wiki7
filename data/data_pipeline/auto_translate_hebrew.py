"""Auto-translate English values to Hebrew in the mapping YAML.

Runs after generate_mapping_stub.py creates the YAML with empty values,
and before the user manually reviews. Phase 3a R2: switched primary
backend from Google Translate to the Anthropic API for Claude. Claude is
materially better at Hebrew transliteration of foreign player names
(Eastern European, African, South American — the historical HBS roster
has all three), and self-rates confidence so the reviewer can focus on
flagged entries rather than the full corpus.

Output shape (Phase 3a R2 — nested):
    Centre-Back:
      he: בלם
      src: manual          # manual | wikidata | wikipedia | auto-llm | auto-google | auto-translit
      confidence: high     # high | low
      wikidata_qid: ""     # Q-ID when src=wikidata; "" otherwise (post Phase 3a R2 iter-cycle)
      note: ""             # optional human comment

The legacy flat shape (`Centre-Back: בלם`) is auto-migrated on first load:
existing entries are wrapped in the nested form with `src: manual` and
`confidence: high` (assumes they were human-curated before R2). Auto-fill
only writes entries that were previously empty; manual entries are
preserved unchanged.

Translation backend chain (per category):
- names + clubs + competitions + nationalities:
    1. Wikidata (canonical Hebrew label, when type filter matches) — high confidence
    2. (names only) English Wikipedia langlinks — thin secondary, kept as cheap insurance
    3. Claude API — phonetic / context-aware transliteration
    4. Phonetic transliteration — final fallback when Claude omits the entry
- positions: skips Wikidata + Wikipedia (per docs/research/0003 §1 — positional
  vocabulary is too short / generic for entity disambiguation), goes direct to Claude.

Usage:
    python -m data_pipeline.auto_translate_hebrew [--mapping-path PATH] [...]

CLI flags:
    --mapping-path PATH      Path to mappings.he.yaml (default: merged/)
    --dry-run                Preview without writing
    --review-flagged-only    After auto-fill, print a focused report of all
                             entries currently flagged `confidence: low` so
                             the reviewer can quickly assess + correct.
    --use-google             Force the legacy Google Translate path even when
                             ANTHROPIC_API_KEY is set (debugging / cost cap).

Workflow:
    1. generate_mapping_stub.py  ->  mappings.he.yaml (empty values)
    2. auto_translate_hebrew.py  ->  mappings.he.yaml (auto-filled, nested)
    3. User reviews flagged entries + fixes bad translations
    4. apply_hebrew_mapping.py   ->  players.he.jsonl (final output)
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
import yaml

logger = logging.getLogger(__name__)

DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent / "output" / "merged" / "mappings.he.yaml"

TRANSLATE_DELAY = 0.1
MAX_WORKERS = 5

# Categories the mapping YAML carries. Claude is given a different system-
# prompt-flavour hint per category (e.g. "this is a club name" vs "this is
# a player name") so transliteration quality is calibrated.
CATEGORIES = ("positions", "nationalities", "clubs", "competitions", "names")

# Wikidata entity type per category. Categories absent from this map skip
# the Wikidata pass and go straight to Claude (positions are too generic
# to disambiguate via Wikidata's claims; see docs/research/0003 §1).
_WIKIDATA_ENTITY_TYPE = {
    "names": "player",
    "clubs": "club",
    "competitions": "competition",
    "nationalities": "country",
}

# Chunk size for the Anthropic API calls — keeps each request well inside
# the model's output-token cap and gives readable progress updates without
# spamming the log.
CLAUDE_BATCH_SIZE = 200

# Model choice for the Anthropic path. Sonnet is the right balance of
# transliteration quality (foreign names) vs cost at the all-time corpus
# size. Switch to Opus only if quality complaints surface; Haiku misses
# subtle Russian/Polish transliterations in spot checks.
CLAUDE_MODEL = "claude-sonnet-4-6"

_LATIN_TO_HEBREW = {
    "a": "א", "b": "ב", "c": "ק", "d": "ד", "e": "א", "f": "פ",
    "g": "ג", "h": "ה", "i": "י", "j": "ג'", "k": "ק", "l": "ל",
    "m": "מ", "n": "נ", "o": "ו", "p": "פ", "q": "ק", "r": "ר",
    "s": "ס", "t": "ט", "u": "ו", "v": "ו", "w": "ו", "x": "קס",
    "y": "י", "z": "ז",
}

_DIGRAPHS = {
    "sh": "ש", "ch": "צ'", "th": "ת", "tz": "צ", "zh": "ז'",
    "ph": "פ", "kh": "ח",
}


# ---------------------------------------------------------------------------
# Backward-compat YAML shape handling
# ---------------------------------------------------------------------------


def _is_legacy_flat(entry) -> bool:
    """A pre-R2 entry is either a bare string or None / empty. R2 entries
    are dicts with at least the `he` key.
    """
    return entry is None or isinstance(entry, str)


def _migrate_entry(value: str) -> dict | None:
    """Convert a flat-shape entry into the nested R2 shape.

    Empty strings → kept as empty entries (None for `he` and `src`) so
    `auto_translate` still sees them as "needs filling". Non-empty strings
    are assumed human-curated (legacy was a manual-review file) and marked
    `src: manual`, `confidence: high`.
    """
    if not value:
        return {"he": "", "src": "", "confidence": "", "wikidata_qid": "", "note": ""}
    return {
        "he": value, "src": "manual", "confidence": "high",
        "wikidata_qid": "", "note": "",
    }


def _migrate_section(section: dict) -> tuple[dict, int]:
    """Migrate a whole category section. Returns (new_section, migrated_count)."""
    migrated = 0
    out = {}
    for key, entry in section.items():
        if _is_legacy_flat(entry):
            out[key] = _migrate_entry(entry or "")
            if entry:
                migrated += 1
        else:
            # Already nested — pass through but normalise missing fields.
            # `wikidata_qid` was added during the iteration-cycle phase;
            # older nested entries lack it, default to "".
            out[key] = {
                "he": entry.get("he", ""),
                "src": entry.get("src", ""),
                "confidence": entry.get("confidence", ""),
                "wikidata_qid": entry.get("wikidata_qid", ""),
                "note": entry.get("note", ""),
            }
    return out, migrated


def _migrate_mapping(mapping: dict) -> tuple[dict, int]:
    """Migrate the whole mapping file from flat to nested R2 shape."""
    total_migrated = 0
    for category in CATEGORIES:
        if category in mapping and mapping[category]:
            mapping[category], n = _migrate_section(mapping[category])
            total_migrated += n
    return mapping, total_migrated


def _value(entry) -> str:
    """Resolve a mapping entry to its Hebrew string regardless of shape.

    Used by `apply_hebrew_mapping.py` for backward-compat reads — keep both
    shapes legal on disk during the transition, even though `auto_translate`
    rewrites everything to the nested shape on first run.
    """
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("he", "") or ""
    return ""


# ---------------------------------------------------------------------------
# Phonetic transliteration (fallback when both APIs are unavailable)
# ---------------------------------------------------------------------------


def _transliterate_to_hebrew(text: str) -> str:
    """Phonetic transliteration from Latin script to Hebrew characters.
    Used as fallback when neither Claude nor Google Translate is reachable.
    """
    parts = []
    for word in text.split():
        hebrew_word = []
        i = 0
        lower = word.lower()
        while i < len(lower):
            if i + 1 < len(lower) and lower[i:i + 2] in _DIGRAPHS:
                hebrew_word.append(_DIGRAPHS[lower[i:i + 2]])
                i += 2
            elif lower[i] in _LATIN_TO_HEBREW:
                hebrew_word.append(_LATIN_TO_HEBREW[lower[i]])
                i += 1
            else:
                hebrew_word.append(word[i])
                i += 1
        parts.append("".join(hebrew_word))
    return " ".join(parts)


def _is_latin(text: str) -> bool:
    latin_chars = sum(1 for c in text if c.isalpha() and ord(c) < 0x0250)
    alpha_chars = sum(1 for c in text if c.isalpha())
    return alpha_chars > 0 and latin_chars / alpha_chars > 0.5


# ---------------------------------------------------------------------------
# Anthropic API path (primary)
# ---------------------------------------------------------------------------


_CATEGORY_HINTS = {
    "positions": (
        "Football playing positions. Use standard Hebrew football terminology "
        "(e.g. 'Goalkeeper' → 'שוער', 'Centre-Back' → 'בלם', 'Attacking "
        "Midfield' → 'קשר התקפי'). High confidence is standard for this "
        "category."
    ),
    "nationalities": (
        "Country names. Use the standard Wikipedia Hebrew name for each "
        "country (e.g. 'Israel' → 'ישראל', 'Germany' → 'גרמניה'). High "
        "confidence is standard for this category."
    ),
    "clubs": (
        "Football club names. Use the standard Wikipedia Hebrew name when "
        "known (e.g. 'Maccabi Tel Aviv' → 'מכבי תל אביב'). For less-famous "
        "clubs, transliterate phonetically. Mark confidence=low when the "
        "name is non-Latin source (e.g. Greek, Cyrillic) or when you're "
        "uncertain how an Israeli football fan would render it."
    ),
    "competitions": (
        "Football competition names. Use standard Hebrew names when known "
        "(e.g. 'UEFA Champions League' → 'ליגת האלופות', 'Israeli Premier "
        "League' → 'ליגת העל'). Mark confidence=low for obscure regional "
        "tournaments."
    ),
    "names": (
        "Player and coach names. For Israeli players, the Hebrew form is "
        "the canonical one — use it directly (e.g. 'Sagiv Jehezkel' → "
        "'שגיב יחזקאל'). For foreign players, transliterate phonetically "
        "using common Israeli football media conventions. Mark "
        "confidence=low for non-Latin-source names (Russian, Greek, Arabic, "
        "African languages) where multiple acceptable transliterations "
        "exist."
    ),
}


def _build_system_prompt(category: str) -> str:
    return (
        "You are translating English football terminology to Hebrew for an "
        "Israeli football wiki (Hapoel Beer Sheva fan wiki, called 'ויקישבע').\n\n"
        "Translate accurately, following these rules:\n"
        "- Use the standard Wikipedia Hebrew form when one exists.\n"
        "- For foreign names without an established Hebrew form, transliterate "
        "phonetically using common Israeli football media conventions.\n"
        "- Self-rate confidence honestly: 'high' for entries you're sure about, "
        "'low' for entries where multiple transliterations are plausible or you "
        "lack context.\n\n"
        f"Category-specific guidance: {_CATEGORY_HINTS[category]}\n\n"
        "Respond with ONLY valid JSON in this exact shape — no other text:\n"
        '{"translations": [{"en": "<original>", "he": "<Hebrew>", "confidence": "high|low"}]}'
    )


def _resolve_anthropic_api_key() -> str | None:
    """Phase 3a R2: prefer a wiki7-specific env var so a developer's
    `ANTHROPIC_API_KEY` (used for day-to-day Claude Code subscription work)
    isn't accidentally drained by pipeline runs. Falls back to the standard
    var when the wiki7-specific one isn't set, for backward compat with
    earlier docs / CI configs.
    """
    return (
        os.environ.get("WIKI7_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _translate_batch_via_claude(category: str, items: list[str]) -> list[dict]:
    """Translate one batch via Anthropic API. Returns a list of dicts:
        [{"en": ..., "he": ..., "confidence": "high|low"}]
    Raises an exception on transport failure; caller decides whether to
    fall back to Google Translate / transliteration.
    """
    api_key = _resolve_anthropic_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = (
        f"Translate each of these {len(items)} {category} entries to Hebrew. "
        "Respond with the JSON object specified in the system prompt.\n\n"
        f"Items:\n{json.dumps(items, ensure_ascii=False)}"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        system=[
            {
                "type": "text",
                "text": _build_system_prompt(category),
                # Phase 3a R2: cache the system prompt so subsequent batches
                # (and re-runs) re-use it. Saves ~75% on system-prompt tokens
                # for runs that touch multiple categories.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text if response.content else ""
    # Defensive parsing: some Claude responses wrap JSON in ```json blocks
    # or include leading text. Strip both.
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Claude returned unparseable JSON for %s: %s", category, exc)
        logger.debug("Raw response: %s", text[:500])
        raise

    translations = data.get("translations", [])
    if not isinstance(translations, list):
        raise ValueError(f"Claude response 'translations' not a list: {type(translations)}")

    return translations


def _translate_batch_via_google(texts: list[str], src: str = "en", dest: str = "iw") -> list[str]:
    """Legacy path — kept for `--use-google` and for when ANTHROPIC_API_KEY is unset."""
    if not texts:
        return []
    results = [""] * len(texts)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_translate_one_google, text, src, dest): i
            for i, text in enumerate(texts)
        }
        done = 0
        total = len(texts)
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done += 1
            if done % 50 == 0:
                logger.info("  Translated %d/%d (google fallback)...", done, total)
    return results


def _translate_one_google(text: str, src: str = "en", dest: str = "iw") -> str:
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source=src, target=dest)
        translated = translator.translate(text)
        if not translated or translated == text:
            if _is_latin(text):
                translated = _transliterate_to_hebrew(text)
            else:
                translated = text
        time.sleep(TRANSLATE_DELAY)
        return translated
    except Exception as exc:
        logger.warning("Google translation failed for '%s': %s", text, exc)
        if _is_latin(text):
            return _transliterate_to_hebrew(text)
        return text


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _select_backend(use_google: bool) -> str:
    """Pick the translation backend: 'claude' (default) or 'google' (fallback).

    Phase 3a R2: reads WIKI7_ANTHROPIC_API_KEY first (the operator's billable
    pipeline-only key) and falls back to ANTHROPIC_API_KEY only if the wiki7-
    specific one is unset. Day-to-day Claude Code subscription work that
    only sets ANTHROPIC_API_KEY isn't drained by accident.
    """
    if use_google:
        return "google"
    if _resolve_anthropic_api_key():
        return "claude"
    logger.warning(
        "Neither WIKI7_ANTHROPIC_API_KEY nor ANTHROPIC_API_KEY set — falling "
        "back to Google Translate. For the Phase 3a R2 quality target, export "
        "WIKI7_ANTHROPIC_API_KEY and re-run."
    )
    return "google"


def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _fill_section(
    section: dict,
    category: str,
    backend: str,
    dry_run: bool,
) -> int:
    """Fill empty entries in one category section. Returns the number of
    newly-filled entries (manual entries never overwritten)."""
    empty_keys = [
        key for key, entry in section.items()
        if not _value(entry) or (isinstance(entry, dict) and not entry.get("src"))
    ]
    if not empty_keys:
        logger.info("No empty entries in '%s', skipping", category)
        return 0

    logger.info(
        "Auto-translating %d entries in '%s' via %s...",
        len(empty_keys), category, backend,
    )

    if dry_run:
        return len(empty_keys)

    filled = 0

    # Iteration-cycle phase: Wikidata first-pass for names + clubs +
    # competitions + nationalities. Wikidata returns canonical Hebrew
    # labels with type-aware disambiguation (P31/P641 filters), and
    # covers Israeli-league players the English Wikipedia pass misses.
    # Persisted Q-IDs let later runs skip the search step. See
    # docs/research/0003 for the empirical motivation.
    wd_entity_type = _WIKIDATA_ENTITY_TYPE.get(category)
    if wd_entity_type:
        from data_pipeline.wikidata_lookup import lookup_batch as wd_lookup
        logger.info(
            "  Wikidata lookup pass: %d %s (type=%s)...",
            len(empty_keys), category, wd_entity_type,
        )
        wd_results = wd_lookup(empty_keys, entity_type=wd_entity_type)
        wd_filled = set()
        for key, result in wd_results.items():
            if not result:
                continue
            he, qid = result
            section[key] = {
                "he": he,
                "src": "wikidata",
                "confidence": "high",
                "wikidata_qid": qid,
                "note": "",
            }
            wd_filled.add(key)
            filled += 1
        empty_keys = [k for k in empty_keys if k not in wd_filled]
        logger.info(
            "  Wikidata resolved %d; %d remaining for next backend.",
            len(wd_filled), len(empty_keys),
        )
        if not empty_keys:
            return filled

    # Phase 3a R2: English Wikipedia langlinks pass for the `names`
    # category. Kept as a thin secondary AFTER Wikidata — covers a small
    # tail of cases where Wikidata's type filter rejects a candidate but
    # Wikipedia's `redirects=1` parameter handles a spelling variation.
    # Cheap (zero API cost). If empirical data shows it adds zero on top
    # of Wikidata, remove in a follow-up.
    if category == "names":
        from data_pipeline.wikipedia_lookup import lookup_batch
        logger.info("  Wikipedia langlinks pass: %d names...", len(empty_keys))
        wikipedia_results = lookup_batch(empty_keys)
        wp_filled_keys = set()
        for key, he in wikipedia_results.items():
            if he:
                section[key] = {
                    "he": he,
                    "src": "wikipedia",
                    "confidence": "high",
                    "wikidata_qid": "",
                    "note": "",
                }
                wp_filled_keys.add(key)
                filled += 1
        empty_keys = [k for k in empty_keys if k not in wp_filled_keys]
        logger.info(
            "  Wikipedia resolved %d; %d remaining for %s fallback.",
            len(wp_filled_keys), len(empty_keys), backend,
        )
        if not empty_keys:
            return filled

    if backend == "claude":
        for chunk in _chunked(empty_keys, CLAUDE_BATCH_SIZE):
            try:
                results = _translate_batch_via_claude(category, chunk)
            except Exception as exc:
                logger.error(
                    "Claude batch failed for '%s' (%d items): %s — falling back to Google",
                    category, len(chunk), exc,
                )
                # Per-batch fallback so a single API hiccup doesn't kill the whole
                # category. The reviewer will see `src: auto-google` for those
                # entries and can prioritise them.
                google_results = _translate_batch_via_google(chunk)
                for key, he in zip(chunk, google_results):
                    section[key] = {
                        "he": he, "src": "auto-google", "confidence": "low",
                        "wikidata_qid": "", "note": "",
                    }
                    filled += 1
                continue
            results_by_en = {r.get("en"): r for r in results if r.get("en")}
            for key in chunk:
                r = results_by_en.get(key)
                if not r or not r.get("he"):
                    # Claude omitted this entry from its response — fall back to
                    # transliteration so the file isn't left with an empty slot.
                    fallback = _transliterate_to_hebrew(key) if _is_latin(key) else ""
                    section[key] = {
                        "he": fallback,
                        "src": "auto-translit" if fallback else "",
                        "confidence": "low",
                        "wikidata_qid": "",
                        "note": "Claude did not return a translation; phonetic fallback applied.",
                    }
                    if fallback:
                        filled += 1
                    continue
                section[key] = {
                    "he": r["he"],
                    "src": "auto-llm",
                    "confidence": r.get("confidence", "low"),
                    "wikidata_qid": "",
                    "note": "",
                }
                filled += 1
    else:
        # Google fallback path. All entries come back as `auto-google` /
        # `confidence: low` so the reviewer treats them as needing a pass.
        results = _translate_batch_via_google(empty_keys)
        for key, he in zip(empty_keys, results):
            section[key] = {
                "he": he or "",
                "src": "auto-google" if he else "",
                "confidence": "low",
                "wikidata_qid": "",
                "note": "",
            }
            if he:
                filled += 1

    return filled


def _print_flagged_report(mapping: dict) -> None:
    """Stdout report of every entry currently flagged `confidence: low`.

    Triggered by `--review-flagged-only`. Reviewer scans the focused list
    and edits the corresponding entries in the YAML (or accepts them as-is
    by re-running with the entry's confidence promoted to 'high' manually).
    """
    print()
    print("=" * 70)
    print("Flagged entries (confidence: low) — review and correct as needed:")
    print("=" * 70)
    total = 0
    for category in CATEGORIES:
        section = mapping.get(category, {})
        flagged = [
            (key, entry)
            for key, entry in section.items()
            if isinstance(entry, dict) and entry.get("confidence") == "low"
        ]
        if not flagged:
            continue
        print(f"\n[{category}] — {len(flagged)} flagged")
        for key, entry in flagged:
            src = entry.get("src", "?")
            he = entry.get("he", "")
            print(f"  {src:>13}  {key:40}  →  {he}")
        total += len(flagged)
    print()
    print(f"Total flagged: {total}")
    print("=" * 70)


def auto_translate(
    mapping_path: Path | None = None,
    dry_run: bool = False,
    review_flagged_only: bool = False,
    use_google: bool = False,
) -> dict:
    """Auto-fill empty Hebrew values in the mapping YAML.

    Preserves existing manual entries (marked `src: manual`). Auto-fills only
    entries that are empty or where `src` is unset. Returns a summary dict
    with per-category counts.
    """
    resolved_path = mapping_path or DEFAULT_MAPPING_PATH

    if not resolved_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {resolved_path}")

    with open(resolved_path, "r", encoding="utf-8") as f:
        mapping = yaml.safe_load(f) or {}

    # Phase 3a R2: migrate legacy flat-shape entries to nested on first load.
    # Idempotent — re-running on already-nested files is a no-op.
    mapping, migrated_count = _migrate_mapping(mapping)
    if migrated_count:
        logger.info(
            "Migrated %d legacy flat-shape entries to nested R2 shape "
            "(src: manual, confidence: high — assumed human-curated).",
            migrated_count,
        )

    backend = _select_backend(use_google)

    summary = {cat: 0 for cat in CATEGORIES}
    for category in CATEGORIES:
        section = mapping.setdefault(category, {})
        summary[category] = _fill_section(section, category, backend, dry_run)

    if not dry_run:
        with open(resolved_path, "w", encoding="utf-8") as f:
            yaml.dump(mapping, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        logger.info("Updated mapping saved to %s", resolved_path)

    logger.info(
        "Auto-translation summary (backend=%s): %d positions, %d nationalities, "
        "%d clubs, %d competitions, %d names",
        backend, summary["positions"], summary["nationalities"], summary["clubs"],
        summary["competitions"], summary["names"],
    )

    if review_flagged_only:
        _print_flagged_report(mapping)

    return summary


def main():
    """CLI entry point."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Auto-translate Hebrew mapping values via Claude")
    parser.add_argument(
        "--mapping-path", type=Path, default=None,
        help=f"Path to mappings.he.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--review-flagged-only", action="store_true",
        help="After translation, print a focused report of entries flagged "
             "`confidence: low` so the reviewer can correct them.",
    )
    parser.add_argument(
        "--use-google", action="store_true",
        help="Force the legacy Google Translate path even when ANTHROPIC_API_KEY is set",
    )
    args = parser.parse_args()

    auto_translate(
        mapping_path=args.mapping_path,
        dry_run=args.dry_run,
        review_flagged_only=args.review_flagged_only,
        use_google=args.use_google,
    )


if __name__ == "__main__":
    main()
