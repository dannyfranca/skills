# Price research

Do these steps for each model that `eval_stats.py` names in `prices_needed`. Use the model name exactly as it shows there.

1. Find the current USD price per 1M tokens for the model. Use web search. Use the provider pricing page as the source. When the provider page has no entry for the model, use a third-party price tracker.
2. Find the input, cached input, and output rates. When the source gives no cached input rate, use the input rate. Find the cache write rate only when the source gives one.
3. Write the price to the cache that `prices_needed.cache` names:

   ```bash
   python3 "$SKILL_DIR/scripts/prices.py" --cache <prices_needed.cache> set <model> --input <usd> --cached-input <usd> --output <usd> [--cache-write <usd>] --source <url>
   ```

   The command sets `fetched_at` to today. A model is stale 30 days after its `fetched_at`.

The research is complete when `prices.py set` exits 0 for each model, or when you have a list of the models that have no price on any source.
