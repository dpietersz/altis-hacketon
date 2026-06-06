# Altis hackathon submission

Weather-aware 13-week cash-flow forecast + role-based dashboards for a
private-equity-backed roofing portfolio.

## Repo layout

```
altis-hacketon/
├── backend/                   # Databricks notebooks + plans (the data pipeline)
│   ├── databricks/notebooks/  # 03 silver → 12 Tier 2 charts
│   ├── PLAN.md                # Tier 1 plan + canonical schema
│   └── PLAN_TIER2.md          # Tier 2 plan (workable days, weather)
├── frontend/                  # Lovable-generated React app (Vite + Tailwind + shadcn)
├── CHALLENGE.md               # The hackathon brief (input)
├── README.md                  # This file
├── WORKFLOW_EXPLAINED.html    # Plain-English walkthrough — start here
├── SETUP_AZURE.html           # How to provision the Azure backend
├── BUILD_LOVABLE.html         # How to clone, install and run the frontend
├── FRONTEND_BRIEF.md          # Data contract + labelling rules for the UI
└── HANDOFF_LOVABLE.md         # SQL connection + paste-ready queries per chart
```

## Where to start, depending on who you are

| Reader | Start here |
|---|---|
| Judge wanting the story | [`WORKFLOW_EXPLAINED.html`](WORKFLOW_EXPLAINED.html) |
| Engineer wanting to rebuild the backend | [`SETUP_AZURE.html`](SETUP_AZURE.html) |
| Engineer wanting to run the frontend | [`BUILD_LOVABLE.html`](BUILD_LOVABLE.html) |
| Frontend dev connecting to data | [`HANDOFF_LOVABLE.md`](HANDOFF_LOVABLE.md) |
| Anyone curious about the data model | [`backend/PLAN.md`](backend/PLAN.md) and [`backend/PLAN_TIER2.md`](backend/PLAN_TIER2.md) |

## What's covered

- **Tier 1** — CFO 13-week cash forecast across 3 portcos, 3 scenarios,
  driver decomposition, covenant headroom, traceability to source bookings.
- **Tier 2** — weather → workable-days model translating real KNMI rainfall,
  temperature and wind data into a per-month productive-day count, joined
  with monthly revenue for the headline bar+line chart.

## Reproducing the pipeline

1. Provision Azure resources per [`SETUP_AZURE.html`](SETUP_AZURE.html)
2. Import the notebooks from `backend/databricks/notebooks/` into the
   Databricks workspace
3. Run notebooks 03 → 05 → 06 → 07 → 11 → 08 in order
4. Open the dashboard via [`BUILD_LOVABLE.html`](BUILD_LOVABLE.html)

## Data retention

Per the challenge brief, all Altis-provided data and any derived gold
tables must be destroyed within 3 days after the event from every storage
location.
