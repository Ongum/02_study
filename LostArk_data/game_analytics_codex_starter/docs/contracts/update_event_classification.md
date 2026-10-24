# Update-event classification contract

Status: local deterministic prototype implemented. Labels and prior scores are
rule-based, not human-validated model outputs or measured business impact.

One row represents one Steam news/event candidate. Raw public article excerpts
are classifier input only and are not copied to the derived event dimension.

## Output grain and fields

- grain: `(appid, event_id)`;
- timestamps: `published_at`, nullable independently verified `effective_at`;
- labels: `primary_type`, multi-label `secondary_types`;
- expected monitoring priors: scope, technical risk, monetization risk;
- evidence: public URL, method version, confidence, `human_verified`;
- explicit interpretation: expected prior is not observed post-update impact.

V1 labels cover service transition, class/content, raid/dungeon content,
progression/economy, balance/combat, monetization/store, new/returning UX,
technical operations, live events and community communications.

## Required next validation

Create a human-reviewed label set before reporting classifier accuracy. Verify
the actual deployment/effective time independently. Version every rule/model
change, retain prior outputs for reproducibility, and keep observed reaction
scores in a separate mart.
