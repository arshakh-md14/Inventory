"""Fix the 5 CHECK cases (IGST->CGST/SGST 1-paisa drift): nudge the bill's round-off
adjustment by the residual balance so total == paid exactly. Updates results CHECK->OK."""
import json, http.client, urllib.parse, csv, glob, re, os

csv.field_size_limit(10000000)
ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None
# item status (for reactivation)
ISTAT = {}
for f in glob.glob(r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files\Item_*\Item*.csv"):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        i = row["Item ID"].strip()
        if i: ISTAT[i] = row.get("Status", "").strip()


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


rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
checks = [r for r in rows if r["result"] == "CHECK"]
fixed = set()
LINE_KEYS = ["purchaseorder_item_id", "item_id", "account_id", "name", "description", "rate",
             "quantity", "discount", "unit", "hsn_or_sac", "tax_id", "item_order", "is_billable", "receive_line_items"]
HDR = ["location_id", "vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
       "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
       "source_of_supply", "destination_of_supply", "template_id", "billing_address_id", "reference_number"]
for r in checks:
    bid = r["bill_id"]
    _, bd = call("GET", f"/bills/{bid}"); b = bd["bill"]
    bal = float(b.get("balance") or 0)
    cur_adj = float(b.get("adjustment") or 0)
    if abs(bal) < 0.005:
        print(f"{r['bill_number']}: balance already 0 (total {b['total']}); marking OK"); fixed.add(bid); continue
    new_adj = round(cur_adj - bal, 2)
    item_id = b["line_items"][0].get("item_id")
    bu = {"bill_number": b["bill_number"], "adjustment": new_adj, "adjustment_description": "Round Off",
          "discount_type": "item_level",
          "line_items": [{k: li.get(k) for k in LINE_KEYS if li.get(k) is not None} for li in b["line_items"]]}
    for k in HDR:
        if b.get(k) not in (None, ""): bu[k] = b[k]
    if b.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in b["custom_fields"]]
    react = item_id and ISTAT.get(item_id) == "Inactive"
    if react: call("POST", f"/items/{item_id}/active")
    st, rr = call("PUT", f"/bills/{bid}", bu)
    if react: call("POST", f"/items/{item_id}/inactive")
    _, bd2 = call("GET", f"/bills/{bid}"); b2 = bd2["bill"]
    ok = abs(float(b2.get("balance") or 0)) < 0.005
    print(f"{r['bill_number']}: adj {cur_adj}->{new_adj} | total {b['total']}->{b2['total']} balance {b2.get('balance')} -> {'OK' if ok else 'STILL OFF'}")
    if ok: fixed.add(bid)

for r in rows:
    if r["bill_id"] in fixed and r["result"] == "CHECK":
        r["result"] = "OK"; r["reason"] = "round-off corrected"
with open(RESULTS, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("fixed:", len(fixed))
