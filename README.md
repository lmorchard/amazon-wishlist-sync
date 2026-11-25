# amazon-wishlist-sync

Scrape your Amazon wishlists in a real, logged-in browser and sync them into
[Linkding](https://linkding.link/) as `wishlist` bookmarks.

A companion to [`linkding-wishlist-ssg`](https://github.com/lmorchard/linkding-wishlist-ssg).
The two coordinate **only** through Linkding data — this tool writes bookmarks,
the SSG reads them. Neither knows the other exists.

---

## Why a separate tool

The SSG is read-only against Linkding, and that rule is load-bearing: it keeps
*your intent* (bookmarks you made) separate from *machine observation* (what a
scraper saw). A scraper bug can never corrupt your handwriting if the scraper
lives somewhere else.

This tool is a writer by definition. So it is its own program, its own repo, its
own cron, and its own failure mode. If Amazon changes its markup, the worst
outcome is "sync failed" — not "the site build failed."

It also needs something the SSG doesn't: a real browser with your logged-in
Amazon session, driven over Chrome DevTools Protocol, with all the scrolling and
waiting and DOM-selector fragility that implies. That is script-shaped, not
service-shaped.

---

## Scope

### Does

- Read one or more Amazon wishlists through the homelab Chromium instance.
- Extract per-item: ASIN, canonical URL, title, current price, list price,
  discount note, category/format hint, date added, and any comment you wrote.
- Create Linkding bookmarks for items **not already present**, tagged `wishlist`.
- Preserve the original "added" date so your timeline reflects when you actually
  wanted the thing.
- Write harvested detail into the bookmark's **description** field.
- Report what it did, and what it skipped, to stdout.

### Does not

- **Never touches `notes`.** Not read-modify-write, not append, not ever. Notes
  are yours: Tier 2 override blocks, annotations, whatever you want. This tool
  has no opinion about them and will not write there even if it could help.
- Never modifies or deletes an existing bookmark. If a bookmark exists, it is
  skipped — prices go stale, that is the SSG's problem to handle later, not this
  tool's job to churn.
- No price history, no SQLite. It records what Amazon says today into a
  description and moves on.
- No web UI, no daemon, no API server. A CLI you can cron.

---

## The data contract

The only interface between this tool and the SSG is the Linkding bookmark.

| Field | Written by | Meaning |
|---|---|---|
| `url` | this tool | `https://www.amazon.com/dp/<ASIN>` — canonical, stripped of session noise |
| `title` | this tool | Product title as shown on the list |
| `tags` | this tool | `wishlist`, plus `amazon` and a list-name facet |
| `description` | this tool | **Machine territory.** Category, format, price-at-sync. A cache, safe to overwrite |
| `notes` | **you, only** | Free-form annotations and Tier 2 overrides. Never written by this tool |
| `date_added` | this tool | Backdated to Amazon's "Item added" date |

That split is the whole design. Description accumulates observations; notes stay
a record of intent.

---

## Extraction

Amazon's wishlist cards come in **two shapes**, and the difference is not
cosmetic. Amazon no longer allows adding off-site items, so the external ones are
frozen artifacts of a removed capability — they cannot be re-created and exist
nowhere else. **The external branch is the primary reason this tool exists.**
Confirmed against a live list (2026-11-24, 1220 items: 1013 Amazon, 207 external).

### Shape A — Amazon products

| Field | Source |
|---|---|
| ASIN | `/dp/<ASIN>` segment of the `h2 a` href |
| Title | `h2 a` text |
| Price | `li[data-price]` attribute; `.a-price .a-offscreen` as fallback |
| Date added | `[id^="itemAddedDate_"]` → `"Item added November 20, 2026"` |

### Shape B — off-site wants

These cards have **no `/dp/` link at all**. Amazon marks them explicitly with
`itemExternalId=NON_ASIN%3A...` on the "add to cart" anchor.

| Field | Source |
|---|---|
| URL | first anchor whose href is not an Amazon wishlist `/hz/wishlist/` path |
| Title | card text, split on `From <domain>` |
| Date added | `[id^="itemAddedDate_"]` |

One external card carries the raw URL *as its entire text*, with no anchor:
`https://shopmoonlambo.com/collections/.../vhs-classic-mens-hoodie?variant=...`.
So the text must be scanned for a URL when no external anchor exists.

### Known non-ASIN trap

**`li[data-itemid]` is NOT the ASIN.** It is the wishlist *item* id
(`IROUWUPGJBBG0`), matching the `coliid=` query parameter. Using it as an ASIN
produces `https://www.amazon.com/dp/IROUWUPGJBBG0`, which 404s. The ASIN must be
read from the href.

### Unverified

- **Comments.** `[id^="itemComment_"]` exists but returned `""` on every card
  probed. May require expanding the card. Do not rely on comments until probed
  against an item known to have one.

List pages lazy-load on scroll. A real list (1,220 items as of 2026-11-24)
needed dozens of scroll steps, and the card count kept growing past the first
"stable" reading — three separate runs read 790, then 1,110, then 1,220 items.
The settle threshold must therefore be generous (three consecutive rounds with
no growth, 1.5s apart) plus a hard cap on total scrolls so a misbehaving page
cannot loop forever. **Treat any single count as provisional.**

### Tagging

| Item shape | Tags |
|---|---|
| Amazon product | `wishlist`, `amazon` |
| Off-site want | `wishlist`, `imported:amazon` |

Off-site wants get a provenance tag rather than `amazon`, because they are not
Amazon products — tagging them `amazon` would poison store queries and claim an
association they do not have. The provenance tag records where the item was
*found*, which matters if the migration ever needs re-checking.

---

## Importing into Linkding

**No HTTP writes.** Linkding's REST API is a poor fit for bulk creation; the
individual-bookmark endpoint is not built for a rapid firehose. Instead this tool
emits **Netscape bookmark HTML** and the operator imports it through Linkding's
own UI (Settings → Import).

That is the only import format Linkding accepts, and its importer runs inside
Django — no round trips, batches of 200, and it is a **true upsert** keyed on
`url_normalized`:

| Situation | Behavior |
|---|---|
| URL not present | bookmark created |
| URL already present | bookmark's fields **updated in place** |
| Duplicate URL within one file | rejected, counted as `failed` |

Two consequences that make re-importing safe:

1. **`notes` is never overwritten.** The importer guards the assignment
   (`if netscape_bookmark.notes:`), and the Netscape parser always produces an
   empty string for notes. Tier 2 overrides survive re-import by construction —
   the safety comes from the format, not from a promise in code.
2. **`date_added` is re-applied** from `ADD_DATE`, so backdating is idempotent.

### Pipeline

```
# 1. Extract from a logged-in browser tab (active tab must be the wishlist)
uv run extract.py --list <wishlist-id> --out items.json

# 2. Generate import files, external branch split out
uv run emit_netscape.py items.json --outdir out --split
```

Produces `out/external-import.html` (off-site wants) and
`out/amazon-import.html` (Amazon products). Import the external file first: it
holds the data that exists nowhere else.

The generator dedupes in-file duplicate URLs itself and reports what it dropped,
so the importer reports zero failures rather than a confusing pair.

---

## Usage

```
uv run extract.py --list <id> --out items.json [--max-scroll-rounds 40]
uv run emit_netscape.py items.json --outdir out [--split]
```

Environment: `CDP_URL` (default `http://myriad-docker:9222`). No Linkding
credentials are needed — this tool never talks to Linkding.

---

## Safety

- Extract and emit only. **This tool writes no bookmarks.**
- Reads strictly. It never creates, modifies, or deletes an existing bookmark.
- Never writes `notes` — and cannot, since the output format carries no such
  field.
- Works in the browser tab you point it at and opens none of its own.
- Reconstructed URLs are marked in the bookmark `description` so nothing
  downstream mistakes them for the original product page.

---

## Status

Working, as of 2026-11-24. Extraction and import-file generation are complete and
validated against Linkding's own parser. Known gaps:

- One card (an off-site want whose text is only its URL) has no title and no
  date; it imports with the URL as its title and `ADD_DATE=0`.
- Five items from 2016-2017 had no URL preserved by Amazon at all. Their URLs are
  reconstructed from the recorded host and title, as
  `https://<host>/#wishlist-<slug>`. The fragment is what keeps them distinct
  bookmarks in Linkding.
- No incremental/diff mode yet. A re-run regenerates everything.
