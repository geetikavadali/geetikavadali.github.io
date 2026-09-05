#!/usr/bin/env python3
"""Sync posts from a Substack publication into Jekyll _posts/.

The RSS feed provides post metadata (title, date, slug, link) and a full HTML
body, but rendered LaTeX math is only present on the rendered post page. This
script therefore fetches each post page for the full body and falls back to
the feed content when a page cannot be fetched.

Substack images (single figures and image galleries) are converted into
al-folio-styled `<figure>` HTML blocks with captions below the image; math
delimiters are preserved through kramdown for MathJax. It writes one
`_posts/YYYY-MM-DD-<slug>.md` per post and is idempotent: existing slugs are
skipped so re-runs never duplicate or clobber posts.

Run locally:
    python bin/sync_substack.py [--dry-run]
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as md

PUBLICATION = "geetening.substack.com"
FEED_URL = f"https://{PUBLICATION}/feed"
ARCHIVE_URL = f"https://{PUBLICATION}/api/v1/archive?sort=new&limit=50"
POSTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_posts"
)

# Slugs to skip syncing (e.g. posts you do not want mirrored on the site).
SKIP_SLUGS: set[str] = set()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

FIGURE_MARKER = re.compile(
    r"^[ \t]*(?:\[)?(@@SUBSTACK-FIG-\d+@@)(?:\]\([^)]*\))?[ \t]*$",
    re.MULTILINE,
)


def slug_from_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9-]", "-", slug).strip("-")


def fetch(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response


def fetch_archive() -> dict:
    try:
        posts = fetch(ARCHIVE_URL).json()
        return {p["slug"]: p for p in posts if p.get("slug")}
    except Exception as exc:  # noqa: BLE001 - archive is only an enhancement
        print(f"  ! could not fetch archive API ({exc}); continuing without cover images")
        return {}


def get_body_html(entry) -> str:
    """Rendered page HTML (includes math), falling back to the feed body."""
    try:
        soup = BeautifulSoup(fetch(entry.link).text, "html.parser")
        body = soup.find("div", class_=["body", "markup"])
        if body is not None:
            return str(body)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! page fetch failed ({exc}); using feed content")
    content = entry.get("content")
    return content[0]["value"] if content else entry.get("summary", "")


def clean_soup(soup: BeautifulSoup) -> None:
    """Strip script/style blocks, subscribe widgets and other chrome."""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for tag in soup.select(
        ".subscription-widget-wrap-editor, .subscription-widget, "
        ".paywall, div[class*='subscribe'], div[class*='signup'], "
        "div[class*='recommended'], div[class*='suggested'], div[class*='promote']"
    ):
        tag.decompose()
    for tag in soup.find_all(attrs={"aria-label": True}):
        if "subscribe" in (tag.get("aria-label") or "").lower():
            tag.decompose()

    # Resolve rendered LaTeX: keep the inner \(...\) text.
    for div in soup.select("div.latex-rendered"):
        span = div.find("span")
        if span:
            div.replace_with(NavigableString(span.get_text()))
        else:
            div.decompose()


def collect_figures(soup: BeautifulSoup) -> list[dict]:
    """Replace Substack image markup with markers; return figure specs.

    Each spec is {"srcs": [(src, width, height, alt)], "caption": str}.
    Handles both the rendered-page markup (galleries are flex rows of
    <picture><img>) and the RSS fallback (data-attrs JSON embeds).
    """
    figures: list[dict] = []
    consumed: set[int] = set()

    def image_dims(img: Tag) -> tuple[str, str, str, str]:
        src = img.get("src") or ""
        alt = img.get("alt") or ""

        def num(value) -> int | None:
            try:
                return round(float(value))
            except (TypeError, ValueError):
                return None

        width, height = num(img.get("width")), num(img.get("height"))
        # Original dimensions are often encoded in the CDN URL (_WxH.ext).
        match = re.search(r"_(\d+)x(\d+)\.\w+", src)
        orig_w, orig_h = (int(match.group(1)), int(match.group(2))) if match else (None, None)
        if width and height:
            return src, str(width), str(height), alt
        if orig_w and orig_h:
            if width:
                # Keep the aspect ratio of the original for the served width.
                return src, str(width), str(round(width * orig_h / orig_w)), alt
            if not width and not height:
                return src, str(orig_w), str(orig_h), alt
        return src, "", "", alt

    def consume(element: Tag, srcs: list, caption: str) -> None:
        holder = element
        # Consume a wrapper div whose only content is this element.
        parent = holder.parent
        while (
            parent is not None
            and parent.name == "div"
            and not parent.get_text(strip=True)
            and len(parent.find_all(True, recursive=False)) == 1
            and id(parent) not in consumed
        ):
            holder, parent = parent, parent.parent
        if id(holder) in consumed:
            return
        consumed.add(id(holder))
        marker = soup.new_tag("p")
        marker.string = f"@@SUBSTACK-FIG-{len(figures)}@@"
        holder.replace_with(marker)
        figures.append({"srcs": srcs, "caption": caption})

    # RSS-style gallery embeds carrying their images in a data-attrs JSON blob.
    for div in soup.select("div.image-gallery-embed"):
        srcs: list = []
        caption = ""
        attrs = div.get("data-attrs") or ""
        try:
            data = json.loads(html_mod.unescape(attrs))
            gallery = data.get("gallery", {})
            srcs = [
                (img["src"], None, None, "")
                for img in gallery.get("images", [])
                if img.get("src")
            ]
            caption = gallery.get("caption") or ""
        except Exception:  # noqa: BLE001
            srcs = [image_dims(img) for img in div.select("img") if img.get("src")]
        if srcs:
            consume(div, srcs, caption)
        else:
            div.decompose()

    # Rendered galleries/figures: any element with a direct figcaption child.
    for el in soup.find_all(lambda t: t.find("figcaption", recursive=False) is not None):
        if id(el) in consumed or any(id(p) in consumed for p in el.parents):
            continue
        imgs = [image_dims(img) for img in el.find_all("img") if img.get("src")]
        if not imgs:
            el.decompose()
            continue
        caption = el.find("figcaption", recursive=False).get_text(" ", strip=True)
        consume(el, imgs, caption)

    # Any remaining standalone images (no caption element anywhere).
    for img in soup.find_all("img"):
        spec = image_dims(img)
        if not spec[0]:
            img.decompose()
            continue
        holder = img
        parent = img.parent
        # Standalone images are usually wrapped in an <a> link; consuming the
        # link too keeps markdownify from wrapping the marker in [..](..).
        if parent is not None and parent.name == "a" and not parent.get_text(strip=True):
            holder = parent
        consume(holder, [spec], "")

    return figures


def is_portrait(width: str | None, height: str | None) -> bool:
    try:
        return float(height) > float(width) * 1.15
    except (TypeError, ValueError):
        return False


def img_tag(src: str, width: str | None, height: str | None, alt: str = "") -> str:
    attrs = [f'src="{html_mod.escape(src, quote=True)}"']
    if alt:
        attrs.append(f'alt="{html_mod.escape(alt, quote=True)}"')
    if width:
        attrs.append(f'width="{html_mod.escape(width, quote=True)}"')
    if height:
        attrs.append(f'height="{html_mod.escape(height, quote=True)}"')
    attrs.append('loading="lazy"')
    attrs.append("data-zoomable")
    return f'<img {" ".join(attrs)} />'


def caption_tag(caption: str) -> str:
    return f'<figcaption class="caption">{html_mod.escape(caption)}</figcaption>'


def render_figure(spec: dict) -> str:
    srcs = spec["srcs"]
    caption = (spec.get("caption") or "").strip()
    if not srcs:
        return ""
    if len(srcs) == 1:
        src, width, height, alt = srcs[0]
        classes = "substack-figure"
        if is_portrait(width, height):
            classes += " substack-portrait"
        cap = f"\n  {caption_tag(caption)}" if caption else ""
        return f'<figure class="{classes}">\n  {img_tag(src, width, height, alt)}{cap}\n</figure>'
    images = "\n    ".join(img_tag(src, width, height) for src, width, height, _ in srcs)
    cap = f"\n  {caption_tag(caption)}" if caption else ""
    return (
        '<figure class="substack-gallery">\n'
        '  <div class="substack-gallery-row">\n'
        f"    {images}\n"
        "  </div>"
        f"{cap}\n"
        "</figure>"
    )


def convert_body(body_html: str) -> str:
    soup = BeautifulSoup(body_html, "html.parser")
    clean_soup(soup)
    figures = collect_figures(soup)

    text = md(str(soup), heading_style="ATX", bullets="-")
    text = re.sub(r"[ \t]+\n", "\n", text)
    # De-indent standalone math lines so kramdown doesn't parse them as code blocks.
    text = re.sub(r"^[ ]{2,}(\\\()", r"\1", text, flags=re.MULTILINE)
    text = protect_math(text)

    specs = {f"@@SUBSTACK-FIG-{i}@@": render_figure(spec) for i, spec in enumerate(figures)}
    text = FIGURE_MARKER.sub(lambda m: specs.get(m.group(1), ""), text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def to_markdown(html_str: str) -> str:
    return convert_body(html_str)


def protect_math(text: str) -> str:
    """Preserve LaTeX math delimiters through kramdown (GFM).

    kramdown strips the backslash from markdown escapes such as '\\(' or
    '\\_', so a single backslash in the markdown source would be lost and
    MathJax would never see the '\\(...\\)' delimiters. Doubling the
    backslashes on the delimiters makes kramdown emit a literal '\\(' /
    '\\)', while inner escapes like '\\_' stay single (kramdown turns those
    into a literal '_' without triggering emphasis).
    """

    def make_replacer(open_delim, close_delim):
        def repl(match):
            inner = re.sub(r"\s+", " ", match.group(1))
            # Escape pipes so kramdown GFM doesn't parse the math as a table.
            inner = inner.replace("|", "\\|")
            return open_delim + inner + close_delim

        return repl

    text = re.sub(
        r"\\\((.*?)\\\)",
        make_replacer("\\\\(", "\\\\)"),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\\[(.*?)\\\]",
        make_replacer("\\\\[", "\\\\]"),
        text,
        flags=re.DOTALL,
    )
    return text


def yaml_str(value: str) -> str:
    return f'"{value.strip().replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'


def write_post(entry, archive: dict, dry_run: bool):
    slug = slug_from_url(entry.link)
    if slug in SKIP_SLUGS:
        print(f"skip  {slug} (in SKIP_SLUGS)")
        return
    published = entry.published_parsed or entry.updated_parsed
    date = datetime(*published[:6])
    fname = f"{date.strftime('%Y-%m-%d')}-{slug}.md"
    path = os.path.join(POSTS_DIR, fname)
    if os.path.exists(path):
        print(f"skip  {fname} (already exists)")
        return

    meta = archive.get(slug, {})
    description = (meta.get("subtitle") or entry.get("summary") or "").strip()
    thumbnail = meta.get("cover_image") or ""

    body = to_markdown(get_body_html(entry))

    front_matter = [
        "---",
        "layout: post",
        f"title: {yaml_str(entry.title)}",
        f"date: {date.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if description:
        front_matter.append(f"description: {yaml_str(description)}")
    if thumbnail:
        front_matter.append(f"thumbnail: {yaml_str(thumbnail)}")
    front_matter.append("tags:")
    front_matter.append("  - substack")
    front_matter.append("---")
    front_matter.append("")

    content = "\n".join(front_matter) + "\n" + body + "\n"

    if dry_run:
        print(f"new   {fname} (dry run)")
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"wrote {fname}")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    feed = feedparser.parse(FEED_URL)
    if not feed.entries:
        print("no entries in feed; aborting")
        return 1
    archive = fetch_archive()
    for entry in feed.entries:
        write_post(entry, archive, dry_run=dry_run)
    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())