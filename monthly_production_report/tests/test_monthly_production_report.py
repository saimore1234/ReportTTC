"""Integration tests: real Work Orders + real submitted Manufacture Stock Entries.

Run:  bench --site <site> run-tests --app monthly_production_report
Everything is created inside the test transaction and rolled back afterwards.
"""

import re
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, nowdate

from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as make_wo_stock_entry
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from monthly_production_report.monthly_production_report.report.monthly_production_quantity_and_value import (
	monthly_production_quantity_and_value as report,
)

PREFIX = "MPR-TEST"


def run(**filters):
	columns, data, _msg, chart, summary = report.execute(filters)
	return frappe._dict(columns=columns, data=data, chart=chart, summary=summary)


def detail_rows(result):
	return [r for r in result.data if not r.get("is_total_row")]


class TestMonthlyProductionReport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {"name": ["like", "%Demo%"]}) or frappe.db.get_value(
			"Company", {}, "name"
		)
		cls.abbr = frappe.db.get_value("Company", cls.company, "abbr")

		today = getdate(nowdate())
		cls.this_month = get_first_day(today)
		cls.last_month = get_first_day(add_months(today, -1))
		cls.d_last = add_days(cls.last_month, 9)  # inside previous month
		cls.d_this = add_days(cls.this_month, 0)  # 1st of the current month (<= today)
		cls.d_today = today

		cls.stores = cls.make_warehouse("Stores")
		cls.wip = cls.make_warehouse("WIP")
		cls.fg_wh = cls.make_warehouse("FG")

		cls.rm1 = cls.make_item("RM1")
		cls.rm2 = cls.make_item("RM2")
		cls.fg1 = cls.make_item("FG1")
		cls.fg2 = cls.make_item("FG2")
		cls.scrap = cls.make_item("SCRAP")

		# RM1 @10, RM2 @20; ample stock well before every posting date
		for item, rate in ((cls.rm1, 10), (cls.rm2, 20)):
			make_stock_entry(
				item_code=item,
				qty=1000,
				to_warehouse=cls.stores,
				rate=rate,
				company=cls.company,
				posting_date=add_days(cls.last_month, -5),
				set_posting_time=1,
			)

		# FG1 = 2 x RM1 + 1 x RM2 = 40 / unit ; FG2 = 3 x RM1 = 30 / unit
		cls.bom1 = cls.make_bom(cls.fg1, [(cls.rm1, 2), (cls.rm2, 1)])
		cls.bom2 = cls.make_bom(cls.fg2, [(cls.rm1, 3)])

		cls.wo1 = cls.make_wo(cls.fg1, cls.bom1, 10)
		cls.wo2 = cls.make_wo(cls.fg2, cls.bom2, 5)

		# WO1: 6 last month (240) + 4 this month (160 + 50 additional cost)
		cls.se1 = cls.manufacture(cls.wo1, 6, cls.d_last)
		cls.se2 = cls.manufacture(cls.wo1, 4, cls.d_this, additional_cost=50)
		# WO2: 5 today (150)
		cls.se3 = cls.manufacture(cls.wo2, 5, cls.d_today)

	# ----------------------------------------------------------------- helpers
	@classmethod
	def make_warehouse(cls, name):
		wh = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"{PREFIX} {name}",
				"company": cls.company,
				"parent_warehouse": f"All Warehouses - {cls.abbr}",
			}
		).insert()
		return wh.name

	@classmethod
	def make_item(cls, name):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"{PREFIX}-{name}",
				"item_name": f"{PREFIX} {name}",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"valuation_rate": 10,
			}
		)
		# India Compliance (if installed) makes an HSN code mandatory on Items
		if frappe.get_meta("Item").has_field("gst_hsn_code"):
			hsn = "99999999"
			if not frappe.db.exists("GST HSN Code", hsn):
				frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": hsn, "description": "Test"}).insert(
					ignore_permissions=True
				)
			item.gst_hsn_code = hsn
		return item.insert().name

	@classmethod
	def make_bom(cls, item, components):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": item,
				"quantity": 1,
				"company": cls.company,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": rm, "qty": qty, "rate": 0} for rm, qty in components],
			}
		)
		bom.insert()
		bom.submit()
		return bom.name

	@classmethod
	def make_wo(cls, item, bom, qty):
		wo = frappe.new_doc("Work Order")
		wo.production_item = item
		wo.bom_no = bom
		wo.qty = qty
		wo.company = cls.company
		wo.fg_warehouse = cls.fg_wh
		wo.wip_warehouse = cls.wip
		wo.source_warehouse = cls.stores
		wo.skip_transfer = 1
		wo.get_items_and_operations_from_bom()
		for row in wo.required_items:
			row.source_warehouse = cls.stores
		wo.insert()
		wo.submit()
		return wo.name

	@classmethod
	def manufacture(cls, wo, qty, posting_date, additional_cost=0, submit=True):
		se = frappe.get_doc(make_wo_stock_entry(wo, "Manufacture", qty))
		se.posting_date = posting_date
		se.set_posting_time = 1
		if additional_cost:
			account = frappe.get_cached_value("Company", cls.company, "expenses_included_in_valuation")
			se.append(
				"additional_costs",
				{"expense_account": account, "description": "Test overhead", "amount": additional_cost},
			)
		se.insert()
		if submit:
			se.submit()
		return se.name

	def mine(self, result):
		"""Restrict (shared-site) rows to the ones created by this test class."""
		return [r for r in detail_rows(result) if r.work_order in (self.wo1, self.wo2)]

	def full_range(self):
		return {"from_date": self.last_month, "to_date": self.d_today}

	# ------------------------------------------------------------------ tests
	def test_finished_goods_rows_only(self):
		"""One row per FG; raw materials (and scrap) never appear."""
		rows = self.mine(run(**self.full_range()))
		self.assertEqual(len(rows), 3)
		self.assertEqual({r.item_code for r in rows}, {self.fg1, self.fg2})
		self.assertNotIn(self.rm1, {r.item_code for r in rows})
		self.assertTrue(all(r.warehouse == self.fg_wh for r in rows))

	def test_production_value_matches_stock_ledger(self):
		"""Report value == value ERPNext posted to the stock ledger for the FG row."""
		for se in (self.se1, self.se2, self.se3):
			row = [r for r in self.mine(run(**self.full_range())) if r.stock_entry == se][0]
			sle_value = frappe.db.get_value(
				"Stock Ledger Entry",
				{"voucher_no": se, "item_code": row.item_code, "actual_qty": [">", 0], "is_cancelled": 0},
				"stock_value_difference",
			)
			self.assertAlmostEqual(row.value, flt(sle_value), places=2, msg=se)

	def test_value_not_double_counted(self):
		"""FG value = raw material cost (+ additional cost), not RM + FG."""
		by_se = {r.stock_entry: r for r in self.mine(run(**self.full_range()))}
		self.assertAlmostEqual(by_se[self.se1].value, 240)  # 6 x 40
		self.assertAlmostEqual(by_se[self.se2].value, 210)  # 4 x 40 + 50 additional cost
		self.assertAlmostEqual(by_se[self.se3].value, 150)  # 5 x 30
		self.assertAlmostEqual(by_se[self.se1].valuation_rate, 40)
		self.assertAlmostEqual(by_se[self.se2].valuation_rate, 52.5)  # (160 + 50) / 4
		self.assertEqual(by_se[self.se1].uom, "Nos")

	def test_work_order_filter(self):
		res = run(work_order=self.wo1, **self.full_range())
		rows = detail_rows(res)
		self.assertEqual({r.work_order for r in rows}, {self.wo1})
		self.assertEqual(sum(r.qty for r in rows), 10)
		self.assertAlmostEqual(sum(r.value for r in rows), 450)

	def test_blank_work_order_returns_multiple_work_orders(self):
		rows = self.mine(run(**self.full_range()))
		self.assertEqual({r.work_order for r in rows}, {self.wo1, self.wo2})

	def test_custom_date_range(self):
		res = run(from_date=self.last_month, to_date=add_days(self.last_month, 27))
		rows = self.mine(res)
		self.assertEqual([r.stock_entry for r in rows], [self.se1])

		# boundaries are inclusive
		res = run(from_date=self.d_last, to_date=self.d_last, work_order=self.wo1)
		self.assertEqual([r.stock_entry for r in detail_rows(res)], [self.se1])

		# nothing outside the range
		res = run(from_date=add_days(self.last_month, -60), to_date=add_days(self.last_month, -30))
		self.assertEqual(self.mine(res), [])

	def test_current_month_defaults_are_dynamic(self):
		js = (Path(report.__file__).with_suffix(".js")).read_text()
		self.assertIn("default: frappe.datetime.month_start()", js)
		self.assertIn("default: frappe.datetime.get_today()", js)
		self.assertIsNone(re.search(r"20\d\d-\d\d-\d\d", js), "no hard-coded dates allowed")

		# current month only => the two current-month entries
		res = run(from_date=self.this_month, to_date=self.d_today)
		self.assertEqual({r.stock_entry for r in self.mine(res)}, {self.se2, self.se3})

	def test_monthly_aggregation(self):
		res = run(view="Monthly Summary", **self.full_range())
		data = res.data
		self.assertEqual(len(data), 3)  # 2 months + total
		last, this, total = data
		self.assertEqual(last["month"], frappe.utils.formatdate(self.last_month, "MMM yyyy"))
		# other (non-test) site data would only add; here we assert at least our share
		self.assertGreaterEqual(last["qty"], 6)
		# when run for our work orders only, numbers are exact
		wo1 = run(view="Monthly Summary", work_order=self.wo1, **self.full_range()).data
		self.assertEqual((wo1[0]["qty"], wo1[0]["value"], wo1[0]["avg_rate"]), (6, 240, 40))
		self.assertEqual((wo1[1]["qty"], wo1[1]["value"], wo1[1]["avg_rate"]), (4, 210, 52.5))
		self.assertEqual((wo1[2]["qty"], wo1[2]["value"], wo1[2]["avg_rate"]), (10, 450, 45))
		self.assertEqual(wo1[2]["month"], "Total")

	def test_kpis_and_charts(self):
		res = run(work_order=self.wo1, **self.full_range())
		kpi = {s["label"]: s["value"] for s in res.summary}
		self.assertEqual(kpi["Total Production Quantity"], 10)
		self.assertAlmostEqual(kpi["Total Production Value"], 450)
		self.assertAlmostEqual(kpi["Average Production Rate"], 45)
		self.assertEqual(kpi["Number of Work Orders"], 1)

		res = run(**self.full_range())
		kpi = {s["label"]: s["value"] for s in res.summary}
		self.assertGreaterEqual(kpi["Number of Work Orders"], 2)

		self.assertEqual(res.chart["type"], "bar")
		monthly = res.chart["monthly"]
		self.assertEqual(len(monthly["labels"]), 2)
		self.assertEqual(len(monthly["qty"]), len(monthly["value"]))

	def test_total_row(self):
		res = run(work_order=self.wo1, **self.full_range())
		total = res.data[-1]
		self.assertTrue(total["is_total_row"])
		self.assertEqual(total["qty"], 10)
		self.assertAlmostEqual(total["value"], 450)
		self.assertAlmostEqual(total["valuation_rate"], 45)  # weighted, never a sum of rates

	def test_draft_entries_excluded(self):
		# a draft Manufacture entry on its own Work Order
		draft = self.manufacture(self.make_wo(self.fg2, self.bom2, 3), 3, self.d_today, submit=False)
		self.assertEqual(frappe.db.get_value("Stock Entry", draft, "docstatus"), 0)
		rows = detail_rows(run(**self.full_range()))
		self.assertNotIn(draft, {r.stock_entry for r in rows})

	def test_cancelled_entries_excluded(self):
		wo = self.make_wo(self.fg2, self.bom2, 2)
		se = self.manufacture(wo, 2, self.d_today)
		self.assertEqual({r.stock_entry for r in detail_rows(run(work_order=wo, **self.full_range()))}, {se})
		frappe.get_doc("Stock Entry", se).cancel()
		res = run(work_order=wo, **self.full_range())
		self.assertEqual(res.data, [])
		self.assertEqual({s["label"]: s["value"] for s in res.summary}["Total Production Quantity"], 0)

	def test_multiple_fg_rows_in_one_entry_and_scrap_excluded(self):
		"""Two FG rows (same item, split) + a scrap row: only FG rows are reported."""
		wo = self.make_wo(self.fg2, self.bom2, 4)
		se = frappe.get_doc(make_wo_stock_entry(wo, "Manufacture", 4))
		se.posting_date, se.set_posting_time = self.d_today, 1
		fg = [d for d in se.items if d.is_finished_item][0]
		fg.qty = fg.transfer_qty = 3
		second = frappe.copy_doc(fg)
		second.qty = second.transfer_qty = 1
		se.append("items", second)
		se.append(
			"items",
			{
				"item_code": self.scrap,
				"qty": 1,
				"t_warehouse": self.fg_wh,
				"is_scrap_item": 1,
				"basic_rate": 5,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)
		se.insert()
		se.submit()
		rows = detail_rows(run(work_order=wo, **self.full_range()))
		self.assertEqual(len(rows), 2)
		self.assertEqual({r.item_code for r in rows}, {self.fg2})
		self.assertEqual(sum(r.qty for r in rows), 4)
		# ERPNext carves the scrap value (1 x 5) out of the FG cost: 4 x 30 - 5. Must equal the ledger.
		sle = frappe.db.get_value(
			"Stock Ledger Entry",
			{"voucher_no": se.name, "item_code": self.fg2, "actual_qty": [">", 0]},
			"sum(stock_value_difference)",
		)
		self.assertAlmostEqual(sum(r.value for r in rows), 115)
		self.assertAlmostEqual(sum(r.value for r in rows), flt(sle))

	def test_date_validation(self):
		with self.assertRaises(frappe.ValidationError):
			run(from_date="2026-11-30", to_date="2026-11-01")
		with self.assertRaises(frappe.ValidationError):
			run(to_date="2026-11-01")
		with self.assertRaises(frappe.ValidationError):
			run(from_date="2026-11-01")

	def test_zero_quantity_and_empty_results(self):
		self.assertEqual(report.average_rate(0, 0), 0.0)
		self.assertEqual(report.average_rate(100, 0), 0.0)
		res = run(from_date=add_days(self.last_month, -400), to_date=add_days(self.last_month, -399))
		self.assertEqual(res.data, [])
		self.assertIsNone(res.chart)
		self.assertEqual({s["label"]: s["value"] for s in res.summary}["Average Production Rate"], 0)
		# zero-filled months, total rate 0 without ZeroDivisionError
		res = run(
			view="Monthly Summary",
			from_date=add_days(self.last_month, -400),
			to_date=add_days(self.last_month, -399),
		)
		self.assertEqual(res.data, [])

	def test_user_permission_company_restriction(self):
		other = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if not other:
			self.skipTest("needs a second company")
		email = "mpr_test_user@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "MPR",
					"send_welcome_email": 0,
					"roles": [{"role": "Stock User"}, {"role": "Manufacturing User"}],
				}
			).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "User Permission", "user": email, "allow": "Company", "for_value": other}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=email)
		try:
			frappe.set_user(email)
			self.assertEqual(self.mine(run(**self.full_range())), [])
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(len(self.mine(run(**self.full_range()))), 3)
