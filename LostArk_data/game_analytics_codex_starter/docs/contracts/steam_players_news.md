# Steam players and news local contracts

Status: implemented locally and smoke-tested against the official anonymous
endpoints on 2026-10-21.

## Current players

`ISteamUserStats/GetNumberOfCurrentPlayers/v1/` produces one append-only row:
`appid`, `observed_at`, `player_count`, `run_id`, endpoint and response hash.
The run ID is idempotent: an identical run ID is returned from storage without
another request. The value is concurrent connected Steam players at one instant,
not DAU, retention or a historical series. Official contract:
<https://partner.steamgames.com/doc/webapi/ISteamUserStats>.

## App news

`ISteamNews/GetNewsForApp/v2/` uses bounded older-than pagination and a fixed
date range. Raw content is local-only. Normalized rows omit author and article
body, retaining ID, title, URL, feed and publication time. Title rules create
exploratory candidate types. `effective_at` is always null until separately
verified; publication is not assumed to be deployment. Official contract:
<https://partner.steamgames.com/doc/webapi/ISteamNews>.
