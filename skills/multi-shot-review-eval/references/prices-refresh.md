# Price table refresh

`prices.toml` is the only skill file that this skill changes without a user request.

1. Find the current USD price per 1M tokens for each model in `[models]` and for each model in `stats.json` `prices.missing_models`. Use web search. Use the provider pricing page as the source. When the provider page has no entry for the model, use a third-party price tracker and record it as a third-party source.
2. Write `input`, `cached_input`, and `output` for each model. Write `cache_write` only when the provider gives a cache write price. Set `source` to a key in `[sources]`.
3. Write the page URL and today's date in the `[sources]` entry that each model uses.
4. Set `updated` and `fetched_at` to today.
5. Check the file: `python3 -c 'import sys; sys.path.insert(0, "<skill_dir>/scripts"); from pathlib import Path; from prices import load_price_table; load_price_table(Path("<skill_dir>/references/prices.toml"))'`.

The refresh is complete when the check passes and every model has a price with a source dated today.
