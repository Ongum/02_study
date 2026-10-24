# Churn-risk and game-health contract

Status: evaluation code implemented; real user-level churn dataset and model
are **not implemented** because the public Steam sources do not expose a
continuous per-user activity outcome.

## Two products, not one misleading score

1. **Public-data game health**: app/update/language/time cohorts using reviews,
   concurrent-player snapshots, issues and events. The output is an operational
   health-risk grade, not an individual churn probability.
2. **First-party user churn**: only possible after consented activity telemetry
   supplies a stable privacy-minimized user key, feature cutoff and future
   inactivity outcome. This product may output a user probability and grade.

Aggregate player decline, a negative review, or a review author's last-played
field is not by itself a verified user churn label.

## Proposed reusable entities

| Model | Grain | Key fields | Availability |
|---|---|---|---|
| `dim_game` | one game/service | `game_id`, platform, region | Lost Ark seed available |
| `dim_update_event` | one verified deployment event | event ID, type labels, effective time, evidence | candidate news only |
| `fact_game_event` | one operational/content event occurrence | event ID, observed/effective time | proposed |
| `fact_review_history` | one observed review version | app, review ID, updated time | implemented locally |
| `fact_player_snapshot` | one app observation time | app, observed time | implemented locally |
| `mart_game_health_snapshot` | game × segment × scoring time | signals, coverage, health risk | proposed |
| `fact_user_activity` | pseudonymous user × activity time | activity, game, session | unavailable publicly |
| `mart_user_churn_score` | user × feature cutoff × horizon | probability, grade, model version | requires first-party data |
| `mart_risk_grade_performance` | model/version × grade × outcome window | count, churn count/rate | evaluator implemented |

Full Steam author IDs must not enter analytics marts. A future first-party
identity key requires documented consent, access, retention and deletion rules.

## Label contract for true churn

Example only; the owner must select the business horizon before training:

- feature cutoff: all features use data at or before time `T`;
- observation window: enough prior activity to define an eligible active user;
- outcome: no qualifying activity during `(T, T + H]`;
- censoring: exclude users whose full outcome window is unavailable;
- reactivation: report separately instead of rewriting the historical label;
- split: use chronological train/validation/test windows, never random future
  leakage.

The horizon `H`, eligible population and qualifying activity are open business
decisions. Illustrative values must never be written as measured policy.

## Evaluation

- `AUC`: probability that a randomly selected churner receives a higher score
  than a randomly selected non-churner.
- `AR = 2 × AUC - 1`: ranking discrimination; it does not measure probability
  calibration or business value.
- `CP@20`: churners captured in the deterministic highest-risk 20% divided by
  all observed churners.
- grade outcome rate: observed churn count / eligible users in each risk grade.
- Brier score: probability calibration error; smaller is better.

`R1` is the highest-risk grade. Production reporting must show denominators,
outcome windows, confidence intervals, calibration, coverage and model version.
AR and CP@20 require actual matured labels; they must remain null/unreported for
the current public Steam dataset.

## Initial collection policy

Review Updated every 6 hours, current players hourly, and news daily remain the
initial configurable policy. Frequency performance will be judged after enough
local observations exist: missed intervals, freshness, API attempts, duplicate
replay ratio, cost estimate and downstream detection delay. It is not frozen
as a production SLA yet.
