# Deal Tracker — Submarket Data Pipeline

Auto-refreshes the Submarket Analysis tab. GitHub Actions runs `build_data.py`
on the 16th of every month, pulls the sources below, and commits a fresh
`submarkets.json`. The dashboard fetches that file every time you open the tab.

**What updates automatically:** rent, YoY growth, 3-mo momentum (Zillow ZORI),
own-vs-rent premium (Zillow ZHVI), supply pipeline % (Census permits).
**Optional drop-in:** download Apartment List's vacancy CSV into
`source_data/apartment_list_vacancy.csv` whenever you want vacancy refreshed —
the script picks it up on the next run.

## One-time setup (~15 min)

1. Create a **public** GitHub repo (e.g. `afelikian-design/deal-tracker-data`)
   — public is what makes the raw JSON fetchable by the dashboard, and none of
   this data is sensitive.
2. Push this folder's contents to it (including the hidden `.github` folder).
3. Repo → Actions tab → enable workflows → open "Refresh submarket data" →
   **Run workflow**. First run takes ~2–3 min (the Zillow ZIP files are big).
4. When it finishes, your feed URL is:
   `https://raw.githubusercontent.com/<user>/<repo>/main/submarkets.json`
5. In the dashboard's Submarket Analysis tab, click **⚙ Feed**, paste that URL.
   Done — it now self-updates monthly, forever, for free.

## Tunables
- `MORTGAGE_RATE` (default 6.3) and `TAX_INS_PCT` (default 1.6) — env vars in
  the workflow if you want to adjust the own-vs-rent math.
- `submarkets_map.json` — add/edit submarkets (name, ZIPs, CBSA) and metro
  stock estimates. Add a row here + a matching row in the dashboard's sheet or
  baked data and it's tracked.

## Later upgrades
- BLS metro job growth (free API key) — replaces the static jobs column
- Paid feed (ApartmentIQ / Yardi Matrix) — concessions, true vacancy, loan
  maturities; would slot into `build_data.py` the same way
