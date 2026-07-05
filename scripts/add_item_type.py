import csv, glob, re, os
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
RECON = r"C:\Users\Jogesh Behera\Code file\Inventory\bill_po_reconciliation.csv"
NOPO = r"C:\Users\Jogesh Behera\Code file\Inventory\bills_no_matching_po.csv"


def norm(t):
    return re.sub(r'[^0-9A-Za-z]', '', t).upper()


# ---- Item master: Item ID -> category ----
item_cat = {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            iid = row["Item ID"].strip()
            if not iid:
                continue
            t = row.get("Item Type", "").strip()
            s = row.get("Status", "").strip()
            if t == "Inventory":
                cat = "Inventory (active)" if s == "Active" else "Inventory (inactive)"
            elif t == "Sales and Purchases":
                cat = "Sales & Purchase"
            elif t == "Sales":
                cat = "Sales"
            elif t == "Purchases":
                cat = "Purchase"
            else:
                cat = t or "Unknown"
            item_cat[iid] = cat


def summarize(product_ids):
    c = Counter()
    for pid in product_ids:
        c[item_cat.get(pid, "Unknown")] += 1
    if not c:
        return "No item link"
    order = ["Inventory (active)", "Sales & Purchase", "Inventory (inactive)",
             "Sales", "Purchase", "Unknown"]
    parts = []
    for k in order:
        if c.get(k):
            parts.append(f"{k} x{c[k]}")
    for k in c:  # any unexpected types
        if k not in order:
            parts.append(f"{k} x{c[k]}")
    return "; ".join(parts)


# ---- PO file: normalized MD ref -> list of Product IDs ----
ref_pids = defaultdict(list)
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = norm(row["CF.PO Number"].strip())
            if ref.startswith("MD"):
                pid = row["Product ID"].strip()
                if pid:
                    ref_pids[ref].append(pid)

# ---- Bill file: Bill Number -> list of Product IDs (Sep 2025+) ----
bill_pids = defaultdict(list)
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["Bill Date"] < "2025-09-01":
                continue
            pid = row["Product ID"].strip()
            if pid:
                bill_pids[row["Bill Number"]].append(pid)


# ---- Update bill_po_reconciliation.csv (item types from PO) ----
rows = list(csv.DictReader(open(RECON, encoding="utf-8-sig")))
for r in rows:
    pids = []
    for ref in (t.strip() for t in r["MD Reference(s)"].split(";")):
        if ref:
            pids.extend(ref_pids.get(ref, []))
    r["Item Type(s) (from PO)"] = summarize(pids)
fields = list(rows[0].keys())
with open(RECON, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(rows)
print("bill_po_reconciliation.csv updated:", len(rows), "rows")
c1 = Counter()
for r in rows:
    for p in r["Item Type(s) (from PO)"].split(";"):
        c1[p.strip().rsplit(" x", 1)[0]] += 1
print("  item-type occurrences:", dict(c1))

# ---- Update bills_no_matching_po.csv (item types from Bill) ----
rows2 = list(csv.DictReader(open(NOPO, encoding="utf-8-sig")))
linked = 0
for r in rows2:
    pids = bill_pids.get(r["Bill Number"], [])
    if pids:
        linked += 1
    r["Item Type(s) (from Bill)"] = summarize(pids)
fields2 = list(rows2[0].keys())
with open(NOPO, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields2)
    w.writeheader(); w.writerows(rows2)
print("bills_no_matching_po.csv updated:", len(rows2), "rows; with item link:", linked)
