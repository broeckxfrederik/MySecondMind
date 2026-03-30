import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass


@dataclass
class ScrapedPage:
    title: str
    text: str
    url: str


async def scrape_url(url: str) -> ScrapedPage:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
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
