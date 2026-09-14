# Typed Relations Filter and Visual Integration Design

## Goal

Make the graph's typed-relation overlay truthful, filterable, and visually consistent with the existing dark neon UI.

Only resolved relations are in scope. Relations whose target memory no longer exists remain a data-cleanup concern and are not represented by synthetic graph nodes.

## Current Failure

The relation selector reloads the graph, but `loadGraph()` never sends its value as `relation_types`. The global graph builder also ignores `relation_types`, even when a caller supplies it. Consequently, selecting a concrete relation type returns and renders every resolved declared relation.

The current declared-edge style also gives every relation the same saturated orange color, `2.2px` width, and comparatively large arrow treatment. This overpowers the thinner semantic links and does not communicate relation type.

## Behavior

- `Typed relations` remains an opt-in overlay over the selected semantic or hub graph mode.
- When the overlay is disabled, no declared edges are returned or rendered, and the relation selector is disabled and visually muted.
- `All declared relations` returns every resolved declared relation whose two endpoints are present in the overview graph.
- Selecting a concrete type sends that type to `/api/graph` and returns only matching declared edges.
- Semantic and hub edges remain visible as contextual background when a concrete declared type is selected.
- The graph budget label reports the number of declared edges actually returned after filtering.
- `available_relation_types` describes all resolved types available before filtering, so changing to one type does not remove the other choices from the selector.
- `hidden_by_relation_filter` reports resolved declared edges excluded by the active type filter.

## API Data Flow

`loadGraph()` reads `#relation-filter`. When typed relations are enabled and the value is non-empty, it appends a URL-encoded `relation_types` query parameter. An empty value means all resolved types.

The overview branch of `build_graph()` obtains the typed-edge records once, excludes unresolved or absent endpoints, derives the complete available-type set, applies the optional type filter, and appends only the filtered declared edges to the existing semantic or hub edges. The response keeps semantic, hub, and declared counts distinct.

No filtering is performed only in the browser: the response and its counts must already reflect the selected relation type.

## Visual System

Typed-relation colors are defined in one JavaScript mapping and reused for graph edges and sidebar relation badges:

| Family | Relation types | Colors |
| --- | --- | --- |
| Structure | `part_of`, `extends` | cyan / sky |
| Dependency | `depends`, `requires` | violet / magenta |
| Integration | `runs_on`, `uses`, `connects_to` | emerald / teal / blue |
| Evolution | `supersedes` | amber |
| Conflict | `invalidates`, `contradicts` | rose / red |
| Fallback | unknown non-empty type, `linked` | slate |

Declared edges use approximately `1.35px` width, restrained opacity, small arrowheads, tighter curves, and compact labels with a dark backing for contrast. Semantic edges use approximately `0.85px` width and a quieter dashed indigo-gray. Hover and selection increase contrast without changing the type's identity.

The checkbox and relation selector are presented as one joined control. A small multicolor status dot identifies the typed-relation feature. The selector is muted and disabled while the overlay is off. When one type is selected, the control accent follows that type's color; the all-types state uses the compact multicolor accent.

Sidebar typed-relation rows retain the existing structure but derive their border, arrow, and badge colors from the same relation palette. Embedding-only rows remain visually distinct and subdued.

## Components and Boundaries

- `server/server.py` owns resolved-edge selection, relation-type filtering, available types, and response counts.
- `static/graph.js` owns query construction, one relation-style lookup, graph rendering, and control state synchronization.
- `static/index.html` owns the joined typed-relation control markup and its initial options.
- `static/style.css` owns the joined control and palette-driven sidebar presentation.
- Python tests cover server filtering and counts; static JavaScript tests cover query parameters and style helpers; repository static checks guard the control markup.

## Error and Edge Cases

- An empty or omitted `relation_types` value means no type restriction.
- Selecting a type with zero resolved edges returns zero declared edges while preserving the semantic graph.
- Unknown relation types use the fallback slate style rather than failing rendering.
- Unresolved typed relations are ignored in the graph, consistent with the existing endpoint requirement.
- Turning typed relations off does not discard the selected type, so re-enabling restores the user's last selection.

## Verification

- Unit-test the overview graph with multiple declared types, unresolved targets, and a one-type filter.
- Assert that the unfiltered view returns all resolved declared relations and all available resolved types.
- Assert that the filtered view returns only the requested type, reports excluded resolved relations, and preserves the full available-type list.
- Test that `loadGraph()` includes `relation_types` only when typed relations are enabled and a concrete type is selected.
- Test relation-style mapping, including fallback behavior.
- Run the relevant Python and Node test suites.
- Inspect the live graph in the collaborative browser with the overlay off, all types selected, and one concrete type selected.
