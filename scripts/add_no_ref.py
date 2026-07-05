import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
SHEET = r"C:\Users\Jogesh Behera\Code file\Inventory\bills_no_matching_po.csv"
mdpat = re.compile(r'^MD', re.I)


def q(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


# ---- Sep 2025+ bills with NO MD reference, grouped by Bill ID ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["Bill Date"] < "2025-09-01":
                continue
            raw = row["PurchaseOrder"].strip()
            if raw and mdpat.match(raw):   # has an MD ref -> skip (already handled)
                continue
            bid = row["Bill ID"]
            b = bills.get(bid)
            if b is None:
                b = bills[bid] = {
                    "bill_num": row["Bill Number"], "date": row["Bill Date"],
                    "status": row["Bill Status"], "vendor": row["Vendor Name"],
                    "raw_ref": raw, "qtys": [],
                }
            qv = q(row["Quantity"])
            if qv is not None:
                b["qtys"].append(qv)

print("Sep 2025+ bills with no MD reference:", len(bills))

# ---- load existing sheet, append blank-ref rows ----
rows = list(csv.DictReader(open(SHEET, encoding="utf-8-sig")))
fields = list(rows[0].keys())
for b in bills.values():
    bqty = sorted(b["qtys"])
    rows.append({
        "Bill Number": b["bill_num"],
        "Bill Date": b["date"],
        "MD Reference(s)": "",                # blank, per request
        "Bill Item Count": len(bqty),
        "Bill Quantities": "; ".join(f"{x:g}" for x in bqty),
        "Multi-Ref Bill": "",
        "Bill Status": b["status"],
        "Vendor": b["vendor"],
        "PO DB Status": "",
        "Is Deleted": "",
        "Zoho PO Created": "",
    })

rows.sort(key=lambda r: (r["MD Reference(s)"] == "", r["Bill Date"]))
with open(SHEET, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print("total rows in sheet now:", len(rows))
print("  with MD ref (no matching PO):", sum(1 for r in rows if r["MD Reference(s)"]))
print("  blank MD ref:", sum(1 for r in rows if not r["MD Reference(s)"]))
