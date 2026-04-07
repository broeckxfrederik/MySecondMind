import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass


@dataclass
class ScrapedPage:
    title: str
    text: str
    url: str


def _is_reddit(url: str) -> bool:
    return bool(re.search(r"(reddit\.com|redd\.it)", url))


async def _scrape_reddit(url: str, client: httpx.AsyncClient) -> ScrapedPage:
    """
    Use Reddit's JSON API to extract post content.
    Share links (/s/...) are resolved by following redirects first,
    then the resolved URL gets .json appended.
    """
    headers = {"User-Agent": "MySecondMind/1.0 (knowledge base scraper)"}

    # Resolve short/share links to canonical URL
    head = await client.get(url, headers=headers)
    canonical = str(head.url)

    # Strip query params and trailing slash, then append .json
    clean = canonical.split("?")[0].rstrip("/")
    json_url = clean + ".json"

    resp = await client.get(json_url, headers=headers, params={"raw_json": "1"})
    resp.raise_for_status()
    data = resp.json()

    post = data[0]["data"]["children"][0]["data"]
    title = post.get("title", "Reddit Post")
    selftext = post.get("selftext", "").strip()

    # Build readable text: post body + top comments
    parts = []
    if selftext:
        parts.append(selftext)

    if len(data) > 1:
        for child in data[1]["data"]["children"][:10]:
            body = child.get("data", {}).get("body", "").strip()
            if body and body != "[deleted]" and len(body) > 30:
                parts.append(body)

    text = "\n\n".join(parts) or title

    if len(text) > 12000:
        text = text[:12000] + "\n\n[... content truncated ...]"

    return ScrapedPage(title=title, text=text, url=canonical)


async def scrape_url(url: str) -> ScrapedPage:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        if _is_reddit(url):
            return await _scrape_reddit(url, client)

        response = await client.get(url, headers=headers)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "form", "button", "iframe", "noscript", "ads"]):
        tag.decompose()

    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else url

    # Try article/main first, fallback to body
    content_node = (
        soup.find("article")
        or soup.find("main")
        or soup.find(id="content")
        or soup.find(class_="content")
        or soup.body
    )

    if content_node:
        # Preserve paragraph structure
        paragraphs = []
        for elem in content_node.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
            txt = elem.get_text(separator=" ", strip=True)
            if len(txt) > 30:
                paragraphs.append(txt)
        text = "\n\n".join(paragraphs)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Trim to ~12k chars to stay within token limits
    if len(text) > 12000:
        text = text[:12000] + "\n\n[... content truncated ...]"

    return ScrapedPage(title=title, text=text, url=url)
