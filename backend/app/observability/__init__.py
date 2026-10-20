"""Turn-level observability: what happened, what it cost, and was it any good.

- `turns` folds one turn's events and reply into a single object;
- `checks` grades that object against its own trace (read-time, never stored);
- `service` assembles one mission's payload for the API;
- `overview` aggregates every conversation for the settings page.

Nothing here writes. The only durable facts remain the events and messages the
orchestration layer already records.
"""
