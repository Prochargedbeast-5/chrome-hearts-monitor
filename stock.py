"""Conservative product-level restocks from explicit Product structured data."""
import json
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen

class StockPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []
        self.documents = []
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.capture = dict(attrs).get('type') == 'application/ld+json'
            self.parts = []
    def handle_data(self, data):
        if self.capture: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'script' and self.capture:
            try: self.documents.append(json.loads(''.join(self.parts)))
            except (ValueError, TypeError): pass
            self.capture = False

def availability(body):
    page = StockPage(); page.feed(body)
    states = []
    def walk(node):
        if isinstance(node, list):
            for item in node: walk(item)
        elif isinstance(node, dict):
            types = node.get('@type', [])
            if isinstance(types, str): types = [types]
            if 'Product' in types:
                offers = node.get('offers', [])
                if isinstance(offers, dict): offers = [offers]
                for offer in offers:
                    if not isinstance(offer, dict): continue
                    value = str(offer.get('availability', '')).rsplit('/', 1)[-1]
                    if value == 'InStock': states.append(True)
                    elif value in ('OutOfStock', 'SoldOut'): states.append(False)
            if '@graph' in node: walk(node['@graph'])
    for document in page.documents: walk(document)
    if True in states: return True
    if states and all(s is False for s in states): return False
    return None

def fetch_stock(url):
    req = Request(url, headers={'User-Agent': 'ChromeHeartsChangeMonitor/1.0', 'Cache-Control': 'no-cache'})
    with urlopen(req, timeout=12) as response:
        body = response.read().decode('utf-8', errors='replace')
    if any(s in body.lower() for s in ('cf-chl-', 'just a moment...')): return None
    return availability(body)

def collect(products):
    def one(url):
        try: return url, fetch_stock(url)
        except Exception as exc:
            print(f'Stock check unavailable for {url}: {type(exc).__name__}', flush=True)
            return url, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = dict(pool.map(one, products))
    known = sum(value is not None for value in result.values())
    print(f'Explicit stock status: {known}/{len(result)} products', flush=True)
    return result

def update(previous, observations, products, now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    current = dict(previous)
    alerts = {}
    for url, value in observations.items():
        if value is None: continue  # Missing/blocked data must never look like a sellout.
        before = previous.get(url)
        if before and before.get('available') is False and value is True:
            key = 'Restock:' + url + ':' + now
            alerts[key] = f'Restock detected: {products.get(url, url)}\n{url}\nProduct page now reports in stock. Check sizes and checkout availability.'
        current[url] = {'available': value, 'observed_at': now}
    return current, alerts
