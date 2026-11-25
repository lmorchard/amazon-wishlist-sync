"""Emit Linkding import files (Netscape bookmark HTML) from extracted items.

Linkding has exactly one import format: Netscape bookmark HTML. Its importer
(runs inside Django, batches of 200) is a true UPSERT keyed on url_normalized:

  - no existing bookmark  -> create
  - existing bookmark     -> update its fields in place

So re-importing a regenerated file refreshes properties without duplicating.
`notes` is never touched, because Netscape HTML has no notes field — the Tier 2
override safety is a property of the format, not a promise in code.

In-file duplicate URLs are rejected by the importer (counted as failures), so
this generator dedupes them itself and reports what it dropped.

Usage:
    uv run emit_netscape.py items-final.json --outdir out --split
"""

import argparse
import html
import json
import os
import sys
import time
from collections import Counter

from tagging import tags_for


def fmt_add_date(added):
    """'November 20, 2026' -> unix seconds. Netscape ADD_DATE is epoch seconds."""
    if not added:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return int(time.mktime(time.strptime(added.strip(), fmt)))
        except ValueError:
            continue
    return None


def description_for(item):
    """Machine territory: scraped detail only. Never user intent."""
    bits = []
    if item.get("price"):
        bits.append(f"Price at sync: {item['price']}")
    if item.get("asin"):
        bits.append(f"ASIN: {item['asin']}")
    if item.get("url_source") == "reconstructed":
        bits.append(
            "URL reconstructed from Amazon's record — the original product URL "
            "was not preserved. The link points at the shop's site, not this item."
        )
    if item.get("external_id"):
        bits.append(f"Amazon external id: {item['external_id']}")
    return " · ".join(bits)


def emit(items, title, dedupe_report=True):
    """Render a Netscape bookmark HTML document."""
    seen = {}
    kept, dropped = [], []
    for item in items:
        url = item.get("url")
        if not url:
            dropped.append((item, "no url"))
            continue
        if url in seen:
            dropped.append((item, f"duplicate of {seen[url].get('title')!r}"))
            continue
        seen[url] = item
        kept.append(item)

    if dedupe_report and dropped:
        print(f"\n  dropped {len(dropped)} before emit:", file=sys.stderr)
        for item, why in dropped:
            print(f"    {(item.get('title') or 'NONE')[:50]:50} {why}", file=sys.stderr)

    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- This is an automatically generated file.",
        "     It will be read and overwritten.",
        "     DO NOT EDIT! -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        f"<TITLE>{html.escape(title)}</TITLE>",
        f"<H1>{html.escape(title)}</H1>",
        "<DL><p>",
    ]

    skipped_no_date = 0
    for item in kept:
        # Netscape requires a title. Moonlambo has none; use its URL (Les's call).
        title_text = item.get("title") or item.get("url") or "(untitled)"
        tags = ",".join(tags_for(item))
        desc = description_for(item)
        epoch = fmt_add_date(item.get("added"))
        if epoch is None:
            skipped_no_date += 1

        attrs = [
            f'HREF="{html.escape(item["url"], quote=True)}"',
            f'ADD_DATE="{epoch if epoch is not None else 0}"',
            f'TAGS="{html.escape(tags, quote=True)}"',
        ]
        if item.get("price"):
            attrs.append('PRIVATE="0"')
        lines.append(f'    <DT><A {" ".join(attrs)}>{html.escape(title_text)}</A>')
        if desc:
            lines.append(f"    <DD>{html.escape(desc)}")

    lines.append("</DL><p>")
    if skipped_no_date:
        print(f"  note: {skipped_no_date} item(s) had no parseable date "
              f"(ADD_DATE=0)", file=sys.stderr)
    return "\n".join(lines) + "\n", kept, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items_json")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--split", action="store_true",
                    help="emit separate external and amazon files")
    args = ap.parse_args()

    with open(args.items_json) as f:
        payload = json.load(f)

    items = payload["items"]
    os.makedirs(args.outdir, exist_ok=True)

    groups = (
        [
            ("external", [i for i in items if i["shape"] == "external"],
             "Amazon WANT — off-site wants (rescued)"),
            ("amazon", [i for i in items if i["shape"] == "amazon"],
             "Amazon WANT — Amazon products"),
        ]
        if args.split
        else [("all", items, "Amazon WANT")]
    )

    summary = {}
    for name, group, title in groups:
        print(f"\n=== {name}: {len(group)} items ===", file=sys.stderr)
        doc, kept, dropped = emit(group, title)
        path = os.path.join(args.outdir, f"{name}-import.html")
        with open(path, "w") as f:
            f.write(doc)
        print(f"  wrote {path} ({len(doc)} bytes, {len(kept)} bookmarks)",
              file=sys.stderr)
        summary[name] = {
            "input": len(group),
            "emitted": len(kept),
            "dropped": len(dropped),
            "path": path,
        }

    print("\n=== summary ===", file=sys.stderr)
    print(json.dumps(summary, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
