"""
Parsers for uploaded document formats (ePub, PDF).
Both functions return (title, text) tuples.
"""
import io


def parse_epub(data: bytes) -> tuple[str, str]:
    """Extract (title, plain-text) from an ePub file."""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(io.BytesIO(data))

    title_meta = book.get_metadata("DC", "title")
    title = title_meta[0][0] if title_meta else "Untitled ePub"

    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "lxml")
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > 100:
            parts.append(text)

    return title, "\n\n".join(parts)


def parse_pdf(data: bytes) -> tuple[str, str]:
    """Extract (title, plain-text) from a PDF file."""
    import pdfplumber

    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        meta = pdf.metadata or {}
        title = meta.get("Title", "").strip() or "Untitled PDF"
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)

    return title, "\n\n".join(parts)
