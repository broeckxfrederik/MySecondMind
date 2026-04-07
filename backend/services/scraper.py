import json
import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass
from fastapi import HTTPException


@dataclass
class ScrapedPage:
    title: str
    text: str
    url: str


def _is_reddit(url: str) -> bool:
    return bool(re.search(r"(reddit\.com|redd\.it)", url))


def _is_medium(url: str) -> bool:
    return bool(re.search(r"medium\.com", url))


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


async def _scrape_medium(url: str, client: httpx.AsyncClient) -> ScrapedPage:
    """
    Medium blocks standard scraping with 403. Use their unofficial ?format=json
    endpoint which returns the full story content. Medium prepends a )]}'\\n XSSI
    prefix that must be stripped before parsing.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    json_url = url.split("?")[0].rstrip("/") + "?format=json"
    resp = await client.get(json_url, headers=headers)
    resp.raise_for_status()

    # Strip XSSI prefix "])}'\n" that Medium prepends
    raw = resp.text
    if raw.startswith("])}'\n"):
        raw = raw[5:]
    elif raw.startswith(")]}'"):
        raw = raw[4:]

    data = json.loads(raw)

    # Navigate Medium's JSON structure
    payload = data.get("payload", {})
    post_value = payload.get("value", {})

    title = post_value.get("title", "") or post_value.get("slug", "Medium Article")

    # Extract paragraph content
    content = post_value.get("content", {})
    body_model = content.get("bodyModel", {})
    paragraphs = body_model.get("paragraphs", [])

    parts = []
    for para in paragraphs:
        ptype = para.get("type", "")
        ptext = para.get("text", "").strip()
        if not ptext:
            continue
        # Type 3 = H1, 8 = H2, 9 = H3, 1 = P, 6 = BLOCKQUOTE, 4 = IMAGE caption
        if ptype in (3,):
            parts.append(f"# {ptext}")
        elif ptype in (8, 9):
            parts.append(f"## {ptext}")
        elif ptext:
            parts.append(ptext)

    text = "\n\n".join(parts) if parts else title

    if len(text) > 12000:
        text = text[:12000] + "\n\n[... content truncated ...]"

    return ScrapedPage(title=title, text=text, url=url)


async def scrape_url(url: str) -> ScrapedPage:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            if _is_reddit(url):
                return await _scrape_reddit(url, client)

            if _is_medium(url):
                return await _scrape_medium(url, client)

            response = await client.get(url, headers=headers)
            response.raise_for_status()

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 403:
            raise HTTPException(
                status_code=422,
                detail=f"The site blocked scraping (403 Forbidden): {url}. "
                       "Try pasting the article text directly instead."
            )
        raise HTTPException(status_code=422, detail=f"Failed to fetch URL (HTTP {status}): {url}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=422, detail=f"Could not reach URL: {e}")

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
        paragraphs = []
        for elem in content_node.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
            txt = elem.get_text(separator=" ", strip=True)
            if len(txt) > 30:
                paragraphs.append(txt)
        text = "\n\n".join(paragraphs)
    else:
        text = soup.get_text(separator="\n", strip=True)

    if len(text) > 12000:
        text = text[:12000] + "\n\n[... content truncated ...]"

    return ScrapedPage(title=title, text=text, url=url)
