# uncharted

Pulls running activities from the Garmin Connect Personal API, matches GPS tracks
to the OpenStreetMap street network, and renders a map of which streets you have run.

<p align="center">
  <img src="docs/demo.png" alt="Demo covered and uncovered streets in inner Oslo" width="600">
</p>

## Setup

```sh
uv sync
cp .env.example .env          # add GARMIN_PERSONAL_API_KEY
uv run uncharted auth-check
```

## Usage

```sh
uv run uncharted sync [--since YYYY-MM-DD] [--full]   # download runs (ZIP → FIT in data/raw)
uv run uncharted streets [--refresh]                  # OSM network + bydeler (cached)
uv run uncharted match [--rebuild]                    # tracks → covered edges
uv run uncharted stats                                # table + data/output/stats.json
uv run uncharted render                               # data/output/coverage_map.html
uv run uncharted all                                  # all of the above
```

All personal data lives in `data/` (gitignored). Personal config overrides go in
`config.local.toml` (also gitignored); it is merged over `config.toml`.
