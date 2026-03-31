# ephemeralREST — API Reference

## Introduction

ephemeralREST is a REST API for astronomical chart calculations. It uses the **Swiss Ephemeris** — the same calculation engine used by professional astrological software — to compute precise planetary positions, house cusps, and angles for any date, time, and location between 1800 and 2200 CE.

This document explains how to connect to the API, what you can ask it to do, and what you will receive back. Every endpoint is shown with a working `curl` example you can run directly from a terminal.

If you are new to REST APIs, the short version is: you send a **request** containing data formatted as JSON, and the server sends back a **response** also formatted as JSON. JSON is just a way of structuring data that both humans and computers can read.

---

## Licence

ephemeralREST is open source software, released under the **GNU Affero General Public License v3 (AGPL v3)**.

The source code is available on GitHub. As a user of this API you are entitled to receive the source code of the running application — this is a requirement of the AGPL v3 network service clause, which applies because the software uses the Swiss Ephemeris library (itself AGPL v3).

---

## Before you start

### Getting an API key

All calculation endpoints require an API key. To get one:

1. Go to the home page and fill out the **Request a Key** form.
2. Provide your name, email address, and a domain name (use `*` if you do not have a website — for example, if you are writing a script or desktop tool).
3. Your key will be emailed to you once approved. It looks something like this: `eph_a1b2c3d4e5f6...`

**Your key is shown once and only once.** Save it somewhere safe as soon as you receive it. If you lose it, you will need to request a rotation via your portal.

Keys are free for development and low-volume personal use. If you need higher limits for a production application, mention your expected volume in the request form.

### Your key portal

Once you have a key, you can sign in at `/login.php` to:

- View your key details and rate limits
- Rotate your key if it has been compromised
- Configure your output defaults (which bodies and fields are returned)

---

## Making requests

### Base URL

All endpoints are relative to the API base URL. This document uses:

```
https://api.yourdomain.com
```

Replace this with the actual URL of the instance you are connecting to.

### Authentication

Include your API key in a header called `X-API-Key` on every request that requires authentication:

```bash
curl https://api.yourdomain.com/health
# No key needed for /health

curl https://api.yourdomain.com/calculate \
  -H "X-API-Key: eph_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"chart_name":"Test","datetime":"2026-01-01 12:00:00","location":"London"}'
```

If you omit the key on a protected endpoint, or supply an invalid key, you will receive a `401` error:

```json
{ "error": "Invalid or missing API key", "status": 401 }
```

### Request format

For endpoints that accept a body (all `POST` requests), send JSON with the `Content-Type: application/json` header. Omitting this header is a common source of `400` errors.

```bash
# Correct
curl -X POST https://api.yourdomain.com/calculate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{ ... }'

# Wrong — missing Content-Type
curl -X POST https://api.yourdomain.com/calculate \
  -H "X-API-Key: eph_your_key_here" \
  -d '{ ... }'
```

### Response format

All responses are JSON objects. Successful responses contain the requested data. Error responses always contain an `error` field and a `status` field:

```json
{
  "error": "chart_name is required",
  "status": 400
}
```

---

## Error reference

| HTTP status | Meaning | Common causes |
|---|---|---|
| `400` | Bad request | Missing required field, invalid date format, unknown house system |
| `401` | Unauthorized | Missing or invalid `X-API-Key` header |
| `403` | Forbidden | Endpoint requires admin access |
| `404` | Not found | Chart UUID does not exist, or endpoint path is wrong |
| `409` | Conflict | Attempting to create something that already exists |
| `429` | Too many requests | Rate limit exceeded — wait before retrying |
| `500` | Server error | Unexpected internal error — check server logs |

When you receive a `429`, the response will tell you which limit was hit. Wait for the window to reset (one minute, one hour, or one day depending on which limit) before retrying. Do not retry immediately in a tight loop.

---

## Understanding coordinates

The API returns positions in two coordinate systems.

**Geocentric** means positions as seen from the centre of the Earth. This is the standard for astrological chart work. All planets, nodes, angles, and special points are available geocentrically.

**Heliocentric** means positions as seen from the centre of the Sun. This is used in some research and cosmobiological traditions. The Moon and lunar nodes have no heliocentric position. Earth itself has a heliocentric position (it is not available geocentrically).

Both systems use the **ecliptic** as the reference plane — the apparent path of the Sun through the sky over the course of a year. Positions are given as **ecliptic longitude** (0–360°), which corresponds to zodiacal position, and **ecliptic latitude** (deviation north or south of the ecliptic plane).

The API also returns **equatorial coordinates** — right ascension and declination — which are useful for relating planetary positions to the observable sky.

### Reading a longitude

Longitude runs from 0° to 360°. The zodiac divides this into twelve 30° segments:

| Longitude range | Sign |
|---|---|
| 0°–30° | Aries |
| 30°–60° | Taurus |
| 60°–90° | Gemini |
| 90°–120° | Cancer |
| 120°–150° | Leo |
| 150°–180° | Virgo |
| 180°–210° | Libra |
| 210°–240° | Scorpio |
| 240°–270° | Sagittarius |
| 270°–300° | Capricorn |
| 300°–330° | Aquarius |
| 330°–360° | Pisces |

So a Sun at longitude `354.1` is at 24°06' Pisces (354.1 − 330 = 24.1°, and 0.1 × 60 = 6').

### Retrograde motion

A planet is **retrograde** when it appears to be moving backwards against the stars as seen from Earth. This is an optical effect caused by the relative speeds of Earth and the other planet in their orbits. The `retrograde` field is a boolean: `true` means the planet is retrograde at the given moment. A negative `longitude_speed` also indicates retrograde motion.

---

## Dates and times

### Formats accepted

The API accepts several date and time formats:

| Format | Example |
|---|---|
| ISO 8601 with time | `1985-06-12T14:30:00` |
| ISO 8601 with UTC offset | `1985-06-12T14:30:00+10:00` |
| Space-separated | `1985-06-12 14:30:00` |
| Date only | `1985-06-12` |

When you supply a time without a UTC offset, it is interpreted as **local time at the given location**. The API looks up the timezone and daylight saving status for the location and automatically converts to UTC for the calculation. This is almost always what you want.

If you supply a UTC offset explicitly (e.g. `+10:00`), that offset is used as-is and no timezone lookup is performed.

A date with no time component is treated as midnight local time at the given location.

### Historical dates

The Swiss Ephemeris supports dates from 1800 BCE to 2400 CE. For dates before the Gregorian calendar reform (before 1582-10-15), the Julian calendar is used. Most astrological software handles this automatically, and so does this API.

---

## How locations work

The API accepts a location as a plain text string, geocodes it using Google Maps, and returns the canonical coordinates, timezone, and daylight saving status. Results are cached for 30 days so repeated requests for the same location do not consume Google API quota.

Examples of location strings that work:

```
"London"
"London, UK"
"New York, NY"
"Sydney, Australia"
"48.8566, 2.3522"           ← latitude,longitude pair
```

The API always returns the resolved location details alongside the chart data so you can confirm that the correct place was found.

If you want to resolve a location before submitting a chart calculation, use the `/locations/resolve` endpoint described below.

---

## House systems

House systems divide the chart into twelve segments (houses) based on the relationship between the ecliptic and the local horizon. The choice of house system affects only the house cusps and which house a planet falls in — it does not affect planetary longitudes.

The API supports fifteen house systems:

| Value | System | Notes |
|---|---|---|
| `placidus` | Placidus | Most widely used in Western astrology |
| `koch` | Koch | Popular in Germany and Central Europe |
| `whole_sign` | Whole Sign | Each sign = one house; ASC determines which sign is the first house |
| `equal` | Equal | ASC at the start of the first house, all houses equal 30° |
| `regiomontanus` | Regiomontanus | Space-based system dividing the celestial equator |
| `campanus` | Campanus | Space-based system dividing the prime vertical |
| `porphyrius` | Porphyrius | Divides each quadrant into three equal parts |
| `vehlow_equal` | Vehlow Equal | Equal houses with ASC in the middle of the first house |
| `meridian` | Meridian / Axial Rotation | Divides the equator |
| `azimuthal` | Azimuthal / Horizontal | Based on the horizon |
| `topocentric` | Topocentric (Polich/Page) | Approximates Placidus using a simpler algorithm |
| `alcabitus` | Alcabitus | Medieval semi-arc system |
| `morinus` | Morinus | Space-based, does not use ASC or MC |
| `krusinski` | Krusinski-Pisa-Goelzer | Based on the ecliptic intersecting the prime vertical |
| `gauquelin` | Gauquelin Sectors | 36 equal sectors, used in statistical research |

To omit house cusps entirely from the response, pass `null` or omit the `house_system` field. The ASC and MC are still calculated and returned when a location is provided, regardless of whether house cusps are requested.

---

## Output configuration

Every API key has an output configuration stored against it that controls which bodies and fields appear in responses. The server has a set of defaults, and your key can override any of them.

You can manage your output configuration via the key portal at `/login.php`.

In addition, any individual request can include an `output` object to override settings for just that one request. This is useful for requests where you need a body or field that is off by default, or to reduce response size by excluding data you do not need.

```bash
curl -X POST https://api.yourdomain.com/calculate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "chart_name": "Test",
    "datetime": "1985-06-12 14:30:00",
    "location": "London",
    "output": {
      "heliocentric": false,
      "right_ascension": false,
      "declination": false,
      "bodies": {
        "asteroids": false,
        "mean_node": false,
        "true_node": true,
        "mean_lilith": true
      },
      "meta": {
        "api_usage": false
      }
    }
  }'
```

Per-request overrides are applied on top of your key's stored configuration, which is itself applied on top of the server defaults. The full resolution order is:

```
Server defaults → Key output config → Per-request output overrides
```

Only the fields you include in an override are changed. Everything else retains its value from the level above.

---

## Endpoints

---

### Health check

#### `GET /health`

No authentication required. Returns the server status and basic information about the running instance. Use this to confirm the API is reachable before making authenticated requests.

```bash
curl https://api.yourdomain.com/health
```

```json
{
  "status": "ok",
  "timestamp": "2026-03-21T12:00:00",
  "supported_house_systems": ["placidus", "koch", "whole_sign", "..."],
  "registered_users": ["cosmobiology.online", "mindforce"]
}
```

---

### Location endpoints

#### `GET /autocomplete?q=<query>`

Suggest location names matching a partial string. No authentication required. Useful for building a location search field in a frontend application.

```bash
curl "https://api.yourdomain.com/autocomplete?q=Perth"
```

```json
{
  "predictions": [
    { "description": "Perth WA, Australia", "place_id": "ChIJgf3RJh9HqakR0WBB..." },
    { "description": "Perth, Scotland, UK",  "place_id": "ChIJ..." }
  ]
}
```

The `place_id` value can be passed directly as the `location` field in a calculate request instead of a text string, which avoids an additional geocoding round-trip.

---

#### `POST /locations/resolve`

Resolve a location string to its canonical record including coordinates, timezone, and daylight saving offset. No authentication required. Results are cached — a second request for the same place returns immediately from cache without consuming Google API quota.

```bash
curl -X POST https://api.yourdomain.com/locations/resolve \
  -H "Content-Type: application/json" \
  -d '{"place_name": "Melbourne, Australia"}'
```

```json
{
  "success": true,
  "place": {
    "formatted_name": "Melbourne VIC, Australia",
    "latitude": -37.8136,
    "longitude": 144.9631,
    "timezone_id": "Australia/Melbourne",
    "utc_offset_seconds": 39600,
    "dst_offset_seconds": 3600,
    "daylight_saving": true,
    "source": "google"
  }
}
```

The `source` field is `"google"` on the first request and `"cache"` on subsequent requests for the same location. The `daylight_saving` field reflects whether DST was active at the time of the request, not at the time of any chart.

---

### Chart calculation

#### `POST /calculate`

Calculate a natal or event chart. This is the core endpoint of the API.

The response is automatically saved and a UUID (`chart_id`) returned. You can retrieve the chart later using `GET /chart/<chart_id>` without recalculating it, which saves both time and API quota.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `chart_name` | string | Yes | A label for this chart — for your reference only |
| `datetime` | string | Yes | The date and time of the event |
| `location` | string | Yes | A place name, coordinates, or Google place ID |
| `house_system` | string | No | House system to use — see list above. Omit for no cusps |
| `recalc` | boolean | No | If `true`, recalculates an existing chart in place |
| `chart_id` | string | No | Required when `recalc` is `true` |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/calculate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "chart_name": "Albert Einstein",
    "datetime": "1879-03-14 11:30:00",
    "location": "Ulm, Germany",
    "house_system": "placidus"
  }'
```

**Response (abridged)**

```json
{
  "chart_id": "a3f2c1d4-7e8b-4f2a-9c1d-3e4f5a6b7c8d",
  "chart_name": "Albert Einstein",
  "datetime_utc": "1879-03-14T10:30:00+00:00",
  "datetime_local": "1879-03-14T11:30:00+01:00",
  "julian_day": 2407335.9375,
  "location": {
    "formatted_address": "Ulm, Germany",
    "latitude": 48.3974,
    "longitude": 9.9936,
    "timezone": "Europe/Berlin",
    "utc_offset_seconds": 3600,
    "daylight_saving": false
  },
  "house_cusps": {
    "system": "placidus",
    "cusps": {
      "1": 11.24,
      "2": 44.71,
      "3": 77.80,
      "4": 101.58,
      "5": 116.89,
      "6": 122.44,
      "7": 191.24,
      "8": 224.71,
      "9": 257.80,
      "10": 281.58,
      "11": 296.89,
      "12": 302.44
    },
    "asc": 11.24,
    "mc": 281.58,
    "vertex": 194.38,
    "east_point": 14.81,
    "armc": 279.61
  },
  "planetary_positions": {
    "sun": {
      "longitude": 354.10,
      "latitude": 0.00,
      "distance_au": 0.9940,
      "longitude_speed": 1.013,
      "latitude_speed": 0.000,
      "declination_speed": 0.390,
      "right_ascension": 352.78,
      "declination": -2.15,
      "retrograde": false
    },
    "moon": {
      "longitude": 245.60,
      "latitude": -3.81,
      "distance_au": 0.00267,
      "longitude_speed": 12.214,
      "latitude_speed": -0.044,
      "declination_speed": -4.271,
      "right_ascension": 243.19,
      "declination": -24.33,
      "retrograde": false
    },
    "mercury": { "...": "..." },
    "asc": { "longitude": 11.24, "latitude": 0, "...": "..." },
    "mc":  { "longitude": 281.58, "latitude": 0, "...": "..." }
  },
  "heliocentric": {
    "earth": { "longitude": 174.10, "...": "..." },
    "mercury": { "...": "..." }
  },
  "meta": {
    "from_cache": false,
    "api_usage": { "requests_used": 42, "requests_remaining": 4958 }
  }
}
```

**Position fields explained**

| Field | Description |
|---|---|
| `longitude` | Ecliptic longitude 0°–360° |
| `latitude` | Ecliptic latitude — deviation from ecliptic plane |
| `distance_au` | Distance from reference point in astronomical units |
| `longitude_speed` | Degrees per day in longitude. Negative = retrograde |
| `latitude_speed` | Degrees per day in latitude |
| `declination_speed` | Degrees per day in declination |
| `right_ascension` | Equatorial right ascension in degrees (0–360°) |
| `declination` | Equatorial declination in degrees. Negative = south |
| `retrograde` | `true` if the body is retrograde at this moment |

---

#### `GET /chart/<chart_id>`

Retrieve a previously calculated chart by its UUID. No recalculation is performed.

```bash
curl https://api.yourdomain.com/chart/a3f2c1d4-7e8b-4f2a-9c1d-3e4f5a6b7c8d \
  -H "X-API-Key: eph_your_key_here"
```

Returns `404` if the chart does not exist. The response structure is identical to the `/calculate` response.

---

### Derived charts

Derived charts are charts calculated by modifying or relating to an existing natal chart. They are linked to the original (called the **radix**) by its `chart_id` and automatically saved. Each derived chart receives its own `derived_chart_id`.

#### `POST /chart/<chart_id>/progressions`

Secondary progressions use the **day-for-a-year** method: each day after birth corresponds to one year of life. The progressed chart is calculated at noon UT on the Julian day corresponding to the progressed date.

| Field | Type | Required | Description |
|---|---|---|---|
| `progression_date` | string | Yes | The date to progress to |
| `location` | string | No | Current location — affects progressed ASC/MC. Defaults to natal location |
| `house_system` | string | No | House system for progressed angles |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/chart/a3f2c1d4-.../progressions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "progression_date": "2026-03-21",
    "location": "Sydney, Australia",
    "house_system": "placidus"
  }'
```

```json
{
  "derived_chart_id": "b8c1d2e3-...",
  "chart_type": "secondary_progression",
  "progression_date": "2026-03-21",
  "days_elapsed": 53279,
  "progressed_jd": 2460756.5,
  "natal_chart_id": "a3f2c1d4-...",
  "planetary_positions": {
    "sun":  { "longitude": 66.31, "retrograde": false, "...": "..." },
    "moon": { "longitude": 102.47, "...": "..." }
  }
}
```

---

#### `POST /chart/<chart_id>/solar-arc`

Solar arc directions apply a uniform arc to all natal positions. The arc is the difference between the progressed Sun's longitude and the natal Sun's longitude, calculated using the secondary progression method.

| Field | Type | Required | Description |
|---|---|---|---|
| `progression_date` | string | Yes | The date to direct to |
| `location` | string | No | Current location for directed angles |
| `house_system` | string | No | House system for directed angles |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/chart/a3f2c1d4-.../solar-arc \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "progression_date": "2026-03-21"
  }'
```

```json
{
  "derived_chart_id": "c9d2e3f4-...",
  "chart_type": "solar_arc",
  "progression_date": "2026-03-21",
  "solar_arc_geo": 55.412,
  "solar_arc_helio": 55.391,
  "natal_chart_id": "a3f2c1d4-...",
  "planetary_positions": {
    "sun":  { "longitude": 49.51, "...": "..." },
    "moon": { "longitude": 301.01, "...": "..." }
  }
}
```

The `solar_arc_geo` and `solar_arc_helio` values are the arcs applied — approximately one degree per year of life.

---

#### `POST /chart/<chart_id>/solar-return`

Finds the exact moment the Sun returns to its natal longitude in a given year, using Newton's method for precision to within a few seconds. Calculates a full chart for that moment.

| Field | Type | Required | Description |
|---|---|---|---|
| `return_year` | integer | Yes | Year of the solar return (1800–2200) |
| `location` | string | No | Current residence — affects angles and houses. Defaults to natal location |
| `house_system` | string | No | House system override |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/chart/a3f2c1d4-.../solar-return \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "return_year": 2026,
    "location": "London, UK",
    "house_system": "placidus"
  }'
```

```json
{
  "derived_chart_id": "d1e2f3a4-...",
  "chart_type": "solar_return",
  "return_year": 2026,
  "return_datetime_utc": "2026-03-14T09:47:32",
  "return_datetime_local": "2026-03-14T09:47:32+00:00",
  "natal_sun_longitude": 354.098,
  "return_sun_longitude": 354.098,
  "natal_chart_id": "a3f2c1d4-...",
  "location": {
    "formatted_address": "London, UK",
    "latitude": 51.5074,
    "longitude": -0.1278
  },
  "planetary_positions": { "...": "..." },
  "house_cusps": { "...": "..." }
}
```

---

#### `POST /chart/<chart_id>/lunar-return`

Finds the exact moment the Moon returns to its natal longitude in a given month. There are approximately 13 lunar returns per year.

| Field | Type | Required | Description |
|---|---|---|---|
| `return_year` | integer | Yes | Year |
| `return_month` | integer | Yes | Month (1–12) |
| `location` | string | No | Current residence |
| `house_system` | string | No | House system override |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/chart/a3f2c1d4-.../lunar-return \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "return_year": 2026,
    "return_month": 3,
    "location": "London, UK",
    "house_system": "whole_sign"
  }'
```

```json
{
  "derived_chart_id": "e2f3a4b5-...",
  "chart_type": "lunar_return",
  "return_year": 2026,
  "return_month": 3,
  "return_datetime_utc": "2026-03-13T22:18:44",
  "natal_moon_longitude": 245.603,
  "return_moon_longitude": 245.603,
  "natal_chart_id": "a3f2c1d4-...",
  "planetary_positions": { "...": "..." },
  "house_cusps": { "...": "..." }
}
```

---

#### `GET /chart/<chart_id>/derived`

List all derived charts saved for a given radix. Optionally filter by type.

**Query parameters**

| Parameter | Values | Description |
|---|---|---|
| `type` | `solar_return`, `lunar_return`, `secondary_progression`, `solar_arc` | Filter by chart type |

```bash
# All derived charts
curl https://api.yourdomain.com/chart/a3f2c1d4-.../derived \
  -H "X-API-Key: eph_your_key_here"

# Only solar returns
curl "https://api.yourdomain.com/chart/a3f2c1d4-.../derived?type=solar_return" \
  -H "X-API-Key: eph_your_key_here"
```

```json
{
  "count": 4,
  "radix_chart_id": "a3f2c1d4-...",
  "derived_charts": [
    {
      "id": "d1e2f3a4-...",
      "chart_type": "solar_return",
      "chart_name": "Albert Einstein — Solar Return 2026",
      "reference_date": "2026-03-14",
      "created_at": "2026-03-21T10:00:00",
      "last_accessed": "2026-03-21T10:00:00",
      "access_count": 1
    },
    {
      "id": "e2f3a4b5-...",
      "chart_type": "lunar_return",
      "reference_date": "2026-03-13",
      "created_at": "2026-03-21T10:05:00"
    }
  ]
}
```

---

#### `GET /derived/<derived_chart_id>`

Retrieve a specific derived chart including full position data.

```bash
curl https://api.yourdomain.com/derived/d1e2f3a4-... \
  -H "X-API-Key: eph_your_key_here"
```

Returns the same structure as the endpoint that created it.

---

#### `DELETE /derived/<derived_chart_id>`

Permanently delete a derived chart. This cannot be undone.

```bash
curl -X DELETE https://api.yourdomain.com/derived/d1e2f3a4-... \
  -H "X-API-Key: eph_your_key_here"
```

```json
{ "message": "Derived chart deleted" }
```

---

### Monthly ephemeris

#### `POST /ephemeris`

Returns planetary positions at **noon UT** for every day of a given month. Because no location is involved, house cusps, ASC, MC, and Part of Fortune are not included.

This endpoint is useful for generating tabular ephemeris data, plotting planetary motion over a month, or identifying when planets change sign or direction.

| Field | Type | Required | Description |
|---|---|---|---|
| `year` | integer | Yes | Year (1800–2200) |
| `month` | integer | Yes | Month (1–12) |
| `output` | object | No | Per-request output overrides |

```bash
curl -X POST https://api.yourdomain.com/ephemeris \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{"year": 2026, "month": 3}'
```

```json
{
  "year": 2026,
  "month": 3,
  "month_name": "March",
  "days": {
    "2026-03-01": {
      "julian_day": 2461104.0,
      "geocentric": {
        "sun":     { "longitude": 340.91, "latitude": 0.0, "retrograde": false, "longitude_speed": 0.993 },
        "moon":    { "longitude": 136.02, "latitude": 4.21, "longitude_speed": 13.47 },
        "mercury": { "longitude": 322.41, "latitude": -2.10, "retrograde": false },
        "venus":   { "...": "..." }
      },
      "heliocentric": {
        "earth":   { "longitude": 160.91, "latitude": -0.0003, "distance_au": 0.9915 },
        "mercury": { "...": "..." }
      }
    },
    "2026-03-02": { "...": "..." }
  }
}
```

The response includes all 28–31 days of the month. For March, that means 31 day entries each containing all active bodies.

---

### Apsides

Apsides are the closest and furthest points in an orbit. For the Moon these are called **perigee** (closest to Earth) and **apogee** (furthest from Earth). For planets they are **perihelion** (closest to the Sun) and **aphelion** (furthest from the Sun).

Apside positions and events are used in mundane astrology, cosmobiology, and astronomical research. The Moon's apogee is also called the **Black Moon** or **Lilith**.

#### `POST /apsides`

Returns the current positions of lunar and planetary apsides for a given datetime.

| Field | Type | Required | Description |
|---|---|---|---|
| `datetime` | string | Yes | The datetime to calculate for |
| `output` | object | No | Output overrides — controls which bodies are included |

```bash
curl -X POST https://api.yourdomain.com/apsides \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{"datetime": "2026-03-21 12:00:00"}'
```

```json
{
  "datetime_utc": "2026-03-21T12:00:00",
  "julian_day": 2461124.0,
  "lunar_apsides": {
    "perigee": {
      "longitude": 142.33,
      "distance_au": 0.002475
    },
    "apogee": {
      "longitude": 322.33,
      "distance_au": 0.002718
    }
  },
  "planetary_apsides": {
    "mercury": {
      "perihelion": { "longitude": 77.46,  "distance_au": 0.3075 },
      "aphelion":   { "longitude": 257.46, "distance_au": 0.4667 }
    },
    "venus": {
      "perihelion": { "longitude": 131.53, "distance_au": 0.7184 },
      "aphelion":   { "longitude": 311.53, "distance_au": 0.7282 }
    },
    "mars": { "...": "..." }
  }
}
```

Mean Lilith (mean apogee) and True Lilith (oscillating apogee) are available but off by default. Enable them with a per-request output override:

```bash
curl -X POST https://api.yourdomain.com/apsides \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "datetime": "2026-03-21 12:00:00",
    "output": {
      "bodies": {
        "mean_lilith": true,
        "true_lilith": true
      }
    }
  }'
```

---

#### `POST /apsides/next`

Finds all upcoming (or past) apside events for specified bodies within a search window. Returns every event found within the window, sorted by datetime — not just the first one.

This is useful for building a list of upcoming Moon apogees, or finding when Mercury reaches perihelion over a given period.

| Field | Type | Required | Description |
|---|---|---|---|
| `reference_date` | string | Yes | Start the search from this date |
| `bodies` | array of strings | No | Which bodies to search. Default: all supported |
| `events` | array of strings | No | Which events to find. Default: all four |
| `max_search_years` | integer | No | How many years to search. Range 1–50. Default: 20 |

**Event types:** `perigee`, `apogee`, `perihelion`, `aphelion`

**Supported bodies:** `moon`, `mercury`, `venus`, `mars`, `jupiter`, `saturn`, `uranus`, `neptune`, `pluto`, `ceres`, `pallas`, `juno`, `vesta`, `chiron`

```bash
# Next 6 Moon apogees (apogee = Black Moon / Lilith position)
curl -X POST https://api.yourdomain.com/apsides/next \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "reference_date": "2026-03-21",
    "bodies": ["moon"],
    "events": ["apogee"],
    "max_search_years": 1
  }'
```

```json
{
  "reference_date": "2026-03-21",
  "max_search_years": 1,
  "count": 14,
  "events": [
    {
      "body": "moon",
      "event": "apogee",
      "datetime_utc": "2026-04-02T08:14:22",
      "julian_day": 2461136.843,
      "longitude": 22.61,
      "distance_au": 0.002718
    },
    {
      "body": "moon",
      "event": "apogee",
      "datetime_utc": "2026-04-29T14:52:07",
      "julian_day": 2461163.120,
      "longitude": 50.84,
      "distance_au": 0.002721
    }
  ]
}
```

**Expected event frequencies**

| Body | Events per year |
|---|---|
| Moon perigees | ~13 |
| Moon apogees | ~13 |
| Mercury perihelions | ~4 |
| Mercury aphelions | ~4 |
| Venus perihelions | ~1.6 |
| Mars perihelions | ~0.5 |

---

### Lunations

A **lunation** is any of the four principal Moon phases: New Moon, First Quarter, Full Moon, and Last Quarter. These are the moments when the angular separation between the Sun and Moon reaches 0°, 90°, 180°, and 270° respectively.

#### `POST /lunations`

Find lunation events near a reference date. Operates in two modes:

**Direction mode** (default) — finds the next, previous, or both occurrences of each requested phase relative to the reference date.

**Range mode** — finds all events within a date range. Activated by providing both `start_date` and `end_date`.

| Field | Type | Required | Description |
|---|---|---|---|
| `reference_date` | string | Yes | The date to search from |
| `direction` | string | No | `next` (default), `previous`, or `both` |
| `phases` | array | No | Phases to find. Default: all four |
| `start_date` | string | No | Range start — activates range mode |
| `end_date` | string | No | Range end — required with `start_date`. Max 2 years |

**Phase values:** `new_moon`, `first_quarter`, `full_moon`, `last_quarter`

```bash
# Next and previous New Moon from today
curl -X POST https://api.yourdomain.com/lunations \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "reference_date": "2026-03-21",
    "direction": "both",
    "phases": ["new_moon"]
  }'
```

```json
{
  "mode": "both",
  "reference_date": "2026-03-21",
  "count": 2,
  "lunations": [
    {
      "phase": "new_moon",
      "direction": "previous",
      "datetime_utc": "2026-03-01T07:36:11",
      "julian_day": 2461104.817,
      "sun_longitude": 341.21,
      "moon_longitude": 341.21,
      "sun_moon_angle": 0.0000009
    },
    {
      "phase": "new_moon",
      "direction": "next",
      "datetime_utc": "2026-03-30T10:58:22",
      "julian_day": 2461133.957,
      "sun_longitude": 9.96,
      "moon_longitude": 9.96,
      "sun_moon_angle": 0.0000012
    }
  ]
}
```

```bash
# All lunations in the first half of 2026
curl -X POST https://api.yourdomain.com/lunations \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "reference_date": "2026-01-01",
    "start_date": "2026-01-01",
    "end_date": "2026-06-30"
  }'
```

A six-month range will return approximately 24–26 lunations (all four phases across roughly six lunar cycles).

---

### Cache and administration

#### `GET /cache/stats`

Returns statistics about the server's chart and location caches.

```bash
curl https://api.yourdomain.com/cache/stats \
  -H "X-API-Key: eph_your_key_here"
```

```json
{
  "charts_cached": 1247,
  "derived_charts": 382,
  "canonical_places": 94,
  "place_aliases": 218,
  "place_cache_active": 86,
  "place_cache_expired": 8
}
```

---

### Self-service key management

These endpoints allow you to manage your own key without admin access.

#### `GET /me`

Returns the identity and settings of the currently authenticated key.

```bash
curl https://api.yourdomain.com/me \
  -H "X-API-Key: eph_your_key_here"
```

```json
{
  "id": "42",
  "name": "Jane Smith",
  "identifier": "myapp.com",
  "key_type": "domain",
  "is_domain": true,
  "is_user": false,
  "admin": false,
  "active": true,
  "rate_limits": {
    "per_minute": 20,
    "per_hour": 200,
    "per_day": 1000
  }
}
```

---

#### `GET /me/output`

Returns the full output configuration for your key — both the stored overrides and the effective resolved configuration.

```bash
curl https://api.yourdomain.com/me/output \
  -H "X-API-Key: eph_your_key_here"
```

```json
{
  "key_id": 42,
  "identifier": "myapp.com",
  "stored": {
    "heliocentric": true,
    "bodies": { "mean_lilith": true }
  },
  "effective": {
    "geocentric": true,
    "heliocentric": true,
    "right_ascension": true,
    "bodies": {
      "sun": true,
      "moon": true,
      "mean_lilith": true
    }
  },
  "defaults": { "...": "server defaults..." }
}
```

The `stored` object contains only the overrides saved for your key. The `effective` object is the full merged configuration that will actually be applied to your requests.

---

#### `POST /me/output`

Update your key's output configuration. Send a partial object — only the fields you include are changed.

```bash
# Enable mean Lilith and disable heliocentric for all your requests
curl -X POST https://api.yourdomain.com/me/output \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "output_config": {
      "heliocentric": false,
      "bodies": {
        "mean_lilith": true,
        "true_node": true
      }
    }
  }'
```

```json
{ "message": "Output configuration updated" }
```

To reset to server defaults (remove all overrides), send `null`:

```bash
curl -X POST https://api.yourdomain.com/me/output \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{"output_config": null}'
```

---

#### `POST /me/rotate`

Generate a new API key. Your current key stops working immediately and cannot be recovered. The new key is returned in the response — save it immediately.

```bash
curl -X POST https://api.yourdomain.com/me/rotate \
  -H "X-API-Key: eph_your_key_here"
```

```json
{
  "message": "Key rotated successfully",
  "key_id": 42,
  "identifier": "myapp.com",
  "key_prefix": "eph_a1b2",
  "api_key": "eph_a1b2c3d4e5f6...",
  "warning": "Save this key — it will not be shown again"
}
```

---

## Output configuration reference

### Top-level fields

| Field | Type | Default | Description |
|---|---|---|---|
| `geocentric` | boolean | `true` | Geocentric ecliptic positions |
| `heliocentric` | boolean | `true` | Heliocentric ecliptic positions |
| `right_ascension` | boolean | `true` | Equatorial right ascension |
| `declination` | boolean | `true` | Equatorial declination |
| `longitude_speed` | boolean | `true` | Daily motion in longitude |
| `latitude_speed` | boolean | `true` | Daily motion in latitude |
| `declination_speed` | boolean | `true` | Daily motion in declination |
| `retrograde` | boolean | `true` | Retrograde flag |
| `default_house_system` | string | `null` | Default house system when not specified in request |

### `output.angles`

| Field | Default | Description |
|---|---|---|
| `asc` | `true` | Ascendant |
| `mc` | `true` | Midheaven |
| `vertex` | `true` | Vertex |
| `east_point` | `true` | East Point / Equatorial Ascendant |
| `armc` | `false` | ARMC — Sidereal time angle |

### `output.bodies`

| Field | Default | Notes |
|---|---|---|
| `sun` | `true` | |
| `moon` | `true` | Geocentric only |
| `mercury` | `true` | |
| `venus` | `true` | |
| `mars` | `true` | |
| `jupiter` | `true` | |
| `saturn` | `true` | |
| `uranus` | `true` | |
| `neptune` | `true` | |
| `pluto` | `true` | |
| `earth` | `true` | Heliocentric only |
| `asteroids` | `true` | Master switch — if `false`, overrides all asteroid settings below |
| `ceres` | `true` | |
| `pallas` | `true` | |
| `juno` | `true` | |
| `vesta` | `true` | |
| `chiron` | `true` | |
| `mean_node` | `true` | Mean North Node |
| `true_node` | `false` | True/Osculating North Node |
| `south_node` | `false` | Derived: North Node + 180° |
| `mean_lilith` | `false` | Mean Black Moon Lilith |
| `true_lilith` | `false` | True/Osculating Black Moon Lilith |
| `part_of_fortune` | `false` | ASC + Moon − Sun. Requires location |

### `output.meta`

| Field | Default | Description |
|---|---|---|
| `api_usage` | `true` | Include Google API usage statistics |
| `from_cache` | `true` | Include cache status flag |

---

## Rate limits

Rate limits are applied per API key and reset on a rolling basis.

| Class | Per minute | Per hour | Per day |
|---|---|---|---|
| Domain key | 20 | 200 | 1,000 |
| Wildcard key (`*`) | 5 | 30 | 100 |

When a limit is exceeded the API returns `429 Too Many Requests`:

```json
{
  "error": "Rate limit exceeded",
  "status": 429
}
```

Do not retry immediately. Wait for the relevant window to reset. If you need higher limits for a production application, contact the administrator.

---

## Key registration

#### `POST /register/domain`

Submit a registration request for an API key. No authentication required.

| Field | Type | Required | Description |
|---|---|---|---|
| `domain` | string | Yes | Your domain, or `*` for personal/direct access |
| `name` | string | Yes | Your name |
| `contact_email` | string | Yes | Email address — your key will be sent here |
| `reason` | string | No | What you are building |

```bash
curl -X POST https://api.yourdomain.com/register/domain \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "myapp.com",
    "name": "Jane Smith",
    "contact_email": "jane@myapp.com",
    "reason": "Building an astrology chart web app"
  }'
```

```json
{
  "message": "Registration request submitted. You will be notified by email.",
  "request_id": 7
}
```

---

#### `GET /register/verify?t=<token>`

Activate a user key via email verification link. This endpoint is typically called by clicking the link in the verification email rather than directly.

```bash
curl "https://api.yourdomain.com/register/verify?t=your-verification-token"
```

On success, returns the API key. **This is the only time the key is ever shown** — copy it immediately.

---

---

### Views

A view is a saved JSON blob identified by a UUID. Views exist to enable clean share URLs for chart states — instead of encoding all chart parameters in a long querystring, the client saves the relevant state to a view and shares a minimal URL such as `https://example.com?v=<uuid>`. When someone opens that URL, the client fetches the view by UUID and restores whatever state it stored.

The structure of the JSON is entirely defined by the client application. ephemeralREST stores and returns it opaquely without inspecting or validating its contents.

#### `POST /views`

Save a new view. Always generates a fresh UUID. Returns the UUID to include in share URLs.

| Field | Type | Required | Description |
|---|---|---|---|
| `data` | object | Yes | Any valid JSON object — the client defines the structure |

```bash
curl -X POST https://api.yourdomain.com/views \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "data": {
      "chart_id": "a3f2c1d4-7e8b-4f2a-9c1d-3e4f5a6b7c8d",
      "dial_orb": 1.5,
      "bodies": ["sun", "moon", "mars"],
      "mode": "bidial"
    }
  }'
```

```json
{ "view_id": "f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d" }
```

The response is a `201 Created`. The `view_id` is what you embed in a share URL — e.g. `https://yourapp.com?v=f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d`.

---

#### `PUT /views/<view_id>`

Update an existing view in place. The blob is replaced entirely. Returns `404` if the UUID does not exist.

```bash
curl -X PUT https://api.yourdomain.com/views/f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d \
  -H "Content-Type: application/json" \
  -H "X-API-Key: eph_your_key_here" \
  -d '{
    "data": {
      "chart_id": "a3f2c1d4-7e8b-4f2a-9c1d-3e4f5a6b7c8d",
      "dial_orb": 2.0,
      "bodies": ["sun", "moon", "mars", "saturn"],
      "mode": "bidial"
    }
  }'
```

```json
{ "view_id": "f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d" }
```

---

#### `GET /views?v=<view_id>`

Retrieve a saved view by UUID. **No authentication required** — this endpoint is public so that share URLs work without a key. Anyone with the UUID can retrieve the view.

```bash
curl "https://api.yourdomain.com/views?v=f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d"
```

```json
{
  "view_id": "f7e6d5c4-b3a2-4190-8e7d-6c5b4a3f2e1d",
  "data": {
    "chart_id": "a3f2c1d4-7e8b-4f2a-9c1d-3e4f5a6b7c8d",
    "dial_orb": 1.5,
    "bodies": ["sun", "moon", "mars"],
    "mode": "bidial"
  },
  "created_at": "2026-03-31T08:10:00",
  "updated_at": "2026-03-31T08:10:00"
}
```

Returns `404` if the UUID does not exist.

---

## Endpoint quick reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | No | Server status |
| `GET` | `/autocomplete?q=` | No | Location autocomplete |
| `POST` | `/locations/resolve` | No | Resolve place to coordinates |
| `POST` | `/register/domain` | No | Request a domain API key |
| `POST` | `/register/user` | No | Register a personal user key |
| `GET` | `/register/verify?t=` | No | Verify email and activate user key |
| `GET` | `/me` | Yes | Your key identity and settings |
| `GET` | `/me/output` | Yes | Your output configuration |
| `POST` | `/me/output` | Yes | Update your output configuration |
| `POST` | `/me/rotate` | Yes | Rotate your key |
| `POST` | `/calculate` | Yes | Calculate a chart |
| `GET` | `/chart/<id>` | Yes | Retrieve a chart |
| `GET` | `/cache/stats` | Yes | Cache statistics |
| `POST` | `/chart/<id>/progressions` | Yes | Secondary progressions |
| `POST` | `/chart/<id>/solar-arc` | Yes | Solar arc directions |
| `POST` | `/chart/<id>/solar-return` | Yes | Solar return |
| `POST` | `/chart/<id>/lunar-return` | Yes | Lunar return |
| `GET` | `/chart/<id>/derived` | Yes | List derived charts |
| `GET` | `/derived/<id>` | Yes | Retrieve derived chart |
| `DELETE` | `/derived/<id>` | Yes | Delete derived chart |
| `POST` | `/ephemeris` | Yes | Monthly ephemeris |
| `POST` | `/apsides` | Yes | Current apside positions |
| `POST` | `/apsides/next` | Yes | Next apside events |
| `POST` | `/lunations` | Yes | Lunation events |
| `POST` | `/eclipses` | Yes | Solar and lunar eclipses |
| `POST` | `/views` | Yes | Save a new view |
| `PUT` | `/views/<id>` | Yes | Update an existing view |
| `GET` | `/views?v=` | No | Retrieve a view by UUID |