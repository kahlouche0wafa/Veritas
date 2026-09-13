"""Demo catalog: subjects and sites the agent knows how to resolve.

The LLM's normalise step can pick a subject_key or site_key from these lists
but never invents coordinates or phone numbers on its own — those come from
this catalog, deterministically. That's what keeps free-text intake safe:
the LLM's role is entity linking, not data fabrication.

Add subjects and sites here to expand the demo surface. Everything else
(verdict rules, storage, API) works unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectRecord:
    subject_id: str
    phone: str
    display_name: str
    aliases: tuple[str, ...] = ()   # names / nicknames the LLM might see


@dataclass(frozen=True)
class SiteRecord:
    key: str
    label: str
    lat: float
    lon: float
    radius_m: float
    aliases: tuple[str, ...] = ()


# The simulator's fixed device location.
SIM_LAT, SIM_LON = 47.486276, 19.079156


SUBJECTS: dict[str, SubjectRecord] = {
    "worker-001": SubjectRecord(
        subject_id="worker-001", phone="+99999991000",
        display_name="Ahmed Reda", aliases=("Ahmed", "Reda", "worker 1", "worker one"),
    ),
    "worker-002": SubjectRecord(
        subject_id="worker-002", phone="+99999991001",
        display_name="Sara Malik", aliases=("Sara", "Malik", "worker 2", "worker two"),
    ),
    "worker-003": SubjectRecord(
        subject_id="worker-003", phone="+99999991003",
        display_name="Omar Nabil", aliases=("Omar", "Nabil", "worker 3", "worker three"),
    ),
    "worker-004": SubjectRecord(
        subject_id="worker-004", phone="+99999991002",
        display_name="Layla Hassan", aliases=("Layla", "Hassan", "worker 4", "worker four"),
    ),
}


SITES: dict[str, SiteRecord] = {
    "budapest_hq": SiteRecord(
        key="budapest_hq", label="Budapest HQ",
        lat=SIM_LAT, lon=SIM_LON, radius_m=500.0,
        aliases=("HQ", "headquarters", "Budapest", "Budapest office", "the office"),
    ),
    "riyadh_a": SiteRecord(
        key="riyadh_a", label="Riyadh Site A",
        lat=24.7136, lon=46.6753, radius_m=500.0,
        aliases=("Riyadh", "Site A", "the Riyadh site", "KSA site A"),
    ),
    "cairo_b": SiteRecord(
        key="cairo_b", label="Cairo Site B",
        lat=30.0444, lon=31.2357, radius_m=500.0,
        aliases=("Cairo", "Site B", "the Cairo site", "Egypt site"),
    ),
}


def catalog_summary_for_prompt() -> str:
    """Compact catalog description injected into the normalise prompt.

    Kept small on purpose — the LLM sees keys, display names, and aliases,
    but never coordinates. The Python side maps the chosen key back to
    the full record.
    """
    subject_lines = [
        f"  - {s.subject_id}: {s.display_name}, phone={s.phone} "
        f"(aliases: {', '.join(s.aliases) or '—'})"
        for s in SUBJECTS.values()
    ]
    site_lines = [
        f"  - {s.key}: {s.label} (aliases: {', '.join(s.aliases) or '—'})"
        for s in SITES.values()
    ]
    return (
        "Known subjects:\n" + "\n".join(subject_lines)
        + "\n\nKnown sites:\n" + "\n".join(site_lines)
    )


import re as _re


def _word_bounded_match(needle: str, haystack: str) -> bool:
    """True iff `needle` appears in `haystack` at word boundaries, case-
    insensitive. Prevents 'Site A' from matching into 'site at'.
    """
    pat = r"\b" + _re.escape(needle) + r"\b"
    return _re.search(pat, haystack, flags=_re.IGNORECASE) is not None


def find_subject_by_alias(text: str) -> SubjectRecord | None:
    """Cheap deterministic lookup used as a fallback when the LLM is
    unreachable, so the normalise step is never fully blocked.

    Also matches on the subject's phone number verbatim — claims like
    "Verify the person using phone +99999991004" resolve to worker-004
    without needing the LLM to reason about it.
    """
    for s in SUBJECTS.values():
        # Direct phone match — no word boundary since '+' isn't a word char.
        if s.phone and s.phone in text:
            return s
        for candidate in (s.display_name, s.subject_id, *s.aliases):
            if _word_bounded_match(candidate, text):
                return s
    return None


def find_site_by_alias(text: str) -> SiteRecord | None:
    for s in SITES.values():
        for candidate in (s.label, s.key, *s.aliases):
            if _word_bounded_match(candidate, text):
                return s
    return None
