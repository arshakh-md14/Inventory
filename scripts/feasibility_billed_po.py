"""Feasibility test: can a BILLED PO's line rate be edited?
Target RCP-4308 / PO-05737. Restores prior state if it breaks."""
import json, http.client, urllib.parse, time

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
BILL_ID = "2432338000006676693"
PO_ID = "2432338000006231001"
ITEM_ID = "2432338000006218035"
ORIG_RATE = 3153.40

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


_, bd = call("GET", f"/bills/{BILL_ID}"); bill_total = float(bd["bill"]["total"])
_, pd = call("GET", f"/purchaseorders/{PO_ID}"); po = pd["purchaseorder"]; pl = po["line_items"][0]
cur_rate = float(pl["rate"]); cur_adj = float(po.get("adjustment") or 0); qty = float(pl["quantity"])
print(f"BEFORE: PO billed={po['billed_status']} rate={cur_rate} adj={cur_adj} total={po['total']} | bill_total={bill_total} linked_bill_ids in PO")

# reactivate item (inactive items block line edits)
_, itd = call("GET", f"/items/{ITEM_ID}"); st_item = itd.get("item", {}).get("status")
if st_item != "active":
    print("reactivate item:", call("POST", f"/items/{ITEM_ID}/active")[1].get("message"))

# attempt: restore original rate + round-off adjustment so PO total == bill total
upd = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": ITEM_ID, "rate": ORIG_RATE,
                       "quantity": qty, "tax_id": pl.get("tax_id")}],
       "adjustment": 0.0, "adjustment_description": "Round Off"}
st, r = call("PUT", f"/purchaseorders/{PO_ID}", upd)
print(f"PUT billed-PO rate -> HTTP {st} code={r.get('code')} msg={r.get('message')}")
_, pd2 = call("GET", f"/purchaseorders/{PO_ID}"); p2 = pd2["purchaseorder"]; pl2 = p2["line_items"][0]
rate_changed = abs(float(pl2["rate"]) - ORIG_RATE) < 0.01
print(f"AFTER PUT: rate={pl2['rate']} (target {ORIG_RATE}) changed={rate_changed} total={p2['total']} billed={p2['billed_status']}")

feasible = (st // 100 == 2) and r.get("code") in (0, None) and rate_changed and p2.get("billed_status") == "billed"

if feasible:
    # finish round-off so the case stays correct
    diff = round(bill_total - float(p2["total"]), 2)
    call("PUT", f"/purchaseorders/{PO_ID}", {"adjustment": round(float(p2.get("adjustment") or 0) + diff, 2),
                                             "adjustment_description": "Round Off"})
    _, pd3 = call("GET", f"/purchaseorders/{PO_ID}"); p3 = pd3["purchaseorder"]
    print(f"FINAL: rate={p3['line_items'][0]['rate']} total={p3['total']} (bill {bill_total}) billed={p3['billed_status']}")
    print("\nRESULT: FEASIBLE — billed-PO rate edit works.")
else:
    # restore prior state
    call("PUT", f"/purchaseorders/{PO_ID}", {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": ITEM_ID,
                                             "rate": cur_rate, "quantity": qty, "tax_id": pl.get("tax_id")}],
                                             "adjustment": cur_adj, "adjustment_description": "Round Off"})
    _, pd3 = call("GET", f"/purchaseorders/{PO_ID}"); p3 = pd3["purchaseorder"]
    print(f"RESTORED to prior: rate={p3['line_items'][0]['rate']} total={p3['total']}")
    print("\nRESULT: NOT FEASIBLE — billed-PO rate edit blocked/ineffective.")

# deactivate item back
if st_item != "active":
    call("POST", f"/items/{ITEM_ID}/inactive")
    print("item set back to inactive")
