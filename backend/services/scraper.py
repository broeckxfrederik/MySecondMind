import asyncio
import json
import re
import httpx
import trafilatura
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


async def _scrape_reddit(url: str, client: httpx.AsyncClient) -> ScrapedPage:
    """Use Reddit's JSON API — handles share links (/s/...) via redirect resolution."""
    headers = {"User-Agent": "MySecondMind/1.0 (knowledge base scraper)"}

    head = await client.get(url, headers=headers)
    canonical = str(head.url)

    clean = canonical.split("?")[0].rstrip("/")
    resp = await client.get(clean + ".json", headers=headers, params={"raw_json": "1"})
    resp.raise_for_status()
    data = resp.json()

    post = data[0]["data"]["children"][0]["data"]
    title = post.get("title", "Reddit Post")
    selftext = post.get("selftext", "").strip()

    parts = [selftext] if selftext else []
    if len(data) > 1:
        for child in data[1]["data"]["children"][:10]:
            body = child.get("data", {}).get("body", "").strip()
            if body and body != "[deleted]" and len(body) > 30:
                parts.append(body)

    text = "\n\n".join(parts) or title
    return ScrapedPage(title=title, text=text[:12000], url=canonical)


def _extract_title(html: str, url: str) -> str:
    """Pull title from Open Graph, then <title>, then <h1>."""
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title:
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else url


def _bs_fallback(html: str, url: str) -> ScrapedPage:
    """BeautifulSoup extraction when trafilatura returns nothing."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "button", "iframe", "noscript"]):
        tag.decompose()

    title = _extract_title(html, url)

    content_node = (
        soup.find("article") or soup.find("main")
        or soup.find(id="content") or soup.find(class_="content")
        or soup.body
    )
    if content_node:
        paragraphs = [
            elem.get_text(separator=" ", strip=True)
            for elem in content_node.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"])
            if len(elem.get_text(strip=True)) > 30
        ]
        text = "\n\n".join(paragraphs)
    else:
        text = soup.get_text(separator="\n", strip=True)

    return ScrapedPage(title=title, text=text[:12000], url=url)


async def _trafilatura_fetch(url: str) -> str | None:
    """
    Run trafilatura.fetch_url() in a thread (it's synchronous).
    trafilatura uses its own headers and retry logic designed to bypass
    common anti-scraping measures.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, trafilatura.fetch_url, url)


def _trafilatura_extract(html: str, url: str) -> ScrapedPage | None:
    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        favor_recall=True,
    )
    if not extracted or len(extracted.strip()) < 200:
        return None
    return ScrapedPage(title=_extract_title(html, url), text=extracted[:12000], url=url)


async def scrape_url(url: str) -> ScrapedPage:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # ── Reddit: dedicated JSON API ─────────────────────────────────────────────
    if _is_reddit(url):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                return await _scrape_reddit(url, client)
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=422, detail=f"Reddit fetch failed (HTTP {e.response.status_code})")
        except httpx.RequestError as e:
            raise HTTPException(status_code=422, detail=f"Could not reach Reddit: {e}")

    # ── General: try trafilatura fetcher first (best anti-block headers) ───────
    html = await _trafilatura_fetch(url)
    if html:
        result = _trafilatura_extract(html, url)
        if result:
            return result
        # trafilatura fetched but extracted too little — try BS fallback on same html
        return _bs_fallback(html, url)

    # ── trafilatura fetch failed (blocked/timeout) — try httpx ────────────────
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 403:
            raise HTTPException(
                status_code=422,
                detail="The site blocked scraping (403). Paste the article text directly instead."
            )
        raise HTTPException(status_code=422, detail=f"Failed to fetch URL (HTTP {status}): {url}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=422, detail=f"Could not reach URL: {e}")

    html = response.text
    result = _trafilatura_extract(html, url)
    if result:
        return result
    return _bs_fallback(html, url)
