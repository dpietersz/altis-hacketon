# Altis Groep Challenge: Weather-Aware Cash Flow Forecast & Role-Based Dashboards

## Mission

Build a working prototype that gives the Altis CFO, operating company MDs, and project leads a **weather-aware 13-week cash flow forecast** and/or **role-based dashboards** — all built on a single reconciled data foundation drawn from four accounting systems.

> Not a generic BI template. A decision-support tool built for the realities of a roofing portfolio under private equity governance.

---

## What We Receive

| Item | What It Is |
|------|------------|
| Sample data exports (.csv/.xlsx) | Anonymised exports from each accounting system (Gilde, Yuki, Exact, Snelstart) |
| Chart of accounts mapping (.xlsx/.csv) | GL account mappings across the four systems |
| Project & WIP data (.csv/.xlsx) | Project-level WIP, milestones, and billing status |
| Weather data (.csv/API) | Historical and forecast weather data relevant to roofing |
| Covenant terms (.docx/.pdf) | Covenant headroom thresholds and calculation rules |
| Business context document (.docx/.pdf) | How Altis operates, role descriptions, decision processes |

**Useful API directories:**
- https://github.com/whizkydee/Awesome-APIs
- https://github.com/public-apis/public-apis

### Data Usage Terms

All Altis-provided datasets are anonymised and licensed **for this hackathon only**. Within **3 days after the hackathon**, all copies must be permanently deleted from every storage location (local drives, cloud, repos, notebooks, VMs). Failure to comply may result in liability under applicable privacy regulations.

---

## The Four Roles

The system surfaces different views depending on who is looking. Each must be specific to roofing and private equity — not a generic dashboard with a different logo.

| Role | What They Need to See |
|------|----------------------|
| **PE Board** | Covenant headroom before a board meeting; consolidated portfolio view |
| **CFO** | 13-week cash flow forecast by driver; scenario toggles; cross-opco comparison |
| **Opco MD** | WIP exposure for their operating company; project-level risk signals |
| **Project Lead** | Next invoiceable milestone; materials outflows ahead of execution; schedule risk from weather |

---

## The Cash Flow Driver Model

Don't lump all cash movements together. Model separate streams:

| Driver | What It Represents |
|--------|-------------------|
| **Materials outflows** | Payments for roofing materials, ordered ahead of execution |
| **Subcontractor payments** | Payments tied to project progress |
| **Milestone billing** | Invoiceable milestones per project, tied to completion stage |
| **Customer payment behaviour** | Payment lag from invoice to receipt, per customer segment |
| **Weather impact** | Rain/frost → schedule delay → deferred billing and shifted outflows |

> Each driver must be **independently tunable**. A judge should click any forecast figure and see which drivers produced it.

---

## The Output

- **Format:** Interactive dashboard / prototype — technology is up to us
- **Data foundation:** Reconciled from four accounting systems into one schema
- **Forecast horizon:** 13 weeks
- **Scenarios:** Base / wet-quarter / dry-quarter — toggle cleanly, affect the right downstream numbers
- **Traceability:** Every forecast number traces back to its drivers; every assumption traces back to the toggle that produced it
- Not a mockup — a functional system that processes real or realistic data and produces defensible outputs

---

## Challenge Tiers

> Pick your ambition level. **Depth beats breadth** — a serious answer to one role outperforms a surface pass at all four.

### Tier 1 — CFO Dashboard (baseline)

Single-role 13-week cash flow forecast for the CFO, drawing from at least one accounting system.

**Required:**
- Data ingestion from at least one accounting system
- Basic driver separation (materials, subcontractors, billing as distinct categories)
- 13-week forecast view with week-by-week projections
- Covenant headroom indicator — flagged when approaching threshold
- Scenario toggle — at minimum a base scenario; ability to adjust key assumptions
- Traceability — any figure traced back to source data and assumptions

**Bonus:**
- Weather data integrated (even a simple multiplier)
- A second accounting system reconciled into the same schema
- Payment lag modelling as a separate driver

### Tier 2 — Multi-Role Dashboard

Two or more roles (CFO + opco MD minimum), three or more accounting systems, meaningful weather-to-schedule translation.

**Required:**
- Everything from Tier 1 for the CFO
- Opco MD view: WIP exposure, project-level risk signals, subcontractor and materials commitments
- Multi-system reconciliation (3+ systems into one schema)
- Weather-to-schedule translation: not a flat multiplier — a model that translates weather delays into shifted billing and outflows
- Three scenarios: Base / wet-quarter / dry-quarter

**Bonus:**
- LLM-assisted GL account mapping (suggestions reviewable by a controller)
- Project lead view with next invoiceable milestone and schedule risk

> **Key challenge:** Reconcile data from systems with different structures into one schema, while each role sees something specific and meaningful.

### Tier 3 — Full Forecast Platform

All four roles, complete driver model, LLM-assisted GL mapping, architectural resilience.

**Required:**
- Everything from Tier 2, expanded to all four roles
- Full driver model: all five drivers as separate, independently tunable streams
- Driver model survives edge cases (new GL account, late correction journal, slipping project)
- LLM-assisted GL mapping: required — suggestions reviewable and auditable by a controller
- New opco onboarding: a configuration change, not a rebuild

**Platform-level:**
- Logical architecture separating: data ingestion → reconciliation → driver modelling → scenario generation → role-based presentation
- One schema, one source of truth, feeding all roles
- Full auditability: click any figure → see drivers → see assumption → see toggle → trace to source data
- Documentation: solution understandable without the team that built it; a controller can adjust the system

> **Key challenge:** Data consistency and forecast credibility across four accounting systems, five drivers, weather uncertainty, and four role-based views — all auditable and resilient.

---

## What Is Up to Us

The judges do **not** judge on:

- Programming languages, frameworks, or BI tools
- Web app, desktop app, notebook, or something else
- How data ingestion and reconciliation are implemented
- Which weather data source or API is used
- Whether and how LLMs are used for GL account mapping
- How the scenario engine is built internally

They judge on **results**: defensible numbers, traceable assumptions, credible decisions.

---

## How Submissions Are Judged (100 pts)

| Category | Points |
|----------|--------|
| Challenge-Specific Criteria | 60 |
| Overall Execution Quality | 30 |
| Innovation Bonus | 10 |

### Challenge-Specific Criteria (60 pts)

| Criterion | Pts | What Matters |
|-----------|-----|--------------|
| **Impact & Relevance** | 24 | Does it solve a real decision the finance team makes weekly? Would the CFO open this on Monday instead of a spreadsheet? Does it go beyond obvious requirements to unspoken operational realities? |
| **Technical Depth** | 19 | Does the prototype work end-to-end? Does the architecture survive edge cases (new GL account, late correction, slipping project)? Do multiple accounting systems reconcile into one schema? |
| **Auditability** | 17 | Can a controller trace any forecast number back to its drivers? Can any assumption be traced to the toggle that produced it? Is every number defensible to the board? |

### Overall Execution Quality (30 pts)

| Criterion | Pts | What Matters |
|-----------|-----|--------------|
| User Experience | 8 | Intuitive interface; graceful error handling; first-time user can accomplish the core task without docs |
| Documentation | 6 | Clear README, setup guide, architecture overview |
| Polish & Attention to Detail | 5 | Visual consistency, edge cases handled, no obvious bugs |
| Setup & Onboarding | 4 | Easy to run; dependencies clear; first-run experience works |
| Reproducibility & Code Quality | 4 | Someone else can run it, understand it, modify it |
| Deployment Readiness | 3 | Could Altis actually deploy with minimal additional work? |

### Innovation Bonus (10 pts)

- **0–2:** Standard hackathon execution; nothing unexpected
- **3–5:** One genuinely fresh idea that adds value
- **6–8:** Multiple creative contributions; "that's clever"
- **9–10:** Breakthrough thinking; "why hasn't anyone done this before?"

> Innovation must add value, not just novelty.

### Alternative Execution-Quality Rubric (from brief)

| # | Criterion | Weight |
|---|-----------|--------|
| 01 | Impact & Relevance | 40% |
| 02 | Technical Depth | 32% |
| 03 | Auditability | 28% |

---

## Deliverables

1. Data ingestion from at least one accounting system and reconciliation into a unified schema
2. 13-week cash flow forecast view with week-by-week projections, separated by driver streams
3. Covenant headroom indicator — flagged when approaching threshold
4. Scenario toggle (base / wet-quarter / dry-quarter) that affects the right downstream numbers
5. Traceability: any forecast figure can be traced back to its drivers and source data
6. README with run instructions and architecture overview

---

## Tools & Resources

- Weather data API (KNMI, Open-Meteo, or other Dutch weather source)
- Database or data layer capable of handling the unified schema from four accounting systems
- Charting and dashboard library (D3, Chart.js, Recharts, etc.)
- Forecast modelling approach (statistical, ML-based, or scenario-driven)
- LLM for GL account mapping (optional — suggestions reviewable by a controller)

---

## Quick-Reference Checklist

- [ ] Data from at least one accounting system ingested and reconciled
- [ ] Cash flow drivers modelled as separate streams (not lumped together)
- [ ] 13-week forecast view with week-by-week projections
- [ ] Covenant headroom flagged when approaching threshold
- [ ] Scenario toggle works (at minimum: base scenario)
- [ ] Any forecast figure can be traced back to its drivers and source data
- [ ] Role-based views are specific to roofing/PE — not generic BI templates
- [ ] Weather integration affects timing meaningfully (not just a flat multiplier)
- [ ] Architecture could absorb a new GL account or late correction without breaking
- [ ] VAT and Dutch GAAP treatments handled correctly (WIP, periodisering)
- [ ] Same source of truth feeds both forecast and dashboard (no two numbers disagreeing)
- [ ] Demo shows a controller-level walkthrough of the audit trail
- [ ] Deployment path is realistic on Altis's existing stack (no re-platforming required)
- [ ] README with run instructions included

---

## FAQ

**Do we have to build both forecasting and dashboards?**
Depth beats breadth. An excellent submission can win by going deep on one area, provided the data foundation makes the other plausible. A surface-level pass at both will lose to a serious answer to one.

**Can we use LLMs or AI?**
Encouraged, especially for GL account mapping. But with auditability in mind — "an LLM mapped this account" is fine; "we trust it blindly" is not. Suggestions must be reviewable and auditable by a controller.

**What about Dutch GAAP and VAT?**
Dutch GAAP, VAT, and weather data realities matter. Project-based revenue, WIP, materials accruals, and subcontractor cost are central to roofing companies. A SaaS-style P&L solution will miss the point.

**How does weather tie into cash flow?**
Weather delays roofing projects → delays milestone invoicing → delays collections. The forecast must ingest weather data and show how wet vs dry scenarios change the cash picture. Not a flat multiplier — a model that translates weather delays into shifted billing and outflows.

**Data retention?**
No. All Altis-provided data must be permanently deleted within 3 days after the hackathon, from every storage location.
