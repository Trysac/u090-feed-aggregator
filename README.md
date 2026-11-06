# feed-aggregator

[![PyPI version](https://img.shields.io/pypi/v/trysac-feed-aggregator)](https://pypi.org/project/trysac-feed-aggregator/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-feed-aggregator)](LICENSE)
[![CI](https://github.com/Trysac/u090-feed-aggregator/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u090-feed-aggregator/actions/workflows/ci.yml)

Aggregate RSS 2.0, RSS 1.0, Atom and Hacker News items into normalized entries with DTD-free XML parsing, canonical URLs and deduplication

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-feed-aggregator
# or
uv add trysac-feed-aggregator
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import feed_aggregator

# normalize() trims surrounding whitespace and lower-cases the text.
print(feed_aggregator.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    feed_aggregator.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
