#!/usr/bin/env python3
"""ANI-RSS helper for the ani-rss-auto-subscribe Agent Skill."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


LOCAL_KEY_FILE = "ani-rss-key.txt"
LOCAL_CONFIG_FILE = "ani-rss-config.local.json"
SKILL_ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("mikan", "ani-bt", "anime-garden")
SOURCE_LABELS = {"mikan": "Mikan", "ani-bt": "AniBT", "anime-garden": "AnimeGarden"}
DEFAULT_TIMEOUT = 60
EPISODE_RANGE_LIMIT = 200
CURRENT_RESULT_FILE: Path | None = None
PATCHABLE_ANI_FIELDS = {
    "match",
    "exclude",
    "appendMatch",
    "appendExclude",
    "standbyRssList",
    "releaseDate",
    "season",
    "offset",
    "totalEpisodeNumber",
    "customEpisode",
    "customEpisodeStr",
    "customEpisodeGroupIndex",
    "tmdb",
    "themoviedbName",
}
STANDBY_RSS_FIELDS = {"label", "url", "offset"}
CONFIRMATION_ANI_FIELDS = (
    "title",
    "season",
    "offset",
    "releaseDate",
    "totalEpisodeNumber",
    "match",
    "exclude",
    "standbyRssList",
    "themoviedbName",
    "customEpisode",
    "customEpisodeStr",
    "customEpisodeGroupIndex",
    "enable",
    "ova",
)
BATCH_KEYWORD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("keyword:合集", re.compile(r"合集")),
    ("keyword:全集", re.compile(r"全集")),
    ("keyword:全季", re.compile(r"全季")),
    ("keyword:season pack", re.compile(r"(?i)\bseason\s+pack\b")),
    ("keyword:complete season", re.compile(r"(?i)\bcomplete\s+season\b")),
    ("keyword:complete series", re.compile(r"(?i)\bcomplete\s+series\b")),
    ("keyword:batch", re.compile(r"(?i)\bbatch\b")),
]
EPISODE_RANGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "pattern:SxxExx-Eyy",
        re.compile(r"(?i)\bS\d{1,2}E(?P<start>\d{1,3})\s*[-~]\s*E?(?P<end>\d{1,3})\b"),
    ),
    (
        "pattern:EPxx-yy",
        re.compile(r"(?i)\b(?:EP?|E)\s*(?P<start>\d{1,3})\s*[-~]\s*(?P<end>\d{1,3})\b"),
    ),
    (
        "pattern:第xx-yy话",
        re.compile(r"第\s*(?P<start>\d{1,3})\s*[-~]\s*(?P<end>\d{1,3})\s*[话集]"),
    ),
    (
        "pattern:[xx-yy]",
        re.compile(r"[\[(](?P<start>\d{1,3})\s*[-~]\s*(?P<end>\d{1,3})[\])]")
    ),
]


class AniRssError(Exception):
    """User-facing ANI-RSS helper error."""


def fail(message: str, exit_code: int = 1) -> None:
    print_json({"ok": False, "error": message, "exit_code": exit_code})
    print(f"ANI-RSS error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)


def print_json(value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=True, indent=2)
    print(rendered, flush=True)
    if CURRENT_RESULT_FILE is None:
        return
    target = CURRENT_RESULT_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(rendered + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        print(
            f"ANI-RSS result file write failed ({target}): {exc}",
            file=sys.stderr,
            flush=True,
        )


def json_sha256(value: Any) -> str:
    """Return a stable digest for binding evidence to an exact JSON object."""
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def confirmation_url(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        netloc = parsed.netloc
        if parsed.username is not None or parsed.password is not None:
            hostname = parsed.hostname or ""
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            netloc = f"[redacted]@{hostname}{port}"
        if not parsed.query:
            return urllib.parse.urlunsplit(
                parsed._replace(
                    netloc=netloc,
                    fragment="[redacted]" if parsed.fragment else "",
                )
            )
        sensitive = re.compile(
            r"token|key|pass|secret|auth|signature|credential", re.IGNORECASE
        )
        query = urllib.parse.urlencode(
            [
                (name, "[redacted]" if sensitive.search(name) else item)
                for name, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        return urllib.parse.urlunsplit(
            parsed._replace(
                netloc=netloc,
                query=query,
                fragment="[redacted]" if parsed.fragment else "",
            )
        )
    except ValueError:
        return "[URL omitted]"


def confirmation_summary(ani: dict[str, Any]) -> dict[str, Any]:
    """Return the exact Ani values that must be shown before a write."""
    tmdb = ani.get("tmdb")
    tmdb_summary = None
    if isinstance(tmdb, dict):
        tmdb_summary = {
            key: tmdb.get(key)
            for key in ("id", "name", "originalName", "tmdbType")
            if key in tmdb
        }
    fields = {key: ani.get(key) for key in CONFIRMATION_ANI_FIELDS}
    standby_rss = fields.get("standbyRssList")
    if isinstance(standby_rss, list):
        fields["standbyRssList"] = [
            {**entry, "url": confirmation_url(entry.get("url"))}
            if isinstance(entry, dict)
            else entry
            for entry in standby_rss
        ]
    return {
        "source": {
            "subgroup": ani.get("subgroup"),
            "url": confirmation_url(ani.get("url")),
            "type": ani.get("type"),
        },
        "fields": {
            **fields,
            "tmdb": tmdb_summary,
        },
    }


def compare_confirmation_fields(
    submitted: dict[str, Any], persisted: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    submitted_summary = confirmation_summary(submitted)
    persisted_summary = confirmation_summary(persisted)
    comparisons: dict[str, dict[str, Any]] = {}
    for section in ("source", "fields"):
        for field, submitted_value in submitted_summary[section].items():
            persisted_value = persisted_summary[section].get(field)
            comparisons[field] = {
                "submitted": submitted_value,
                "persisted": persisted_value,
                "matches": submitted_value == persisted_value,
            }
    return comparisons


def read_key_from_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AniRssError(f"Cannot read API key file: {path}: {exc}") from exc


def local_config_path() -> Path:
    explicit = os.environ.get("ANI_RSS_CONFIG_FILE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    path = Path(LOCAL_CONFIG_FILE)
    return path.resolve() if path.exists() else SKILL_ROOT / LOCAL_CONFIG_FILE


def load_local_config() -> dict[str, Any]:
    path = local_config_path()
    if not path.exists():
        if os.environ.get("ANI_RSS_CONFIG_FILE"):
            raise AniRssError("ANI_RSS_CONFIG_FILE does not exist")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AniRssError(f"Cannot read local config {LOCAL_CONFIG_FILE}: {exc}") from exc
    if not isinstance(value, dict):
        raise AniRssError(f"Local config {LOCAL_CONFIG_FILE} must contain a JSON object.")
    return value


def resolve_config(args: argparse.Namespace) -> tuple[str, str]:
    local_config = load_local_config()
    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("ANI_RSS_BASE_URL")
        or local_config.get("base_url")
    )
    if not base_url:
        raise AniRssError(
            "Missing ANI-RSS base URL. Configure ani-rss-config.local.json, "
            "set ANI_RSS_BASE_URL, or pass --base-url."
        )
    base_url = base_url.rstrip("/")

    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("ANI_RSS_API_KEY")
        or local_config.get("api_key")
    )
    key_file = (
        getattr(args, "api_key_file", None)
        or os.environ.get("ANI_RSS_API_KEY_FILE")
        or local_config.get("api_key_file")
        or (str(Path(LOCAL_KEY_FILE).resolve()) if Path(LOCAL_KEY_FILE).exists() else None)
        or (str(SKILL_ROOT / LOCAL_KEY_FILE) if (SKILL_ROOT / LOCAL_KEY_FILE).exists() else None)
    )
    if key_file and not getattr(args, "api_key_file", None) and not os.environ.get("ANI_RSS_API_KEY_FILE"):
        key_file = str(local_config_path().parent / key_file)
    if not api_key and key_file:
        api_key = read_key_from_file(key_file)
    if not api_key:
        raise AniRssError(
            "Missing ANI-RSS API key. Set ANI_RSS_API_KEY, "
            "ANI_RSS_API_KEY_FILE, or create ani-rss-key.txt."
        )
    return base_url, api_key


def request_json(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: Any | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    data = None
    headers = {"api-key": api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise AniRssError(f"HTTP {exc.code} from ANI-RSS: {raw}") from exc
    except urllib.error.URLError as exc:
        raise AniRssError(f"Cannot connect to ANI-RSS at {base_url}: {exc}") from exc
    except TimeoutError as exc:
        raise AniRssError(f"ANI-RSS request timed out after {timeout}s") from exc

    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise AniRssError(f"ANI-RSS returned non-JSON response: {raw[:200]}") from exc

    if isinstance(parsed, dict) and parsed.get("code") not in (None, 200):
        raise AniRssError(
            f"ANI-RSS returned code={parsed.get('code')}: {parsed.get('message')}"
        )
    return parsed


def season_body(args: argparse.Namespace) -> dict[str, Any]:
    year = getattr(args, "year", None)
    season = getattr(args, "season", None)
    if not year and not season:
        return {}
    body: dict[str, Any] = {"select": True}
    if year:
        body["year"] = year
    if season:
        body["season"] = season
    if year and season:
        body["seasonLabel"] = f"{year}年{season}"
    return body


def unwrap_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response.get("data")
    return response


def flatten_mikan_items(data: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for week in ensure_list(data.get("weeks")):
        week_label = week.get("weekLabel") if isinstance(week, dict) else None
        for item in ensure_list(week.get("items") if isinstance(week, dict) else None):
            if isinstance(item, dict):
                normalized = normalize_mikan_candidate(item)
                normalized["week_label"] = week_label
                items.append(normalized)
    return items


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def bgm_id_from_url(value: Any) -> str | None:
    match = re.search(r"/subject/(\d+)(?:[/?#]|$)", str(value or ""))
    return match.group(1) if match else None


def normalize_mikan_candidate(item: dict[str, Any]) -> dict[str, Any]:
    groups = ensure_list(item.get("groups"))
    return {
        "source": "mikan",
        "title": item.get("title"),
        "url": item.get("url"),
        "bangumi_id": item.get("bangumiId"),
        "bgm_url": item.get("bgmUrl"),
        "bgm_id": bgm_id_from_url(item.get("bgmUrl")),
        "exists": item.get("exists"),
        "score": item.get("score"),
        "cover": item.get("cover"),
        "group_count": len(groups),
        "groups": [normalize_group(group) for group in groups if isinstance(group, dict)],
    }


def normalize_group(
    group: dict[str, Any], *, sample_limit: int = 5, source: str = "mikan"
) -> dict[str, Any]:
    items = [item for item in ensure_list(group.get("items")) if isinstance(item, dict)]
    if sample_limit < 1:
        raise AniRssError("sample-limit must be positive")
    samples = [normalize_item(item) for item in items[:sample_limit]]
    label = group.get("label") if source == "mikan" else group.get("name")
    tags = []
    group_regex = group.get("groupRegex")
    if isinstance(group_regex, dict):
        tags = [str(tag) for tag in ensure_list(group_regex.get("tags"))]
    batch_evidence = batch_evidence_from_samples(label, samples)
    return {
        "source": source,
        "label": label,
        "subgroup_id": group.get("subgroupId") if source == "mikan" else group.get("groupId", group.get("id")),
        "slug": group.get("slug"),
        "rss": group.get("rss"),
        "bgm_url": group.get("bgmUrl") or (f"https://bgm.tv/subject/{group['bgmId']}" if group.get("bgmId") else None),
        "update_day": group.get("updateDay"),
        "tags": tags,
        "language_evidence": language_evidence(label, tags, samples),
        "batch_evidence": batch_evidence,
        "download_risk": (
            "batch-release-detected"
            if batch_evidence.get("has_batch_like_samples")
            else "normal"
        ),
        "samples": samples,
        "returned_item_count": len(items),
        "sample_count": len(samples),
        "full_feed_verified": False,
    }


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title")
    return {
        "title": title,
        "episode": item.get("episode"),
        "subgroup": item.get("subgroup") or (item.get("fansub") or {}).get("name"),
        "format_size": item.get("formatSize"),
        "pub_date": item.get("pubDate", item.get("publishedAt", item.get("createdAt"))),
        "source_episode_key": item.get("episodeKey"),
        "subtitle_languages": item.get("language"),
        "subtitle_mode": item.get("subtitle"),
        "resolution": item.get("resolution"),
        "provider": item.get("provider"),
        "release_detection": detect_release_pattern(title),
    }


def language_evidence(
    group_label: Any, tags: list[str], samples: list[dict[str, Any]]
) -> dict[str, Any]:
    sample_titles = [
        str(sample.get("title"))
        for sample in samples
        if sample.get("title") is not None
    ]
    sample_subgroups = sorted(
        {
            str(sample.get("subgroup"))
            for sample in samples
            if sample.get("subgroup") is not None
        }
    )
    return {
        "note": (
            "No language classification is performed by this script. "
            "Use this evidence to reason about subtitle language/spec and ask the user."
        ),
        "group_label": group_label,
        "tags": tags,
        "sample_titles": sample_titles,
        "sample_subgroups": sample_subgroups,
    }


def detect_release_pattern(title: Any) -> dict[str, Any]:
    title_text = str(title or "")
    reasons: list[str] = []
    matched_keywords: list[str] = []
    matched_ranges: list[dict[str, Any]] = []

    for label, pattern in BATCH_KEYWORD_PATTERNS:
        if pattern.search(title_text):
            reasons.append(label)
            matched_keywords.append(label)

    for label, pattern in EPISODE_RANGE_PATTERNS:
        for match in pattern.finditer(title_text):
            start = int(match.group("start"))
            end = int(match.group("end"))
            if not is_valid_episode_range(start, end):
                continue
            episode_span = end - start + 1
            range_reason = f"{label}:{start:02d}-{end:02d}"
            reasons.append(range_reason)
            matched_ranges.append(
                {
                    "pattern": label,
                    "start_episode": start,
                    "end_episode": end,
                    "episode_span": episode_span,
                    "matched_text": match.group(0),
                }
            )

    max_episode_span = max(
        (entry["episode_span"] for entry in matched_ranges),
        default=1,
    )
    spans_multiple_episodes = max_episode_span > 1
    has_batch_like_range = max_episode_span >= 4
    has_batch_like_samples = bool(matched_keywords) or has_batch_like_range
    return {
        "note": (
            "Batch/season-pack detection is heuristic. Titles that look like "
            "collections or multi-episode packs may not be parsed reliably by ANI-RSS."
        ),
        "is_batch_like": has_batch_like_samples,
        "spans_multiple_episodes": spans_multiple_episodes,
        "max_episode_span": max_episode_span,
        "reasons": reasons,
        "matched_keywords": matched_keywords,
        "matched_ranges": matched_ranges,
    }


def is_valid_episode_range(start: int, end: int) -> bool:
    return (
        1 <= start <= EPISODE_RANGE_LIMIT
        and 1 <= end <= EPISODE_RANGE_LIMIT
        and end > start
    )


def batch_evidence_from_samples(group_label: Any, samples: list[dict[str, Any]]) -> dict[str, Any]:
    matching_samples: list[dict[str, Any]] = []
    reasons: list[str] = []
    has_multi_episode_samples = False

    for sample in samples:
        detection = sample.get("release_detection")
        if not isinstance(detection, dict):
            continue
        if detection.get("spans_multiple_episodes"):
            has_multi_episode_samples = True
        if not detection.get("is_batch_like"):
            continue
        title = sample.get("title")
        sample_reasons = [
            str(reason) for reason in ensure_list(detection.get("reasons")) if reason
        ]
        reasons.extend(sample_reasons)
        matching_samples.append({"title": title, "reasons": sample_reasons})

    unique_reasons = sorted(set(reasons))
    return {
        "note": (
            "Use this signal to warn the user when a subgroup appears to publish "
            "collections, full seasons, or other multi-episode packs."
        ),
        "group_label": group_label,
        "has_batch_like_samples": bool(matching_samples),
        "has_multi_episode_samples": has_multi_episode_samples,
        "matching_sample_count": len(matching_samples),
        "reasons": unique_reasons,
        "matching_sample_titles": [
            sample["title"] for sample in matching_samples if sample.get("title") is not None
        ],
        "matching_samples": matching_samples,
    }


def search_candidates(args: argparse.Namespace, base_url: str, api_key: str) -> list[dict[str, Any]]:
    source = getattr(args, "source", "mikan")
    bgm_url = getattr(args, "bgm_url", None)
    if source == "mikan":
        data = unwrap_data(request_json(
            base_url, api_key, "POST", "/api/mikan", query={"text": args.title},
            body=season_body(args), timeout=args.timeout,
        ))
        if not isinstance(data, dict) or not isinstance(data.get("weeks"), list):
            raise AniRssError("Unexpected Mikan search response; not an empty search result")
        candidates = flatten_mikan_items(data)
    elif source == "ani-bt":
        year, season = getattr(args, "year", None), getattr(args, "season", None)
        data = unwrap_data(request_json(
            base_url, api_key, "POST", "/api/aniBT",
            body={"title": "" if bgm_url else args.title, "bgmUrl": bgm_url or "",
                  "season": f"{year}{season}" if year and season else ""}, timeout=args.timeout,
        ))
        if not isinstance(data, dict) or not isinstance(data.get("byWeekday"), list):
            raise AniRssError("Unexpected AniBT search response; not an empty search result")
        candidates = []
        for week in data["byWeekday"]:
            for item in ensure_list(week.get("animes")):
                titles = item.get("title") or {}
                identifier = str(item.get("bgmId") or "")
                candidates.append({
                    "source": source, "title": titles.get("chinese") or titles.get("primary"),
                    "aliases": titles, "bgm_id": identifier,
                    "bgm_url": f"https://bgm.tv/subject/{identifier}",
                    "url": f"https://anibt.net/anime/{identifier}",
                    "exists": item.get("exists"), "score": item.get("rating"),
                    "release_count": item.get("rssReleaseCount"), "groups": [],
                    "week_label": week.get("weekdayLabel"),
                })
    elif source == "anime-garden":
        if bgm_url:
            data = unwrap_data(request_json(
                base_url, api_key, "POST", "/api/animeGardenList", query={"bgmUrl": bgm_url}, timeout=args.timeout,
            ))
            if not isinstance(data, list):
                raise AniRssError("Unexpected AnimeGarden list response")
            items = [item for week in data for item in ensure_list(week.get("subjects"))]
        else:
            data = unwrap_data(request_json(
                base_url, api_key, "POST", "/api/searchBgm", query={"name": args.title}, timeout=args.timeout,
            ))
            if not isinstance(data, list):
                raise AniRssError("Unexpected Bangumi search response")
            items = data
        candidates = []
        for item in items:
            identifier = str(item.get("id") or "")
            candidates.append({
                "source": source, "title": item.get("nameCn") or item.get("name"),
                "aliases": {"original": item.get("name"), "chinese": item.get("nameCn")},
                "bgm_id": identifier, "bgm_url": f"https://bgm.tv/subject/{identifier}",
                "url": f"https://bgm.tv/subject/{identifier}",
                "exists": None, "metadata_only": True, "date": item.get("date"), "groups": [],
            })
    else:
        raise AniRssError("Unsupported source")
    for candidate in candidates:
        candidate["candidate_id"] = json_sha256({
            "source": source, "url": candidate.get("url"), "bgm_id": candidate.get("bgm_id"),
        })[:16]
    return candidates


def command_search(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    candidates = search_candidates(args, base_url, api_key)
    print_json(
        {
            "ok": True,
            "command": "search",
            "title": args.title,
            "source": getattr(args, "source", "mikan"),
            "count": len(candidates),
            "candidates": candidates,
        }
    )


def fetch_groups(
    base_url: str, api_key: str, url: str, timeout: int, sample_limit: int,
    *, source: str = "mikan", bgm_id: str | None = None,
) -> list[dict[str, Any]]:
    if source == "mikan":
        if not url:
            raise AniRssError("Mikan groups require --url")
        endpoint, query = "/api/mikanGroup", {"url": url}
    else:
        if not bgm_id or not str(bgm_id).isdigit():
            raise AniRssError("AniBT/AnimeGarden groups require a numeric --bgm-id")
        endpoint = "/api/aniBTGroup" if source == "ani-bt" else "/api/animeGardenGroup"
        query = {"bgmId": str(bgm_id)}
    response = request_json(
        base_url,
        api_key,
        "POST",
        endpoint,
        query=query,
        timeout=timeout,
    )
    groups = unwrap_data(response)
    if not isinstance(groups, list):
        raise AniRssError("Unexpected group response; not an empty RSS list")
    normalized = [
        normalize_group(group, sample_limit=sample_limit, source=source)
        for group in ensure_list(groups)
        if isinstance(group, dict)
    ]
    for group in normalized:
        group["group_key"] = json_sha256({"source": source, "rss": group.get("rss"), "label": group.get("label")})[:16]
    return normalized


def command_groups(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    groups = fetch_groups(base_url, api_key, args.url or "", args.timeout, args.sample_limit,
                          source=args.source, bgm_id=args.bgm_id)
    print_json(
        {
            "ok": True,
            "command": "groups",
            "url": args.url,
            "source": args.source,
            "count": len(groups),
            "groups": groups,
        }
    )


def command_plan(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    source = getattr(args, "source", "mikan")
    candidates = search_candidates(args, base_url, api_key)
    for candidate in candidates:
        url = candidate.get("url")
        try:
            candidate["groups"] = fetch_groups(
                base_url, api_key, str(url or ""), args.timeout, args.sample_limit,
                source=source, bgm_id=candidate.get("bgm_id"),
            )
            candidate["group_count"] = len(candidate["groups"])
        except AniRssError as exc:
            candidate["groups"] = []
            candidate["group_error"] = str(exc)
    print_json(
        {
            "ok": True,
            "command": "plan",
            "title": args.title,
            "source": source,
            "next_action": "normalize-title-before-other-sources" if not candidates and source == "mikan" else "inspect-candidates-and-render-options",
            "requires_user_confirmation": True,
            "count": len(candidates),
            "candidates": candidates,
        }
    )


def render_options(plan: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    """Validate LLM descriptions against source samples and render stable chat cards."""
    if plan.get("command") != "plan" or plan.get("ok") is not True:
        raise AniRssError("--plan-json must contain a successful plan result")
    entries = assessment.get("options")
    if not isinstance(entries, list) or not entries:
        raise AniRssError("options must be a non-empty array")
    catalogue = {
        (candidate.get("candidate_id"), group.get("group_key")): (candidate, group)
        for candidate in plan.get("candidates", [])
        for group in candidate.get("groups", [])
        if group.get("rss") and group.get("label")
    }
    output, cards, seen = [], [], set()
    required = {"candidate_id", "group_key", "language_status", "subtitle_languages",
                "subtitle_mode", "resource_spec", "version_filter", "evidence"}
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or set(entry) != required:
            raise AniRssError("Each option requires exactly: " + ", ".join(sorted(required)))
        for field in required - {"evidence"}:
            if not isinstance(entry[field], str) or not entry[field].strip() or "\n" in entry[field] or "\r" in entry[field]:
                raise AniRssError(f"Option {field} must be a non-empty single-line string")
        key = (entry["candidate_id"], entry["group_key"])
        if key not in catalogue:
            raise AniRssError("Option candidate/group does not exist in the saved plan")
        candidate, group = catalogue[key]
        if entry["language_status"] not in {"confirmed", "mixed", "unknown"}:
            raise AniRssError("language_status must be confirmed, mixed, or unknown")
        if entry["language_status"] == "unknown" and entry["subtitle_languages"] != "未知":
            raise AniRssError("Unknown subtitle language must be displayed as 未知")
        if entry["language_status"] != "unknown" and entry["subtitle_languages"] == "未知":
            raise AniRssError("Known subtitle language requires a concrete description")
        references = entry["evidence"]
        if not isinstance(references, list) or (entry["language_status"] != "unknown" and not references):
            raise AniRssError("Known/mixed language requires sample evidence")
        quotes = []
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"sample_index", "field", "quote"}:
                raise AniRssError("Evidence requires sample_index, field, and quote")
            index, field, quote = reference["sample_index"], reference["field"], reference["quote"]
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(group["samples"]):
                raise AniRssError("Evidence sample_index is outside the saved samples")
            if field not in {"title", "subtitle_languages", "subtitle_mode", "resolution"}:
                raise AniRssError("Evidence must cite subtitle/spec sample fields, not group names or audio")
            value = group["samples"][index].get(field)
            value_text = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value or "")
            if not isinstance(quote, str) or not quote.strip() or quote not in value_text or "\n" in quote or "\r" in quote:
                raise AniRssError("Evidence quote does not occur in the cited source sample")
            quotes.append(f"样本{index + 1} {field}「{quote}」")
        option_id = json_sha256(entry)[:16]
        if option_id in seen:
            raise AniRssError("Duplicate option")
        seen.add(option_id)
        source = candidate["source"]
        risk = "发现合集/多集包标记，需核查" if group["download_risk"] == "batch-release-detected" else "当前样本未发现合集标记；仅代表已观测范围"
        card = (
            f"{number}. {SOURCE_LABELS[source]} · {group['label']}\n"
            f"番剧：{candidate.get('title')}\n"
            f"字幕语言：{entry['subtitle_languages']}（{ {'confirmed': '已确认', 'mixed': '混合版本', 'unknown': '未知'}[entry['language_status']] }）\n"
            f"字幕形式：{entry['subtitle_mode']}\n"
            f"资源规格：{entry['resource_spec']}\n"
            f"版本条件：{entry['version_filter']}\n"
            f"依据：{'；'.join(quotes) or '未找到可确认字幕语言的样本证据'}\n"
            f"风险：{risk}\n"
            f"RSS：{confirmation_url(group['rss'])}"
        )
        cards.append(card)
        output.append({"number": number, "option_id": option_id, **entry,
                       "source": source, "rss": group["rss"], "subgroup": group["label"],
                       "bgm_url": candidate.get("bgm_url"), "title": candidate.get("title")})
    return {"ok": True, "command": "render-options", "requires_user_selection": True,
            "plan_sha256": json_sha256(plan), "options": output,
            "text": "\n\n".join(cards) + "\n\n请选择编号；推荐不代表已经选择。"}


def command_render_options(args: argparse.Namespace) -> None:
    print_json(render_options(read_object_arg(args.plan_json, "--plan-json"),
                              read_object_arg(args.options_json, "--options-json")))


def command_build_from_rss(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    evidence = read_object_arg(args.options_evidence, "--options-evidence")
    if evidence.get("ok") is not True or evidence.get("command") != "render-options":
        raise AniRssError("build requires a successful render-options result")
    selections = [option for option in evidence.get("options", []) if option.get("option_id") == args.option_id]
    if len(selections) != 1:
        raise AniRssError("Selected option-id must identify exactly one rendered option")
    selected = selections[0]
    if any(selected.get(field) != value for field, value in (
        ("rss", args.rss), ("source", args.type), ("subgroup", args.subgroup), ("bgm_url", args.bgm_url)
    )):
        raise AniRssError("Selected RSS/type/group/Bangumi does not match the rendered option")
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(args.rss).query)
    expected_id = bgm_id_from_url(args.bgm_url)
    if args.type in ("ani-bt", "anime-garden"):
        field = "bgmId" if args.type == "ani-bt" else "subject"
        source_id = params.get(field, [None])[0]
        if not expected_id or source_id != expected_id:
            raise AniRssError("RSS Bangumi id does not match --bgm-url for this source")
    body = {
        "url": args.rss,
        "type": args.type,
        "bgmUrl": args.bgm_url,
        "subgroup": args.subgroup,
        "enable": not args.disabled,
    }
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/rssToAni",
        body=body,
        timeout=args.timeout,
    )
    ani = unwrap_data(response)
    if not isinstance(ani, dict) or any(ani.get(field) != body[field] for field in ("url", "type", "subgroup")):
        raise AniRssError("RSS draft source does not match the selected RSS/type/group")
    if bgm_id_from_url(ani.get("bgmUrl")) != expected_id:
        raise AniRssError("RSS draft Bangumi identity does not match the selected work")
    print_json(
        {
            "ok": True,
            "command": "build-from-rss",
            "source": body,
            "selected_option": selected,
            "ani": ani,
        }
    )


def read_json_arg(value: str) -> Any:
    if value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(value).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AniRssError(f"Invalid JSON in {value}: {exc}") from exc


def extract_ani_payload(value: Any) -> Any:
    if isinstance(value, dict):
        if isinstance(value.get("ani"), dict):
            return value["ani"]
        if isinstance(value.get("data"), dict):
            return value["data"]
    return value


def read_object_arg(value: str, label: str) -> dict[str, Any]:
    parsed = read_json_arg(value)
    if not isinstance(parsed, dict):
        raise AniRssError(f"{label} must contain a JSON object")
    return parsed


def normalize_tmdb_proxy(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise AniRssError("tmdb_proxy must be a non-empty proxy URL or an empty string")
    proxy_url = value.strip()
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
        port = parsed.port
    except ValueError as exc:
        raise AniRssError("tmdb_proxy contains an invalid port") from exc
    if parsed.scheme.lower() != "http":
        raise AniRssError("tmdb_proxy must use http")
    if not parsed.hostname or port is None:
        raise AniRssError("tmdb_proxy must include a host and port")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise AniRssError("tmdb_proxy must not include a path, query, or fragment")
    return proxy_url


def resolve_tmdb_credentials(
    args: argparse.Namespace,
) -> tuple[str, str | None, str | None, str | None]:
    local_config = load_local_config()
    base_url = (
        getattr(args, "tmdb_base_url", None)
        or os.environ.get("TMDB_API_BASE_URL")
        or local_config.get("tmdb_api_base_url")
        or "https://api.themoviedb.org/3"
    ).rstrip("/")
    token = (
        getattr(args, "tmdb_api_token", None)
        or os.environ.get("TMDB_API_TOKEN")
        or local_config.get("tmdb_api_token")
    )
    api_key = (
        getattr(args, "tmdb_api_key", None)
        or os.environ.get("TMDB_API_KEY")
        or local_config.get("tmdb_api_key")
    )
    if not token and not api_key:
        raise AniRssError(
            "Missing TMDB credentials. Set TMDB_API_TOKEN (recommended) or TMDB_API_KEY, "
            "or configure tmdb_api_token/tmdb_api_key in ani-rss-config.local.json."
        )
    proxy_url = normalize_tmdb_proxy(local_config.get("tmdb_proxy"))
    return base_url, token, api_key, proxy_url


def read_tmdb_response(
    req: urllib.request.Request,
    *,
    timeout: int,
    proxy_url: str | None,
) -> str:
    if not proxy_url:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def request_tmdb_json(
    base_url: str,
    token: str | None,
    api_key: str | None,
    path: str,
    *,
    query: dict[str, str] | None = None,
    proxy_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    url = f"{base_url}{path}"
    params = dict(query or {})
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        params["api_key"] = api_key
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        raw = read_tmdb_response(req, timeout=timeout, proxy_url=proxy_url)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise AniRssError(f"TMDB HTTP {exc.code}: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise AniRssError(f"Cannot connect to TMDB at {base_url}: {exc}") from exc
    except TimeoutError as exc:
        raise AniRssError(f"TMDB request timed out after {timeout}s") from exc
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise AniRssError(f"TMDB returned non-JSON response: {raw[:200]}") from exc
    if isinstance(parsed, dict) and parsed.get("status_code") not in (None, 1):
        raise AniRssError(
            f"TMDB returned status_code={parsed.get('status_code')}: {parsed.get('status_message')}"
        )
    return parsed


def validate_regex_array(value: Any, field: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AniRssError(f"{field} must be a JSON array of strings")


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    unknown = set(patch) - PATCHABLE_ANI_FIELDS
    if unknown:
        raise AniRssError(
            "Unsupported Ani patch fields: " + ", ".join(sorted(unknown))
        )

    for field in ("match", "exclude", "appendMatch", "appendExclude"):
        if field not in patch:
            continue
        validate_regex_array(patch[field], field)

    if "match" in patch and "appendMatch" in patch:
        raise AniRssError("Use either match or appendMatch, not both")
    if "exclude" in patch and "appendExclude" in patch:
        raise AniRssError("Use either exclude or appendExclude, not both")

    if "standbyRssList" in patch:
        standby = patch["standbyRssList"]
        if not isinstance(standby, list):
            raise AniRssError("standbyRssList must be a JSON array")
        for item in standby:
            if not isinstance(item, dict):
                raise AniRssError("standbyRssList entries must be JSON objects")
            unknown_standby_fields = set(item) - STANDBY_RSS_FIELDS
            if unknown_standby_fields:
                raise AniRssError(
                    "Unsupported standbyRssList fields: "
                    + ", ".join(sorted(unknown_standby_fields))
                )
            if "label" in item and not isinstance(item["label"], str):
                raise AniRssError("standbyRssList label must be a string")
            if not isinstance(item.get("url"), str) or not item["url"].strip():
                raise AniRssError("standbyRssList url must be a non-empty string")
            if (
                "offset" not in item
                or not isinstance(item["offset"], int)
                or isinstance(item["offset"], bool)
            ):
                raise AniRssError("standbyRssList offset must be an integer")

    if "releaseDate" in patch:
        release_date = patch["releaseDate"]
        if not isinstance(release_date, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", release_date
        ):
            raise AniRssError("releaseDate must be a YYYY-MM-DD string")
        try:
            dt.date.fromisoformat(release_date)
        except ValueError as exc:
            raise AniRssError("releaseDate must be a YYYY-MM-DD string") from exc

    for field in ("season", "offset", "totalEpisodeNumber", "customEpisodeGroupIndex"):
        if field not in patch:
            continue
        if not isinstance(patch[field], int) or isinstance(patch[field], bool):
            raise AniRssError(f"{field} must be an integer")
        if field in {"season", "totalEpisodeNumber", "customEpisodeGroupIndex"} and patch[field] < 0:
            raise AniRssError(f"{field} must not be negative")

    if "customEpisode" in patch and not isinstance(patch["customEpisode"], bool):
        raise AniRssError("customEpisode must be a boolean")
    if "customEpisodeStr" in patch and (
        not isinstance(patch["customEpisodeStr"], str)
        or not patch["customEpisodeStr"].strip()
    ):
        raise AniRssError("customEpisodeStr must be a string")
    has_tmdb = "tmdb" in patch
    has_tmdb_name = "themoviedbName" in patch
    if has_tmdb != has_tmdb_name:
        raise AniRssError("tmdb and themoviedbName must be patched together")
    if has_tmdb:
        tmdb = patch["tmdb"]
        if not isinstance(tmdb, dict):
            raise AniRssError("tmdb must be a JSON object")
        for field in ("id", "tmdbType"):
            if not isinstance(tmdb.get(field), str) or not tmdb[field].strip():
                raise AniRssError(f"tmdb {field} must be a non-empty string")
        if not any(
            isinstance(tmdb.get(field), str) and tmdb[field].strip()
            for field in ("name", "originalName")
        ):
            raise AniRssError("tmdb must include a non-empty name or originalName")
    if "themoviedbName" in patch and not isinstance(patch["themoviedbName"], str):
        raise AniRssError("themoviedbName must be a string")
    return patch


def validate_final_ani(ani: dict[str, Any]) -> None:
    if not ani.get("customEpisode"):
        return
    pattern = ani.get("customEpisodeStr")
    group_index = ani.get("customEpisodeGroupIndex")
    if not isinstance(pattern, str) or not pattern.strip():
        raise AniRssError(
            "customEpisode=true requires a non-empty customEpisodeStr in the final Ani object"
        )
    if (
        not isinstance(group_index, int)
        or isinstance(group_index, bool)
        or group_index < 0
    ):
        raise AniRssError(
            "customEpisode=true requires a non-negative customEpisodeGroupIndex"
        )


def tmdb_id_from_ani(ani: dict[str, Any]) -> str | None:
    tmdb = ani.get("tmdb")
    if (
        not isinstance(tmdb, dict)
        or tmdb.get("id") in (None, "")
        or not str(tmdb.get("id")).strip()
    ):
        return None
    return str(tmdb["id"])


def is_tv_ani(ani: dict[str, Any]) -> bool:
    tmdb = ani.get("tmdb")
    if isinstance(tmdb, dict):
        tmdb_type = str(tmdb.get("tmdbType") or tmdb.get("media_type") or "").lower()
        if tmdb_type in {"tv", "series", "tv series", "电视剧"}:
            return True
        if tmdb_type in {"movie", "film", "ova", "剧场版"}:
            return False
    return "season" in ani


def tmdb_season_numbers(lookup_result: dict[str, Any]) -> set[int]:
    """Return the season numbers advertised by a direct TMDB TV lookup."""
    seasons = lookup_result.get("seasons")
    if not isinstance(seasons, list):
        return set()
    numbers: set[int] = set()
    for season in seasons:
        if not isinstance(season, dict):
            continue
        value = season.get("season_number")
        if isinstance(value, bool):
            continue
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def read_evidence_file(path: str, label: str) -> dict[str, Any]:
    value = read_object_arg(path, label)
    if value.get("ok") is False:
        raise AniRssError(f"{label} contains an unsuccessful command result")
    return value


def validate_write_evidence(
    ani: dict[str, Any],
    *,
    preview_path: str | None,
    tmdb_lookup_path: str | None,
    tmdb_season_path: str | None,
) -> dict[str, Any]:
    """Require read-only evidence before a mutating add/set call.

    This deliberately validates identity and object binding only. The LLM still
    decides semantic title/episode alignment from the raw evidence.
    """
    if not preview_path:
        raise AniRssError(
            "Refusing to write without --preview-evidence from the exact final Ani object."
        )
    preview_evidence = read_evidence_file(preview_path, "--preview-evidence")
    if preview_evidence.get("command") != "preview":
        raise AniRssError("--preview-evidence must contain a preview command result")
    expected_hash = preview_evidence.get("input_sha256")
    actual_hash = json_sha256(ani)
    if expected_hash != actual_hash:
        raise AniRssError(
            "Preview evidence does not match the Ani object being written; run preview again."
        )
    coverage = preview_coverage(preview_evidence.get("preview"))
    if coverage["returned_count"] == 0:
        raise AniRssError("Preview contains no items; refusing to write an unverified RSS")
    if is_tv_ani(ani) and coverage["coverage_status"] != "observed-range":
        raise AniRssError("TV preview requires at least three distinct parsed episodes before writing")

    tmdb_id = tmdb_id_from_ani(ani)
    if not tmdb_id:
        raise AniRssError(
            "Refusing to write without a TMDB identity in the final Ani object."
        )
    if not tmdb_lookup_path:
        raise AniRssError(
            "Refusing to write without --tmdb-lookup-evidence from the exact TMDB id."
        )
    lookup_evidence = read_evidence_file(
        tmdb_lookup_path, "--tmdb-lookup-evidence"
    )
    if lookup_evidence.get("command") != "tmdb-lookup":
        raise AniRssError(
            "--tmdb-lookup-evidence must contain a tmdb-lookup command result"
        )
    lookup_query = lookup_evidence.get("query")
    if not isinstance(lookup_query, dict) or str(lookup_query.get("tmdb_id")) != tmdb_id:
        raise AniRssError(
            "TMDB lookup evidence does not match the final Ani TMDB id."
        )
    lookup_result = lookup_evidence.get("result")
    if not isinstance(lookup_result, dict):
        raise AniRssError("TMDB lookup evidence has no object result")
    if lookup_result.get("id") in (None, ""):
        raise AniRssError("TMDB lookup result has no id")
    if str(lookup_result["id"]) != tmdb_id:
        raise AniRssError("TMDB lookup result id does not match the final Ani TMDB id.")

    season_evidence: dict[str, Any] | None = None
    if is_tv_ani(ani):
        if "season" not in ani or not isinstance(ani.get("season"), int):
            raise AniRssError(
                "TV subscriptions require a final integer season before writing."
            )
        advertised_seasons = tmdb_season_numbers(lookup_result)
        if not advertised_seasons:
            raise AniRssError(
                "TMDB lookup evidence has no usable seasons list; inspect the direct "
                "TMDB series result before selecting a TV season."
            )
        if ani["season"] not in advertised_seasons:
            raise AniRssError(
                "Final TMDB season is not present in the direct TMDB seasons list; "
                "do not use the RSS season label as a TMDB season."
            )
        if not tmdb_season_path:
            raise AniRssError(
                "TV subscriptions require --tmdb-season-evidence from tmdb-season."
            )
        season_evidence = read_evidence_file(
            tmdb_season_path, "--tmdb-season-evidence"
        )
        if season_evidence.get("command") != "tmdb-season":
            raise AniRssError(
                "--tmdb-season-evidence must contain a tmdb-season command result"
            )
        season_query = season_evidence.get("query")
        if (
            not isinstance(season_query, dict)
            or str(season_query.get("tmdb_id")) != tmdb_id
            or season_query.get("season_number") != ani.get("season")
        ):
            raise AniRssError(
                "TMDB season evidence does not match the final Ani TMDB id/season."
            )
        season_result = season_evidence.get("result")
        episodes = season_result.get("episodes") if isinstance(season_result, dict) else None
        if not isinstance(episodes, list) or not episodes:
            raise AniRssError(
                "TMDB season evidence must contain a non-empty episode list."
            )
        if (
            isinstance(season_result, dict)
            and season_result.get("season_number") not in (None, ani.get("season"))
        ):
            raise AniRssError("TMDB season result number does not match the final Ani season.")

    return {
        "preview": preview_evidence,
        "tmdb_lookup": lookup_evidence,
        "tmdb_season": season_evidence,
        "ani_sha256": actual_hash,
    }


def apply_patch(ani: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    validate_patch(patch)
    patched = copy.deepcopy(ani)
    field_changes = {
        field: value
        for field, value in patch.items()
        if field not in {"appendMatch", "appendExclude"}
    }
    patched.update(copy.deepcopy(field_changes))
    for patch_field, ani_field in (("appendMatch", "match"), ("appendExclude", "exclude")):
        if patch_field not in patch:
            continue
        current = patched.get(ani_field, [])
        validate_regex_array(current, f"existing Ani {ani_field}")
        patched[ani_field] = copy.deepcopy(current) + copy.deepcopy(patch[patch_field])
    validate_final_ani(patched)
    return patched


def command_patch(args: argparse.Namespace) -> None:
    ani = extract_ani_payload(read_json_arg(args.ani_json))
    if not isinstance(ani, dict):
        raise AniRssError("--ani-json must contain an Ani object or a wrapper with ani/data")
    patch = read_object_arg(args.patch_json, "--patch-json")
    patched = apply_patch(ani, patch)
    print_json(
        {
            "ok": True,
            "command": "patch",
            "requires_user_confirmation": True,
            "patch": patch,
            "ani": patched,
            "confirmation": {
                "ani_sha256": json_sha256(patched),
                **confirmation_summary(patched),
            },
        }
    )


def command_preview(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    ani = extract_ani_payload(read_json_arg(args.ani_json))
    if not isinstance(ani, dict):
        raise AniRssError("--ani-json must contain an Ani object or a wrapper with ani/data")
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/previewAni",
        body=ani,
        timeout=args.timeout,
    )
    preview = unwrap_data(response)
    print_json(
        {
            "ok": True,
            "command": "preview",
            "input_sha256": json_sha256(ani),
            "confirmation": {
                "ani_sha256": json_sha256(ani),
                **confirmation_summary(ani),
            },
            "requires_user_confirmation": True,
            "preview": preview,
            "coverage": preview_coverage(preview),
            "confirmation_text": render_confirmation(ani, preview),
        }
    )


def render_confirmation(ani: dict[str, Any], preview: Any) -> str:
    summary = confirmation_summary(ani)
    fields, source = summary["fields"], summary["source"]
    tmdb = fields.get("tmdb") or {}
    coverage = preview_coverage(preview)
    def display(value: Any) -> str:
        return "未知" if value is None else json.dumps(value, ensure_ascii=False)
    return (
        f"待确认订阅：{ani.get('title') or '未知'}\n"
        f"来源：{SOURCE_LABELS.get(source['type'], source['type'] or '未知')}\n"
        f"字幕组：{source['subgroup'] or '未知'}\nRSS：{source['url'] or '未知'}\n"
        f"TMDB：{tmdb.get('id', '未知')} · {tmdb.get('name') or tmdb.get('originalName') or '未知'}\n"
        f"目标季度：{display(fields['season'])}\n集数偏移：{display(fields['offset'])}\n"
        f"日期：{display(fields['releaseDate'])}\n总集数：{display(fields['totalEpisodeNumber'])}\n"
        f"匹配规则：{display(fields['match'])}\n排除规则：{display(fields['exclude'])}\n"
        f"备用RSS：{display(fields['standbyRssList'])}\n"
        f"预览：{coverage['returned_count']}条资源，{coverage['unique_episode_count']}个不同已解析集数；仅验证已观测范围\n"
        "请确认以上实际参数后再添加。"
    )


def preview_coverage(value: Any) -> dict[str, Any]:
    items = value.get("items") if isinstance(value, dict) else None
    items = [item for item in ensure_list(items) if isinstance(item, dict)]
    parsed_items = [
        item
        for item in items
        if isinstance(item.get("episode"), (int, float))
        and not isinstance(item.get("episode"), bool)
    ]
    episode_values: list[float] = []
    for item in parsed_items:
        episode = item.get("episode")
        episode_values.append(float(episode))
    ordered = sorted(
        parsed_items,
        key=lambda item: float(item.get("episode")),
    )
    distinct_items = list({float(item["episode"]): item for item in ordered}.values())
    unique_episodes = sorted(set(episode_values))
    duplicate_episodes = sorted(
        episode for episode in set(episode_values) if episode_values.count(episode) > 1
    )
    missing_episodes: list[int] = []
    if unique_episodes and all(episode.is_integer() for episode in unique_episodes):
        low = int(unique_episodes[0])
        high = int(unique_episodes[-1])
        if high - low <= EPISODE_RANGE_LIMIT:
            missing_episodes = [
                episode
                for episode in range(low, high + 1)
                if float(episode) not in unique_episodes
            ]

    anchor_indices: list[int] = []
    if distinct_items:
        anchor_indices = [0, len(distinct_items) // 2, len(distinct_items) - 1]
        anchor_indices = list(dict.fromkeys(anchor_indices))
    anchors = [
        {
            "episode": item.get("episode"),
            "title": item.get("title"),
            "pub_date": item.get("pubDate"),
        }
        for index in anchor_indices
        for item in [distinct_items[index]]
    ]
    return {
        "returned_count": len(items),
        "parsed_episode_count": len(episode_values),
        "unparsed_item_count": len(items) - len(parsed_items),
        "unique_episode_count": len(unique_episodes),
        "episode_min": unique_episodes[0] if unique_episodes else None,
        "episode_max": unique_episodes[-1] if unique_episodes else None,
        "duplicate_episodes": duplicate_episodes,
        "missing_episodes": missing_episodes,
        "anchors": anchors,
        "coverage_status": (
            "no-items"
            if not items
            else "insufficient-samples"
            if len(unique_episodes) < 3
            else "observed-range"
        ),
        "full_feed_verified": False,
        "note": (
            "This describes returned preview items only. It does not prove that the RSS feed "
            "contains the complete season; inspect the source range separately."
        ),
    }


def command_tmdb_lookup(args: argparse.Namespace) -> None:
    if args.tmdb_id:
        base_url, token, api_key, proxy_url = resolve_tmdb_credentials(args)
        result = request_tmdb_json(
            base_url,
            token,
            api_key,
            f"/{'movie' if args.movie else 'tv'}/{urllib.parse.quote(args.tmdb_id, safe='')}",
            proxy_url=proxy_url,
            timeout=args.timeout,
        )
        print_json(
            {
                "ok": True,
                "command": "tmdb-lookup",
                "source": "tmdb-api",
                "media_type": "movie" if args.movie else "tv",
                "query": {"tmdb_id": args.tmdb_id},
                "result": result,
            }
        )
        return

    base_url, api_key = resolve_config(args)
    body: dict[str, Any] = {"ova": args.movie}
    if args.title:
        body["title"] = args.title
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/getThemoviedbName",
        body=body,
        timeout=args.timeout,
    )
    print_json(
        {
            "ok": True,
            "command": "tmdb-lookup",
            "query": body,
            "result": unwrap_data(response),
        }
    )


def command_tmdb_season(args: argparse.Namespace) -> None:
    if args.season_number < 0:
        raise AniRssError("TMDB season number must not be negative")
    base_url, token, api_key, proxy_url = resolve_tmdb_credentials(args)
    query = {"language": args.language} if args.language else None
    result = request_tmdb_json(
        base_url,
        token,
        api_key,
        f"/tv/{urllib.parse.quote(args.tmdb_id, safe='')}/season/{args.season_number}",
        query=query,
        proxy_url=proxy_url,
        timeout=args.timeout,
    )
    print_json(
        {
            "ok": True,
            "command": "tmdb-season",
            "source": "tmdb-api",
            "query": {
                "tmdb_id": args.tmdb_id,
                "season_number": args.season_number,
                "language": args.language,
            },
            "result": result,
        }
    )


def command_tmdb_group_details(args: argparse.Namespace) -> None:
    base_url, token, api_key, proxy_url = resolve_tmdb_credentials(args)
    result = request_tmdb_json(
        base_url,
        token,
        api_key,
        f"/tv/episode_group/{urllib.parse.quote(args.group_id, safe='')}",
        proxy_url=proxy_url,
        timeout=args.timeout,
    )
    print_json(
        {
            "ok": True,
            "command": "tmdb-group-details",
            "source": "tmdb-api",
            "query": {"group_id": args.group_id},
            "result": result,
        }
    )


def command_tmdb_groups(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    ani = extract_ani_payload(read_json_arg(args.ani_json))
    if not isinstance(ani, dict):
        raise AniRssError("--ani-json must contain an Ani object or a wrapper with ani/data")
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/getThemoviedbGroup",
        body=ani,
        timeout=args.timeout,
    )
    print_json(
        {
            "ok": True,
            "command": "tmdb-groups",
            "groups": unwrap_data(response),
        }
    )


def flatten_subscriptions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    items: list[dict[str, Any]] = []
    for week in ensure_list(value.get("weekList")):
        if not isinstance(week, dict):
            continue
        items.extend(
            item for item in ensure_list(week.get("items")) if isinstance(item, dict)
        )
    return items


def command_get(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    response = request_json(
        base_url, api_key, "POST", "/api/listAni", timeout=args.timeout
    )
    for ani in flatten_subscriptions(unwrap_data(response)):
        if ani.get("id") == args.id:
            print_json({"ok": True, "command": "get", "ani": ani})
            return
    raise AniRssError(f"Subscription not found: {args.id}")


def command_set(args: argparse.Namespace) -> None:
    if not args.confirm_set:
        fail("Refusing to call /api/setAni without --confirm-set.", exit_code=2)
    base_url, api_key = resolve_config(args)
    ani = extract_ani_payload(read_json_arg(args.ani_json))
    if not isinstance(ani, dict):
        raise AniRssError("--ani-json must contain an Ani object or a wrapper with ani/data")
    if not isinstance(ani.get("id"), str) or not ani["id"].strip():
        raise AniRssError("/api/setAni requires an existing Ani object with a non-empty id")
    evidence = validate_write_evidence(
        ani,
        preview_path=getattr(args, "preview_evidence", None),
        tmdb_lookup_path=getattr(args, "tmdb_lookup_evidence", None),
        tmdb_season_path=getattr(args, "tmdb_season_evidence", None),
    )
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/setAni",
        query={"move": "true"} if args.move_files else None,
        body=ani,
        timeout=args.timeout,
    )
    verification = verify_persisted_subscription(base_url, api_key, ani, args.timeout)
    print_json(
        {
            "ok": True,
            "command": "set",
            "move_files": args.move_files,
            "response": response,
            "submitted": {
                "ani_sha256": evidence["ani_sha256"],
                **confirmation_summary(ani),
            },
            "evidence": {
                "ani_sha256": evidence["ani_sha256"],
                "preview_input_sha256": evidence["preview"].get("input_sha256"),
            },
            "verification": verification,
        }
    )


def subscription_identity_matches(candidate: dict[str, Any], expected: dict[str, Any]) -> bool:
    for field in ("title", "season", "url", "subgroup"):
        if field in expected and candidate.get(field) != expected.get(field):
            return False
    expected_tmdb = expected.get("tmdb")
    candidate_tmdb = candidate.get("tmdb")
    if isinstance(expected_tmdb, dict) and expected_tmdb.get("id"):
        if not isinstance(candidate_tmdb, dict):
            return False
        if str(candidate_tmdb.get("id")) != str(expected_tmdb.get("id")):
            return False
    return True


def subscription_matches_expected(candidate: dict[str, Any], expected: dict[str, Any]) -> bool:
    expected_id = expected.get("id")
    if isinstance(expected_id, str) and expected_id.strip():
        if candidate.get("id") != expected_id:
            return False
    if not subscription_identity_matches(candidate, expected):
        return False
    for field in (
        "match",
        "exclude",
        "standbyRssList",
        "releaseDate",
        "offset",
        "totalEpisodeNumber",
        "customEpisode",
        "customEpisodeStr",
        "customEpisodeGroupIndex",
        "themoviedbName",
    ):
        if field in expected and candidate.get(field) != expected.get(field):
            return False
    return True


def verify_persisted_subscription(
    base_url: str,
    api_key: str,
    expected: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    response = request_json(base_url, api_key, "POST", "/api/listAni", timeout=timeout)
    subscriptions = flatten_subscriptions(unwrap_data(response))
    id_matches = [
        item for item in subscriptions
        if expected.get("id") and item.get("id") == expected.get("id")
    ]
    identity_matches = [
        item for item in subscriptions if subscription_matches_expected(item, expected)
    ]
    matches = id_matches or identity_matches
    if len(matches) != 1:
        raise AniRssError(
            "write returned, but persistence verification found "
            f"{len(matches)} matching subscriptions; do not retry add before reviewing list output"
        )
    persisted = matches[0]
    field_comparison = compare_confirmation_fields(expected, persisted)
    mismatches = [
        field for field, result in field_comparison.items() if not result["matches"]
    ]
    if not subscription_matches_expected(persisted, expected) or mismatches:
        diagnostic_fields = ("season", "offset", "releaseDate", "totalEpisodeNumber")
        diagnostic = {
            field: field_comparison[field]
            for field in diagnostic_fields
            if field in field_comparison and field in mismatches
        }
        raise AniRssError(
            "write returned, but persisted confirmation fields differ from the submitted object: "
            + ", ".join(mismatches or ["identity or unlisted Ani field"])
            + ("; values=" + json.dumps(diagnostic, ensure_ascii=False) if diagnostic else "")
            + "; do not report success or retry before reviewing list output"
        )
    return {
        "verified": True,
        "match_count": len(matches),
        "persisted": persisted,
        "submitted_fields": confirmation_summary(expected),
        "persisted_fields": confirmation_summary(persisted),
        "field_comparison": field_comparison,
        "total_subscriptions": len(subscriptions),
    }


def preflight_duplicate_check(
    base_url: str,
    api_key: str,
    expected: dict[str, Any],
    timeout: int,
    *, distinct_feed_reason: str | None = None,
) -> dict[str, Any]:
    response = request_json(base_url, api_key, "POST", "/api/listAni", timeout=timeout)
    subscriptions = flatten_subscriptions(unwrap_data(response))
    matches = [
        item for item in subscriptions if subscription_identity_matches(item, expected)
    ]
    if matches:
        raise AniRssError(
            "A matching subscription already exists; review list/get and use set for edits."
        )
    same_content = [item for item in subscriptions
                    if tmdb_id_from_ani(expected) and tmdb_id_from_ani(item) == tmdb_id_from_ani(expected)
                    and is_tv_ani(item) == is_tv_ani(expected) and item.get("season") == expected.get("season")]
    if same_content and (not isinstance(distinct_feed_reason, str) or not distinct_feed_reason.strip()):
        raise AniRssError("Same TMDB season is already subscribed with another RSS/group; review list/get. "
                          "Use set for replacement, or --distinct-feed-reason only after the user confirms a distinct content range")
    return {"checked": True, "matching_subscriptions": 0, "same_tmdb_season_count": len(same_content),
            "distinct_feed_reason": distinct_feed_reason}


def command_add(args: argparse.Namespace) -> None:
    if not args.confirm_add:
        fail("Refusing to call /api/addAni without --confirm-add.", exit_code=2)
    base_url, api_key = resolve_config(args)
    ani = extract_ani_payload(read_json_arg(args.ani_json))
    if not isinstance(ani, dict):
        raise AniRssError("--ani-json must contain an Ani object or a wrapper with ani/data")
    evidence = validate_write_evidence(
        ani,
        preview_path=getattr(args, "preview_evidence", None),
        tmdb_lookup_path=getattr(args, "tmdb_lookup_evidence", None),
        tmdb_season_path=getattr(args, "tmdb_season_evidence", None),
    )
    preflight = preflight_duplicate_check(base_url, api_key, ani, args.timeout,
                                          distinct_feed_reason=getattr(args, "distinct_feed_reason", None))
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/addAni",
        body=ani,
        timeout=args.timeout,
    )
    verification = verify_persisted_subscription(base_url, api_key, ani, args.timeout)
    print_json(
        {
            "ok": True,
            "command": "add",
            "response": response,
            "submitted": {
                "ani_sha256": evidence["ani_sha256"],
                **confirmation_summary(ani),
            },
            "evidence": {
                "ani_sha256": evidence["ani_sha256"],
                "preview_input_sha256": evidence["preview"].get("input_sha256"),
            },
            "preflight": preflight,
            "verification": verification,
        }
    )


def command_list(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    response = request_json(
        base_url, api_key, "POST", "/api/listAni", timeout=args.timeout
    )
    data = unwrap_data(response)
    print_json({"ok": True, "command": "list", "subscriptions": data})


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="ANI-RSS base URL")
    parser.add_argument("--api-key", help=argparse.SUPPRESS)
    parser.add_argument("--api-key-file", help="File containing the ANI-RSS API key")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--result-file",
        help="Persist the exact JSON result atomically for terminal/output recovery",
    )


def add_write_evidence_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preview-evidence",
        required=True,
        help="JSON result from preview for the exact final Ani object",
    )
    parser.add_argument(
        "--tmdb-lookup-evidence",
        required=True,
        help="JSON result from tmdb-lookup for the final TMDB id",
    )
    parser.add_argument(
        "--tmdb-season-evidence",
        help="JSON result from tmdb-season; required for TV subscriptions",
    )


def add_tmdb_args(parser: argparse.ArgumentParser) -> None:
    add_common_args(parser)
    parser.add_argument("--tmdb-base-url", help=argparse.SUPPRESS)
    parser.add_argument("--tmdb-api-token", help=argparse.SUPPRESS)
    parser.add_argument("--tmdb-api-key", help=argparse.SUPPRESS)


def add_season_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", type=int, help="Optional season year, e.g. 2026")
    parser.add_argument("--season", help="Optional season label, e.g. 春, 夏, 秋, 冬")


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", choices=SOURCES, default="mikan")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ANI-RSS helper for Mikan-first anime subscription planning."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Read-only search plus group planning")
    add_common_args(plan)
    add_season_args(plan)
    add_source_args(plan)
    plan.add_argument("--bgm-url", help="Exact Bangumi subject for AniBT/AnimeGarden")
    plan.add_argument("title")
    plan.add_argument("--sample-limit", type=int, default=5)
    plan.set_defaults(func=command_plan)

    search = subparsers.add_parser("search", help="Read-only Mikan search")
    add_common_args(search)
    add_season_args(search)
    add_source_args(search)
    search.add_argument("--bgm-url", help="Exact Bangumi subject for AniBT/AnimeGarden")
    search.add_argument("title")
    search.set_defaults(func=command_search)

    groups = subparsers.add_parser("groups", help="Read-only source group lookup")
    add_common_args(groups)
    add_source_args(groups)
    groups.add_argument("--url", help="Mikan anime URL")
    groups.add_argument("--bgm-id", help="Bangumi subject id for AniBT/AnimeGarden")
    groups.add_argument("--sample-limit", type=int, default=5)
    groups.set_defaults(func=command_groups)

    build = subparsers.add_parser("build-from-rss", help="Convert RSS to Ani JSON")
    add_common_args(build)
    build.add_argument("--rss", required=True)
    build.add_argument("--type", choices=SOURCES, default="mikan")
    build.add_argument("--options-evidence", required=True, help="Saved render-options result shown to the user")
    build.add_argument("--option-id", required=True, help="Exact option_id selected by the user")
    build.add_argument("--bgm-url", required=True)
    build.add_argument("--subgroup", required=True)
    build.add_argument("--disabled", action="store_true", help="Create Ani disabled")
    build.set_defaults(func=command_build_from_rss)

    render = subparsers.add_parser("render-options", help="Validate language/spec descriptions and render fixed candidate cards")
    render.add_argument("--plan-json", required=True)
    render.add_argument("--options-json", required=True)
    render.add_argument("--result-file")
    render.set_defaults(func=command_render_options)

    patch_cmd = subparsers.add_parser(
        "patch", help="Apply validated field changes to an Ani JSON object"
    )
    patch_cmd.add_argument(
        "--result-file",
        help="Persist the exact JSON result atomically for terminal/output recovery",
    )
    patch_cmd.add_argument("--ani-json", required=True, help="Ani JSON path or '-' for stdin")
    patch_cmd.add_argument(
        "--patch-json", required=True, help="JSON object containing supported Ani field changes"
    )
    patch_cmd.set_defaults(func=command_patch)

    preview = subparsers.add_parser(
        "preview", help="Preview an Ani object without creating a subscription"
    )
    add_common_args(preview)
    preview.add_argument("--ani-json", required=True, help="Ani JSON path or '-' for stdin")
    preview.set_defaults(func=command_preview)

    tmdb_lookup = subparsers.add_parser(
        "tmdb-lookup", help="Read-only ANI-RSS TMDB lookup"
    )
    add_tmdb_args(tmdb_lookup)
    lookup = tmdb_lookup.add_mutually_exclusive_group(required=True)
    lookup.add_argument("--title", help="Title to look up")
    lookup.add_argument("--tmdb-id", help="Exact TMDB id to retrieve")
    tmdb_lookup.add_argument(
        "--movie", action="store_true", help="Use movie/OVA lookup instead of TV"
    )
    tmdb_lookup.set_defaults(func=command_tmdb_lookup)

    tmdb_season = subparsers.add_parser(
        "tmdb-season", help="Read the complete episode list for one TMDB season"
    )
    add_tmdb_args(tmdb_season)
    tmdb_season.add_argument("--tmdb-id", required=True, help="TMDB TV series id")
    tmdb_season.add_argument("--season-number", required=True, type=int)
    tmdb_season.add_argument("--language", default="en-US")
    tmdb_season.set_defaults(func=command_tmdb_season)

    tmdb_group_details = subparsers.add_parser(
        "tmdb-group-details", help="Read the complete episode-group ordering from TMDB"
    )
    add_tmdb_args(tmdb_group_details)
    tmdb_group_details.add_argument("--group-id", required=True)
    tmdb_group_details.set_defaults(func=command_tmdb_group_details)

    tmdb_groups = subparsers.add_parser(
        "tmdb-groups", help="Read-only ANI-RSS TMDB episode-group lookup"
    )
    add_common_args(tmdb_groups)
    tmdb_groups.add_argument(
        "--ani-json", required=True, help="Ani JSON path or '-' for stdin"
    )
    tmdb_groups.set_defaults(func=command_tmdb_groups)

    get_cmd = subparsers.add_parser("get", help="Read one existing Ani subscription by id")
    add_common_args(get_cmd)
    get_cmd.add_argument("--id", required=True, help="Existing Ani subscription id")
    get_cmd.set_defaults(func=command_get)

    set_cmd = subparsers.add_parser("set", help="Persist edits to an existing Ani subscription")
    add_common_args(set_cmd)
    set_cmd.add_argument("--ani-json", required=True, help="Existing Ani JSON path or '-' for stdin")
    set_cmd.add_argument("--confirm-set", action="store_true")
    add_write_evidence_args(set_cmd)
    set_cmd.add_argument(
        "--move-files",
        action="store_true",
        help="Allow ANI-RSS to move files when the download path changes",
    )
    set_cmd.set_defaults(func=command_set)

    add = subparsers.add_parser("add", help="Add an Ani subscription")
    add_common_args(add)
    add.add_argument("--ani-json", required=True, help="Ani JSON path or '-' for stdin")
    add.add_argument("--confirm-add", action="store_true")
    add.add_argument("--distinct-feed-reason", help="User-confirmed distinct content range despite sharing a TMDB season")
    add_write_evidence_args(add)
    add.set_defaults(func=command_add)

    list_cmd = subparsers.add_parser("list", help="List ANI-RSS subscriptions")
    add_common_args(list_cmd)
    list_cmd.set_defaults(func=command_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    global CURRENT_RESULT_FILE
    parser = build_parser()
    args = parser.parse_args(argv)
    result_file = getattr(args, "result_file", None)
    CURRENT_RESULT_FILE = Path(result_file) if result_file else None
    try:
        args.func(args)
    except AniRssError as exc:
        fail(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
