"""Correct target: (bill_id,po_id) pairs the attach scripts SKIPPED as 'vendor mismatch'
(live vendor_id differ) — the true duplicate-vendor set. Filter to single-PO + inventory +
paid (via sheet), then classify same-GST (repointable) vs genuine. READ-ONLY.
-> vendorfix2_classify.csv + candidate file vendorfix2_candidates.csv (same-GST fixable)."""
import importlib.util, csv, glob, os, re
from collections import Counter

spec = importlib.util.spec_from_file_location("m", r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call = m.call
csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def invonly(itstr):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in itstr.split(";") if t.strip())
    return bool(toks) and toks <= INVSET


# sheet lookup by bill number (single-PO rows): status/paid/inventory
sheet = {}
for r in csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")):
    if r["Multi-PO Bill"] != "Yes":
        sheet[r["Bill Number"]] = r

# collect vendor-mismatch pairs from all logs (with ids)
pairs = {}
for f in glob.glob(os.path.join(INV, "attach_results*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        if "vendor mismatch" in (r.get("reason") or "").lower() and r.get("bill_id") and r.get("po_id"):
            pairs[(r["bill_id"], r["po_id"])] = (r.get("bill_number", ""), r.get("po_number", ""))
print("vendor-mismatch pairs (with ids) from logs:", len(pairs))

vgst = {}


def gst_status(vid):
    if vid not in vgst:
        try:
            _, vd = call("GET", f"/contacts/{vid}"); c = vd.get("contact", {})
            vgst[vid] = ((c.get("gst_no") or "").strip().upper(), c.get("status", "?"), c.get("contact_name", ""))
        except Exception:
            vgst[vid] = ("", "?", "")
    return vgst[vid]


cls = Counter(); rows = []; cand = []
for (bid, pid), (bn, pn) in pairs.items():
    srow = sheet.get(bn)
    scope = srow and srow["Bill Status"] == "Paid" and invonly(srow["Item Type(s) (from PO)"])
    try:
        _, bd = call("GET", f"/bills/{bid}"); b = bd.get("bill")
        _, pd = call("GET", f"/purchaseorders/{pid}"); p = pd.get("purchaseorder")
    except Exception:
        cls["fetch-fail"] += 1; continue
    if not b or not p:
        cls["fetch-fail"] += 1; continue
    if b.get("purchaseorder_ids"):
        cls["already linked"] += 1; continue
    bg, bstat, bname = gst_status(b.get("vendor_id"))
    pg, pstat, pname = gst_status(p.get("vendor_id"))
    same = bool(bg) and bg == pg
    k = ("same-GST" if same else ("blank-GST" if not (bg and pg) else "diff-GST"))
    k += " | " + ("in-scope" if scope else "out-of-scope")
    cls[k] += 1
    rows.append({"bill_number": bn, "po_number": pn, "same_gst": same, "bill_gstin": bg, "po_gstin": pg,
                 "bill_vendor": bname, "bill_vendor_status": bstat, "po_vendor": pname, "po_vendor_status": pstat,
                 "in_scope": bool(scope)})
    if same and scope:
        cand.append({"bill_id": bid, "po_id": pid, "bill_number": bn, "po_number": pn,
                     "bill_amount": b.get("total"), "bill_vendor_status": bstat})

with open(os.path.join(INV, "vendorfix2_classify.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(os.path.join(INV, "vendorfix2_candidates.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "bill_amount", "bill_vendor_status"])
    w.writeheader(); w.writerows(cand)
print("\nCLASSIFICATION:")
for k, v in cls.most_common():
    print(f"  {v:4}  {k}")
print("\nsame-GST + in-scope (single-PO inv paid) candidates:", len(cand))
print("  of which bill-vendor inactive (need activate):", sum(1 for c in cand if c["bill_vendor_status"] != "active"))
