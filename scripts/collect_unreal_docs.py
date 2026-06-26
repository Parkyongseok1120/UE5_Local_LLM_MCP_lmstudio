#!/usr/bin/env python
"""Collect Unreal Engine documentation pages into JSONL.

This crawler is intentionally conservative: it stays on Epic's documentation
domain, keeps the Unreal documentation path, and limits page count by default.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = "CodexUnrealRagStarter/0.1 (+local personal RAG index)"
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form"}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.skip_depth = 0
        self.in_title = False
        self.in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"] or "")
        if tag == "title":
            self.in_title = True
        if tag == "h1":
            self.in_h1 = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
        if tag == "h1":
            self.in_h1 = False
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = html.unescape(data)
        if not text.strip():
            return
        self.parts.append(text)
        if self.in_title:
            self.title_parts.append(text)
        if self.in_h1:
            self.h1_parts.append(text)

    @property
    def text(self) -> str:
        return clean_text(" ".join(self.parts))

    @property
    def title(self) -> str:
        h1 = clean_text(" ".join(self.h1_parts))
        if h1:
            return h1
        title = clean_text(" ".join(self.title_parts))
        return title.replace(" | Unreal Engine Documentation", "").strip()


def clean_text(value: str) -> str:
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_seed_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def normalize_url(url: str, base: str | None = None, version: str = "5.7") -> str | None:
    joined = urljoin(base or "", url)
    parsed = urlparse(joined)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() != "dev.epicgames.com":
        return None
    if not parsed.path.startswith("/documentation/en-us/unreal-engine"):
        return None

    query = dict(parse_qsl(parsed.query, keep_blank_values=False))
    if "application_version" not in query and "/documentation/en-us/unreal-engine" in parsed.path:
        query["application_version"] = version

    normalized_query = urlencode(sorted(query.items()))
    normalized = parsed._replace(
        scheme="https",
        netloc="dev.epicgames.com",
        query=normalized_query,
        fragment="",
    )
    return urlunparse(normalized)


def fetch(url: str, timeout: int) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_page(url: str, html_text: str) -> tuple[str, str, list[str]]:
    parser = PageParser()
    parser.feed(html_text)
    title = parser.title or url
    return title, parser.text, parser.links


def iter_existing_urls(path: Path) -> Iterable[str]:
    if not path.exists():
        return []

    urls: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("url"):
                urls.append(item["url"])
    return urls


def crawl(args: argparse.Namespace) -> None:
    seeds = [normalize_url(url, version=args.version) for url in read_seed_urls(Path(args.seeds))]
    queue = deque(url for url in seeds if url)
    seen = set(iter_existing_urls(Path(args.out))) if args.resume else set()
    queued = set(queue)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    mode = "a" if args.resume else "w"
    with out_path.open(mode, encoding="utf-8") as handle:
        while queue and written < args.max_pages:
            url = queue.popleft()
            queued.discard(url)
            if url in seen:
                continue
            seen.add(url)

            try:
                html_text = fetch(url, timeout=args.timeout)
                title, text, links = parse_page(url, html_text)
            except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
                print(f"[skip] {url} ({exc})")
                continue

            if len(text) >= args.min_chars:
                item = {
                    "id": stable_id(url),
                    "source": "epic_docs",
                    "url": url,
                    "title": title,
                    "text": text,
                    "metadata": {
                        "application_version": args.version,
                        "collected_at_unix": int(time.time()),
                    },
                }
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                handle.flush()
                written += 1
                print(f"[{written}/{args.max_pages}] {title} :: {url}")

            for link in links:
                normalized = normalize_url(link, base=url, version=args.version)
                if normalized and normalized not in seen and normalized not in queued:
                    queue.append(normalized)
                    queued.add(normalized)

            if args.delay > 0:
                time.sleep(args.delay)

    print(f"done: wrote {written} pages to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Unreal Engine docs pages as JSONL.")
    parser.add_argument("--seeds", default="config/unreal_57_seed_urls.txt")
    parser.add_argument("--out", default="data/unreal58/raw_docs.jsonl")
    parser.add_argument("--version", default="5.7")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--min-chars", type=int, default=500)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    crawl(parse_args())
