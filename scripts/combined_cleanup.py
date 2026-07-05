"""
Cleanup the OK cases to canonical form:
  - BILL line = PO DB details (rate = DB rate, discount 0), adjustment = paid - PO_line_total (Round Off);
    bill total stays = paid.
  - PO restored to DB (rate + adjustment from export).
Item temporarily reactivated to edit lines referencing an inactive item.
Skips cases already canonical (bill line rate == DB rate AND PO at DB total).
"""
import json, http.client, urllib.parse, csv, glob, re, os, time

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
DRY_RUN = False
LIMIT = 200
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def f(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None


def token():
    global _tok
    if _tok: return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}"); _tok = json.loads(c.getresponse().read())["access_token"]; return _tok


def call(m, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in"); h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"; c.request(m, f"/books/v3{path}?organization_id={ORG}", json.dumps(body), h)
    else: c.request(m, f"/books/v3{path}?organization_id={ORG}", headers=h)
    r = c.getresponse(); return r.status, json.loads(r.read().decode())


po_orig = {}
for fn in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        pn = row["Purchase Order Number"]
        if pn and pn not in po_orig:
            po_orig[pn] = {"rate": f(row["Item Price"]), "adj": f(row["Adjustment"]), "total": f(row["Total"])}

ok = [r for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")) if r["result"] == "OK"]
done = 0
for r in ok:
    if done >= LIMIT:
        break
    bid, pid, pn = r["bill_id"], r["po_id"], r["po_number"]
    o = po_orig.get(pn)
    if not o:
        print(f"  {r['bill_number']}/{pn}: no export PO -> skip"); continue
    _, bd = call("GET", f"/bills/{bid}"); bill = bd["bill"]; bl = bill["line_items"][0]
    _, pd = call("GET", f"/purchaseorders/{pid}"); po = pd["purchaseorder"]; pl = po["line_items"][0]
    paid = float(bill["total"]); item_id = pl["item_id"]
    po_line_total = round(o["total"] - o["adj"], 2)
    round_off = round(paid - po_line_total, 2)
    bill_canon = abs(float(bl["rate"]) - o["rate"]) < 0.01 and float(bl.get("discount") or 0) == 0
    po_at_db = abs(float(po["total"]) - o["total"]) < 0.005 and f(po.get("adjustment")) == o["adj"]
    if bill_canon and po_at_db:
        print(f"  {r['bill_number']}/{pn}: already canonical; skip"); continue
    done += 1
    if DRY_RUN:
        print(f"{r['bill_number']}/{pn}: bill rate {bl['rate']}->{o['rate']} round_off {round_off} (paid {paid}); PO ->DB {o['total']} adj {o['adj']}")
        continue

    # receive link
    receive = []
    for rec in po.get("purchasereceives", []):
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            if rli.get("item_id") == item_id:
                receive.append({"receive_item_id": rli["line_item_id"], "quantity": float(pl["quantity"])}); break
        if receive: break

    new_line = {"purchaseorder_item_id": pl["line_item_id"], "item_id": item_id,
                "account_id": pl.get("account_id") or bl.get("account_id"), "name": pl.get("name"),
                "description": "", "rate": o["rate"], "quantity": float(pl["quantity"]), "discount": 0,
                "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"),
                "tax_id": pl.get("tax_id") or bl.get("tax_id"), "item_order": 1, "is_billable": False}
    if receive:
        new_line["receive_line_items"] = receive
    ref = bill.get("reference_number") or ""
    if pn not in ref: ref = (ref + "," + pn).strip(",")
    PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms", "exchange_rate",
                "is_inclusive_tax", "is_item_level_tax_calc", "payment_terms", "payment_terms_label",
                "gst_treatment", "gst_no", "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line],
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""): bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]

    _, itd = call("GET", f"/items/{item_id}"); st_item = itd.get("item", {}).get("status")
    if st_item != "active": call("POST", f"/items/{item_id}/active")
    sb, rb = call("PUT", f"/bills/{bid}", bu)
    po_rev = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": item_id, "rate": o["rate"],
                             "quantity": float(pl["quantity"]), "tax_id": pl.get("tax_id")}],
              "adjustment": o["adj"], "adjustment_description": po.get("adjustment_description") or "Round Off"}
    sp, rp = call("PUT", f"/purchaseorders/{pid}", po_rev)
    if st_item != "active": call("POST", f"/items/{item_id}/inactive")

    _, bd2 = call("GET", f"/bills/{bid}"); b2 = bd2["bill"]; bl2 = b2["line_items"][0]
    _, pd2 = call("GET", f"/purchaseorders/{pid}"); p2 = pd2["purchaseorder"]
    ok2 = (abs(float(b2["total"]) - paid) < 0.02 and abs(float(bl2["rate"]) - o["rate"]) < 0.01
           and b2.get("purchaseorder_ids") and abs(float(p2["total"]) - o["total"]) < 0.05)
    print(f"{r['bill_number']}/{pn}: bill total {b2['total']} (paid {paid}) rate {bl2['rate']} adj {b2.get('adjustment')} "
          f"| PO {p2['total']} (DB {o['total']}) adj {p2.get('adjustment')} -> {'OK' if ok2 else 'CHECK'}")
    time.sleep(0.4)

print("\ndone:", done, "| DRY_RUN:", DRY_RUN)
