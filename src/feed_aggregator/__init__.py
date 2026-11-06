"""Aggregate RSS 2.0, RSS 1.0, Atom and Hacker News items into normalized entries."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPMessage
from typing import IO, Literal
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
from xml.parsers import expat

__all__ = [
    "AggregateResult",
    "Aggregator",
    "Entry",
    "FeedError",
    "Source",
    "SourceError",
    "__version__",
    "dedupe",
    "normalize_url",
    "parse_feed",
]

__version__ = "0.1.0"

ErrorCode = Literal["parse", "doctype_forbidden", "too_large", "unsupported_format", "fetch"]

_log = logging.getLogger(__name__)
_log.addHandler(logging.NullHandler())

_MAX_URL = 2048
_MAX_DEPTH = 100
_MAX_SUMMARY = 5000
_MAX_HTML = 100_000
_MAX_RESULT = 10_000
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_HN_BASE = "https://hacker-news.firebaseio.com/v0/"
_TRACKING = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
_ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_ARXIV_RE = re.compile(
    r"^/(?:abs|pdf)/([0-9]{4}[.][0-9]{4,5}|[a-z-]+(?:[.][A-Z]{2})?/[0-9]{7})"
    r"(?:v[0-9]+)?(?:[.]pdf)?$"
)
_PCT_RE = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class FeedError(Exception):
    """Base error of the library; ``code`` is a stable machine-readable identifier."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message[:200])
        self.code: ErrorCode = code


@dataclass(frozen=True)
class Entry:
    """A normalized feed entry. All text is untrusted."""

    id: str
    source: str
    title: str
    url: str
    published: datetime | None
    summary: str
    authors: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Source:
    """A configured source: a feed URL or the Hacker News top stories."""

    name: str
    url: str
    kind: Literal["feed", "hn-topstories"]


@dataclass(frozen=True)
class SourceError:
    """A failure of one source during ``collect``."""

    source: str
    code: str
    message: str


@dataclass(frozen=True)
class AggregateResult:
    """Entries and per-source errors returned by ``Aggregator.collect``."""

    entries: list[Entry] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)


def _is_http(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _unquote_path(path: str) -> str:
    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else "%" + match.group(1).upper()

    return _PCT_RE.sub(fix, path)


def _remove_dots(path: str) -> str:
    segments = path.split("/")[1:]
    out: list[str] = []
    for seg in segments:
        if seg == ".":
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    if segments and segments[-1] in (".", ".."):
        out.append("")
    return "/" + "/".join(out)


def normalize_url(url: str) -> str:
    """Return the canonical form of an http(s) URL (RFC 3986 section 6 plus heuristics).

    Raises:
        FeedError: ``too_large`` for oversized input, ``parse`` for invalid URLs.
    """
    if len(url) > _MAX_URL:
        raise FeedError("too_large", "URL is too long")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise FeedError("parse", "invalid URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        raise FeedError("parse", "URL must be absolute http or https")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise FeedError("parse", "invalid host name") from None
    if ":" in host:
        host = "[" + host + "]"
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = (userinfo + "@" if userinfo else "") + host
    if port is not None and port != (80 if scheme == "http" else 443):
        netloc += ":" + str(port)
    path = _remove_dots(_unquote_path(parts.path) or "/")
    if path != "/":
        path = path.rstrip("/") or "/"
    if host in _ARXIV_HOSTS:
        match = _ARXIV_RE.match(path)
        if match:
            return "https://arxiv.org/abs/" + match.group(1)
    kept = [
        p
        for p in parts.query.split("&")
        if p and p.split("=", 1)[0] not in _TRACKING and not p.startswith("utm_")
    ]
    query = "?" + "&".join(kept) if kept else ""
    return f"{scheme}://{netloc}{path}{query}"


def _guard_xml(data: bytes) -> None:
    depth = 0

    def forbid(*_args: object) -> None:
        raise FeedError("doctype_forbidden", "DOCTYPE and ENTITY declarations are forbidden")

    def start(_name: str, _attrs: object) -> None:
        nonlocal depth
        depth += 1
        if depth > _MAX_DEPTH:
            raise FeedError("parse", "document is nested too deeply")

    def end(_name: str) -> None:
        nonlocal depth
        depth -= 1

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = forbid
    parser.EntityDeclHandler = forbid
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise FeedError("parse", "malformed XML") from None


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    try:
        if parsed is None:
            iso = text[:-1] + "+00:00" if text[-1] in "Zz" else text
            parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup[:_MAX_HTML])
    parser.close()
    return " ".join("".join(parser.parts).split())[:_MAX_SUMMARY]


def _clean(text: str) -> str:
    return " ".join(text.split())


def _ln(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _raw(el: ET.Element, *names: str) -> str:
    for name in names:
        for child in el:
            if _ln(child.tag) == name:
                text = "".join(child.itertext()).strip()
                if text:
                    return text
    return ""


def _texts(el: ET.Element, names: tuple[str, ...]) -> list[str]:
    found = []
    for child in el:
        if _ln(child.tag) in names:
            text = _clean("".join(child.itertext()))
            if text:
                found.append(text)
    return found


def _item(
    el: ET.Element, atom: bool, base: str | None, feed_base: str | None, source: str
) -> Entry | None:
    link = ""
    if atom:
        chain = urljoin(urljoin(base or "", feed_base or ""), el.get(_XML_BASE, ""))
        for child in el:
            href = child.get("href")
            if _ln(child.tag) == "link" and child.get("rel") in (None, "alternate") and href:
                link = urljoin(urljoin(chain, child.get(_XML_BASE, "")), href)
                break
    else:
        raw = _raw(el, "link")
        link = urljoin(base or "", raw) if raw else ""
        if not _is_http(link):
            link = _raw(el, "guid")
    if not _is_http(link):
        return None
    try:
        canonical = normalize_url(link)
    except FeedError:
        return None
    published = None
    for text in _texts(el, ("published", "updated") if atom else ("pubDate", "date")):
        published = _parse_date(text)
        if published:
            break
    if atom:
        authors = [_raw(a, "name") for a in el if _ln(a.tag) == "author"]
        tags = [c.get("term", "") for c in el if _ln(c.tag) == "category"]
        summary = _raw(el, "summary", "content")
    else:
        authors = _texts(el, ("author", "creator"))
        tags = _texts(el, ("category", "subject"))
        summary = _raw(el, "description", "encoded")
    return Entry(
        id=hashlib.sha256(canonical.encode()).hexdigest()[:16],
        source=source,
        title=_clean(_raw(el, "title")),
        url=canonical,
        published=published,
        summary=_to_text(summary),
        authors=tuple(a for a in authors if a),
        tags=tuple(sorted({t for t in tags if t})),
    )


def parse_feed(
    data: bytes,
    *,
    source: str,
    base_url: str | None = None,
    max_bytes: int = 5_000_000,
    max_entries: int = 1000,
) -> list[Entry]:
    """Parse an RSS 2.0, RSS 1.0 or Atom document into entries.

    Raises:
        FeedError: ``too_large``, ``doctype_forbidden``, ``parse`` or ``unsupported_format``.
    """
    if len(data) > max_bytes:
        raise FeedError("too_large", "document exceeds max_bytes")
    _guard_xml(data)
    try:
        root = ET.fromstring(data)  # noqa: S314
    except ET.ParseError:
        raise FeedError("parse", "malformed XML") from None
    kind = _ln(root.tag)
    atom = kind == "feed"
    if atom:
        items = [e for e in root if _ln(e.tag) == "entry"]
    elif kind in ("rss", "RDF"):
        items = [e for e in root.iter() if _ln(e.tag) == "item"]
    else:
        raise FeedError("unsupported_format", "not an RSS or Atom document")
    feed_base = root.get(_XML_BASE)
    out: list[Entry] = []
    for el in items:
        if len(out) >= max_entries:
            break
        entry = _item(el, atom, base_url, feed_base, source)
        if entry is not None:
            out.append(entry)
    return out


def dedupe(entries: Iterable[Entry]) -> list[Entry]:
    """Merge entries sharing a canonical URL, keeping the earliest published one."""
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        groups.setdefault(entry.url, []).append(entry)
    out = []
    for group in groups.values():
        chosen = group[0]
        for cand in group[1:]:
            if cand.published is not None and (
                chosen.published is None or cand.published < chosen.published
            ):
                chosen = cand
        tags = tuple(sorted({t for e in group for t in e.tags}))
        out.append(replace(chosen, tags=tags))
    return out


def _sort_key(entry: Entry) -> tuple[int, float, str]:
    if entry.published is None:
        return (1, 0.0, entry.id)
    return (0, -entry.published.timestamp(), entry.id)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 5

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise FeedError("fetch", "redirect to a non-http scheme refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.UnknownHandler())
    opener.add_handler(_SafeRedirect())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.addheaders = []
    return opener


def _make_fetcher(
    timeout: float,
    max_bytes: int,
    user_agent: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> Callable[[str], bytes]:
    def fetch(url: str) -> bytes:
        if not _is_http(url):
            raise FeedError("fetch", "only http and https URLs are allowed")
        op = opener or _build_opener()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})  # noqa: S310
        try:
            with op.open(req, timeout=timeout) as resp:
                data: bytes = resp.read(max_bytes + 1)
        except FeedError:
            raise
        except Exception as exc:
            raise FeedError("fetch", f"request failed: {type(exc).__name__}") from exc
        if len(data) > max_bytes:
            raise FeedError("too_large", "response exceeds max_bytes")
        return data

    return fetch


def _loads(data: bytes) -> object:
    try:
        result: object = json.loads(data)
    except ValueError:
        raise FeedError("parse", "invalid JSON") from None
    return result


def _hn_entry(item: object, item_id: int, source: str) -> Entry | None:
    if not isinstance(item, dict) or item.get("type") != "story":
        return None
    if item.get("dead") or item.get("deleted"):
        return None
    hn_url = f"https://news.ycombinator.com/item?id={item_id}"
    raw_url = item.get("url")
    link = raw_url if isinstance(raw_url, str) and _is_http(raw_url) else hn_url
    try:
        canonical = normalize_url(link)
    except FeedError:
        canonical = hn_url
    published = None
    stamp = item.get("time")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool):
        try:
            published = datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            published = None
    title = item.get("title")
    by = item.get("by")
    text = item.get("text")
    return Entry(
        id=hashlib.sha256(canonical.encode()).hexdigest()[:16],
        source=source,
        title=_clean(title) if isinstance(title, str) else "",
        url=canonical,
        published=published,
        summary=_to_text(text) if isinstance(text, str) else "",
        authors=(by,) if isinstance(by, str) and by else (),
        tags=(),
    )


class Aggregator:
    """Collect, normalize, deduplicate and sort entries from several sources."""

    def __init__(
        self,
        sources: Sequence[Source],
        *,
        fetch: Callable[[str], bytes] | None = None,
        timeout: float = 10.0,
        max_bytes: int = 5_000_000,
        user_agent: str = "feed-aggregator",
        hn_limit: int = 30,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._sources = tuple(sources)
        self._fetch = fetch or _make_fetcher(timeout, max_bytes, user_agent)
        self._max_bytes = max_bytes
        self._hn_limit = hn_limit
        self._on_event = on_event

    def _hn(self, src: Source, get: Callable[[str], bytes]) -> list[Entry]:
        ids = _loads(get(_HN_BASE + "topstories.json"))
        if not isinstance(ids, list):
            raise FeedError("parse", "unexpected topstories payload")
        out = []
        for item_id in ids[: max(self._hn_limit, 0)]:
            if not isinstance(item_id, int) or isinstance(item_id, bool):
                continue
            entry = _hn_entry(_loads(get(f"{_HN_BASE}item/{item_id}.json")), item_id, src.name)
            if entry is not None:
                out.append(entry)
        return out

    def _load(self, src: Source) -> tuple[list[Entry], SourceError | None]:
        size = 0

        def get(url: str) -> bytes:
            nonlocal size
            data = self._fetch(url)
            size += len(data)
            return data

        try:
            if src.kind == "feed":
                got = parse_feed(
                    get(src.url), source=src.name, base_url=src.url, max_bytes=self._max_bytes
                )
            else:
                got = self._hn(src, get)
        except Exception as exc:
            code = exc.code if isinstance(exc, FeedError) else "fetch"
            message = (str(exc) or type(exc).__name__)[:200]
            _log.warning("source %s failed with %s", src.name, code)
            self._emit(src.name, code, 0, size)
            return [], SourceError(src.name, code, message)
        self._emit(src.name, "ok", len(got), size)
        return got, None

    def _emit(self, source: str, outcome: str, entries: int, size: int) -> None:
        if self._on_event is not None:
            self._on_event(
                {"source": source, "outcome": outcome, "entries": entries, "bytes": size}
            )

    def collect(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> AggregateResult:
        """Fetch every source; failures are reported in ``errors`` and never abort the run."""
        entries: list[Entry] = []
        errors: list[SourceError] = []
        for src in self._sources:
            got, err = self._load(src)
            entries.extend(got)
            if err is not None:
                errors.append(err)
        if since is not None:
            floor = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            entries = [e for e in entries if e.published is not None and e.published >= floor]
        merged = sorted(dedupe(entries), key=_sort_key)[:_MAX_RESULT]
        if limit is not None:
            merged = merged[: max(limit, 0)]
        return AggregateResult(entries=merged, errors=errors)
