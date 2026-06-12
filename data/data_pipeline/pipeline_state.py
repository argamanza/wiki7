"""TM-ID → wiki-page identity state file.

The pipeline writes wiki pages identified by TM player ID. The Hebrew title
those pages live at can change between runs — reviewer-driven YAML overrides
update the canonical Hebrew name; reviewer-initiated MovePages promote drafts
to mainspace or rename them mid-review; Wikidata's sitelinks-first fallback
can flip a previously-Claude-translated name to a hewiki-curated one.

Without a state file, every such rename creates a duplicate or orphan:
- Bot generates `Draft:הלדר לפופסיק` (per Wikidata's stale label).
- Reviewer notices the gibberish, edits mappings.he.yaml + `src: manual` +
  he: `הלדר לופש`. Or just MovePages it directly to `Draft:הלדר לופש`.
- Next bot run still computes the title from mapping → emits `Draft:הלדר לופש`.
- But the *previous* draft was at `Draft:הלדר לפופסיק` — it's now orphaned.
- Worse: if the reviewer MovePaged a draft to mainspace `Foo`, the bot
  doesn't know that, and tries to create `Draft:Foo` again — duplicate.

The state file makes the pipeline's mental model match wiki reality. For
each TM ID we record the page's current actual title and namespace. On the
next run, if the pipeline's generated title differs from what's recorded,
we MovePage the existing page rather than writing a fresh duplicate.

The file is git-ignored (per-environment, since local docker and prod will
diverge as the reviewer promotes pages on prod but not local). Lives under
`pipeline-state/page_index.yaml` relative to the repo root.

Schema:
    "912586":              # TM player ID (string, since URLs are strings)
      he_title: "ניב אליאסי"
      namespace: 3000      # 0 = mainspace, 3000 = NS_DRAFT, 10 = NS_TEMPLATE
      last_seen: "2026-06-12T10:30:00Z"

Iter-cycle 1 (2026-06-12): shipped as Pattern A of the v1+ re-import
architecture. See `[[wiki7-bot-write-strategy]]` memory for the design
discussion + the orphan/duplicate failure modes it prevents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# Pipeline-state directory holds per-environment, non-versioned state that
# changes from run to run (page_index.yaml, scrape_hashes.yaml, etc.).
DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / "pipeline-state"
DEFAULT_PAGE_INDEX_PATH = DEFAULT_STATE_DIR / "page_index.yaml"


def _now_iso() -> str:
    """ISO 8601 UTC timestamp (no microseconds, with Z suffix). Used as
    `last_seen` in state records so re-reading is human-friendly."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PageIndexState:
    """In-memory view of pipeline-state/page_index.yaml.

    Use `.load()` at pipeline start, `.upsert()` per-page during the run,
    and `.save()` at the end (idempotent if nothing changed).

    The file is created if missing — first-ever runs start with an empty
    index and populate as they write pages.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_PAGE_INDEX_PATH
        self._data: dict[str, dict] = {}
        self._dirty = False

    def load(self) -> "PageIndexState":
        """Load the YAML file. Returns self for chaining. Tolerates missing
        file (treats as empty index)."""
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                if not isinstance(loaded, dict):
                    logger.warning(
                        "Page index file %s contains non-dict root; ignoring",
                        self.path,
                    )
                    loaded = {}
                self._data = {str(k): v for k, v in loaded.items()}
            except (yaml.YAMLError, OSError) as exc:
                logger.warning(
                    "Failed to load page index from %s: %s. Starting fresh.",
                    self.path, exc,
                )
                self._data = {}
        return self

    def save(self) -> None:
        """Persist the YAML file if any upserts changed anything. Idempotent
        if no changes were made since load.

        §6 medium fix (2026-06-12 review): write atomically via tmp + rename.
        Pre-fix the file was opened in `"w"` mode and truncated immediately,
        so an interruption mid-write left a partial / empty file on disk.
        Next run loaded the partial file (or hit a YAML parse error,
        triggered the "start fresh" path) and the duplicate-draft problem
        the state file exists to prevent came back. The tmp + os.replace
        sequence is atomic on POSIX — readers either see the old file or
        the new file, never a partial.
        """
        if not self._dirty:
            logger.debug("Page index clean; skipping save")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # sort_keys=True so diffs are stable across runs
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(
                self._data, f,
                allow_unicode=True,
                sort_keys=True,
                default_flow_style=False,
            )
            f.flush()
            try:
                import os
                os.fsync(f.fileno())
            except OSError:
                # Non-POSIX filesystem (e.g. some bind-mounted FUSE) may
                # refuse fsync — accept the slightly weaker durability.
                pass
        # Atomic on POSIX (and on Windows via Python 3.3+'s os.replace).
        import os
        os.replace(tmp_path, self.path)
        logger.info(
            "Saved page index: %d entries -> %s",
            len(self._data), self.path,
        )
        self._dirty = False

    def get(self, tm_id: str | int) -> Optional[dict]:
        """Return the stored {he_title, namespace, last_seen} dict for this
        TM ID, or None if never seen."""
        return self._data.get(str(tm_id))

    def upsert(self, tm_id: str | int, he_title: str, namespace: int) -> Optional[dict]:
        """Record this TM ID's current wiki page identity.

        Returns the PREVIOUS record (or None if first time), so callers can
        detect drift and trigger MovePage as needed.
        """
        tm_id = str(tm_id)
        if not tm_id:
            raise ValueError("TM ID cannot be empty")
        previous = self._data.get(tm_id)
        # Strip namespace prefixes from he_title — we store the bare title +
        # namespace as separate fields so consumers can reconstruct full
        # title (e.g. "Draft:X" or just "X") without parsing.
        normalised_title = he_title
        if ":" in he_title:
            # Could be a namespace prefix (Draft:X) or a Hebrew name with a
            # colon (rare but possible). Heuristic: if the prefix matches a
            # known namespace label, strip; otherwise leave alone.
            for ns_label in ("Draft", "Template", "User", "File", "Category"):
                if he_title.startswith(f"{ns_label}:"):
                    normalised_title = he_title.split(":", 1)[1]
                    break
        new_record = {
            "he_title": normalised_title,
            "namespace": int(namespace),
            "last_seen": _now_iso(),
        }
        # Detect actual change to avoid touching the file when nothing
        # meaningful drifted (only last_seen differs).
        if previous:
            prev_filtered = {k: v for k, v in previous.items() if k != "last_seen"}
            new_filtered = {k: v for k, v in new_record.items() if k != "last_seen"}
            if prev_filtered == new_filtered:
                # Still update last_seen, but don't mark dirty unless title or
                # namespace changed. Trade-off: state file's last_seen drifts
                # over time but the file rewrite cost on every run is high if
                # we treat every touch as dirty. Skip the touch for no-op runs.
                return previous
        self._data[tm_id] = new_record
        self._dirty = True
        return previous

    def remove(self, tm_id: str | int) -> None:
        """Drop an entry (e.g. when its page has been deleted)."""
        tm_id = str(tm_id)
        if tm_id in self._data:
            del self._data[tm_id]
            self._dirty = True

    def all_ids(self) -> list[str]:
        return sorted(self._data.keys())

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, tm_id: str | int) -> bool:
        return str(tm_id) in self._data
