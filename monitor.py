"""Watch official storefront product links and shopping categories. Python stdlib only."""
import json, os, re, sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

BASE = "https://www.chromehearts.com/"
STATE = Path("state.json")
SKIP = {"login", "cart", "contact", "account", "search"}
class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.meta = {}
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "product-metadata" in a.get("class", "").split():
            self.meta[a.get("data-pid","")] = a
        if tag == "a" and a.get("href"):
            u = urlsplit(urljoin(BASE, a["href"]))
            if u.hostname == "www.chromehearts.com":
                url = BASE.rstrip("/") + u.path
                self.links[url] = a.get("aria-label", "") or self.links.get(url, "")
    def categories(self):
        return {u for u in self.links if re.fullmatch(r"/[a-zA-Z0-9-]+/?", urlsplit(u).path)
                and urlsplit(u).path.strip("/") not in SKIP}
    def products(self):
        result = {}
        for url, label in self.links.items():
            path = urlsplit(url).path
            if path.count("/") < 3 or not path.endswith(".html"):
                continue
            pid = path.rsplit("/",1)[-1][:-5]
            m = self.meta.get(pid, {})
            result[url] = m.get("data-name") or path.split("/")[-2].replace("-", " ").title()
        return result

def get_page(url):
    req = Request(url, headers={"User-Agent":"ChromeHeartsChangeMonitor/1.0", "Cache-Control":"no-cache"})
    with urlopen(req, timeout=25) as r:
        body = r.read().decode("utf-8", errors="replace")
    if "chromehearts" not in body.lower() or any(x in body.lower() for x in ["cf-chl-", "just a moment..."]):
        raise RuntimeError("Unexpected or blocked storefront response")
    p = Page()
    p.feed(body)
    return p

def changes(old, products, categories):
    if old is None:
        return []
    events = [("New shopping category", u, u.rsplit("/",1)[-1]) for u in sorted(categories - set(old["categories"]))]
    events += [("New product listing", u, products[u]) for u in sorted(set(products) - set(old["products"]))]
    return events

def notify(webhook, text):
    data = json.dumps({"content":text[:1900], "allowed_mentions":{"parse":[]}}).encode()
    req = Request(webhook, data=data, headers={"Content-Type":"application/json", "User-Agent":"CHMonitor/1.0"})
    with urlopen(req, timeout=25) as r:
        if r.status not in (200,204):
            raise RuntimeError("Discord rejected notification")

def main():
    dry = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK_URL","")
    if not dry and not webhook:
        print("Setup pending: add DISCORD_WEBHOOK_URL repository secret. No baseline advanced.")
        return
    if webhook and not re.fullmatch(r"https://discord.com/api/webhooks/[0-9]+/[^/?]+", webhook):
        raise RuntimeError("Invalid Discord webhook URL")
    old = json.loads(STATE.read_text()) if STATE.exists() else None
    home = get_page(BASE)
    categories = home.categories()
    if not categories:
        raise RuntimeError("No shopping categories found; preserving baseline.")
    products = home.products()
    # Recheck previously observed category URLs even if removed from navigation.
    for url in sorted(categories | (set(old["categories"]) if old else set())):
        products.update(get_page(url).products())
    if not products:
        raise RuntimeError("No products found; preserving baseline.")
    events = changes(old, products, categories)
    print(f"Checked {len(categories)} current categories; {len(products)} listings; {len(events)} new changes.")
    if dry:
        print(json.dumps({"categories":sorted(categories), "products":products, "events":events}, indent=2))
        return
    if old is None:
        notify(webhook, f"Chrome Hearts monitor connected. Baseline: {len(products)} existing listings. Future new listings/categories will alert here. Active sessions check about once a minute, 10am–10pm Central. GitHub may delay session starts. This is a setup confirmation, not a drop.")
    for kind, url, name in events:
        notify(webhook, f"{kind}: {name}\n{url}\nListing detected; stock and checkout availability are not verified.")
    # Only save after all fetches and deliveries succeed. Retain seen URLs to avoid repeat alerts.
    new = {"categories": sorted(categories | (set(old["categories"]) if old else set())),
           "products": dict((old or {}).get("products",{}), **products)}
    if old != new:
        STATE.write_text(json.dumps(new, indent=2, sort_keys=True)+"\n")
    print("Check completed at", datetime.now(timezone.utc).isoformat())
if __name__ == "__main__":
    main()
