"""Candidates for vendor-mismatch fix: single-PO, inventory-only, paid, reason 'vendor mismatch'.
-> vendorfix_candidates.csv (bill_id, po_id, md_ref, bill_number, po_number, bill_amount)."""
import csv, glob, re, os
csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def invonly(r):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    return bool(toks) and toks <= INVSET


poid = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        r = norm(row["CF.PO Number"])
        if r.startswith("MD"):
            poid.setdefault(r, row["Purchase Order ID"])
billid = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        for t in row["PurchaseOrder"].split(","):
            n = norm(t)
            if n.startswith("MD"):
                billid.setdefault((row["Bill Number"], n), row["Bill ID"])

rows = [r for r in csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig"))
        if r["Attach Status"] == "Not Attached" and r["Reason"] == "vendor mismatch"
        and r["Multi-PO Bill"] != "Yes" and r["Bill Status"] == "Paid" and invonly(r)]
out, miss = [], 0
for r in rows:
    ref = r["MD Reference(s)"].strip()
    bid = billid.get((r["Bill Number"], norm(ref))); pid = poid.get(norm(ref))
    if not bid or not pid:
        miss += 1; continue
    out.append({"bill_id": bid, "po_id": pid, "md_ref": ref, "bill_number": r["Bill Number"],
                "po_number": r["PO Numbers"], "bill_amount": r["Bill Amount"],
                "sheet_vendor": r["Vendor"], "sheet_po_vendor": r["PO Vendor"]})
with open(os.path.join(INV, "vendorfix_candidates.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number", "bill_amount", "sheet_vendor", "sheet_po_vendor"])
    w.writeheader(); w.writerows(out)
print("vendor-mismatch single-PO inventory+paid candidates:", len(out), "| missing id:", miss)
