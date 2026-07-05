"""OFFLINE. Classify the script-skipped vendor-mismatch pairs by GSTIN using the Vendors CSV.
Same-GST + in-scope (single-PO inv paid) -> vendorfix2_candidates.csv. No API calls."""
import csv, glob, os, re
from collections import Counter, defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def invonly(itstr):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in itstr.split(";") if t.strip())
    return bool(toks) and toks <= INVSET


# vendor name -> GSTIN (from Vendors CSV); flag names with >1 GSTIN as ambiguous
name_gst = defaultdict(set)
for r in csv.DictReader(open(os.path.join(INV, "Raw Files", "Vendors (1).csv"), encoding="utf-8-sig")):
    nm = r["Contact Name"].strip().upper()
    g = r["GST Identification Number (GSTIN)"].strip().upper()
    if g:
        name_gst[nm].add(g)
AMBIG = {n for n, gs in name_gst.items() if len(gs) > 1}


def gstin_of(name):
    nm = (name or "").strip().upper()
    if nm in AMBIG:
        return None  # ambiguous
    gs = name_gst.get(nm)
    return next(iter(gs)) if gs else ""


# script-skipped vendor-mismatch pairs (with ids)
pairs = {}
for f in glob.glob(os.path.join(INV, "attach_results*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        if "vendor mismatch" in (r.get("reason") or "").lower() and r.get("bill_id") and r.get("po_id"):
            pairs[(r["bill_id"], r["po_id"])] = (r.get("bill_number", ""), r.get("po_number", ""))

sheet = {r["Bill Number"]: r for r in csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig"))
         if r["Multi-PO Bill"] != "Yes"}

cls = Counter(); cand = []
for (bid, pid), (bn, pn) in pairs.items():
    srow = sheet.get(bn)
    if not srow:
        cls["not in single-PO sheet"] += 1
        continue
    bg = gstin_of(srow["Vendor"]); pg = gstin_of(srow["PO Vendor"])
    scope = srow["Bill Status"] == "Paid" and invonly(srow["Item Type(s) (from PO)"])
    if bg is None or pg is None:
        tag = "ambiguous GSTIN"
    elif bg and bg == pg:
        tag = "same-GST"
    elif not (bg and pg):
        tag = "blank GSTIN"
    else:
        tag = "different GST (genuine)"
    cls[tag + (" | in-scope" if scope else " | out-scope")] += 1
    if tag == "same-GST" and scope:
        cand.append({"bill_id": bid, "po_id": pid, "bill_number": bn, "po_number": pn,
                     "bill_vendor": srow["Vendor"], "po_vendor": srow["PO Vendor"], "gstin": bg,
                     "bill_amount": srow["Bill Amount"]})

with open(os.path.join(INV, "vendorfix2_candidates.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "bill_vendor", "po_vendor", "gstin", "bill_amount"])
    w.writeheader(); w.writerows(cand)
print("script-skipped vendor-mismatch pairs:", len(pairs))
print("CLASSIFICATION:")
for k, v in cls.most_common():
    print(f"  {v:4}  {k}")
print("\nsame-GST + in-scope candidates:", len(cand), "-> vendorfix2_candidates.csv")
print("sample:", [(c["bill_number"], c["bill_vendor"], "->", c["po_vendor"]) for c in cand[:5]])
