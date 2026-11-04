"""Watch official storefront product links and shopping categories. Python stdlib only."""
import json, os, re, sys, time
from urllib.error import HTTPError, URLError
from persistence import atomic_write, save_state
import stock
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
    for attempt in range(4):
        try:
            with urlopen(req, timeout=25) as r:
                if r.status not in (200, 204):
                    raise RuntimeError("Discord rejected notification")
            print("Discord accepted notification", flush=True)
            return
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == 3:
                raise RuntimeError("Discord delivery failed; alert retained for retry") from None
            delay = 2 ** (attempt + 1)
            if isinstance(exc, HTTPError) and exc.code == 429:
                try:
                    delay = max(delay, float(exc.headers.get("Retry-After", 0)))
                except (ValueError, TypeError):
                    pass
            time.sleep(min(delay, 30))

def deliver_pending(webhook):
    state = json.loads(STATE.read_text())
    for key, message in list(state.get("pending", {}).items()):
        notify(webhook, message)
        state["pending"].pop(key, None)
        state["delivered"] = sorted(set(state.get("delivered", [])) | {key})
        atomic_write(STATE, state)
        save_state()
        state = json.loads(STATE.read_text())


def main():
    dry = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK_URL","")
    if not dry and not webhook:
        raise RuntimeError("Missing Discord webhook; baseline preserved")
    if webhook and not re.fullmatch(r"https://discord.com/api/webhooks/[0-9]+/[^/?]+", webhook):
        raise RuntimeError("Invalid Discord webhook URL")
    old = json.loads(STATE.read_text()) if STATE.exists() else None
    # Retry saved alerts before fetching: a short-lived listing may have disappeared.
    if not dry and old is not None:
        try:
            deliver_pending(webhook)
        except RuntimeError:
            print("Saved alert still pending; continuing storefront scan", flush=True)
        old = json.loads(STATE.read_text())
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
    observations = stock.collect(products)
    stock_state, restocks = stock.update((old or {}).get("stock", {}), observations, products)
    events = changes(old, products, categories)
    print(f"Checked {len(categories)} current categories; {len(products)} listings; {len(events)} new changes.")
    if dry:
        print(json.dumps({"categories":sorted(categories), "products":products, "events":events, "restocks":restocks}, indent=2))
        return
    pending = dict((old or {}).get("pending", {}))
    delivered = (old or {}).get("delivered", [])
    pending.update(restocks)
    for kind, url, name in events:
        key = kind + ":" + url
        if key not in delivered:
            pending[key] = f"{kind}: {name}\n{url}\nListing detected; stock and checkout availability are not verified."
    if old is None:
        pending["setup"] = f"Chrome Hearts monitor connected. Baseline: {len(products)} listings. This is setup confirmation, not a drop."
    new = {"categories": sorted(categories | set((old or {}).get("categories", []))),
           "products": dict((old or {}).get("products", {}), **products),
           "pending": pending, "delivered": delivered, "stock": stock_state}
    # Commit detected alerts before attempting Discord, so restarts retain them.
    if old != new:
        atomic_write(STATE, new)
        save_state()
    deliver_pending(webhook)
    print("Check completed at", datetime.now(timezone.utc).isoformat())
if __name__ == "__main__":
    main()


