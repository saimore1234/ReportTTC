"""WIP Breakup: real Work Orders, material transfers, manufacture entries, returns."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, nowdate

from erpnext.manufacturing.doctype.work_order.work_order import make_stock_return_entry
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

from monthly_production_report.monthly_production_report.report.wip_breakup import wip_breakup as report
from monthly_production_report.tests import fixtures as fx

P = "MPR-WIP"
TRANSFER = "Material Transfer for Manufacture"


def run(**filters):
	columns, data, _m, chart, summary = report.execute(filters)
	return frappe._dict(columns=columns, data=data, chart=chart, summary=summary)


def rows_of(res):
	return [r for r in res.data if not r.get("is_total_row")]


class TestWipBreakup(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = fx.get_company()
		today = getdate(nowdate())
		cls.d_last = add_days(get_first_day(add_months(today, -1)), 9)
		cls.d_this = get_first_day(today)
		cls.today = today

		cls.stores = fx.make_warehouse(cls.company, f"{P} Stores")
		cls.wip = fx.make_warehouse(cls.company, f"{P} WIP")  # exclusively used by this test
		cls.fgw = fx.make_warehouse(cls.company, f"{P} FG")
		cls.rm1 = fx.make_item(f"{P}-RM1")
		cls.rm2 = fx.make_item(f"{P}-RM2")
		cls.fg1 = fx.make_item(f"{P}-FG1")
		cls.fg2 = fx.make_item(f"{P}-FG2")

		for item, rate in ((cls.rm1, 10), (cls.rm2, 20)):
			make_stock_entry(
				item_code=item, qty=1000, to_warehouse=cls.stores, rate=rate, company=cls.company,
				posting_date=add_days(cls.d_last, -20), set_posting_time=1,
			)

		bom1 = fx.make_bom(cls.company, cls.fg1, [(cls.rm1, 2), (cls.rm2, 1)])  # 40 / unit
		bom2 = fx.make_bom(cls.company, cls.fg2, [(cls.rm1, 3)])  # 30 / unit

		def wo(item, bom, qty, skip=0):
			return fx.make_work_order(cls.company, item, bom, qty, cls.stores, cls.wip, cls.fgw, skip)

		cls.wo1 = wo(cls.fg1, bom1, 10)  # in progress: 10 transferred, 6 made
		cls.wo2 = wo(cls.fg2, bom2, 5)  # completed
		cls.wo3 = wo(cls.fg2, bom2, 4, skip=1)  # no WIP stage
		cls.wo4 = wo(cls.fg1, bom1, 10)  # transfer 4, make 2, return the rest
		cls.wo5 = wo(cls.fg1, bom1, 3)  # only a DRAFT transfer
		cls.wo6 = wo(cls.fg1, bom1, 3)  # transfer cancelled

		e = lambda w, p, q, d, **k: fx.work_order_entry(w, p, q, d, **k)  # noqa: E731
		cls.t1 = e(cls.wo1, TRANSFER, 10, cls.d_last)
		cls.m1 = e(cls.wo1, "Manufacture", 6, cls.d_this)

		e(cls.wo2, TRANSFER, 5, cls.d_last)
		e(cls.wo2, "Manufacture", 5, cls.d_this)

		e(cls.wo3, "Manufacture", 2, cls.d_this)

		e(cls.wo4, TRANSFER, 4, cls.d_last)
		e(cls.wo4, "Manufacture", 2, cls.d_this)
		ret = make_stock_return_entry(cls.wo4)
		ret.posting_date, ret.set_posting_time = cls.today, 1
		ret.insert()
		ret.submit()

		cls.draft = e(cls.wo5, TRANSFER, 3, cls.d_last, submit=False)
		cancelled = e(cls.wo6, TRANSFER, 3, cls.d_last)
		frappe.get_doc("Stock Entry", cancelled).cancel()

		cls.ours = (cls.wo1, cls.wo2, cls.wo3, cls.wo4, cls.wo5, cls.wo6)

	def f(self, **kw):
		f = {"company": self.company, "from_date": self.d_this, "to_date": self.today}
		f.update(kw)
		return f

	def summary(self, **kw):
		return {r.work_order: r for r in rows_of(run(**self.f(**kw))) if r.work_order in self.ours}

	# ------------------------------------------------------------- summary view
	def test_in_progress_work_order(self):
		w = self.summary()[self.wo1]
		self.assertEqual(w.status, "In Process")
		self.assertEqual((w.qty, w.produced_qty, w.pending_qty), (10, 6, 4))
		self.assertAlmostEqual(w.progress, 60)
		self.assertEqual(w.required_qty, 30)  # 20 RM1 + 10 RM2
		self.assertEqual(w.transferred_qty, 30)
		self.assertEqual(w.consumed_qty, 18)  # 12 RM1 + 6 RM2
		self.assertAlmostEqual(w.transferred_value, 400)  # 20x10 + 10x20
		self.assertAlmostEqual(w.consumption_value, 240)  # actual: 6 x 40
		self.assertAlmostEqual(w.fg_value, 240)  # FG valued at the consumed materials
		self.assertAlmostEqual(w.wip_value, 160)  # 8 RM1 x10 + 4 RM2 x20
		self.assertEqual(w.wip_warehouse, self.wip)

	def test_no_double_counting(self):
		"""transferred = consumed + still in WIP; FG value is not added on top."""
		w = self.summary()[self.wo1]
		self.assertAlmostEqual(w.transferred_value, w.consumption_value + w.wip_value)

	def test_completed_work_order_has_no_wip(self):
		w = self.summary()[self.wo2]
		self.assertEqual(w.status, "Completed")
		self.assertEqual((w.produced_qty, w.pending_qty, w.progress), (5, 0, 100))
		self.assertAlmostEqual(w.wip_value, 0)
		self.assertAlmostEqual(w.consumption_value, 150)

	def test_skip_transfer_work_order_never_has_wip(self):
		w = self.summary()[self.wo3]
		self.assertEqual(w.transferred_qty, 0)
		self.assertAlmostEqual(w.consumption_value, 60)  # 2 x 30 consumed straight from stores
		self.assertEqual(w.wip_value, 0)
		self.assertEqual(w.pending_qty, 2)

	def test_return_of_unused_material_reduces_wip(self):
		w = self.summary()[self.wo4]
		# transferred 4 units (160) - returned 2 units (80 non-consumed) = 80 net, 2 units (80) consumed
		self.assertAlmostEqual(w.transferred_value, 80)
		self.assertAlmostEqual(w.consumption_value, 80)
		self.assertAlmostEqual(w.wip_value, 0)

	def test_draft_and_cancelled_transfers_excluded(self):
		self.assertEqual(frappe.db.get_value("Stock Entry", self.draft, "docstatus"), 0)
		for wo in (self.wo5, self.wo6):
			res = run(**self.f(work_order=wo))
			w = rows_of(res)[0]  # selected explicitly -> shown
			self.assertEqual((w.transferred_qty, w.transferred_value, w.wip_value), (0, 0, 0), wo)
			self.assertEqual(w.status, "Not Started")
		# and without an explicit selection they have no activity so are not listed
		self.assertNotIn(self.wo5, self.summary())
		self.assertNotIn(self.wo6, self.summary())

	# ---------------------------------------------------------------- filters
	def test_work_order_filter(self):
		res = run(**self.f(work_order=self.wo1))
		self.assertEqual([r.work_order for r in rows_of(res)], [self.wo1])

	def test_blank_work_order_lists_multiple(self):
		self.assertEqual({self.wo1, self.wo2, self.wo3, self.wo4}, set(self.summary()))

	def test_status_filter(self):
		self.assertEqual({self.wo2}, set(self.summary(status="Completed")))
		self.assertEqual(self.wo1 in self.summary(status="In Process"), True)
		self.assertNotIn(self.wo2, self.summary(status="In Process"))

	def test_production_item_and_wip_warehouse_filters(self):
		self.assertEqual({self.wo2, self.wo3}, set(self.summary(production_item=self.fg2)))
		# ERPNext clears wip_warehouse on skip-transfer Work Orders (wo3): they have no WIP stage
		self.assertIsNone(frappe.db.get_value("Work Order", self.wo3, "wip_warehouse") or None)
		self.assertEqual({self.wo1, self.wo2, self.wo4}, set(self.summary(wip_warehouse=self.wip)))
		self.assertEqual(self.summary(wip_warehouse=self.stores), {})

	def test_item_group_filter(self):
		group = fx.make_item_group(f"{P} Nope")
		self.assertEqual(self.summary(item_group=group), {})
		self.assertIn(self.wo1, self.summary(item_group="All Item Groups"))

	def test_company_filter(self):
		other = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if other:
			self.assertEqual(self.summary(company=other), {})

	def test_date_range_and_as_of_date(self):
		# range with no activity for these WOs (and none In Process before it): nothing listed except in-process ones
		early = self.summary(from_date=add_days(self.d_last, -60), to_date=add_days(self.d_last, -50))
		# only Work Orders that are currently "In Process" remain candidates; completed / untouched ones drop out
		self.assertEqual(set(early), {self.wo1, self.wo3, self.wo4})
		self.assertNotIn(self.wo2, early)
		# measured as of To Date: before the manufacture entry only the transfer exists
		w = self.summary(from_date=self.d_last, to_date=add_days(self.d_last, 1))[self.wo1]
		self.assertEqual((w.produced_qty, w.consumed_qty), (0, 0))
		self.assertAlmostEqual(w.wip_value, 400)
		self.assertEqual(w.pending_qty, 10)

	def test_date_validation(self):
		with self.assertRaises(frappe.ValidationError):
			run(**self.f(from_date=self.today, to_date=self.d_last))
		with self.assertRaises(frappe.ValidationError):
			run(company=self.company, to_date=self.today)
		with self.assertRaises(frappe.ValidationError):
			run(from_date=self.d_this, to_date=self.today)

	# ---------------------------------------------------------- material view
	def test_material_wise_breakup(self):
		res = run(**self.f(work_order=self.wo1, view="Material-wise Breakup"))
		rows = {r.item_code: r for r in rows_of(res)}
		self.assertEqual(set(rows), {self.rm1, self.rm2})
		r1, r2 = rows[self.rm1], rows[self.rm2]
		self.assertEqual((r1.required_qty, r1.transferred_qty, r1.consumed_qty, r1.remaining_qty), (20, 20, 12, 8))
		self.assertEqual((r2.required_qty, r2.transferred_qty, r2.consumed_qty, r2.remaining_qty), (10, 10, 6, 4))
		self.assertAlmostEqual(r1.valuation_rate, 10)
		self.assertAlmostEqual(r1.material_value, 80)
		self.assertAlmostEqual(r2.material_value, 80)
		self.assertEqual({r.work_order for r in rows_of(res)}, {self.wo1})  # correctly linked
		# material rows add up to the Work Order summary
		wo = rows_of(run(**self.f(work_order=self.wo1)))[0]
		self.assertAlmostEqual(sum(r.material_value for r in rows_of(res)), wo.wip_value)
		self.assertAlmostEqual(sum(r.consumption_value for r in rows_of(res)), wo.consumption_value)
		self.assertTrue(res.data[-1]["is_total_row"])

	# --------------------------------------------- KPIs, charts, total, ledger
	def test_kpis_total_and_charts(self):
		res = run(**self.f(work_order=self.wo1))
		kpi = {s["label"]: s["value"] for s in res.summary}
		self.assertAlmostEqual(kpi["Total WIP Value"], 160)
		self.assertEqual(kpi["Total Work Orders"], 1)
		self.assertEqual(kpi["Total Pending Production Qty"], 4)
		self.assertAlmostEqual(kpi["Total Material Consumption Value"], 240)
		self.assertTrue(res.data[-1]["is_total_row"])
		self.assertAlmostEqual(res.data[-1]["wip_value"], 160)
		self.assertEqual(set(res.chart["extra"]), {"wip", "pending", "consumption"})
		self.assertEqual(res.chart["extra"]["pending"]["values"], [4])

	def test_empty_result(self):
		res = run(**self.f(work_order=self.wo1, status="Completed"))
		self.assertEqual(res.data, [])
		self.assertIsNone(res.chart)
		self.assertEqual({s["label"]: s["value"] for s in res.summary}["Total WIP Value"], 0)

	def test_wip_value_reconciles_with_wip_warehouse_ledger(self):
		"""This test's WIP warehouse is used only by these Work Orders, so the report's total WIP
		value must equal the actual stock-ledger value sitting in that warehouse."""
		ledger = flt(
			frappe.db.sql(
				"""select sum(stock_value_difference) from `tabStock Ledger Entry`
				where warehouse = %s and is_cancelled = 0""",
				self.wip,
			)[0][0]
		)
		total = sum(w.wip_value for w in self.summary().values())
		self.assertAlmostEqual(total, ledger, places=2)
		self.assertAlmostEqual(total, 160)  # only WO1 still holds material
		bin_qty = flt(frappe.db.sql("select sum(actual_qty) from `tabBin` where warehouse=%s", self.wip)[0][0])
		self.assertEqual(bin_qty, 12)  # 8 RM1 + 4 RM2
