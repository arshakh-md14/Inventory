import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
OUT = r"C:\Users\Jogesh Behera\Code file\Inventory\bills_no_matching_po.csv"
mdpat = re.compile(r'^MD', re.I)


def q(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def norm(token):
    return re.sub(r'[^0-9A-Za-z]', '', token).upper()


def parse_refs(field):
    return [n for n in (norm(t) for t in field.split(',')) if n.startswith('MD')]


# ---- All PO MD refs (normalized) ----
po_refs = set()
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            r = norm(row["CF.PO Number"].strip())
            if r.startswith("MD"):
                po_refs.add(r)

# ---- Bills: Sep 2025+ with MD ref, grouped by Bill ID ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = row["PurchaseOrder"].strip()
            if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
                continue
            bid = row["Bill ID"]
            b = bills.get(bid)
            if b is None:
                b = bills[bid] = {
                    "bill_num": row["Bill Number"], "date": row["Bill Date"],
                    "status": row["Bill Status"], "vendor": row["Vendor Name"],
                    "refs": parse_refs(raw), "qtys": [],
                }
            qv = q(row["Quantity"])
            if qv is not None:
                b["qtys"].append(qv)

# ---- Keep bills where NONE of the MD refs exist in any PO ----
rows_out = []
for b in bills.values():
    if any(r in po_refs for r in b["refs"]):
        continue
    bqty = sorted(b["qtys"])
    rows_out.append({
        "Bill Number": b["bill_num"],
        "Bill Date": b["date"],
        "MD Reference(s)": "; ".join(b["refs"]),
        "Bill Item Count": len(bqty),
        "Bill Quantities": "; ".join(f"{x:g}" for x in bqty),
        "Multi-Ref Bill": "Yes" if len(b["refs"]) > 1 else "",
        "Bill Status": b["status"],
        "Vendor": b["vendor"],
    })

rows_out.sort(key=lambda r: r["Bill Date"])
fields = ["Bill Number", "Bill Date", "MD Reference(s)", "Bill Item Count",
          "Bill Quantities", "Multi-Ref Bill", "Bill Status", "Vendor"]
with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(rows_out)

print("Wrote", len(rows_out), "bills with no matching PO to", OUT)
