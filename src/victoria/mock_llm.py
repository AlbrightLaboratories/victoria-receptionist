"""Deterministic mock LLM for local development.

When `MODE=local`, the conversation engine routes generation requests
here instead of Triton. The mock returns canned-but-plausible replies
so the widget renders end-to-end without GPU or network.
"""
from __future__ import annotations

import re
from typing import Iterable


_CANNED = {
    r"\b(hi|hello|hey|greetings)\b": (
        "Hi, I'm Victoria Albright — welcome to Albright Laboratories. "
        "How can I help you today? You can ask about our ventures, careers, "
        "partnerships, or how to reach the team."
    ),
    r"\b(career|job|hir(?:e|ing)|apply|position|role)\b": (
        "We're actively hiring across executive, engineering, federal, and "
        "venture-lead roles. The full list lives at /careers/. For specific "
        "questions, email coreymalbright@gmail.com."
    ),
    r"\b(partner|partnership|teaming|reseller|affiliate)\b": (
        "Our Partners page lists six tracks — Federal Prime, Reseller, "
        "Technology, Academic, Capital, and Affiliate. You can submit an "
        "intake at /partners/ or call (202) 642-6739."
    ),
    r"\b(brightflow|trading|quant|algo)\b": (
        "BrightFlow is Albright's algorithmic-trading platform. We run a "
        "research surface (brightflow.albrightlab.com) and a live execution "
        "stack. For demos, email coreymalbright@gmail.com."
    ),
    r"\b(contact|phone|email|reach|talk to)\b": (
        "You can reach the team directly: phone (202) 642-6739 or email "
        "coreymalbright@gmail.com. I'll also flag your message for follow-up."
    ),
    r"\b(price|pricing|cost|how much|quote)\b": (
        "Pricing varies by engagement scope. I'm not able to quote figures, "
        "but Corey can — please email coreymalbright@gmail.com or call "
        "(202) 642-6739."
    ),
}

_DEFAULT = (
    "I'm Victoria, the Albright Laboratories receptionist. I don't have "
    "a confident answer for that yet — I'll flag this so our team can "
    "document it. In the meantime, please email coreymalbright@gmail.com "
    "or call (202) 642-6739."
)


def mock_generate(user_message: str) -> str:
    """Return a canned reply that pattern-matches the user's question.

    Picks the first regex that matches. Falls back to the polite-escalation
    default if nothing matches. Used in `MODE=local` only.
    """
    low = user_message.lower()
    for pattern, reply in _CANNED.items():
        if re.search(pattern, low):
            return reply
    return _DEFAULT


def mock_rebrand(draft: str, sources: Iterable[str]) -> str:
    """Stand-in for the rebrand pass — preserves the draft, appends sources."""
    src_list = ", ".join(sources)
    suffix = f"\n\nSource: {src_list}" if src_list else ""
    return f"{draft.strip()}{suffix}"
