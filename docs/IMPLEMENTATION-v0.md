# Implementation notes — v0

What was built, and where it diverges from the original plan. Read `README.md`
first for the *why*.

---

## 1. What shipped

Three Python modules, no daemon, no HTTP client:

| File | Role |
|---|---|
| `extract.py` | Drive the browser over the `agent-browser` CLI, scroll the list to exhaustion, extract every card |
| `tagging.py` | Tag vocabulary in one place |
| `emit_netscape.py` | Turn extracted JSON into Linkding import files |

Plus `validate_parse.py`, which parses a generated file **with Linkding's own
parser** inside the container. That is the acceptance test: if Linkding cannot
parse it, it does not work, no matter what our own reader thinks.

## 2. The design changed during the build

### 2.1 No HTTP writes — emit a file instead

The original plan had the tool POST bookmarks to Linkding's REST API.

That was dropped. Linkding's import path is far better suited to bulk work: its
importer runs inside Django, batches at 200, and is a **true upsert** keyed on
`url_normalized`. A rapid-fire loop of individual API calls has to reimplement
all of that over a network round trip per bookmark, and is exactly the shape that
misbehaved in an earlier import project.

So the tool never contacts Linkding at all. It emits Netscape bookmark HTML and
the operator imports it through the UI. The tool needs no Linkding credentials.

### 2.2 Netscape HTML, not JSON

Linkding accepts exactly **one** import format. There is no JSON import path —
the settings view reads one uploaded file, decodes it as text, and hands it to
`import_netscape_html`. JSON exists only on the *export* and REST API sides.

Netscape HTML also carries `ADD_DATE` per bookmark, which is what backdating
needs, and `TAGS`, which carries the vocabulary.

### 2.3 Two branches, external first

The original plan assumed an Amazon card has an ASIN. Many do not.

Amazon used to allow adding off-site items to a wishlist and no longer does. Those
items are **frozen artifacts of a removed capability** — they cannot be
re-created, and they exist nowhere else. They also outnumber what the plan
assumed (207 of 1220, not "a rounding error").

Consequences baked into the code:

- Extraction has two shapes: `/dp/<ASIN>` cards, and off-site cards whose URL is
  an arbitrary external anchor.
- `li[data-itemid]` is **not** the ASIN — it is a wishlist item id. Using it as an
  ASIN produces `/dp/IROUWUPGJBBG0`, which 404s.
- The external branch is reported separately so the irreplaceable data gets
  reviewed on its own terms.

### 2.4 Five items have no URL at all

The oldest off-site cards (2016–2017) retain a title, a host, and a date, but
Amazon never preserved the URL — there is no link in the DOM at all.

These are reconstructed as `https://<host>/#wishlist-<slug>`. The fragment is
load-bearing: Linkding's `normalize_url` preserves fragments when computing
`url_normalized`, so each one is a distinct bookmark rather than colliding on the
bare host. See `linkding-wishlist-ssg` SPEC §4.7 for the test that established
this.

Reconstructed URLs are marked in the bookmark description so nothing downstream
mistakes them for the original product page.

## 3. Field contract

| Netscape field | Source | Notes |
|---|---|---|
| `HREF` | extracted URL | reconstructed ones marked in description |
| `ADD_DATE` | Amazon "Item added" date | backdating; `0` when unavailable |
| `TAGS` | `tagging.py` | `wishlist` + `amazon` or `imported:amazon` |
| `TITLE` | card title | URL used as title when no title exists |
| `DD` (description) | price, ASIN, provenance | **machine territory** |

**`notes` is never written, and cannot be** — the Netscape record has no notes
field. The importer additionally guards the assignment
(`if netscape_bookmark.notes:`), and the parser always yields an empty string.
Tier 2 overrides survive re-import by construction rather than by convention.

## 4. Verification performed

Run 2026-11-24 against a live list (1,220 items):

- `extract.py` → 1220 items, 0 missing URLs, 1013 Amazon / 207 external.
- `emit_netscape.py --split` → 205 external + 1013 Amazon bookmarks.
- Both files parsed by Linkding's own `bookmarks.services.parser.parse`:
  counts match, no missing titles, no missing tags, zero duplicate normalized
  URLs within either file.
- Two in-file duplicate URLs (the same product wanted twice) were dropped by the
  generator and reported.

## 5. Known gaps

- **Card count is provisional.** Three successive runs read 790, 1110, then 1220
  items. The scroll loop's settle threshold is generous but the list is large and
  lazy-loaded; treat any single number as a floor until two runs agree.
- **Comments unverified.** `[id^="itemComment_"]` exists but returned empty on
  every card probed.
- **One item lacks a title and date.** Its card text is only its URL.
- **No incremental mode.** Every run regenerates everything. Safe because import
  upserts, but wasteful.
- **Reconstructed fragments are not stable.** If the title or host of a
  reconstructed item changed, its slug would change and re-import would create a
  duplicate rather than updating. Worth a stable-id flag if these ever matter.
