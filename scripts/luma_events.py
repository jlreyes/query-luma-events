#!/usr/bin/env python3
"""Query Luma's public, read-only event discovery API."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_URL = "https://api.luma.com"
USER_AGENT = "Mozilla/5.0 (compatible; query-luma-events/1.0; public read-only discovery)"
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 3
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_API = 5
EXIT_RATE_LIMIT = 7


class LumaError(Exception):
    """Base error with an intended process exit code."""

    exit_code = EXIT_API


class NotFoundError(LumaError):
    exit_code = EXIT_NOT_FOUND


class RateLimitError(LumaError):
    exit_code = EXIT_RATE_LIMIT


@dataclass(frozen=True)
class Boundary:
    raw: str
    date_only: date | None
    instant: datetime | None


def eprint(*values: Any) -> None:
    print(*values, file=sys.stderr)


def clean_params(params: Mapping[str, Any] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        else:
            cleaned[key] = str(value)
    return cleaned


def retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    value = headers.get("Retry-After")
    if value:
        try:
            return min(max(float(value), 0.0), 30.0)
        except ValueError:
            pass
    return min(2**attempt, 8)


def get_json(
    path: str,
    params: Mapping[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    query = urlencode(clean_params(params))
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"

    for attempt in range(MAX_RETRIES + 1):
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise LumaError(
                        f"Luma returned non-JSON content for {path}: {exc}"
                    ) from exc
        except HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace")
            if exc.code == 404:
                raise NotFoundError(f"Luma resource not found: {path}") from exc
            if exc.code == 429:
                if attempt < MAX_RETRIES:
                    time.sleep(retry_delay(exc.headers, attempt))
                    continue
                raise RateLimitError(
                    "Luma rate limited the request after conservative retries"
                ) from exc
            if exc.code >= 500 and attempt < MAX_RETRIES:
                time.sleep(retry_delay(exc.headers, attempt))
                continue
            detail = body.strip().replace("\n", " ")[:500]
            raise LumaError(
                f"Luma API returned HTTP {exc.code} for {path}"
                + (f": {detail}" if detail else "")
            ) from exc
        except (URLError, TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(min(2**attempt, 8))
                continue
            raise LumaError(f"Could not reach Luma: {exc}") from exc

    raise LumaError("Luma request failed")


def luma_slug(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise LumaError("Expected a Luma ID, slug, or URL")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        host = parsed.netloc.lower().split(":", 1)[0]
        if host not in {"luma.com", "www.luma.com", "lu.ma", "www.lu.ma"}:
            raise LumaError(f"Not a Luma URL: {value}")
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            raise LumaError(f"Luma URL has no public slug: {value}")
        return segments[0]
    candidate = candidate.split("?", 1)[0].split("#", 1)[0].strip("/")
    if "/" in candidate:
        candidate = candidate.split("/", 1)[0]
    if not candidate:
        raise LumaError(f"Could not extract a Luma slug from: {value}")
    return candidate


def resolve_slug(value: str) -> dict[str, Any]:
    slug = luma_slug(value)
    data = get_json("/url", {"url": slug})
    if not isinstance(data, dict) or not data.get("kind"):
        raise LumaError("Luma returned an unexpected slug-resolution response")
    return data


def unwrap_entity(resolved: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    data = resolved.get("data")
    if not isinstance(data, dict):
        return None
    entity = data.get(name)
    return entity if isinstance(entity, dict) else None


def normalize_space(value: str) -> str:
    return " ".join(value.casefold().split())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug


def bootstrap_places() -> list[dict[str, Any]]:
    data = get_json("/discover/bootstrap-page")
    rows = data.get("places", []) if isinstance(data, dict) else []
    places: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("place"), dict):
            place = dict(row["place"])
            for field in ("event_count", "num_events", "distance_km", "is_subscriber"):
                if field in row and field not in place:
                    place[field] = row[field]
            places.append(place)
    return places


def resolve_place(value: str) -> dict[str, Any]:
    query = normalize_space(value)
    places = bootstrap_places()
    exact = [
        place
        for place in places
        if query in {
            normalize_space(str(place.get("name", ""))),
            normalize_space(str(place.get("slug", ""))),
            normalize_space(str(place.get("api_id", ""))),
        }
    ]
    if exact:
        return exact[0]

    slug = slugify(value)
    data = get_json("/discover/get-place", {"slug": slug})
    if isinstance(data, dict):
        place = data.get("place")
        if isinstance(place, dict):
            return place
    raise NotFoundError(f"Could not resolve Luma place: {value}")


def list_categories() -> list[dict[str, Any]]:
    data = get_json(
        "/discover/category/list-categories", {"pagination_limit": 100}
    )
    rows = data.get("entries", []) if isinstance(data, dict) else []
    categories: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("category"), dict):
            category = dict(row["category"])
            for field in (
                "event_count",
                "num_upcoming_events",
                "subscriber_count",
                "is_subscriber",
            ):
                if field in row:
                    category[field] = row[field]
            categories.append(category)
    return categories


def resolve_category(value: str) -> dict[str, Any]:
    query = normalize_space(value)
    categories = list_categories()
    for category in categories:
        if query in {
            normalize_space(str(category.get("name", ""))),
            normalize_space(str(category.get("slug", ""))),
            normalize_space(str(category.get("api_id", ""))),
        }:
            return category
    raise NotFoundError(f"Could not resolve Luma category: {value}")


def canonical_url(slug_or_url: Any) -> str | None:
    if not isinstance(slug_or_url, str) or not slug_or_url.strip():
        return None
    value = slug_or_url.strip()
    if value.startswith(("https://", "http://")):
        return value
    return f"https://luma.com/{value.lstrip('/')}"


def coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def event_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    event = entry.get("event")
    if isinstance(event, dict):
        return event
    return dict(entry)


def host_names(entry: Mapping[str, Any]) -> list[str]:
    values = entry.get("hosts")
    if not isinstance(values, list):
        values = event_payload(entry).get("hosts")
    names: list[str] = []
    if isinstance(values, list):
        for host in values:
            if isinstance(host, dict) and host.get("name"):
                names.append(str(host["name"]))
    return names


def normalize_event(entry: Mapping[str, Any]) -> dict[str, Any]:
    event = event_payload(entry)
    location = event.get("geo_address_info")
    if not isinstance(location, dict):
        location = {}
    coordinate = event.get("coordinate")
    if not isinstance(coordinate, dict):
        coordinate = {}
    calendar = entry.get("calendar")
    if not isinstance(calendar, dict):
        calendar = event.get("calendar")
    if not isinstance(calendar, dict):
        calendar = {}
    ticket_info = entry.get("ticket_info")
    if not isinstance(ticket_info, dict):
        ticket_info = event.get("ticket_info")
    if not isinstance(ticket_info, dict):
        ticket_info = {}
    price = ticket_info.get("price")
    if not isinstance(price, dict):
        price = None

    api_id = coalesce(event.get("api_id"), entry.get("event_api_id"), entry.get("api_id"))
    slug = event.get("url")
    return {
        "api_id": api_id,
        "name": event.get("name"),
        "url": canonical_url(slug),
        "start_at": coalesce(event.get("start_at"), entry.get("start_at")),
        "end_at": event.get("end_at"),
        "timezone": event.get("timezone"),
        "location_type": event.get("location_type"),
        "location": {
            "name": location.get("address"),
            "short_address": location.get("short_address"),
            "full_address": location.get("full_address"),
            "city": coalesce(location.get("city_state"), location.get("city")),
            "country": location.get("country"),
        },
        "coordinate": {
            "latitude": coordinate.get("latitude"),
            "longitude": coordinate.get("longitude"),
        },
        "calendar": {
            "api_id": coalesce(calendar.get("api_id"), event.get("calendar_api_id")),
            "name": calendar.get("name"),
            "slug": calendar.get("slug"),
        },
        "hosts": host_names(entry),
        "guest_count": coalesce(entry.get("guest_count"), event.get("guest_count")),
        "ticket_count": coalesce(entry.get("ticket_count"), event.get("ticket_count")),
        "is_free": ticket_info.get("is_free"),
        "price": price,
        "is_sold_out": ticket_info.get("is_sold_out"),
        "spots_remaining": ticket_info.get("spots_remaining"),
        "registration_availability": coalesce(
            entry.get("registration_availability"),
            event.get("registration_availability"),
        ),
        "platform": entry.get("platform", "luma"),
    }


def rich_text_to_plain(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    parts: list[str] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        text = node.get("text")
        if isinstance(text, str):
            parts.append(text)
        node_type = node.get("type")
        if node_type == "hard_break":
            parts.append("\n")
        children = node.get("content")
        if isinstance(children, list):
            for child in children:
                visit(child)
        if node_type in {"paragraph", "heading", "bullet_list", "ordered_list"}:
            parts.append("\n")

    visit(value)
    plain = "".join(parts)
    plain = re.sub(r"[ \t]+\n", "\n", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return plain or None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_boundary(value: str | None) -> Boundary | None:
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return Boundary(text, date.fromisoformat(text), None)
        except ValueError as exc:
            raise LumaError(f"Invalid date: {value}") from exc
    instant = parse_iso_datetime(text)
    if instant is None:
        raise LumaError(
            f"Invalid date/time {value!r}; use YYYY-MM-DD or an ISO 8601 timestamp"
        )
    return Boundary(text, None, instant)


def event_local_date(event: Mapping[str, Any], start: datetime) -> date:
    timezone_name = event.get("timezone")
    if isinstance(timezone_name, str) and timezone_name:
        try:
            return start.astimezone(ZoneInfo(timezone_name)).date()
        except ZoneInfoNotFoundError:
            pass
    return start.astimezone(timezone.utc).date()


def boundary_matches(
    event: Mapping[str, Any],
    start: datetime,
    lower: Boundary | None,
    upper: Boundary | None,
) -> bool:
    if lower and lower.date_only is not None:
        if event_local_date(event, start) < lower.date_only:
            return False
    elif lower and lower.instant is not None and start < lower.instant:
        return False

    if upper and upper.date_only is not None:
        if event_local_date(event, start) > upper.date_only:
            return False
    elif upper and upper.instant is not None and start > upper.instant:
        return False
    return True


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def event_matches(
    event: dict[str, Any],
    args: argparse.Namespace,
    *,
    center: tuple[float, float] | None,
) -> bool:
    start = parse_iso_datetime(event.get("start_at"))
    if start is None:
        return False
    if not boundary_matches(event, start, args.date_from, args.date_to):
        return False
    if args.mode and event.get("location_type") != args.mode:
        return False
    if args.price == "free" and event.get("is_free") is not True:
        return False
    if args.price == "paid" and event.get("is_free") is not False:
        return False
    if args.available and (
        event.get("is_sold_out") is True
        or event.get("registration_availability")
        in {"closed", "sold_out", "sold-out"}
    ):
        return False
    if args.min_guests is not None:
        guests = numeric(event.get("guest_count"))
        if guests is None or guests < args.min_guests:
            return False
    if args.radius_km is not None:
        if center is None:
            raise LumaError("--radius-km requires --city or --latitude/--longitude")
        coordinate = event.get("coordinate")
        if not isinstance(coordinate, dict):
            return False
        lat = numeric(coordinate.get("latitude"))
        lon = numeric(coordinate.get("longitude"))
        if lat is None or lon is None:
            return False
        distance = haversine_km(center[0], center[1], lat, lon)
        event["distance_km"] = round(distance, 2)
        if distance > args.radius_km:
            return False
    return True


def dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for event in events:
        key = str(coalesce(event.get("api_id"), event.get("url"), event.get("name")))
        if key in seen:
            continue
        seen.add(key)
        output.append(event)
    return output


def paginate(
    path: str,
    params: Mapping[str, Any],
    *,
    args: argparse.Namespace,
    center: tuple[float, float] | None,
) -> dict[str, Any]:
    cursor = args.cursor
    events: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []
    pages = 0
    has_more = True
    next_cursor: str | None = None

    while pages < args.max_pages and has_more and len(events) < args.limit:
        page_params = dict(params)
        page_params["pagination_limit"] = args.page_size
        if cursor:
            page_params["pagination_cursor"] = cursor
        data = get_json(path, page_params, timeout=args.timeout)
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise LumaError(f"Luma returned an unexpected pagination shape for {path}")
        pages += 1
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            raw_entries.append(entry)
            normalized = normalize_event(entry)
            if event_matches(normalized, args, center=center):
                events.append(normalized)
                if len(events) >= args.limit:
                    break
        has_more = bool(data.get("has_more"))
        next_cursor = data.get("next_cursor")
        if not has_more or not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor

    result_events: Any
    if args.raw:
        result_events = raw_entries
    else:
        result_events = dedupe_events(events)[: args.limit]
        result_events.sort(
            key=lambda item: parse_iso_datetime(item.get("start_at"))
            or datetime.max.replace(tzinfo=timezone.utc)
        )
    return {
        "count": len(result_events),
        "scanned_pages": pages,
        "truncated": bool(
            has_more and (pages >= args.max_pages or len(events) >= args.limit)
        ),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "events": result_events,
    }


def search_selector(args: argparse.Namespace) -> tuple[str, dict[str, Any], tuple[float, float] | None, dict[str, Any]]:
    place: dict[str, Any] | None = None
    category: dict[str, Any] | None = None
    calendar_id: str | None = None
    selector_meta: dict[str, Any] = {}

    if args.url:
        resolved = resolve_slug(args.url)
        kind = resolved.get("kind")
        if kind == "calendar":
            calendar = unwrap_entity(resolved, "calendar")
            if not calendar:
                raise LumaError("Resolved calendar did not contain calendar data")
            calendar_id = str(calendar["api_id"])
            selector_meta["calendar"] = {
                "api_id": calendar_id,
                "name": calendar.get("name"),
                "slug": calendar.get("slug"),
            }
        elif kind == "discover_place":
            place = unwrap_entity(resolved, "place")
        elif kind == "category":
            category = unwrap_entity(resolved, "category")
        elif kind == "event":
            raise LumaError("The supplied URL is an event; use the event command")
        else:
            raise LumaError(f"Unsupported resolved Luma kind: {kind}")

    if args.city:
        place = resolve_place(args.city)
    elif args.place_id:
        data = get_json(
            "/discover/get-place", {"discover_place_api_id": args.place_id}
        )
        place = data.get("place") if isinstance(data, dict) else None
        if not isinstance(place, dict):
            raise NotFoundError(f"Could not resolve Luma place ID: {args.place_id}")

    if args.category:
        category = resolve_category(args.category)
    elif args.category_id:
        category = {"api_id": args.category_id, "name": None, "slug": None}

    if args.calendar:
        calendar_id, calendar_meta = resolve_calendar(args.calendar)
        selector_meta["calendar"] = calendar_meta

    center: tuple[float, float] | None = None
    if args.latitude is not None or args.longitude is not None:
        if args.latitude is None or args.longitude is None:
            raise LumaError("Provide both --latitude and --longitude")
        center = (args.latitude, args.longitude)
    elif place:
        coordinate = place.get("coordinate")
        if isinstance(coordinate, dict):
            lat = numeric(coordinate.get("latitude"))
            lon = numeric(coordinate.get("longitude"))
            if lat is not None and lon is not None:
                center = (lat, lon)

    if place:
        selector_meta["place"] = {
            "api_id": place.get("api_id"),
            "name": place.get("name"),
            "slug": place.get("slug"),
            "timezone": place.get("timezone"),
        }
    if category:
        selector_meta["category"] = {
            "api_id": category.get("api_id"),
            "name": category.get("name"),
            "slug": category.get("slug"),
        }

    if calendar_id:
        params: dict[str, Any] = {
            "calendar_api_id": calendar_id,
            "period": "future",
            "query": args.query,
            "available_only": args.available or None,
        }
        return "/calendar/get-items", params, center, selector_meta

    params = {"query": args.query}
    if category:
        params["discover_category_api_id"] = category.get("api_id")
        if center:
            params["latitude"], params["longitude"] = center
    elif place:
        params["discover_place_api_id"] = place.get("api_id")
    elif center:
        params["latitude"], params["longitude"] = center
    else:
        raise LumaError(
            "search needs --city, --place-id, --category, --category-id, "
            "--calendar, --url, or coordinates"
        )
    return "/discover/get-paginated-events", params, center, selector_meta


def resolve_calendar(value: str) -> tuple[str, dict[str, Any]]:
    if value.startswith("cal-"):
        return value, {"api_id": value, "name": None, "slug": None}
    resolved = resolve_slug(value)
    if resolved.get("kind") != "calendar":
        raise LumaError(
            f"{value!r} resolves to {resolved.get('kind')}, not a calendar"
        )
    calendar = unwrap_entity(resolved, "calendar")
    if not calendar or not calendar.get("api_id"):
        raise LumaError("Resolved calendar response did not contain an API ID")
    return str(calendar["api_id"]), {
        "api_id": calendar.get("api_id"),
        "name": calendar.get("name"),
        "slug": calendar.get("slug"),
    }


def command_search(args: argparse.Namespace) -> dict[str, Any]:
    path, params, center, selector = search_selector(args)
    result = paginate(path, params, args=args, center=center)
    return {"source": "luma_public_discovery", "selector": selector, **result}


def command_event(args: argparse.Namespace) -> dict[str, Any]:
    identifier = luma_slug(args.event)
    data = get_json(
        "/event/get", {"event_api_id": identifier}, timeout=args.timeout
    )
    if not isinstance(data, dict):
        raise LumaError("Luma returned an unexpected event-detail response")
    if args.raw:
        event_data: Any = data
    else:
        event_data = normalize_event(data)
        event = data.get("event")
        if isinstance(event, dict):
            event_data["description"] = coalesce(
                data.get("description"),
                event.get("description"),
                data.get("description_md"),
                rich_text_to_plain(data.get("description_mirror")),
                rich_text_to_plain(event.get("description_mirror")),
            )
        event_data["ticket_types"] = data.get("ticket_types")
        event_data["registration_questions"] = data.get("registration_questions")
    return {"source": "luma_public_discovery", "event": event_data}


def calendar_period(args: argparse.Namespace) -> str:
    if args.period != "auto":
        return args.period
    if args.date_to and args.date_to.instant:
        if args.date_to.instant < datetime.now(timezone.utc):
            return "past"
    if args.date_to and args.date_to.date_only:
        if args.date_to.date_only < datetime.now(timezone.utc).date():
            return "past"
    return "future"


def command_calendar(args: argparse.Namespace) -> dict[str, Any]:
    calendar_id, calendar_meta = resolve_calendar(args.calendar)
    period = calendar_period(args)
    params: dict[str, Any] = {
        "calendar_api_id": calendar_id,
        "period": period,
        "query": args.query,
        "available_only": args.available or None,
    }
    result = paginate(
        "/calendar/get-items", params, args=args, center=None
    )
    return {
        "source": "luma_public_discovery",
        "selector": {"calendar": calendar_meta, "period": period},
        **result,
    }


def command_resolve(args: argparse.Namespace) -> dict[str, Any]:
    data = resolve_slug(args.value)
    if args.raw:
        return data
    kind = data.get("kind")
    entity_name = {
        "calendar": "calendar",
        "event": "event",
        "discover_place": "place",
        "category": "category",
    }.get(str(kind))
    entity = unwrap_entity(data, entity_name) if entity_name else None
    return {
        "kind": kind,
        "api_id": entity.get("api_id") if entity else None,
        "name": entity.get("name") if entity else None,
        "slug": entity.get("slug") if entity else luma_slug(args.value),
        "url": canonical_url(
            entity.get("url") if entity and entity.get("url") else luma_slug(args.value)
        ),
    }


def match_rows(rows: Sequence[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    if not query:
        return list(rows)
    needle = normalize_space(query)
    exact = [
        row
        for row in rows
        if needle
        in {
            normalize_space(str(row.get("name", ""))),
            normalize_space(str(row.get("slug", ""))),
            normalize_space(str(row.get("api_id", ""))),
        }
    ]
    if exact:
        return exact
    return [
        row
        for row in rows
        if needle
        in normalize_space(
            " ".join(
                str(row.get(field, ""))
                for field in ("name", "slug", "api_id", "description")
            )
        )
    ]


def command_places(args: argparse.Namespace) -> dict[str, Any]:
    rows = match_rows(bootstrap_places(), args.match)
    fields = (
        "api_id",
        "name",
        "slug",
        "timezone",
        "coordinate",
        "event_count",
        "geo_continent_name",
    )
    return {
        "count": len(rows),
        "places": [{field: row.get(field) for field in fields} for row in rows],
    }


def command_categories(args: argparse.Namespace) -> dict[str, Any]:
    rows = match_rows(list_categories(), args.match)
    fields = (
        "api_id",
        "name",
        "slug",
        "description",
        "event_count",
        "subscriber_count",
    )
    return {
        "count": len(rows),
        "categories": [{field: row.get(field) for field in fields} for row in rows],
    }


def add_common_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--query", help="API-native keyword query")
    parser.add_argument(
        "--from",
        dest="date_from_raw",
        metavar="DATE",
        help="Inclusive YYYY-MM-DD or ISO timestamp lower bound",
    )
    parser.add_argument(
        "--to",
        dest="date_to_raw",
        metavar="DATE",
        help="Inclusive YYYY-MM-DD or ISO timestamp upper bound",
    )
    parser.add_argument("--mode", choices=("online", "offline"))
    parser.add_argument("--price", choices=("free", "paid"))
    parser.add_argument(
        "--available", action="store_true", help="Exclude sold-out/closed events"
    )
    parser.add_argument("--min-guests", type=int)
    parser.add_argument("--radius-km", type=float)
    parser.add_argument("--limit", type=int, default=10, help="Output event limit")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Maximum API pages to scan (default: 5)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Events requested per API page (default: 50)",
    )
    parser.add_argument("--cursor", help="Opaque Luma pagination cursor")
    parser.add_argument(
        "--raw", action="store_true", help="Return raw API entries instead of normalized events"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query Luma's public, read-only event discovery API"
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Find upcoming public events")
    search.add_argument("--city", help="Luma city name or slug")
    search.add_argument("--place-id", help="Luma discplace-... ID")
    search.add_argument("--category", help="Luma category name or slug")
    search.add_argument("--category-id", help="Luma cat-... ID")
    search.add_argument("--calendar", help="Calendar ID, slug, or URL")
    search.add_argument("--url", help="Public place, category, or calendar URL")
    search.add_argument("--latitude", type=float)
    search.add_argument("--longitude", type=float)
    add_common_filters(search)
    search.set_defaults(handler=command_search)

    event = subparsers.add_parser("event", help="Get one public event")
    event.add_argument("event", help="evt-... ID, public slug, or Luma URL")
    event.add_argument("--raw", action="store_true")
    event.set_defaults(handler=command_event)

    calendar = subparsers.add_parser(
        "calendar", help="List public events from one calendar"
    )
    calendar.add_argument("calendar", help="cal-... ID, public slug, or Luma URL")
    calendar.add_argument(
        "--period",
        choices=("auto", "future", "past"),
        default="auto",
        help="Calendar period (default: auto)",
    )
    add_common_filters(calendar)
    calendar.set_defaults(handler=command_calendar)

    resolve = subparsers.add_parser(
        "resolve", help="Resolve a Luma slug or URL to its public entity"
    )
    resolve.add_argument("value")
    resolve.add_argument("--raw", action="store_true")
    resolve.set_defaults(handler=command_resolve)

    places = subparsers.add_parser("places", help="List discovery places")
    places.add_argument("--match", help="Filter by name, slug, ID, or description")
    places.set_defaults(handler=command_places)

    categories = subparsers.add_parser(
        "categories", help="List discovery categories"
    )
    categories.add_argument("--match", help="Filter by name, slug, ID, or description")
    categories.set_defaults(handler=command_categories)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if hasattr(args, "date_from_raw"):
        args.date_from = parse_boundary(args.date_from_raw)
        args.date_to = parse_boundary(args.date_to_raw)
        if (
            args.date_from
            and args.date_to
            and args.date_from.date_only
            and args.date_to.date_only
            and args.date_from.date_only > args.date_to.date_only
        ):
            raise LumaError("--from must not be after --to")
    if hasattr(args, "limit") and not 1 <= args.limit <= 500:
        raise LumaError("--limit must be between 1 and 500")
    if hasattr(args, "max_pages") and not 1 <= args.max_pages <= 50:
        raise LumaError("--max-pages must be between 1 and 50")
    if hasattr(args, "page_size") and not 1 <= args.page_size <= 100:
        raise LumaError("--page-size must be between 1 and 100")
    if args.timeout <= 0:
        raise LumaError("--timeout must be positive")
    if hasattr(args, "radius_km") and args.radius_km is not None and args.radius_km <= 0:
        raise LumaError("--radius-km must be positive")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        result = args.handler(args)
        json.dump(
            result,
            sys.stdout,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except LumaError as exc:
        eprint(f"error: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        eprint("error: interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
