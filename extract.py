"""Extract Amazon wishlist items from the homelab browser via agent-browser CLI.

Two card shapes, extracted separately and deliberately:

  Shape A (Amazon product): /dp/<ASIN> in the h2 a href. Routine.
  Shape B (off-site want):  no /dp/ link. Amazon removed the ability to add
                            these, so they exist nowhere else. **Primary value.**

Shape B is extracted FIRST and independently, so a broken Amazon branch cannot
block recovering the irreplaceable data.

Usage:
    uv run extract.py --list 1QWYI6P2JF3Q5 --out items.json
"""

import argparse
import json
import subprocess
import sys
import time

CLI = "agent-browser"

# Chunk size for eval calls. Each call must finish well inside the CLI's
# hard 30s timeout; 150 cards per call leaves comfortable headroom.
CHUNK = 150

# Page-side extraction for a slice of cards. Kept as one string so the exact
# selectors are visible in one place.
EXTRACT_JS = r"""
((start, end) => {
  const cards = Array.from(document.querySelectorAll('li[data-itemid]')).slice(start, end);
  const AMZ = /\/dp\/([A-Z0-9]{10})/;
  const out = [];

  // Amazon's per-card action chrome, which rides along in innerText.
  const ACTION_CHROME = /\s*(Add a note|Move|Delete|Shop products|Add to (?:List|Cart)|Edit comment)\s*/gi;

  // Reconstruct a fragment-style placeholder URL for an off-site want whose
  // original URL Amazon never retained. The fragment is what makes it a
  // distinct Linkding bookmark. Returns null when there is no usable host.
  const reconstruct = (title, text) => {
    const fromMatch = text.match(/\s+From\s+([\w.-]+\.[a-z]{2,})/i);
    if (!fromMatch) return null;
    const host = fromMatch[1].toLowerCase();
    const slug = (title || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60);
    if (!slug) return null;
    return 'https://' + host + '/#wishlist-' + slug;
  };

  for (const li of cards) {
    const dateEl = li.querySelector('[id^="itemAddedDate_"]');
    const added = dateEl ? dateEl.innerText.trim().replace(/^Item added\s+/, '') : null;

    const priceAttr = li.getAttribute('data-price');
    const priceEl = li.querySelector('.a-price .a-offscreen');

    const titleLink = li.querySelector('h2 a') || li.querySelector('a[id^="itemName_"]');
    const href = titleLink ? titleLink.getAttribute('href') : null;
    const asinMatch = href ? href.match(AMZ) : null;

    if (asinMatch) {
      // Shape A
      let amzPrice = li.getAttribute('data-price');
      if (amzPrice && !/^[0-9]/.test(amzPrice)) amzPrice = null;
      if (!amzPrice && priceEl) amzPrice = priceEl.innerText.trim();
      out.push({
        shape: 'amazon',
        asin: asinMatch[1],
        url: 'https://www.amazon.com/dp/' + asinMatch[1],
        url_source: 'original',
        title: titleLink ? titleLink.innerText.trim() : null,
        price: amzPrice,
        added: added,
        wishlist_item_id: li.getAttribute('data-itemid')
      });
      continue;
    }

    // Shape B. Find the first anchor that is not Amazon wishlist chrome.
    let url = null;
    for (const a of li.querySelectorAll('a[href]')) {
      const h = a.getAttribute('href');
      if (!h) continue;
      if (/^javascript:/i.test(h)) continue;
      if (h === '#' || h === '') continue;          // placeholder anchors
      if (/^\/hz\/wishlist\//.test(h)) continue;
      if (/\/gp\/registry/.test(h)) continue;
      if (h.startsWith('/') && !/^\/\//.test(h)) continue;   // site-relative chrome
      url = h.startsWith('//') ? 'https:' + h : h;
      break;
    }

    const text = li.innerText.replace(/\s+/g, ' ').trim();

    // Some external cards carry the raw URL as their entire text, no anchor.
    // Strip Amazon's per-card action chrome, which rides along in innerText.
    const ACTION_CHROME = /\s*(Add a note|Move|Delete|Shop products|Add to (?:List|Cart)|Edit comment)\s*/gi;
    let titleText = text.replace(ACTION_CHROME, ' ').replace(/\s+/g, ' ').trim();

    if (!url) {
      const m = titleText.match(/https?:\/\/[^\s"]+/);
      if (m) url = m[0];
    }

    // Title: text before "From <domain>", else before the URL, else the cleaned text.
    let title = null;
    if (titleText) {
      const fromIdx = titleText.search(/\s+From\s+[\w.-]+\.[a-z]{2,}/i);
      if (fromIdx > 0) {
        title = titleText.slice(0, fromIdx).trim();
      } else if (url && titleText.startsWith(url)) {
        // card text is just the URL — no usable title
        title = null;
      } else {
        title = titleText;
      }
      if (title && title.length > 400) title = title.slice(0, 400);
      // innerText sometimes leaves entities behind on these cards.
      if (title) {
        const ta = document.createElement('textarea');
        ta.innerHTML = title;
        title = ta.value.replace(/\s+/g, ' ').trim();
      }
    }

    // data-price is not numeric on off-site cards; never record a sentinel.
    let price = priceAttr;
    if (price && !/^[0-9]/.test(price)) price = null;
    if (!price && priceEl) {
      const p = priceEl.innerText.trim();
      if (/^[0-9$£€]/.test(p)) price = p;
    }

    const externalIdEl = li.querySelector('a[href*="itemExternalId"]');
    const externalId = externalIdEl
      ? (externalIdEl.getAttribute('href').match(/itemExternalId=([^&]+)/) || [])[1] || null
      : null;

    // Oldest off-site cards (2016-2017) have no link at all: Amazon kept the
    // title but never retained the URL. Reconstruct a placeholder so the want
    // survives, and mark it so nothing downstream mistakes it for the real page.
    let urlSource = 'original';
    if (!url) {
      const rebuilt = reconstruct(title, titleText);
      if (rebuilt) {
        url = rebuilt;
        urlSource = 'reconstructed';
      } else {
        urlSource = 'unavailable';
      }
    }

    out.push({
      shape: 'external',
      asin: null,
      url: url,
      url_source: urlSource,
      title: title,
      price: price,
      added: added,
      wishlist_item_id: li.getAttribute('data-itemid'),
      external_id: externalId ? decodeURIComponent(externalId) : null
    });
  }
  return JSON.stringify(out);
})
"""

SCROLL_JS = r"""
(async (maxRounds) => {
  let prev = document.querySelectorAll('li[data-itemid]').length;
  let stable = 0, n = 0;
  while (stable < 3 && n < maxRounds) {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 1500));
    const now = document.querySelectorAll('li[data-itemid]').length;
    if (now === prev) stable++; else stable = 0;
    prev = now;
    n++;
  }
  return JSON.stringify({ count: prev, rounds: n, stable: stable });
})
"""

COUNT_JS = "document.querySelectorAll('li[data-itemid]').length"


def run_js(js, stdin_js=True):
    """Run JS on the current tab. Returns parsed JSON or raises."""
    if stdin_js:
        proc = subprocess.run(
            [CLI, "eval", "--stdin"],
            input=js, capture_output=True, text=True, timeout=28,
        )
    else:
        proc = subprocess.run(
            [CLI, "eval", js], capture_output=True, text=True, timeout=28,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"agent-browser eval failed: {proc.stderr[:400]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError("empty eval output")
    # CLI prints a JSON-encoded string; unwrap once.
    val = json.loads(out)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def scroll_to_end(max_rounds=40):
    rounds_left = max_rounds
    last = 0
    while rounds_left > 0:
        res = run_js(f"({SCROLL_JS.strip()})({min(8, rounds_left)})")
        last = res["count"]
        rounds_left -= res["rounds"]
        print(f"  scrolled: {res['count']} cards (stable {res['stable']}, "
              f"{res['rounds']} rounds)", file=sys.stderr)
        if res["stable"] >= 3:
            break
        # still growing — keep going
    return last


def extract_all(total):
    items = []
    for start in range(0, total, CHUNK):
        end = min(start + CHUNK, total)
        js = f"({EXTRACT_JS.strip()})({start}, {end})"
        chunk = run_js(js)
        items.extend(chunk)
        print(f"  extracted {start}-{end}: {len(chunk)}", file=sys.stderr)
        time.sleep(0.15)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="Amazon wishlist id")
    ap.add_argument("--out", required=True, help="JSON output path")
    ap.add_argument("--max-scroll-rounds", type=int, default=40)
    args = ap.parse_args()

    print(f"Scrolling list {args.list} to load all cards...", file=sys.stderr)
    total = scroll_to_end(args.max_scroll_rounds)
    if total == 0:
        print("ERROR: no cards found. Is the wishlist tab active and logged in?",
              file=sys.stderr)
        return 2

    print(f"Extracting {total} cards...", file=sys.stderr)
    items = extract_all(total)

    amazon = [i for i in items if i["shape"] == "amazon"]
    external = [i for i in items if i["shape"] == "external"]
    reconstructed = [i for i in items if i.get("url_source") == "reconstructed"]
    unavailable = [i for i in items if i.get("url_source") == "unavailable"]

    payload = {
        "list": args.list,
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totals": {
            "all": len(items),
            "amazon": len(amazon),
            "external": len(external),
            "url_reconstructed": len(reconstructed),
            "url_unavailable": len(unavailable),
            "missing_url": sum(1 for i in items if not i["url"]),
            "missing_title": sum(1 for i in items if not i["title"]),
            "missing_added": sum(1 for i in items if not i["added"]),
        },
        "items": items,
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1)

    print(f"\nWrote {args.out}", file=sys.stderr)
    print(json.dumps(payload["totals"], indent=1), file=sys.stderr)

    # The rescue branch gets its own report.
    print("\n--- OFF-SITE WANTS (the irreplaceable ones) ---", file=sys.stderr)
    print(f"{len(external)} found, {sum(1 for i in external if not i['url'])} "
          f"missing a URL, {sum(1 for i in external if not i['title'])} "
          f"missing a title", file=sys.stderr)
    for i in external[:5]:
        print(f"  {i['title']!r:55.55} -> {i['url']}", file=sys.stderr)

    if reconstructed:
        print(f"\n--- {len(reconstructed)} RECONSTRUCTED URLs (Amazon lost the "
              f"original) ---", file=sys.stderr)
        for i in reconstructed:
            print(f"  {i['title']!r:52.52} -> {i['url']}", file=sys.stderr)

    if unavailable:
        print(f"\n--- {len(unavailable)} WITH NO RECOVERABLE URL AT ALL ---",
              file=sys.stderr)
        for i in unavailable:
            print(f"  {i['title']!r:52.52} ({i['added']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
