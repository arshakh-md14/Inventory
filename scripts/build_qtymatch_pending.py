"""Qty-mismatch / Both-match cases still PENDING (not yet attached), clean, any line count."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
mdpat = re.compile(r'^MD', re.I)
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


def load(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig"))) if os.path.exists(f) else []


# already attached (any log, OK) by (bill_number, po_number)
done = set()
for f in ["attach_results.csv", "attach_results_multiline.csv", "attach_results_qtymatch.csv"]:
    for r in load(os.path.join(INV, f)):
        if r["result"] == "OK":
            done.add((r["bill_number"], r["po_number"]))

po = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            d = po.setdefault(ref, {"po_id": row["Purchase Order ID"], "recd": 0.0})
            d["recd"] += num(row["QuantityReceived"])
bmap = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
            continue
        for t in raw.split(","):
            n = norm(t)
            if n.startswith("MD"):
                bmap.setdefault((row["Bill Number"], n), row["Bill ID"])

recon = load(os.path.join(INV, "bill_po_reconciliation.csv"))
rows, cls = [], defaultdict(int)
for r in recon:
    if r["Status"] != "Qty mismatch" or not r["Amount Verdict (Qty mismatch)"].startswith("Both match"):
        continue
    if r["Multi-PO Bill"] == "Yes":
        continue
    if (r["Bill Number"], r["PO Numbers"]) in done:
        continue
    if "Draft" in r["PO Status(es)"] or r["Vendor Match"] == "No":
        continue
    ref = r["MD Reference(s)"].strip()
    p = po.get(ref)
    if not p or p["recd"] <= 0:
        continue
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    if toks <= {"Inventory (active)", "Inventory (inactive)"}:
        item_class = "inventory"
    elif toks <= {"Sales & Purchase"}:
        item_class = "sales_purchase"
    else:
        continue
    bid = bmap.get((r["Bill Number"], ref))
    if not bid:
        continue
    n_lines = int(r["Bill Item Count"])
    cls[("multi" if n_lines > 1 else "single", item_class)] += 1
    rows.append({"bill_id": bid, "po_id": p["po_id"], "md_ref": ref, "bill_number": r["Bill Number"],
                 "po_number": r["PO Numbers"], "bill_vendor": r["Vendor"], "po_vendor": r["PO Vendor"],
                 "n_lines": n_lines, "item_class": item_class})

single = [r for r in rows if r["n_lines"] == 1]
multi = [r for r in rows if r["n_lines"] > 1]
for name, subset in [("qtymatch_pending_single.csv", single), ("qtymatch_pending_multi.csv", multi)]:
    with open(os.path.join(INV, name), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number", "bill_vendor", "po_vendor", "n_lines", "item_class"])
        w.writeheader(); w.writerows(subset)
print("pending qty-value-match clean:", len(rows))
print("  single-line:", len(single), "| multi-line:", len(multi))
print("  by (line,class):", dict(cls))
