# Luma public discovery API reference

Verified against Luma's live web frontend and public responses on 2026-07-25.

This is an undocumented, read-only surface used by `luma.com`. It is distinct from Luma's documented API-key product at `https://public-api.luma.com`. No authentication was required for the routes below when verified. Treat every route and field as unstable.

## Base behavior

- Base URL: `https://api.luma.com`
- Method: `GET`
- Response: JSON
- Recommended headers:
  - `Accept: application/json`
  - A descriptive `User-Agent`
- Pagination envelope:

```json
{
  "entries": [],
  "has_more": true,
  "next_cursor": "opaque"
}
```

Send the returned cursor as `pagination_cursor`. Never decode or construct a cursor.

## Routes used by the skill

### Resolve a public slug

`GET /url`

Parameters:

- `url`: first path segment from a `luma.com` or legacy `lu.ma` URL

Returns a discriminated object whose `kind` is commonly `event`, `calendar`, `discover_place`, or `category`, with the entity under `data`.

### List discovery events

`GET /discover/get-paginated-events`

Observed parameters:

- `discover_place_api_id`: place/city ID (`discplace-...`)
- `discover_category_api_id`: category ID (`cat-...`)
- `slug`: place or category slug
- `query`: keyword search
- `latitude`, `longitude`: center used for category-near-location queries
- `north`, `south`, `east`, `west`: map bounding box
- `pagination_limit`
- `pagination_cursor`

Important: sending both a place ID and category ID did not combine the filters when verified. Luma's frontend combines them by sending category ID plus the place coordinate. The CLI reproduces that behavior.

Each entry normally includes:

- `api_id`
- `event`
- `calendar`
- `hosts`
- `guest_count`
- `ticket_count`
- `ticket_info`
- `registration_availability`
- `featured_city`

The `event.url` field is usually a bare slug, not an absolute URL.

### Get event detail

`GET /event/get`

Parameters:

- `event_api_id`: accepts an `evt-...` ID or a public event slug

The response contains the event, calendar, hosts, ticket types, registration state, descriptions, venue/virtual metadata, and other public page data. Exact fields vary by event.

### List calendar items

`GET /calendar/get-items`

Parameters:

- `calendar_api_id`: resolved `cal-...` ID
- `query`: keyword search
- `period`: `future`, `past`, or `specific`
- `after`, `before`: ISO timestamps for `period=specific`
- `tag_api_ids`: comma-separated calendar tag IDs
- `available_only`: boolean
- `pagination_limit`
- `pagination_cursor`

Entries can have `platform: "luma"` or `platform: "external"`. A Luma calendar can therefore contain externally hosted events.

### Resolve a place

`GET /discover/get-place`

Parameters:

- `slug`
- `discover_place_api_id`

### Discovery bootstrap

`GET /discover/bootstrap-page`

Returns `featured_place`, `places`, `categories`, and featured `calendars`. The skill uses the `places` array to map human city names such as `New York` to Luma slugs, coordinates, IDs, and timezones.

### List categories

`GET /discover/category/list-categories`

Parameters:

- `pagination_limit`
- `pagination_cursor`

Each entry contains a `category` object with `api_id`, `name`, `slug`, description, and counts.

## Source trail

- Live Luma web frontend: `https://luma.com/discover`
- Luma documented management API: `https://docs.luma.com/reference/getting-started-with-your-api`
- Luma management OpenAPI: `https://public-api.luma.com/openapi.json`

The route inventory was verified from Luma's public frontend bundles and then exercised with small, read-only requests. Do not infer authorization to use mutation or private endpoints from their presence in frontend code.
