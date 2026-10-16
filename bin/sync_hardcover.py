#!/usr/bin/env python3
"""Sync the bookshelf from a Hardcover account into Jekyll _data/books.yml.

Three shelves are written, each with the books that belong on it:

    currently reading   the books Hardcover has as currently reading (up to 5)
    recently read       the 10 most recently finished books
    favorites           everything in the "Favorites" list on Hardcover

A token cannot live in a static site (anyone could read the whole library with
it), so nothing is fetched from the browser: this script reads Hardcover, writes
`_data/books.yml`, and the site renders from that file. Run it by hand, or let
`.github/workflows/sync-hardcover.yml` run it on a schedule with the token kept
in the repository's secrets.

What it writes, per book: title, author, cover (downloaded and converted to
WebP), rating, status, year, pages, dates, genres, series, review and a stable
slug used for ?book=<slug> deep links. If Hardcover has no cover for a book, the
shelf shows it in plain book cloth instead.

Run locally:
    python3 bin/sync_hardcover.py --dry-run        # show what it would write
    python3 bin/sync_hardcover.py                  # write _data/books.yml
    python3 bin/sync_hardcover.py --refresh-covers # re-download every cover

The token is read from HARDCOVER_TOKEN, or from --token-file (default
.secrets/hardcover-token, which is gitignored). Covers are converted with
ImageMagick; if that is missing the original cover URLs are kept instead.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date

API = "https://api.hardcover.app/v1/graphql"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "_data", "books.yml")
DEFAULT_COVERS = os.path.join(ROOT, "assets", "img", "books")
DEFAULT_TOKEN_FILE = os.path.join(ROOT, ".secrets", "hardcover-token")

# Hardcover's status ids.
STATUS_READ = 3
STATUS_READING = 2

# The shelves, in the order they are shelved on the page. `source` is either a
# reading status or a Hardcover list; `limit` 0 means everything on it.
SHELVES = [
    {
        "slug": "currently-reading",
        "source": "status",
        "status_id": STATUS_READING,
        "order": "date_added",
        "limit": 5,
    },
    {
        "slug": "recently-read",
        "source": "status",
        "status_id": STATUS_READ,
        "order": "last_read_date",
        "require_date": True,
        "limit": 10,
    },
    {
        "slug": "favorites",
        "source": "list",
        "list_slug": "favorites",
        "order": "position",
        "limit": 0,
    },
]

BOOK_FIELDS = """
    id
    rating
    review_raw
    has_review
    date_added
    first_read_date
    last_read_date
    privacy_setting_id
    user_book_status { slug }
    user_book_reads(order_by: { finished_at: desc_nulls_last }) { started_at finished_at }
    book {
      id
      title
      subtitle
      pages
      release_date
      release_year
      slug
      image { url }
      default_cover_edition { id isbn_13 image { url } }
      contributions { author { name } contributor_role { name } }
      taggings { tag { tag tag_category { slug } } }
      book_series { position series { name } }
    }
    edition { pages release_date isbn_13 reading_format { format } image { url } }
"""

# Subtitles are often edition noise rather than information: "Winner of the
# Booker Prize", "Stage 4: Three Men in a Boat", "roman", "Die Lehren meines
# Lebens". Those are dropped; a real subtitle is kept and shown in the card.
NOISE_SUBTITLE = re.compile(
    r"(?i)\b("
    r"a novel|a memoir|edition|winner|prize|award|bestsell|translat|unabridged|"
    r"complete|illustrated|annotated|anniversary|foreword|preface|introduction|"
    r"with a new|now a major|movie tie-in|television|netflix|hbo|large print|"
    r"reprint|school edition|student edition"
    r")\b"
)
# a subtitle in another language belongs to that edition, not to this shelf
FOREIGN_LEAD = re.compile(
    r"(?i)^(die|das|der|ein|eine|einen|dem|den|les|le|la|un|une|des|du|el|los|las|"
    r"il|lo|gli|een|het|de|del|roman|novela|romanzo)\b"
)


# --------------------------------------------------------------------------- #
# api


def read_token(args: argparse.Namespace) -> str:
    token = os.environ.get("HARDCOVER_TOKEN", "").strip()
    if token:
        return token
    path = args.token_file
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
        if token:
            return token
    sys.exit(
        "No Hardcover token. Put one in %s, or set HARDCOVER_TOKEN.\n"
        "Create it at hardcover.app -> Account settings -> Hardcover API." % path
    )


def gql(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + token,
            "user-agent": "geetikavadali.github.io bookshelf sync",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 401:
            sys.exit("Hardcover rejected the token (401). It may have expired: " + detail)
        sys.exit("Hardcover API error %s: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        sys.exit("Could not reach Hardcover: %s" % exc)
    if payload.get("errors"):
        sys.exit("Hardcover GraphQL error: %s" % json.dumps(payload["errors"])[:400])
    return payload["data"]


def whoami(token: str) -> dict:
    users = gql(token, "{ me { id username } }", {})["me"]
    if not users:
        sys.exit("Hardcover did not return a user for this token.")
    return users[0]


def shelf_rows(token: str, user_id: int) -> list[tuple[str, dict]]:
    """Every book on the three shelves, in the order it should be shelved."""
    picked: list[tuple[str, dict]] = []
    seen: set = set()

    for shelf in SHELVES:
        if shelf["source"] == "status":
            order = shelf["order"]
            where = "user_id: { _eq: %d }, status_id: { _eq: %d }" % (user_id, shelf["status_id"])
            if shelf.get("require_date"):
                where += ", %s: { _is_null: false }" % order
            query = """
                query Shelf($limit: Int!) {
                  user_books(where: { %s }, order_by: { %s: desc_nulls_last }, limit: $limit) { %s }
                }
            """ % (where, order, BOOK_FIELDS)
            rows = gql(token, query, {"limit": shelf["limit"] or 200})["user_books"]
        else:
            query = """
                query List {
                  lists(where: { user_id: { _eq: %d }, slug: { _eq: "%s" } }) {
                    name
                    list_books(order_by: { position: asc_nulls_last }) { book_id }
                  }
                }
            """ % (user_id, shelf["list_slug"])
            lists = gql(token, query, {})["lists"]
            if not lists:
                print("! no list called %r on this account" % shelf["list_slug"])
                continue
            ids = [lb["book_id"] for lb in lists[0]["list_books"]]
            print("  %s: %s (%d books)" % (shelf["slug"], lists[0]["name"], len(ids)))
            if not ids:
                continue
            query = """
                query Books($ids: [Int!]!) {
                  user_books(where: { user_id: { _eq: %d }, book_id: { _in: $ids } }) { %s }
                }
            """ % (user_id, BOOK_FIELDS)
            rows = gql(token, query, {"ids": ids})["user_books"]
            by_book = {r["book"]["id"]: r for r in rows}
            rows = [by_book[i] for i in ids if i in by_book]

        if shelf["limit"]:
            rows = rows[: shelf["limit"]]
        added = 0
        for row in rows:
            key = (row.get("book") or {}).get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append((shelf["slug"], row))
            added += 1
        print("  %s: %d books" % (shelf["slug"], added))
    return picked


# --------------------------------------------------------------------------- #
# mapping


def plain_text(value: str | None) -> str:
    """Reviews arrive as HTML when they were written in the editor."""
    if not value:
        return ""
    text = value
    if re.search(r"</?[a-z][^>]*>", text, re.IGNORECASE):
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "- ", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "book"


def fmt_date(value: str | None) -> str:
    if not value:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return match.group(1) if match else ""


def is_noise_subtitle(value: str, title: str = "") -> bool:
    if not value:
        return True
    if value.lower() == title.lower() or value.lower() in title.lower():
        return True
    if NOISE_SUBTITLE.search(value):
        return True
    if FOREIGN_LEAD.match(value) and len(value.split()) <= 6:
        return True
    return False


def entry_of(shelf: str, row: dict) -> dict | None:
    book = row.get("book") or {}
    title = one_line(book.get("title") or "")
    if not title:
        return None

    subtitle = one_line(book.get("subtitle") or "")
    if is_noise_subtitle(subtitle, title):
        subtitle = ""

    authors = [
        one_line((c.get("author") or {}).get("name") or "")
        for c in (book.get("contributions") or [])
        if (c.get("contributor_role") or {}).get("name", "").lower() == "author"
    ]
    authors = [a for a in authors if a]
    if not authors:  # never leave a book unattributed
        authors = [
            one_line((c.get("author") or {}).get("name") or "") for c in (book.get("contributions") or [])
        ]
        authors = [a for a in authors if a]

    genres: list[str] = []
    for tagging in book.get("taggings") or []:
        tag = tagging.get("tag") or {}
        if (tag.get("tag_category") or {}).get("slug") != "genre":
            continue
        name = one_line(tag.get("tag") or "")
        if name and name.lower() not in [g.lower() for g in genres]:
            genres.append(name)

    series_name, series_pos = "", ""
    for link in book.get("book_series") or []:
        name = one_line((link.get("series") or {}).get("name") or "")
        if name:
            series_name = name
            pos = link.get("position")
            series_pos = str(pos) if pos not in (None, "") else ""
            break

    edition = row.get("edition") or {}
    cover = (
        ((book.get("default_cover_edition") or {}).get("image") or {}).get("url")
        or (book.get("image") or {}).get("url")
        or (edition.get("image") or {}).get("url")
        or ""
    )
    isbn = (book.get("default_cover_edition") or {}).get("isbn_13") or edition.get("isbn_13") or ""

    reads = [r for r in (row.get("user_book_reads") or []) if r.get("finished_at") or r.get("started_at")]
    finished = fmt_date(row.get("last_read_date") or (reads[0].get("finished_at") if reads else ""))
    started = fmt_date(row.get("first_read_date") or (reads[-1].get("started_at") if reads else ""))
    added = fmt_date(row.get("date_added"))

    status = "reading" if shelf == "currently-reading" else "read"
    rating = row.get("rating")

    return {
        "shelf": shelf,
        "title": title,
        "subtitle": subtitle,
        "author": " & ".join(authors),
        "slug": one_line(book.get("slug") or "") or slugify(title),
        "cover": cover,
        "rating": float(rating) if rating else 0.0,
        "status": status,
        "year": book.get("release_year") or fmt_date(book.get("release_date"))[:4],
        "pages": book.get("pages") or edition.get("pages") or "",
        "started": started,
        "finished": finished,
        "added": added,
        "genres": genres[:4],
        "series": ("%s #%s" % (series_name, series_pos) if series_name and series_pos else series_name),
        "review": plain_text(row.get("review_raw")),
        "format": one_line((edition.get("reading_format") or {}).get("format") or ""),
        "links": {"hardcover": "https://hardcover.app/books/%s" % (one_line(book.get("slug") or "") or slugify(title))},
        "isbn": isbn,
    }


# --------------------------------------------------------------------------- #
# covers


def have_magick() -> bool:
    for binary in ("magick", "convert"):
        try:
            subprocess.run(
                [binary, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


def fetch_cover(entry: dict, covers_dir: str, width: int, quality: int, refresh: bool) -> str:
    """Download one cover and convert it. Returns the path to use in the data."""
    url = entry["cover"]
    if not url:
        return ""
    name = slugify(entry["slug"]) + ".webp"
    target = os.path.join(covers_dir, name)
    rel = "/assets/img/books/" + name
    if os.path.exists(target) and not refresh:
        return rel
    try:
        req = urllib.request.Request(url, headers={"user-agent": "geetikavadali.github.io bookshelf sync"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print("  ! cover download failed for %s: %s" % (entry["title"][:40], exc))
        return url
    os.makedirs(covers_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tmp:
        tmp.write(data)
        raw = tmp.name
    try:
        if not have_magick():  # pragma: no cover - environment dependent
            print("  ! imagemagick missing, keeping the remote cover for %s" % entry["title"][:40])
            return url
        subprocess.run(
            ["magick", raw, "-resize", "%dx" % width, "-strip", "-quality", str(quality), target],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        print("  ! cover conversion failed for %s" % entry["title"][:40])
        return url
    finally:
        os.unlink(raw)
    return rel


# --------------------------------------------------------------------------- #
# yaml


def yaml_scalar(value: str) -> str:
    """Double-quoted when quoting is needed, bare when it is not - which is
    what prettier asks of a YAML file, so re-runs never churn the file."""
    if value == "":
        return '""'
    bare = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.&'()/-]*", value) and not value.endswith(" ")
    looks_like_scalar = re.fullmatch(r"[-+]?\d+(\.\d+)?", value) or value.lower() in (
        "yes",
        "no",
        "true",
        "false",
        "null",
        "on",
        "off",
        "~",
    )
    if bare and not looks_like_scalar and ": " not in value and not value.startswith(("-", "?", ":")):
        return value
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def yaml_block(value: str, indent: str) -> list[str]:
    """A literal block scalar, one line per line, so reviews keep their shape."""
    lines = ["%s|" % indent]
    body = value.split("\n")
    if body and body[0].strip() == "":
        lines.append("%s  " % indent)
    for line in body:
        lines.append(("%s  %s" % (indent, line)).rstrip())
    return lines


def emit_yaml(entries: list[dict], user: dict, counts: dict) -> str:
    out: list[str] = []
    out.append("# Books on the shelf, read from Hardcover by bin/sync_hardcover.py.")
    out.append("#")
    out.append("# GENERATED FILE - do not edit by hand. Re-run the sync instead:")
    out.append("#     python3 bin/sync_hardcover.py")
    out.append("# or let .github/workflows/sync-hardcover.yml run it on a schedule.")
    out.append("#")
    out.append(
        "# hardcover.app/@%s, synced %s. %d books: %s."
        % (
            user.get("username", ""),
            date.today().isoformat(),
            len(entries),
            ", ".join("%d %s" % (counts[s["slug"]], s["slug"]) for s in SHELVES if s["slug"] in counts),
        )
    )
    out.append("# Fields are documented in the script; every one of them is optional except title.")
    out.append("")
    for e in entries:
        out.append("- title: %s" % yaml_scalar(e["title"]))
        for key, value in (("subtitle", e["subtitle"]), ("author", e["author"]), ("cover", e["cover"])):
            if value:
                out.append("  %s: %s" % (key, yaml_scalar(value)))
        if e["rating"]:
            out.append("  rating: %s" % ("%g" % e["rating"]))
        out.append("  status: %s" % yaml_scalar(e["status"]))
        out.append("  shelf: %s" % yaml_scalar(e["shelf"]))
        for key in ("year", "pages", "started", "finished", "slug"):
            if e[key]:
                out.append("  %s: %s" % (key, yaml_scalar(str(e[key]))))
        if e["added"] and not (e["finished"] or e["started"]):
            out.append("  added: %s" % yaml_scalar(e["added"]))
        if e["series"]:
            out.append("  series: %s" % yaml_scalar(e["series"]))
        if e["genres"]:
            out.append("  genres: [%s]" % ", ".join(yaml_scalar(g) for g in e["genres"]))
        if e["review"]:
            out.extend(
                [
                    "  review: " + line.strip() if i == 0 else line
                    for i, line in enumerate(yaml_block(e["review"], "  "))
                ]
            )
        links = {k: v for k, v in (e["links"] or {}).items() if v}
        if links:
            out.append("  links:")
            for key, value in links.items():
                out.append("    %s: %s" % (key, yaml_scalar(value)))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the bookshelf from Hardcover.")
    parser.add_argument("--dry-run", action="store_true", help="print a summary, write nothing")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the data file")
    parser.add_argument("--covers-dir", default=DEFAULT_COVERS, help="where to keep downloaded covers")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="file holding the API token")
    parser.add_argument("--no-covers", action="store_true", help="keep Hardcover's cover URLs, download nothing")
    parser.add_argument(
        "--refresh-covers", action="store_true", help="re-download covers that are already here"
    )
    parser.add_argument("--prune-covers", action="store_true", help="delete covers this run does not reference")
    parser.add_argument("--cover-width", type=int, default=420, help="cover width in pixels")
    parser.add_argument("--cover-quality", type=int, default=86, help="WebP quality for covers")
    args = parser.parse_args()

    token = read_token(args)
    user = whoami(token)
    print("Hardcover: @%s (id %s)" % (user.get("username"), user.get("id")))

    print("Reading the shelves:")
    rows = shelf_rows(token, user["id"])

    entries: list[dict] = []
    skipped_private = 0
    for shelf, row in rows:
        if row.get("privacy_setting_id") not in (None, 1):
            skipped_private += 1
            continue
        entry = entry_of(shelf, row)
        if entry:
            entries.append(entry)
    if skipped_private:
        print("Skipped %d private reading(s)." % skipped_private)

    counts: dict = {}
    for entry in entries:
        counts[entry["shelf"]] = counts.get(entry["shelf"], 0) + 1
    print(
        "Writing %d books - %d rated, %d with a review, %d with covers."
        % (
            len(entries),
            sum(1 for e in entries if e["rating"]),
            sum(1 for e in entries if e["review"]),
            sum(1 for e in entries if e["cover"]),
        )
    )

    if not args.no_covers:
        print("Covers -> %s" % os.path.relpath(args.covers_dir, ROOT))
        for entry in entries:
            if entry["cover"]:
                entry["cover"] = fetch_cover(
                    entry, args.covers_dir, args.cover_width, args.cover_quality, args.refresh_covers
                )

    if args.prune_covers and not args.no_covers:
        keep = {
            os.path.basename(e["cover"])
            for e in entries
            if e["cover"].startswith("/assets/img/books/")
        }
        names = sorted(os.listdir(args.covers_dir)) if os.path.isdir(args.covers_dir) else []
        for name in names:
            if name.endswith(".webp") and name not in keep:
                os.unlink(os.path.join(args.covers_dir, name))
                print("  - removed unreferenced cover %s" % name)

    yaml_text = emit_yaml(entries, user, counts)
    if args.dry_run:
        print("\n--- %d bytes, first entries ---\n" % len(yaml_text))
        print("\n".join(yaml_text.split("\n")[:70]))
        print("...\n(dry run: nothing written)")
        return

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(yaml_text)
    os.replace(tmp, args.out)
    print(
        "Wrote %s (%d books, %.0f KB)."
        % (os.path.relpath(args.out, ROOT), len(entries), len(yaml_text) / 1024)
    )


if __name__ == "__main__":
    main()
