#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict
from urllib.parse import SplitResult, urlsplit, urlunsplit

from catalog_format import ALL_FILES, ENTRY_RE, TagVocabulary, parse_tag_pairs


SCHEMA_VERSION: Final = 1


class SourceJson(TypedDict):
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class Source:
    file: str
    line: int

    def label(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True, slots=True)
class CatalogError(Exception):
    source: Source | None
    message: str

    def __str__(self) -> str:
        prefix = f"{self.source.label()}: " if self.source else ""
        return f"{prefix}{self.message}"


def public_mapping(value: dict, where: str) -> dict:
    if not isinstance(value, dict):
        raise CatalogError(None, f"tags.json: {where} must be an object")
    return {key: item for key, item in value.items() if not key.startswith("$")}


def load_tag_values(root: Path) -> TagVocabulary:
    path = root / "tags.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(None, "tags.json: file not found") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(None, f"tags.json:{exc.lineno}: invalid JSON: {exc.msg}") from exc
    axes = public_mapping(raw.get("axes", {}), "axes")
    expected = {"agent", "type"}
    if set(axes) != expected:
        raise CatalogError(None, f"tags.json: axes must be exactly {sorted(expected)}")
    agent = public_mapping(axes["agent"].get("values", {}), "axes.agent.values")
    types = public_mapping(axes["type"].get("values", {}), "axes.type.values")
    return TagVocabulary(frozenset(agent), frozenset(types))


def parse_url(raw: str, source: Source) -> tuple[SplitResult, str, int | None]:
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname or ""
        port = parts.port
    except ValueError as exc:
        raise CatalogError(source, f"invalid URL: {exc}") from None
    return parts, hostname.lower(), port


def normalized_url(raw: str, source: Source) -> tuple[str, dict[str, str] | None]:
    parts, hostname, port = parse_url(raw, source)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise CatalogError(source, f"invalid absolute HTTP(S) URL: {raw}")
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = parts.path.rstrip("/")
    github: dict[str, str] | None = None
    segments = [segment for segment in path.split("/") if segment]
    if hostname == "github.com" and len(segments) >= 2:
        owner = segments[0]
        repo = segments[1][:-4] if segments[1].lower().endswith(".git") else segments[1]
        segments[0:2] = [owner, repo]
        path = "/" + "/".join(segments)
        github = {"owner": owner, "repo": repo}
    normalized = urlunsplit(SplitResult(parts.scheme.lower(), netloc, path, parts.query, ""))
    return normalized.lower(), github


def route_slug(basis: str, source: Source) -> str:
    ascii_name = unicodedata.normalize("NFKD", basis).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise CatalogError(source, "name cannot produce a route slug")
    return slug


def parse_category(root: Path, filename: str, vocabulary: TagVocabulary) -> list[dict]:
    relative = f"categories/{filename}"
    path = root / relative
    lines = path.read_text(encoding="utf-8").splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    if not title:
        raise CatalogError(Source(relative, 1), "missing category title")
    try:
        entries_index = lines.index("## Entries")
    except ValueError as exc:
        raise CatalogError(Source(relative, 1), "missing '## Entries' section") from exc
    projects: list[dict] = []
    for index, line in enumerate(lines[entries_index + 1 :], start=entries_index + 2):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or line.startswith("### "):
            continue
        source = Source(relative, index)
        if not line.startswith("- ["):
            continue
        match = ENTRY_RE.fullmatch(line)
        if match is None:
            raise CatalogError(source, "malformed entry")
        tags, unknown = parse_tag_pairs(match.group("tags") or "", vocabulary)
        if unknown:
            raise CatalogError(source, f"unknown tag(s): {', '.join(unknown)}")
        identity, github = normalized_url(match.group("url"), source)
        identity_parts, _, _ = parse_url(identity, source)
        github_path = "-".join(segment for segment in identity_parts.path.split("/") if segment)
        slug_basis = github_path if github is not None else match.group("name")
        project = {
            "id": identity,
            "slug": route_slug(slug_basis, source),
            "name": match.group("name"),
            "url": match.group("url"),
            "category": {"key": Path(filename).stem, "title": title},
            "summary": match.group("summary"),
            "tags": tags,
            "source": {"file": source.file, "line": source.line},
        }
        if github is not None:
            project["github"] = github
        projects.append(project)
    return projects


def build_catalog(root: Path, categories: tuple[str, ...]) -> bytes:
    vocabulary = load_tag_values(root)
    projects = [
        project
        for filename in categories
        for project in parse_category(root, filename, vocabulary)
    ]
    urls: dict[str, SourceJson] = {}
    slugs: dict[str, SourceJson] = {}
    for project in projects:
        source = project["source"]
        for value, seen, label in (
            (project["id"], urls, "duplicate normalized URL"),
            (project["slug"], slugs, "slug collision"),
        ):
            previous = seen.get(value)
            if previous is not None:
                current = Source(source["file"], source["line"])
                first = f"{previous['file']}:{previous['line']}"
                raise CatalogError(current, f"{label} {value!r}; first declared at {first}")
            seen[value] = source
    projects.sort(key=lambda project: (project["slug"], project["id"]))
    payload = {"schemaVersion": SCHEMA_VERSION, "projects": projects}
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the deterministic awesome-jev catalog")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output or args.root / "catalog.json"
    categories = tuple(args.categories) if args.categories else ALL_FILES
    try:
        generated = build_catalog(args.root, categories)
        if args.check:
            if not output.exists() or output.read_bytes() != generated:
                raise CatalogError(None, f"{output.name} is out of date; run scripts/export-catalog.py")
        else:
            output.write_bytes(generated)
    except (CatalogError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
