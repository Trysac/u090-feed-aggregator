# Project record

## Objective

Aggregate RSS 2.0, RSS 1.0, Atom and Hacker News items into normalized entries with DTD-free XML parsing, canonical URLs and deduplication

## Scope

- In scope: the public API documented in [README.md](../README.md), typed and tested.
- Out of scope: anything that needs runtime dependencies or I/O without an accepted ADR.

## Timeline

| Date | Event |
|---|---|
| 2026 | Repository created from the factory utility template. |

## Effort metrics

Measured values only; a value that has not been measured stays "pending measurement".

| Metric | Value |
|---|---|
| Tokens (input / output) | 197595 / 292788 |
| Hours (human) | pending measurement |
| Cost (USD) | 1.5579 |

## Factory run u090 (measured from API usage)

| Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|
| generation | claude-sonnet-5-5 | yes | 2485 | 128000 | 0 | 4246 | 0.6429 |
| generation | claude-sonnet-5-5 | yes | 2485 | 128000 | 0 | 4246 | 0.6429 |
| generation | claude-sonnet-5-5 | yes | 2485 | 31157 | 0 | 4246 | 0.1587 |
| fix-1 | claude-sonnet-5-5 | no | 6 | 863 | 8514 | 42822 | 0.0342 |
| fix-2 | claude-sonnet-5-5 | no | 0 | 0 | 0 | 0 | 0.0000 |
| fix-3 | claude-sonnet-5-5 | no | 0 | 0 | 0 | 0 | 0.0000 |
| audit | claude-sonnet-5-5 | no | 0 | 0 | 0 | 0 | 0.0000 |
| docs | claude-haiku-5-5 | no | 134574 | 4768 | 0 | 0 | 0.0792 |

Total: $1.5579. Prices: Anthropic list prices.
