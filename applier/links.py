from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import requests

# Sites that block scripts (403/429, LinkedIn's 999) are probably fine; 404s and DNS failures aren't.
BLOCKED_BUT_LIKELY_REAL = {401, 403, 429, 999}


def ok(url: str | None) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        r = requests.get(url, timeout=15, allow_redirects=True, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/130 Safari/537.36"})
        r.close()
        return r.status_code < 400 or r.status_code in BLOCKED_BUT_LIKELY_REAL
    except requests.RequestException:
        return False


def check_all(urls: list[str | None]) -> list[bool]:
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(ok, urls))


def google(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)
