# Monthly Production Report (ERPNext v15)

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
16 integration tests create real BOMs, Work Orders and submitted Manufacture entries (rolled back afterwards). They cover WO filter, blank WO, multiple WOs, date ranges, month defaults (no hard-coded dates), FG-only rows, split FG rows plus scrap, additional cost, ledger reconciliation, monthly aggregation, KPIs/charts, total row, draft and cancelled exclusion, date validation, empty/zero cases and company user permissions.

## Known limits
* Totals are in raw numbers; if companies with different currencies are visible together, values are mixed.
* The total row is a normal data row (bold); sorting a column can move it.
