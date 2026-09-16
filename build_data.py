#!/usr/bin/env python3
"""
BSP Deal Tracker — Submarket data pipeline
Runs monthly via GitHub Actions. Pulls free public sources, computes per-submarket
metrics, and writes submarkets.json (which the dashboard fetches on open).

Sources (no API keys required):
  - Zillow ZORI (ZIP-level rents)  -> rent, YoY, 3-mo annualized momentum
  - Zillow ZHVI (ZIP-level values) -> own-vs-rent premium
  - Census Building Permits Survey (metro-level) -> supply pipeline % of stock
Optional (drop-in): source_data/apartment_list_vacancy.csv -> vacancy + trend
"""
import csv, io, json, math, os, sys, urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = json.load(open(os.path.join(HERE, "submarkets_map.json")))
SUBS = MAP["submarkets"]
STOCK = MAP["metro_stock_5plus"]

MORTGAGE_RATE = float(os.environ.get("MORTGAGE_RATE", "6.3")) / 100.0
TAX_INS_PCT   = float(os.environ.get("TAX_INS_PCT", "1.6")) / 100.0   # annual, % of value
UA = {"User-Agent": "Mozilla/5.0 (BSP-deal-tracker data pipeline)"}

ZORI = "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv"
ZHVI = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"

def fetch(url, timeout=180):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def zip_series(csv_text, wanted):
    """Return {zip: [monthly values...]} for wanted ZIPs from a Zillow ZIP-level file."""
    out = {}
    rdr = csv.reader(io.StringIO(csv_text))
    head = next(rdr)
    zi = head.index("RegionName")
    for row in rdr:
        z = row[zi].zfill(5)
        if z not in wanted:
            continue
        vals = []
        for v in row[9:]:
            try: vals.append(float(v))
            except ValueError: vals.append(None)
        # keep trailing non-null run
        series = [v for v in vals if v is not None]
        if len(series) >= 13:
            out[z] = series
    return out

def avg_at(series_list, back):
    vals = [s[len(s)-1-back] for s in series_list if len(s) > back]
    if not vals: return None
    vals.sort()
    n = len(vals)
    return vals[n//2] if n % 2 else (vals[n//2-1]+vals[n//2])/2.0   # median — robust to sparse-ZIP outliers

def monthly_pi(value):
    """Monthly P&I on 80% LTV, 30yr, plus taxes+insurance."""
    r = MORTGAGE_RATE/12.0
    n = 360
    loan = value*0.8
    pi = loan * (r*(1+r)**n)/((1+r)**n - 1)
    return pi + value*TAX_INS_PCT/12.0

def census_permits():
    """Trailing-12-month 5+ unit permits per CBSA from Census cbsamonthly_YYYYMM.xls files (post-2024 format)."""
    import pandas as pd
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(16):
        m -= 1
        if m == 0: y -= 1; m = 12
        months.append((y, m))
    per_cbsa = {}
    got = 0
    known = {s.get("cbsa") for s in SUBS if s.get("cbsa")}
    for (y, m) in months:
        if got >= 12: break
        url = f"https://www.census.gov/construction/bps/xls/cbsamonthly_{y}{m:02d}.xls"
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            df = pd.read_excel(io.BytesIO(raw), header=None)
        except Exception as e:
            print(f"  permits {y}-{m:02d}: skip ({type(e).__name__})")
            continue
        # find header row containing "5 Units" and the Units column inside that group
        hdr_row = units_col = None
        for i in range(min(10, len(df))):
            for j, cell in enumerate(df.iloc[i]):
                if isinstance(cell, str) and "5 Unit" in cell:
                    hdr_row, g5 = i, j
                    # next header row labels Bldgs/Units/Value under the group
                    sub = df.iloc[i+1] if i+1 < len(df) else None
                    units_col = g5 + 1
                    if sub is not None:
                        for k in range(g5, min(g5+3, len(sub))):
                            if isinstance(sub[k], str) and "Unit" in sub[k]:
                                units_col = k; break
                    break
            if hdr_row is not None: break
        if hdr_row is None:
            print(f"  permits {y}-{m:02d}: no 5+ header found"); continue
        # CBSA code column: the column whose values best match our known CBSA codes
        best_col, best_hits = None, 0
        data = df.iloc[hdr_row+2:]
        for j in range(min(6, df.shape[1])):
            col = data.iloc[:, j].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
            hits = col.isin(known).sum()
            if hits > best_hits: best_col, best_hits = j, hits
        if best_col is None or best_hits < 10:
            print(f"  permits {y}-{m:02d}: CBSA column not found"); continue
        for _, row in data.iterrows():
            code = str(row.iloc[best_col]).replace(".0", "").zfill(5)
            if code not in known: continue
            try: units = int(float(row.iloc[units_col]))
            except (ValueError, TypeError): continue
            per_cbsa[code] = per_cbsa.get(code, 0) + units
        got += 1
    return per_cbsa, got

def apartment_list_vacancy():
    """Optional: metro-level vacancy + 12-mo trend from a dropped-in Apartment List export."""
    path = os.path.join(HERE, "source_data", "apartment_list_vacancy.csv")
    if not os.path.exists(path): return {}
    out = {}
    with open(path, newline="") as f:
        rdr = csv.reader(f)
        head = [h.lower() for h in next(rdr)]
        try:
            name_i = next(i for i,h in enumerate(head) if "name" in h or "location" in h)
        except StopIteration:
            return {}
        month_cols = list(range(len(head)))[-13:]
        for row in rdr:
            loc = row[name_i]
            try:
                cur = float(row[month_cols[-1]]); prior = float(row[month_cols[0]])
            except (ValueError, IndexError):
                continue
            out[loc.lower()] = {"vac": cur*100 if cur < 1 else cur,
                                "vacT": (cur-prior)*100 if cur < 1 else (cur-prior)}
    return out

def main():
    wanted = set()
    for s in SUBS: wanted.update(z.zfill(5) for z in s["zips"])
    print(f"{len(SUBS)} submarkets, {len(wanted)} ZIPs")

    print("Fetching Zillow ZORI ...");  rents = zip_series(fetch(ZORI), wanted)
    print(f"  matched {len(rents)} ZIPs")
    print("Fetching Zillow ZHVI ...");  values = zip_series(fetch(ZHVI), wanted)
    print(f"  matched {len(values)} ZIPs")
    print("Fetching Census permits ..."); permits, months_found = census_permits()
    print(f"  {months_found} monthly files, {len(permits)} CBSAs")
    al = apartment_list_vacancy()
    if al: print(f"  Apartment List vacancy: {len(al)} locations")

    out_rows, skipped = [], []
    for s in SUBS:
        zlist = [z.zfill(5) for z in s["zips"]]
        rser = [rents[z] for z in zlist if z in rents]
        row = {"name": s["name"]}
        if rser:
            cur, yr, m3 = avg_at(rser,0), avg_at(rser,12), avg_at(rser,3)
            if cur and yr and m3:
                row["rent"] = round(cur)
                row["yoy"]  = round(100*(cur/yr-1), 1)
                row["mom3"] = round(100*((cur/m3)**4-1), 1)
        vser = [values[z] for z in zlist if z in values]
        if vser and row.get("rent"):
            v = avg_at(vser, 0)
            if v:
                row["own"] = round(100*(monthly_pi(v)/row["rent"] - 1))
        cbsa = s.get("cbsa")
        stock = STOCK.get(s["metro"])
        if cbsa and cbsa in permits and stock:
            row["pipe"] = round(100.0*permits[cbsa]/stock, 1)
        if al:
            for loc, d in al.items():
                if s["metro"].lower() in loc:
                    row["vac"] = round(d["vac"],1); row["vacT"] = round(d["vacT"],1); break
        if len(row) > 1: out_rows.append(row)
        else: skipped.append(s["name"])

    payload = {"updated": date.today().isoformat(),
               "sources": {"zori_zips": len(rents), "zhvi_zips": len(values),
                            "permit_months": months_found, "mortgage_rate": MORTGAGE_RATE*100},
               "rows": out_rows}
    with open(os.path.join(HERE, "submarkets.json"), "w") as f:
        json.dump(payload, f, indent=1)
    print(f"Wrote submarkets.json: {len(out_rows)} rows" + (f", skipped {len(skipped)}: {skipped[:5]}" if skipped else ""))
    if len(out_rows) < len(SUBS)*0.5:
        sys.exit("FAILED: fewer than half of submarkets resolved — check source URLs")

if __name__ == "__main__":
    main()
