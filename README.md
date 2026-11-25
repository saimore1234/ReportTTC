# Monthly Production Report (ERPNext v15)

Reports in this app: **Monthly Production Quantity and Value**, **Stock Summary With Detailed Breakup**, **WIP Breakup**.

Script Report **Monthly Production Quantity and Value** (module *Monthly Production Report*, ref doctype *Stock Entry*).
No ERPNext/Frappe core files are modified.

## Install
```bash
cd ~/frappe-bench
bench get-app /path/to/monthly_production_report      # or: pip install -e apps/monthly_production_report
bench --site <site> install-app monthly_production_report
bench --site <site> migrate && bench --site <site> clear-cache
```
Open `/app/query-report/Monthly Production Quantity and Value`.

## Filters
| Filter | Notes |
|---|---|
| Work Order | Link (searchable, submitted WOs). Blank = all Work Orders |
| From Date | mandatory, default first day of current month (`frappe.datetime.month_start()`) |
| To Date | mandatory, default today; From > To is rejected |
| View | `Detailed` (default) or `Monthly Summary` |

## Data source (verified in ERPNext 15.78.1 source)
* `Stock Entry` where `purpose = 'Manufacture'`, `docstatus = 1`, `work_order` set (`StockEntry.update_work_order` links production to the WO).
* `Stock Entry Detail` rows with `is_finished_item = 1`. Raw materials (source warehouse only), scrap (`is_scrap_item`) are excluded. `validate_finished_goods` allows only one distinct FG item per Manufacture entry; the same item may appear in several rows.
* Quantity = `transfer_qty` (stock UOM, shown with `stock_uom`), so rates are comparable.
* Production Value = `Stock Entry Detail.amount` = `basic_amount + additional_cost`; Valuation Rate = `valuation_rate` (= amount / transfer_qty). Raw-material cost is already distributed into the FG row by `set_basic_rate`, so nothing is added twice. Scrap value is carved out of FG cost by ERPNext, and the report follows that. Verified equal to the Stock Ledger Entry `stock_value_difference`.
* Rows with zero quantity are skipped; averages guard against division by zero.

## Output
Columns, KPI cards (Total Quantity, Total Value, Average Rate, Number of Work Orders), total row (rate is value/qty, never a sum), three charts (monthly quantity bar, monthly value bar, value trend line), clickable Stock Entry / Work Order / Item links, and standard Menu → Export to Excel / CSV. Months in range with no production are zero-filled.

## Permissions
Roles: Manufacturing User/Manager, Stock User/Manager, System Manager. SQL applies `get_match_cond("Stock Entry")`, so User Permissions (e.g. Company) are respected. The date range is part of the query's WHERE clause.

## Tests
```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app monthly_production_report
```
49 integration tests (16 production, 15 stock summary, 18 WIP) create real BOMs, Work Orders and submitted Manufacture entries (rolled back afterwards). They cover WO filter, blank WO, multiple WOs, date ranges, month defaults (no hard-coded dates), FG-only rows, split FG rows plus scrap, additional cost, ledger reconciliation, monthly aggregation, KPIs/charts, total row, draft and cancelled exclusion, date validation, empty/zero cases and company user permissions.

## Known limits
* Totals are in raw numbers; if companies with different currencies are visible together, values are mixed.
* The total row is a normal data row (bold); sorting a column can move it.

---

# Stock Summary With Detailed Breakup

Filters: Company (mandatory), From Date (optional), To Date (default today), Item, Item Group (incl. children),
Warehouse (a group warehouse includes its children), Stock UOM, Project, Group By (Item and Warehouse / Item /
Warehouse / Item Group), Include Zero Stock.

* Source: `Stock Ledger Entry` (`is_cancelled = 0`), never the current Bin, so any historical date works.
* Opening = entries before From Date; Inward / Outward = entries inside the period with qty (value) >= 0 / < 0;
  Closing = everything up to To Date. Value uses `stock_value_difference` (what ERPNext actually posted), not qty x rate.
  Valuation Rate = closing value / closing qty.
* Without From Date the Opening/Inward/Outward columns are hidden and only the balance as of To Date is shown.
* Reconciled in tests against ERPNext's standard **Stock Balance** report (opening, in, out, closing, qty and value).
  Only difference: vouchers flagged "Opening Stock" dated *inside* the period count as inward here.
* KPI cards, total row (weighted rate), bar chart (top 15 by value), item/warehouse links, and a "View Ledger" link
  that opens the standard Stock Ledger for that item/warehouse and period. Excel/CSV via the report menu.
* Batch / serial numbers are not broken out (v15 stores them in Serial and Batch Bundles); inventory dimensions are not included.

# WIP Breakup

Filters: Company, From Date, To Date, Work Order, Production Item, Item Group, WIP Warehouse, Production Status,
View (Work Order Summary / Material-wise Breakup).

How ERPNext records WIP (checked in source): `Material Transfer for Manufacture` moves raw material to the WIP
warehouse (`is_return = 1` returns it); `Manufacture` consumes it from WIP and receives the finished good;
`Material Consumption for Manufacture` consumes without producing; Work Orders with *Skip Material Transfer* never use WIP.

Per Work Order and material, from submitted Stock Entry Detail rows up to To Date:
`transferred = transfers - returns`, `consumed = actual consumption rows`, `remaining = max(transferred - consumed, 0)`,
**WIP value = remaining qty x the material's transfer rate**. FG output and scrap are shown separately and never added.
Planned/BOM costs are never used. Consumption is valued at the real posted amount.
Why not "transferred value - consumed value": ERPNext values consumption at the WIP warehouse's moving average, which blends
Work Orders that share a warehouse; the transfer rate keeps WIP attributed to the right Work Order. With one Work Order per
warehouse the total equals the WIP warehouse ledger value (tested).

* Listed Work Orders: submitted, with a transfer/manufacture/consumption entry between From and To Date, **or** currently "In Process".
  Choosing a Work Order always shows it. Quantities/values are as of To Date; Status is the current status.
* Produced Qty comes from Manufacture finished-good rows (stock UOM). Pending = max(WO Qty - Produced, 0).
* Required/Transferred/Consumed qty totals add different materials/UOMs, as requested; use the material view for precision.
* KPI cards (Total WIP Value, Work Orders, Pending Qty, Material Consumption Value), total row, and three charts
  (WIP value, pending qty, consumption value by Work Order; top 15 each).

## Update / deploy
```bash
cd ~/frappe-bench/apps/monthly_production_report && git pull
cd ~/frappe-bench
bench --site <site> migrate          # registers the two new reports
bench build --app monthly_production_report && bench --site <site> clear-cache
bench restart
```
