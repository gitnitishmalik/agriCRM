"""
Duplicate detection for organisations, at creation time.

The exit gate for Phase 1 is not "we can find duplicates later" — it is
"duplicate blocking demonstrably prevents a duplicate on the create form"
(Doc 15). Catching them afterwards means a merge, and a merge means deciding
which of two half-filled records is the real one, usually months later, using
a field agent's memory.

**Why not plain trigram similarity on the raw name.** Indian organisation names
defeat ordered-string comparison in three specific ways:

  1. *Suffix noise.* "Kisan Unnati Farmer Producer Company Limited" and
     "Kisan Unnati FPC Ltd." share almost no characters in their second half
     and are the same organisation. The legal form is not identifying
     information; every FPO in the country ends in one of about a dozen
     suffixes.
  2. *Token order.* "Sahkari Ganna Vikas Samiti Bijnor" versus "Bijnor Sahkari
     Ganna Vikas Samiti". Same body, different order, poor ordered ratio.
  3. *Transliteration.* Chaudhary / Chaudhri / Choudhary, Samiti / Samithi,
     Krishi / Krushi. One Devanagari spelling, several Latin ones, and no
     authority deciding between them.

So trigram does what it is good at — an index-backed prefilter that narrows
600 candidates to 20 — and the score that decides is a token-set Dice
coefficient with fuzzy token equality, computed here. That handles all three.

**Why the block is scoped to a district.** "Kisan Seva Kendra" is a real name
in most states, several times over, and they are genuinely different
organisations. A national name match is noise; a name match inside one
district is nearly always the same body. Scope falls back to state when the
district is unknown, and to nothing at all when neither is — an unplaced
organisation gets a warning, not a block, because we have no basis for one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.contrib.postgres.search import TrigramSimilarity

#: Legal form and honorific tokens that carry no identifying information.
#: Stripped before comparison so "FPC Ltd" and "Farmer Producer Company
#: Limited" reduce to the same thing. Devanagari equivalents included because
#: `name_local` is compared with the same function.
NOISE_TOKENS = frozenset(
    {
        # English legal forms and abbreviations
        "ltd",
        "limited",
        "pvt",
        "private",
        "public",
        "company",
        "co",
        "corporation",
        "corp",
        "producer",
        "farmer",
        "farmers",
        "fpc",
        "fpo",
        "cpc",
        "llp",
        "inc",
        "society",
        "societies",
        "cooperative",
        "co-operative",
        "coop",
        "cooperatives",
        "sahakari",
        "sahkari",
        "samiti",
        "samithi",
        "sangh",
        "sangha",
        "union",
        "federation",
        "association",
        "trust",
        "foundation",
        "mills",
        "mill",
        "sugar",
        "industries",
        "industry",
        "unit",
        "works",
        "factory",
        "the",
        "and",
        "of",
        "&",
        # Devanagari
        "लिमिटेड",
        "प्राइवेट",
        "सहकारी",
        "समिति",
        "संघ",
        "किसान",
        "उत्पादक",
    }
)

#: Two tokens this close count as the same word. Tuned for transliteration
#: drift (chaudhary/chaudhri = 0.86, krishi/krushi = 0.83) without collapsing
#: genuinely different words (bijnor/bijapur = 0.62).
TOKEN_MATCH_RATIO = 0.82

#: Above this, the create form refuses to save without an explicit override.
#: Doc 15 sets it; it is here so the admin, the API and the importer cannot
#: quietly disagree about what "duplicate" means.
BLOCK_THRESHOLD = 0.6

#: The trigram prefilter, used only when the search is not district-scoped.
#:
#: Its job is to get a candidate set into memory cheaply, never to decide
#: anything — and it is bad at deciding, which is the whole reason this module
#: exists. "Kisan Unnati FPC Ltd" against "Kisan Unnati Farmer Producer Company
#: Limited" scores about 0.27 on raw trigrams, because the suffix dominates the
#: character count. Anything above roughly 0.35 would therefore drop real
#: duplicates before the Dice score ever sees them.
PREFILTER_THRESHOLD = 0.15

#: How many prefiltered candidates to re-score. Beyond this the tail is noise.
CANDIDATE_LIMIT = 25


@dataclass(frozen=True)
class DuplicateCandidate:
    """A possible duplicate, with the score that made it one."""

    organisation: object  # Organisation; annotated, not imported, to stay import-cycle free
    score: float

    @property
    def blocks(self) -> bool:
        return self.score >= BLOCK_THRESHOLD


def normalise_name(name: str) -> list[str]:
    """
    Reduce an organisation name to its identifying tokens.

    Case-folded, accent-stripped, punctuation removed, legal-form and
    honorific tokens dropped. Returns tokens rather than a string because
    every comparison downstream is set-based, and rejoining them would only
    invite someone to compare the strings.
    """
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    # Drop combining marks, but only for scripts where they are diacritics.
    # Devanagari matras are combining characters that carry meaning, so the
    # range check matters: stripping them would turn को into क.
    stripped = "".join(
        ch for ch in decomposed if not (unicodedata.combining(ch) and ord(ch) < 0x0900)
    )
    words = re.split(r"[^\w]+", stripped, flags=re.UNICODE)
    return [w for w in words if w and w not in NOISE_TOKENS]


def _tokens_match(a: str, b: str) -> bool:
    if a == b:
        return True
    # Length gate first: SequenceMatcher on wildly different lengths cannot
    # clear the ratio anyway, and this runs 25 x n x m times per keystroke.
    if abs(len(a) - len(b)) > 3:
        return False
    return SequenceMatcher(None, a, b).ratio() >= TOKEN_MATCH_RATIO


def name_similarity(left: str, right: str) -> float:
    """
    Token-set Dice coefficient with fuzzy token equality, in [0, 1].

    Dice rather than Jaccard because Jaccard punishes an extra descriptive
    word harder than it deserves: "Bijnor Ganna Vikas" against "Bijnor Ganna
    Vikas Kendra" is 0.75 Jaccard but 0.86 Dice, and it is the same body.
    """
    left_tokens = normalise_name(left)
    right_tokens = normalise_name(right)
    if not left_tokens or not right_tokens:
        return 0.0

    unmatched = list(right_tokens)
    matched = 0
    for token in left_tokens:
        for index, candidate in enumerate(unmatched):
            if _tokens_match(token, candidate):
                matched += 1
                unmatched.pop(index)
                break

    return (2 * matched) / (len(left_tokens) + len(right_tokens))


def find_duplicates(
    name: str,
    *,
    district_id: int | None = None,
    state_id: int | None = None,
    exclude_id=None,
    threshold: float = BLOCK_THRESHOLD,
) -> list[DuplicateCandidate]:
    """
    Candidates for `name` within its geography, best match first.

    Returns everything at or above `threshold`. An empty list means nothing
    similar enough exists in scope — not that nothing similar exists anywhere,
    which is a different and much less useful claim.
    """
    from .models import Organisation  # local: models imports nothing from here

    if not name or not name.strip():
        return []

    queryset = Organisation.live.all()
    district_scoped = district_id is not None
    if district_scoped:
        queryset = queryset.filter(district_id=district_id)
    elif state_id is not None:
        queryset = queryset.filter(state_id=state_id)
    if exclude_id is not None:
        queryset = queryset.exclude(pk=exclude_id)

    queryset = queryset.select_related("district", "state")

    if district_scoped:
        # A district holds hundreds of organisations, not hundreds of
        # thousands. Score all of them rather than let the prefilter — which
        # is exactly the comparison this module exists to replace — decide
        # what the real scorer is allowed to see.
        candidates = queryset.order_by("name")[: CANDIDATE_LIMIT * 40]
    else:
        candidates = (
            queryset.annotate(trigram=TrigramSimilarity("name", name))
            .filter(trigram__gte=PREFILTER_THRESHOLD)
            .order_by("-trigram")[:CANDIDATE_LIMIT]
        )

    scored = [
        DuplicateCandidate(organisation=org, score=name_similarity(name, org.name))
        for org in candidates
    ]
    return sorted((c for c in scored if c.score >= threshold), key=lambda c: c.score, reverse=True)
