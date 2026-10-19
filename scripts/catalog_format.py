from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


ENTRY_RE: Final = re.compile(
    r"^(?P<head>- \[(?P<name>[^\]]+)\]\((?P<url>[^)]+)\))"
    r"(?:\s*`\{(?P<tags>[^}]*)\}`)?\s*-\s*(?P<summary>.+)$"
)

CATEGORIES: Final = (
    "classification-routing.md",
    "adaptive-realtime-ui.md",
    "verification-guardrails.md",
    "scoring-ranking.md",
    "agent-decisions.md",
    "data-labeling-curation.md",
    "evaluation-benchmarking.md",
    "calibration-research.md",
    "infra-sdks-integrations.md",
    "game-simulation.md",
    "robotics-physical.md",
    "finance-trading.md",
    "compliance-legal.md",
    "content-moderation.md",
    "related-practices-discussions.md",
)
OPEN_TRACKING: Final = ("scientific-pipelines.md",)
ALL_FILES: Final = CATEGORIES + OPEN_TRACKING


@dataclass(frozen=True, slots=True)
class TagVocabulary:
    agent_values: frozenset[str]
    type_values: frozenset[str]


def parse_tag_pairs(raw: str, vocabulary: TagVocabulary) -> tuple[dict[str, str], list[str]]:
    tags: dict[str, str] = {}
    unknown: list[str] = []
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            unknown.append(pair)
            continue
        kind, value = (part.strip().lower() for part in pair.split(":", 1))
        if kind == "agent" and value in vocabulary.agent_values:
            tags["agent"] = value
        elif kind == "type" and value in vocabulary.type_values:
            tags["type"] = value
        else:
            unknown.append(f"{kind}: {value}")
    return tags, unknown
