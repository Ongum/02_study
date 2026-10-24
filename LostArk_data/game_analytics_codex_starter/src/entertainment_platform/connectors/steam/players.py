"""Contract for Steam's anonymous current-player response."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

CURRENT_PLAYERS_ENDPOINT = (
    "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
)


class SteamPlayerCountValidationError(ValueError):
    """Raised when a player-count response does not match the public contract."""


@dataclass(frozen=True, slots=True)
class PlayerCountSnapshot:
    appid: int
    observed_at: str
    player_count: int
    run_id: str
    source_endpoint: str
    response_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_player_count(
    payload: object,
    *,
    appid: int,
    observed_at: datetime,
    run_id: str,
) -> PlayerCountSnapshot:
    if not isinstance(payload, dict):
        raise SteamPlayerCountValidationError("response must be an object")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise SteamPlayerCountValidationError("missing response envelope")
    player_count = response.get("player_count")
    if (
        not isinstance(player_count, int)
        or isinstance(player_count, bool)
        or player_count < 0
    ):
        raise SteamPlayerCountValidationError("player_count must be unsigned integer")
    result = response.get("result")
    if result is not None and result != 1:
        raise SteamPlayerCountValidationError("Steam result did not indicate success")
    if appid <= 0:
        raise SteamPlayerCountValidationError("appid must be positive")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise SteamPlayerCountValidationError("observed_at must be timezone-aware")
    if not run_id.strip():
        raise SteamPlayerCountValidationError("run_id must not be empty")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PlayerCountSnapshot(
        appid=appid,
        observed_at=observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        player_count=player_count,
        run_id=run_id,
        source_endpoint=CURRENT_PLAYERS_ENDPOINT,
        response_sha256=digest,
    )
