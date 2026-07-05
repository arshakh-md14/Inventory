"""Build MULTI-LINE matched candidates: Status=Matched, >1 line, single PO,
items all-Inventory OR all-Sales&Purchase (not mixed, no Purchase/Sales/Unknown)."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
OUT = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_candidates_multiline.csv"
mdpat = re.compile(r'^MD', re.I)
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


# Item master: id -> type
item_type = {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        iid = row["Item ID"].strip()
        if iid:
            item_type[iid] = row.get("Item Type", "").strip()

# PO: ref -> {po_ids, qtys, po_number, vendor, product_ids}
po = defaultdict(lambda: {"po_ids": set(), "qtys": [], "po_number": "", "vendor": "", "pids": []})
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
        if row["Product ID"].strip():
            m["pids"].append(row["Product ID"].strip())

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

rows, cls_count = [], defaultdict(int)
for bid, b in bills.items():
    if len(b["qtys"]) <= 1 or len(b["refs"]) != 1:        # MULTI-line, single ref
        continue
    ref = b["refs"][0]
    m = po.get(ref)
    if not m or len(m["po_ids"]) != 1:                    # single PO
        continue
    if sorted(b["qtys"]) != sorted(m["qtys"]):            # matched (qty multiset)
        continue
    types = {item_type.get(p) for p in m["pids"]}
    if types <= {"Inventory"}:
        cls = "inventory"
    elif types <= {"Sales and Purchases"}:
        cls = "sales_purchase"
    else:
        continue                                          # mixed / other -> excluded
    cls_count[cls] += 1
    rows.append({"bill_id": bid, "po_id": next(iter(m["po_ids"])), "md_ref": ref,
                 "bill_number": b["bill_number"], "po_number": m["po_number"],
                 "bill_vendor": b["vendor"], "po_vendor": m["vendor"],
                 "n_lines": len(b["qtys"]), "item_class": cls})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                       "bill_vendor", "po_vendor", "n_lines", "item_class"])
    w.writeheader(); w.writerows(rows)
print("multi-line single-PO matched candidates:", len(rows), "->", OUT)
print("  by class:", dict(cls_count))
