# Embedding Health Cache Design

## Goal

Make the Embeddings page useful immediately after navigation or a browser
restart without running the bounded local health inspection again. Bring the
projection policy note into the existing visual language instead of leaving it
as prominent unstyled text.

## Scope

- Cache the most recent successful `/api/health` response in browser
  `localStorage` together with the client-side check timestamp.
- Restore that cached result when the Embeddings page is opened.
- Add a `Last check` health card showing the cached check time and its local
  origin.
- Style the projection policy as a compact, muted status badge.
- Keep health inspection explicitly user-triggered.

Server-side persistence, automatic background health checks, cross-browser
synchronization, and changes to the health API are out of scope.

## Behavior

The cache uses a versioned key so incompatible historical data can be ignored
without migration. A valid cache record contains a schema version, an ISO-8601
client timestamp, and the health response object.

When the user opens the Embeddings page:

1. The projection loads through the existing on-demand projection flow.
2. The client reads the health cache without making a health request.
3. A valid record is rendered through the same renderer used by a fresh health
   inspection.
4. With no valid record, the existing on-request empty state remains visible.

When `Inspect local health` succeeds, the client records the response and the
current time, then renders it. When it fails, the current error is shown but the
last successful cache is not overwritten. A later page visit can therefore
restore the last known successful result.

Malformed JSON, wrong schema versions, missing timestamps, or non-object health
payloads are treated as cache misses. Cache access failures (for example browser
storage restrictions) must not prevent a fresh inspection or break the page.

## UI

The health cards keep their current grid and styling. A new `Last check` card
shows a locale-formatted date and time as its primary value and `Cached locally`
as supporting text. It appears for both restored and newly completed successful
checks.

The projection policy text becomes a small `.projection-policy` badge inside
the existing filter bar. It uses muted text, a low-contrast border and
background, compact padding, and no glow so it reads as secondary operational
context.

## Components and Data Flow

- Pure cache helpers validate, serialize, and deserialize the versioned record.
- A health renderer receives a health payload and check timestamp and owns all
  health panel markup, including `Last check`.
- `loadHealth()` remains the only network-triggering health action. It delegates
  successful rendering and cache persistence to the helpers.
- `switchPage('embedding')` restores cached health before starting the existing
  projection load.

Keeping cache handling separate from rendering makes validation testable and
avoids storing rendered HTML in browser storage.

## Verification

- Unit-test valid cache round trips, invalid JSON, schema mismatch, missing
  fields, and storage failures.
- Verify the Embeddings navigation path restores cached health without calling
  `/api/health`.
- Verify successful inspections write the cache and failed inspections preserve
  the last successful record.
- Add UI contract coverage for the `Last check` card path and the styled
  projection policy class.
- Run the repository test suite and inspect the deployed page after refresh.
