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


def read_key_from_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AniRssError(f"Cannot read API key file: {path}: {exc}") from exc


def load_local_config() -> dict[str, Any]:
    path = Path(LOCAL_CONFIG_FILE)
    if not path.exists():
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
        or (LOCAL_KEY_FILE if Path(LOCAL_KEY_FILE).exists() else None)
    )
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


def normalize_mikan_candidate(item: dict[str, Any]) -> dict[str, Any]:
    groups = ensure_list(item.get("groups"))
    return {
        "title": item.get("title"),
        "url": item.get("url"),
        "bangumi_id": item.get("bangumiId"),
        "bgm_url": item.get("bgmUrl"),
        "exists": item.get("exists"),
        "score": item.get("score"),
        "cover": item.get("cover"),
        "group_count": len(groups),
        "groups": [normalize_group(group) for group in groups if isinstance(group, dict)],
    }


def normalize_group(group: dict[str, Any], *, sample_limit: int = 5) -> dict[str, Any]:
    items = [item for item in ensure_list(group.get("items")) if isinstance(item, dict)]
    samples = [normalize_item(item) for item in items[:sample_limit]]
    tags = []
    group_regex = group.get("groupRegex")
    if isinstance(group_regex, dict):
        tags = [str(tag) for tag in ensure_list(group_regex.get("tags"))]
    batch_evidence = batch_evidence_from_samples(group.get("label"), samples)
    return {
        "label": group.get("label"),
        "subgroup_id": group.get("subgroupId"),
        "rss": group.get("rss"),
        "bgm_url": group.get("bgmUrl"),
        "update_day": group.get("updateDay"),
        "tags": tags,
        "language_evidence": language_evidence(group.get("label"), tags, samples),
        "batch_evidence": batch_evidence,
        "download_risk": (
            "batch-release-detected"
            if batch_evidence.get("has_batch_like_samples")
            else "normal"
        ),
        "samples": samples,
    }


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = item.get("title")
    return {
        "title": title,
        "episode": item.get("episode"),
        "subgroup": item.get("subgroup"),
        "format_size": item.get("formatSize"),
        "pub_date": item.get("pubDate"),
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


def command_search(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/mikan",
        query={"text": args.title},
        body=season_body(args),
        timeout=args.timeout,
    )
    data = unwrap_data(response)
    candidates = flatten_mikan_items(data)
    print_json(
        {
            "ok": True,
            "command": "search",
            "title": args.title,
            "count": len(candidates),
            "candidates": candidates,
        }
    )


def fetch_groups(
    base_url: str, api_key: str, url: str, timeout: int, sample_limit: int
) -> list[dict[str, Any]]:
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/mikanGroup",
        query={"url": url},
        timeout=timeout,
    )
    groups = unwrap_data(response)
    return [
        normalize_group(group, sample_limit=sample_limit)
        for group in ensure_list(groups)
        if isinstance(group, dict)
    ]


def command_groups(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    groups = fetch_groups(base_url, api_key, args.url, args.timeout, args.sample_limit)
    print_json(
        {
            "ok": True,
            "command": "groups",
            "url": args.url,
            "count": len(groups),
            "groups": groups,
        }
    )


def command_plan(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
    response = request_json(
        base_url,
        api_key,
        "POST",
        "/api/mikan",
        query={"text": args.title},
        body=season_body(args),
        timeout=args.timeout,
    )
    candidates = flatten_mikan_items(unwrap_data(response))
    for candidate in candidates:
        if candidate.get("groups"):
            continue
        url = candidate.get("url")
        if not url:
            candidate["groups"] = []
            candidate["group_error"] = "candidate has no Mikan URL"
            continue
        try:
            candidate["groups"] = fetch_groups(
                base_url, api_key, str(url), args.timeout, args.sample_limit
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
            "requires_user_confirmation": True,
            "count": len(candidates),
            "candidates": candidates,
        }
    )


def command_build_from_rss(args: argparse.Namespace) -> None:
    base_url, api_key = resolve_config(args)
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
    print_json(
        {
            "ok": True,
            "command": "build-from-rss",
            "source": body,
            "ani": unwrap_data(response),
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


def resolve_tmdb_credentials(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
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
    return base_url, token, api_key


def request_tmdb_json(
    base_url: str,
    token: str | None,
    api_key: str | None,
    path: str,
    *,
    query: dict[str, str] | None = None,
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
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
            "requires_user_confirmation": True,
            "preview": preview,
            "coverage": preview_coverage(preview),
        }
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
    if ordered:
        anchor_indices = [0, len(ordered) // 2, len(ordered) - 1]
        anchor_indices = list(dict.fromkeys(anchor_indices))
    anchors = [
        {
            "episode": item.get("episode"),
            "title": item.get("title"),
            "pub_date": item.get("pubDate"),
        }
        for index in anchor_indices
        for item in [ordered[index]]
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
            if len(episode_values) < 3
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
        base_url, token, api_key = resolve_tmdb_credentials(args)
        result = request_tmdb_json(
            base_url,
            token,
            api_key,
            f"/{'movie' if args.movie else 'tv'}/{urllib.parse.quote(args.tmdb_id, safe='')}",
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
    base_url, token, api_key = resolve_tmdb_credentials(args)
    query = {"language": args.language} if args.language else None
    result = request_tmdb_json(
        base_url,
        token,
        api_key,
        f"/tv/{urllib.parse.quote(args.tmdb_id, safe='')}/season/{args.season_number}",
        query=query,
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
    base_url, token, api_key = resolve_tmdb_credentials(args)
    result = request_tmdb_json(
        base_url,
        token,
        api_key,
        f"/tv/episode_group/{urllib.parse.quote(args.group_id, safe='')}",
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
    if not subscription_matches_expected(persisted, expected):
        raise AniRssError(
            "write returned, but the persisted subscription does not match the submitted object; "
            "do not retry add before reviewing list output"
        )
    return {
        "verified": True,
        "match_count": len(matches),
        "persisted": persisted,
        "total_subscriptions": len(subscriptions),
    }


def preflight_duplicate_check(
    base_url: str,
    api_key: str,
    expected: dict[str, Any],
    timeout: int,
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
    return {"checked": True, "matching_subscriptions": 0}


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
    preflight = preflight_duplicate_check(base_url, api_key, ani, args.timeout)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ANI-RSS helper for Mikan-first anime subscription planning."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Read-only search plus group planning")
    add_common_args(plan)
    add_season_args(plan)
    plan.add_argument("title")
    plan.add_argument("--sample-limit", type=int, default=5)
    plan.set_defaults(func=command_plan)

    search = subparsers.add_parser("search", help="Read-only Mikan search")
    add_common_args(search)
    add_season_args(search)
    search.add_argument("title")
    search.set_defaults(func=command_search)

    groups = subparsers.add_parser("groups", help="Read-only Mikan group lookup")
    add_common_args(groups)
    groups.add_argument("--url", required=True, help="Mikan anime URL")
    groups.add_argument("--sample-limit", type=int, default=5)
    groups.set_defaults(func=command_groups)

    build = subparsers.add_parser("build-from-rss", help="Convert RSS to Ani JSON")
    add_common_args(build)
    build.add_argument("--rss", required=True)
    build.add_argument("--type", default="mikan")
    build.add_argument("--bgm-url", required=True)
    build.add_argument("--subgroup", required=True)
    build.add_argument("--disabled", action="store_true", help="Create Ani disabled")
    build.set_defaults(func=command_build_from_rss)

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
