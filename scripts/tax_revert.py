"""
Revert the 8 TAX-ALIGNED cases -> pre-match state, mark MANUAL.
  - restore bill original account line (rate + discount), unlink PO
  - restore PO original rate + ORIGINAL TAX + adjustment (we had changed its tax)
Targets attach_results.csv rows whose reason contains 'tax aligned'.
"""
import json, http.client, urllib.parse, csv, glob, re, os, time

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
REVERTED = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_reverted.csv"
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
DRY_RUN = False
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def f(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_tok = None


def token():
    global _tok
    if _tok:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _tok = json.loads(c.getresponse().read())["access_token"]
    return _tok


def call(m, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"
        c.request(m, f"/books/v3{path}?organization_id={ORG}", json.dumps(body), h)
    else:
        c.request(m, f"/books/v3{path}?organization_id={ORG}", headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read().decode())


rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
targets = [r for r in rows if "tax aligned" in r["reason"].lower()]
tbids = {r["bill_id"] for r in targets}

bill_orig = {}
for fn in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        if row["Bill ID"] in tbids and row["Bill ID"] not in bill_orig:
            bill_orig[row["Bill ID"]] = {"rate": f(row["Rate"]), "disc": f(row["Discount Amount"]),
                                         "qty": f(row["Quantity"]), "desc": row["Description"], "total": f(row["Total"]),
                                         "before_tax": (row.get("Is Discount Before Tax", "true").lower() != "false")}
po_orig = {}
for fn in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        pn = row["Purchase Order Number"]
        if pn and pn not in po_orig:
            po_orig[pn] = {"rate": f(row["Item Price"]), "adj": f(row["Adjustment"]), "tax_id": (row.get("Tax ID") or "").strip()}

reverted_ids = set()
log = []
for r in targets:
    bid, pid, pn = r["bill_id"], r["po_id"], r["po_number"]
    bo = bill_orig.get(bid)
    po_o = po_orig.get(pn, {})
    _, bd = call("GET", f"/bills/{bid}")
    bill = bd["bill"]; bl = bill["line_items"][0]
    line = {"account_id": bl.get("account_id"), "name": bo["desc"] or bl.get("name"), "description": bo["desc"],
            "rate": bo["rate"], "quantity": bo["qty"], "tax_id": bl.get("tax_id"), "item_order": 1}
    if bo["disc"] > 0:
        line["discount"] = bo["disc"]
    PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms", "adjustment",
                "adjustment_description", "exchange_rate", "is_inclusive_tax", "is_item_level_tax_calc",
                "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bill_revert = {"bill_number": bill["bill_number"], "line_items": [line],
                   "discount_type": "item_level", "is_discount_before_tax": bo["before_tax"]}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bill_revert[k] = bill[k]
    bill_revert["reference_number"] = (bill.get("reference_number") or "").replace(pn, "").strip(", ")

    if DRY_RUN:
        print(f"{r['bill_number']}/{pn}: bill->rate {bo['rate']} disc {bo['disc']} (exp tot {bo['total']}); "
              f"PO->rate {po_o.get('rate')} tax {po_o.get('tax_id')} adj {po_o.get('adj')}; unlink+MANUAL")
        continue

    call("PUT", f"/bills/{bid}", bill_revert)
    _, bd2 = call("GET", f"/bills/{bid}"); b2 = bd2["bill"]
    _, pdd = call("GET", f"/purchaseorders/{pid}"); po = pdd["purchaseorder"]; pl = po["line_items"][0]
    po_restore = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                                  "rate": po_o.get("rate", float(pl["rate"])), "quantity": float(pl["quantity"]),
                                  "tax_id": po_o.get("tax_id") or pl.get("tax_id")}],
                  "adjustment": po_o.get("adj", 0.0), "adjustment_description": "Round Off"}
    call("PUT", f"/purchaseorders/{pid}", po_restore)
    _, pd2 = call("GET", f"/purchaseorders/{pid}"); p2 = pd2["purchaseorder"]
    ok = abs(float(b2["total"]) - bo["total"]) < 0.1 and not b2.get("purchaseorder_ids") and p2.get("billed_status") != "billed"
    print(f"{r['bill_number']}/{pn}: bill {b2['total']} (orig {bo['total']}) linked={bool(b2.get('purchaseorder_ids'))} "
          f"PO billed={p2.get('billed_status')} tax%={p2['line_items'][0].get('tax_percentage')} -> {'OK' if ok else 'CHECK'}")
    if ok:
        reverted_ids.add(bid)
        log.append({**r, "result": "REVERTED", "reason": "tax mismatch -> MANUAL (pre-match restored)"})
    time.sleep(0.5)

if not DRY_RUN and reverted_ids:
    newlog = not os.path.exists(REVERTED)
    with open(REVERTED, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if newlog:
            w.writeheader()
        w.writerows(log)
    remaining = [x for x in rows if x["bill_id"] not in reverted_ids]
    with open(RESULTS, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(remaining)
    print(f"\nreverted {len(reverted_ids)} tax cases -> manual; removed from results")
else:
    print(f"\n[DRY] {len(targets)} tax-aligned cases")
