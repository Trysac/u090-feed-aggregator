# Hardened stdlib-only feed parsing with a fetch port

Status: accepted (generated, pending human review)

## Context

Monitoring pipelines ingest feeds in several formats with tracking parameters and duplicates. Naive XML parsing is exposed to entity attacks, and feedparser adds a dependency and leaves dedupe to the caller.

## Decision

Use only the standard library. A first expat pass rejects DOCTYPE and ENTITY declarations and limits nesting depth before ElementTree parses the document. Canonical URLs follow RFC 3986 section 6 plus documented heuristics. Dedupe is deterministic. Fetching goes through an injectable port whose default opener has only HTTP and HTTPS handlers and refuses redirects to other schemes.

## Consequences

There are no runtime dependencies and the library is DTD-free, so feeds that use DTDs are rejected. Trailing-slash removal is a heuristic beyond the RFC. Private-network targets are not defended because sources are trusted configuration. Summaries are reduced to plain text but entries remain untrusted. Title-based dedupe, caching and scheduling are out of scope.
