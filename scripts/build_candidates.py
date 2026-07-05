"""Build single-line MATCHED attach candidates with exact Zoho IDs."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
OUT = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_candidates.csv"
mdpat = re.compile(r'^MD', re.I)
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


# PO export: ref -> {po_ids, qtys, po_number, vendor}
po = defaultdict(lambda: {"po_ids": set(), "qtys": [], "po_number": "", "vendor": ""})
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if not ref.startswith("MD"):
            continue
        m = po[ref]
        m["po_ids"].add(row["Purchase Order ID"])
        m["po_number"] = row["Purchase Order Number"]
        m["vendor"] = row["Vendor Name"]
        q = num(row["QuantityOrdered"])
        if q is not None:
            m["qtys"].append(q)

# Bills (Sep 2025+, MD ref) by Bill ID
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
            continue
        bid = row["Bill ID"]
        b = bills.get(bid)
        if b is None:
            b = bills[bid] = {"bill_number": row["Bill Number"], "vendor": row["Vendor Name"],
                              "refs": [n for n in (norm(t) for t in raw.split(",")) if n.startswith("MD")],
                              "qtys": []}
        q = num(row["Quantity"])
        if q is not None:
            b["qtys"].append(q)

rows = []
for bid, b in bills.items():
    if len(b["refs"]) != 1 or len(b["qtys"]) != 1:
        continue                              # single ref + single line bill
    ref = b["refs"][0]
    m = po.get(ref)
    if not m or len(m["po_ids"]) != 1 or len(m["qtys"]) != 1:
        continue                              # single PO + single line PO
    if sorted(b["qtys"]) != sorted(m["qtys"]):
        continue                              # quantity must match (MATCHED)
    rows.append({"bill_id": bid, "po_id": next(iter(m["po_ids"])), "md_ref": ref,
                 "bill_number": b["bill_number"], "po_number": m["po_number"],
                 "bill_vendor": b["vendor"], "po_vendor": m["vendor"],
                 "qty": b["qtys"][0]})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number",
                                       "po_number", "bill_vendor", "po_vendor", "qty"])
    w.writeheader(); w.writerows(rows)
print("single-line matched candidates:", len(rows), "->", OUT)
