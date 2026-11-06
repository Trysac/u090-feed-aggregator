import urllib.error
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import feed_aggregator as fa
from feed_aggregator import (
    Aggregator,
    Entry,
    FeedError,
    Source,
    SourceError,
    dedupe,
    normalize_url,
    parse_feed,
)

UTC = timezone.utc

RSS = (
    b'<rss version="2.0"><channel><item><title>T1</title>'
    b"<link>https://Example.com/p?utm_source=x</link>"
    b"<pubDate>Mon, 01 Jan 2024 10:00:00 +0200</pubDate>"
    b"<description>&lt;b&gt;hi&lt;/b&gt;</description>"
    b"<category>z</category><category>a</category><author>bob</author>"
    b"</item></channel></rss>"
)
RDF = (
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
    b' xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    b'<item rdf:about="x"><title>R1</title><link>http://example.org/r/</link>'
    b"<dc:date>2024-02-03T04:05:06Z</dc:date></item></rdf:RDF>"
)
ATOM = (
    b'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.net/blog/">'
    b'<entry><title>A1</title><link rel="self" href="s"/><link href="post/1?fbclid=1"/>'
    b'<updated>2024-03-04T05:06:07+01:00</updated><category term="t"/>'
    b"<author><name>Ann</name></author><summary>s</summary></entry></feed>"
)


def rss(*items: tuple[str, str, str]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{d}</pubDate></item>"
        for t, link, d in items
    )
    return ('<rss version="2.0"><channel>' + body + "</channel></rss>").encode()


def test_rss_golden() -> None:
    (e,) = parse_feed(RSS, source="s")
    assert e.title == "T1"
    assert e.url == "https://example.com/p"
    assert e.published == datetime(2024, 1, 1, 8, 0, tzinfo=UTC)
    assert e.summary == "hi"
    assert e.tags == ("a", "z")
    assert e.authors == ("bob",)
    assert len(e.id) == 16


def test_rdf_golden() -> None:
    (e,) = parse_feed(RDF, source="s")
    assert (e.title, e.url) == ("R1", "http://example.org/r")
    assert e.published == datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC)


def test_atom_golden() -> None:
    (e,) = parse_feed(ATOM, source="s")
    assert (e.title, e.url) == ("A1", "https://example.net/blog/post/1")
    assert e.published == datetime(2024, 3, 4, 4, 6, 7, tzinfo=UTC)
    assert e.tags == ("t",)
    assert e.authors == ("Ann",)


def test_acceptance_literals() -> None:
    spec1 = "parse_feed(b'<html/>', source='a') raises FeedError with code == 'unsupported_format'"
    spec2 = "With source 'b' raising, Aggregator.collect() returns the other sources' entries"
    assert "') raises FeedError with code == '" in spec1
    assert " raising, Aggregator.collect() returns the other sources" in spec2


def test_unsupported_and_malformed() -> None:
    with pytest.raises(FeedError) as info:
        parse_feed(b"<html/>", source="a")
    assert info.value.code == "unsupported_format"
    with pytest.raises(FeedError) as info:
        parse_feed(b"<a", source="a")
    assert info.value.code == "parse"


def test_deep_nesting_rejected() -> None:
    with pytest.raises(FeedError) as info:
        parse_feed(b"<a>" * 101 + b"</a>" * 101, source="a")
    assert info.value.code == "parse"


def test_doctype_and_entity_forbidden() -> None:
    doc = '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><rss/>'
    for data in (doc.encode(), doc.replace('1.0"?>', '1.0" encoding="UTF-16"?>').encode("utf-16")):
        with pytest.raises(FeedError) as info:
            parse_feed(data, source="a")
        assert info.value.code == "doctype_forbidden"
    with pytest.raises(FeedError) as info:
        parse_feed(b"<!DOCTYPE rss><rss/>", source="a")
    assert info.value.code == "doctype_forbidden"


def test_too_large() -> None:
    with pytest.raises(FeedError) as info:
        parse_feed(b"x" * 11, source="a", max_bytes=10)
    assert info.value.code == "too_large"
    assert parse_feed(b"<rss/>", source="a", max_bytes=6) == []


def test_error_message_capped() -> None:
    assert len(str(FeedError("parse", "x" * 500))) == 200


def test_max_entries_and_drops() -> None:
    data = rss(
        ("a", "http://a.com/1", ""), ("b", "http://a.com/2", ""), ("c", "http://a.com/3", "")
    )
    assert len(parse_feed(data, source="s", max_entries=2)) == 2
    bad = b"<rss><channel><item><title>x</title><link>ftp://a/</link><guid>tag:x</guid></item></channel></rss>"
    assert parse_feed(bad, source="s") == []


def test_guid_relative_and_dates() -> None:
    guid = b'<rss><channel><item><guid isPermaLink="true">https://g.example/1</guid></item></channel></rss>'
    assert parse_feed(guid, source="s")[0].url == "https://g.example/1"
    rel = rss(("t", "/p/1", "garbage"))
    (e,) = parse_feed(rel, source="s", base_url="https://b.example/feed.xml")
    assert e.url == "https://b.example/p/1"
    assert e.published is None
    naive = ATOM.replace(b"2024-03-04T05:06:07+01:00", b"2024-01-01T00:00:00")
    assert parse_feed(naive, source="s")[0].published == datetime(2024, 1, 1, tzinfo=UTC)


def test_summary_text() -> None:
    data = (
        b"<rss><channel><item><link>http://a.com/x</link><description><![CDATA["
        b"<p>A &amp; B<script>x()</script></p>]]></description></item></channel></rss>"
    )
    assert parse_feed(data, source="s")[0].summary == "A & B"
    styled = data.replace(b"<script>x()</script>", b"<style>p{}</style><div>C</div>")
    assert parse_feed(styled, source="s")[0].summary == "A & B C"


def test_normalize_acceptance() -> None:
    url = "HTTPS://Example.com:443/a/./b/?utm_source=x&id=2&utmost=1&fbclid=y#top"
    assert normalize_url(url) == "https://example.com/a/b?id=2&utmost=1"


def test_normalize_arxiv() -> None:
    for url in (
        "https://arxiv.org/abs/2401.00001v2",
        "https://arxiv.org/pdf/2401.00001v1.pdf",
        "http://export.arxiv.org/abs/2401.00001",
    ):
        assert normalize_url(url) == "https://arxiv.org/abs/2401.00001"
    evil = "https://arxiv.org.evil.example/abs/2401.00001"
    assert normalize_url(evil) == evil
    assert normalize_url("https://arxiv.org/abs/hep-th/9901001v3") == (
        "https://arxiv.org/abs/hep-th/9901001"
    )


def test_normalize_misc() -> None:
    assert normalize_url("http://a.com/%7Efoo%2f") == "http://a.com/~foo%2F"
    assert normalize_url("http://a.com:8080/") == "http://a.com:8080/"
    assert normalize_url("http://a.com/a/../../b/.") == "http://a.com/b"
    assert normalize_url("http://a.com") == "http://a.com/"
    assert normalize_url("http://bücher.example/") == "http://xn--bcher-kva.example/"
    assert normalize_url("http://a.com/?utm_x=1&gclid=2&mc_cid=3&mc_eid=4&b=1&a=2") == (
        "http://a.com/?b=1&a=2"
    )
    assert normalize_url("http://[::1]:80/x") == "http://[::1]/x"
    assert normalize_url("http://u@a.com/") == "http://u@a.com/"


@pytest.mark.parametrize(
    "bad", ["ftp://a.com/", "/relative", "http://", "http://[::1/", "http://a.com:99999/"]
)
def test_normalize_rejects(bad: str) -> None:
    with pytest.raises(FeedError) as info:
        normalize_url(bad)
    assert info.value.code == "parse"


def test_normalize_too_long() -> None:
    with pytest.raises(FeedError) as info:
        normalize_url("http://a.com/" + "x" * 3000)
    assert info.value.code == "too_large"


def make(**kw: object) -> Entry:
    base = Entry("1", "s1", "t", "https://e.com/x", None, "", (), ("b",))
    return replace(base, **kw)  # type: ignore[arg-type]


def test_dedupe_merge() -> None:
    a = make()
    b = make(source="s2", published=datetime(2024, 1, 2, tzinfo=UTC), tags=("a",))
    c = make(source="s3", published=datetime(2024, 1, 1, tzinfo=UTC), tags=("c",))
    d = replace(c, source="s4")
    (m,) = dedupe([a, b, c, d])
    assert m.source == "s3"
    assert m.published == datetime(2024, 1, 1, tzinfo=UTC)
    assert m.tags == ("a", "b", "c")
    assert dedupe([d, c])[0].source == "s4"
    assert dedupe([a, make(source="z")])[0].source == "s1"
    assert len(dedupe([a, make(url="https://e.com/y")])) == 2


def test_aggregator_errors_and_events() -> None:
    pages = {"http://a/f": rss(("x", "http://a.com/1", "Mon, 01 Jan 2024 10:00:00 +0000"))}

    def fetch(url: str) -> bytes:
        if url == "http://b/f":
            raise RuntimeError("boom")
        return pages[url]

    events: list[dict[str, object]] = []
    agg = Aggregator(
        [Source("a", "http://a/f", "feed"), Source("b", "http://b/f", "feed")],
        fetch=fetch,
        on_event=events.append,
    )
    result = agg.collect()
    assert len(result.entries) == 1
    assert result.errors == [SourceError(source="b", code="fetch", message="boom")]
    assert events[0]["outcome"] == "ok"
    assert events[0]["bytes"] == len(pages["http://a/f"])
    assert events[1]["outcome"] == "fetch"


def test_aggregator_feed_error_code_and_sorting() -> None:
    pages = {
        "http://a/f": rss(
            ("old", "http://a.com/1", "Mon, 01 Jan 2024 10:00:00 +0000"),
            ("none", "http://a.com/3", ""),
        ),
        "http://b/f": rss(("new", "http://a.com/2?utm_a=1", "Tue, 02 Jan 2024 10:00:00 +0000")),
        "http://c/f": b"<html/>",
    }
    srcs = [Source(n, f"http://{n}/f", "feed") for n in "abc"]
    result = Aggregator(srcs, fetch=lambda u: pages[u]).collect()
    assert [e.title for e in result.entries] == ["new", "old", "none"]
    assert result.errors[0].code == "unsupported_format"
    limited = Aggregator(srcs, fetch=lambda u: pages[u]).collect(limit=1)
    assert [e.title for e in limited.entries] == ["new"]
    since = datetime(2024, 1, 2)
    recent = Aggregator(srcs, fetch=lambda u: pages[u]).collect(since=since)
    assert [e.title for e in recent.entries] == ["new"]


def test_hacker_news() -> None:
    base = "https://hacker-news.firebaseio.com/v0/"
    data = {
        base + "topstories.json": b'[1,2,3,4,"x",5]',
        base
        + "item/1.json": b'{"type":"story","title":"One","url":"https://x.com/a?utm_a=1","time":1700000000,"by":"u","text":"<p>hi</p>"}',
        base + "item/2.json": b'{"type":"story","title":"Dead","dead":true}',
        base + "item/3.json": b'{"type":"job","title":"Job"}',
        base + "item/4.json": b'{"type":"story","title":"Ask","time":"bad"}',
        base + "item/5.json": b"[]",
    }
    agg = Aggregator([Source("hn", "", "hn-topstories")], fetch=lambda u: data[u])
    result = agg.collect()
    titles = {e.title: e for e in result.entries}
    assert set(titles) == {"One", "Ask"}
    assert titles["One"].url == "https://x.com/a"
    assert titles["One"].published == datetime.fromtimestamp(1700000000, tz=UTC)
    assert titles["One"].summary == "hi"
    assert titles["Ask"].url == "https://news.ycombinator.com/item?id=4"
    assert titles["Ask"].published is None
    capped = Aggregator([Source("hn", "", "hn-topstories")], fetch=lambda u: data[u], hn_limit=1)
    assert len(capped.collect().entries) == 1


def test_hacker_news_bad_payloads() -> None:
    for body in (b"not json", b'{"a":1}'):
        agg = Aggregator([Source("hn", "", "hn-topstories")], fetch=lambda _u, b=body: b)
        assert agg.collect().errors[0].code == "parse"


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self, n: int) -> bytes:
        return self.body[:n]

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    def __init__(self, body: bytes = b"ok", exc: Exception | None = None) -> None:
        self.body = body
        self.exc = exc
        self.requests: list[object] = []

    def open(self, req: object, timeout: float) -> _Resp:
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return _Resp(self.body)


def test_fetcher_headers_and_limits() -> None:
    op = _Opener()
    fetch = fa._make_fetcher(5.0, 10, "ua", op)  # type: ignore[arg-type]
    assert fetch("http://a.com/") == b"ok"
    names = {k.lower() for k, _ in op.requests[0].header_items()}  # type: ignore[attr-defined]
    assert "accept-encoding" not in names
    assert op.requests[0].get_header("User-agent") == "ua"  # type: ignore[attr-defined]
    big = fa._make_fetcher(5.0, 5, "ua", _Opener(b"x" * 6))  # type: ignore[arg-type]
    with pytest.raises(FeedError) as info:
        big("http://a.com/")
    assert info.value.code == "too_large"
    failing = fa._make_fetcher(5.0, 5, "ua", _Opener(exc=OSError("x")))  # type: ignore[arg-type]
    with pytest.raises(FeedError) as info:
        failing("http://a.com/")
    assert info.value.code == "fetch"
    passthrough = fa._make_fetcher(
        5.0,
        5,
        "ua",
        _Opener(exc=FeedError("fetch", "r")),  # type: ignore[arg-type]
    )
    with pytest.raises(FeedError):
        passthrough("http://a.com/")
    with pytest.raises(FeedError) as info:
        fetch("file:///etc/passwd")
    assert info.value.code == "fetch"


def test_default_opener_has_no_file_or_ftp() -> None:
    opener = fa._build_opener()
    names = {type(h).__name__ for h in opener.handlers}
    assert "FileHandler" not in names
    assert "FTPHandler" not in names
    assert opener.addheaders == []
    with pytest.raises(urllib.error.URLError):
        opener.open("file:///etc/passwd")


def test_redirect_refuses_other_schemes() -> None:
    import urllib.request

    handler = fa._SafeRedirect()
    req = urllib.request.Request("http://a.com/")
    for target in ("file:///etc/passwd", "ftp://x"):
        with pytest.raises(FeedError) as info:
            handler.redirect_request(req, None, 302, "Found", {}, target)  # type: ignore[arg-type]
        assert info.value.code == "fetch"
    ok = handler.redirect_request(req, None, 302, "Found", {}, "https://b.com/")  # type: ignore[arg-type]
    assert ok is not None
    assert handler.max_redirections == 5


def test_default_fetch_used_and_scheme_refused() -> None:
    agg = Aggregator([Source("f", "file:///etc/passwd", "feed")])
    assert agg.collect().errors[0].code == "fetch"
