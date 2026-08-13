"""Company names as a person writes them, not as they are incorporated.

Yahoo returns the full legal name -- "Applied Materials, Inc.", "ASML Holding
N.V. - New York Registry Shares". The form of incorporation is the same noise
on every row, and it is what pushes the actual name out of a column or stops a
headline matching the company it is about.

Shared because two callers need the same answer: the spending tables print
these in a fixed column, and the news filter asks whether a headline mentions
the company at all.
"""

from __future__ import annotations

import re
from typing import Optional

LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:incorporated|inc|corporation|corp|company|limited|ltd|plc|co|"
    r"l\.?\s*l\.?\s*c|n\.?\s*v|s\.?\s*a|a\.?\s*g|s\.?\s*p\.?\s*a)\.?$",
    re.IGNORECASE,
)
# "Eli Lilly and Company" loses its suffix and is left hanging on the "and".
DANGLING_RE = re.compile(r"[,\s]+(?:and|&)$", re.IGNORECASE)


def strip_legal_form(name: Optional[str]) -> str:
    """"Applied Materials, Inc." -> "Applied Materials"."""
    # "... - New York Registry Shares" and the like hang off a dash.
    text = (name or "").split(" - ")[0].strip()
    # "Holding N.V." sheds one suffix per pass; "Inc." only needs the one.
    for _ in range(3):
        shorter = LEGAL_SUFFIX_RE.sub("", text).strip()
        if shorter == text or not shorter:
            break
        text = shorter
    return DANGLING_RE.sub("", text) or text


def clip(text: Optional[str], width: int) -> str:
    """Any text cut to a width without breaking a word in half.

    Nothing to do with company names -- headlines and publishers want the same
    treatment and would be mangled by having a legal form stripped off them.
    """
    text = (text or "").strip()
    if len(text) <= width:
        return text
    cut = text[:width]
    # A cut that already lands on a boundary keeps its last word rather than
    # dropping it; one that does not falls back to the previous space.
    if text[width] != " ":
        cut = cut.rsplit(" ", 1)[0] or text[:width]
    return cut.rstrip(",")


def shorten(name: Optional[str], width: int) -> str:
    """A company name with the legal form dropped, cut to fit a column."""
    return clip(strip_legal_form(name), width)
