"""P8-T7: **no code path sets a paid status except an authenticated staff action** (AD-03).

A static search of every module under `velora/` for code (not docstrings) that
- names the `paid_staff_verified` status,
- writes to the payments ledger (`INSERT INTO velora.payments`), or
- writes an order's status (`UPDATE velora.orders SET status`, `INSERT INTO velora.orders`).

Each set must equal its reviewed allowlist, so a new writer fails this test until someone
looks at it. The paid moves themselves happen only in `PaymentService`, behind
`verify_payment_pin` (a staff session plus the confirmer's PIN). The database adds its own
guard: no `paid_staff_verified` without a verified ledger entry (`db/test_payments_ledger.py`).
A self-test proves the search finds a planted writer, directly or through a helper.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PAID = "paid_staff_verified"
LEDGER_WRITE = re.compile(r"\bINSERT\s+INTO\s+velora\.payments\b", re.IGNORECASE)
ORDER_STATUS_WRITE = re.compile(
    r"\bUPDATE\s+velora\.orders\s+SET\s+status\b|\bINSERT\s+INTO\s+velora\.orders\b",
    re.IGNORECASE,
)

# Where the paid status may be named, and why.
PAID_MENTIONS = {
    "velora.orders.lifecycle:<module>",  # the status list and the transition table
    "velora.conversation.copy:<module>",  # customer wording for the status
    "velora.conversation.copy:cannot_cancel",
    "velora.conversation.copy:order_update",
    "velora.orders.service:OrderService.move",  # refuses it as a generic move
    "velora.payments.service:PaymentService.verify",  # reference + PIN
    "velora.payments.service:PaymentService.review_late",  # reference + PIN, late payment
    "velora.payments.service:PaymentService.reverse",  # owner + PIN: moves *off* paid
    "velora.monitoring.metrics:collect",  # read-only funnel count, as velora_monitor
}
LEDGER_WRITERS = {
    "velora.payments.service:PaymentService._record_verified",
    "velora.payments.service:PaymentService.reverse",
}
ORDER_STATUS_WRITERS = {
    "velora.orders.lifecycle:transition",  # every move, through the transition table
    "velora.orders.placement:create",  # the first status: cod_confirmed or awaiting_transfer
}
# Every PaymentService path that makes a paid move checks the PIN first.
PIN_CHECKED = {"PaymentService._confirm", "PaymentService.reverse"}


class _Finder(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.docstrings: set[int] = set()
        self.found: dict[str, set[str]] = {"paid": set(), "ledger": set(), "orders": set()}
        self.calls: dict[str, set[str]] = {}

    def _where(self) -> str:
        return f"{self.module}:{'.'.join(self.scope) or '<module>'}"

    def _mark_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.docstrings.add(id(body[0].value))

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        self._mark_docstring(node)
        self.generic_visit(node)

    def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._mark_docstring(node)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = _scoped  # noqa: N815
    visit_FunctionDef = _scoped  # noqa: N815
    visit_AsyncFunctionDef = _scoped  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        if name is not None:
            self.calls.setdefault(".".join(self.scope), set()).add(name)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if not isinstance(node.value, str) or id(node) in self.docstrings:
            return
        where = self._where()
        if PAID in node.value:
            self.found["paid"].add(where)
        if LEDGER_WRITE.search(node.value):
            self.found["ledger"].add(where)
        if ORDER_STATUS_WRITE.search(node.value):
            self.found["orders"].add(where)


def scan(source: str, module: str) -> _Finder:
    finder = _Finder(module)
    finder.visit(ast.parse(source))
    return finder


def scan_tree() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    found: dict[str, set[str]] = {"paid": set(), "ledger": set(), "orders": set()}
    calls: dict[str, set[str]] = {}
    for path in sorted((SRC / "velora").rglob("*.py")):
        module = ".".join(path.relative_to(SRC).with_suffix("").parts)
        finder = scan(path.read_text(encoding="utf-8"), module)
        for key, places in finder.found.items():
            found[key] |= places
        if module == "velora.payments.service":
            calls = finder.calls
    return found, calls


def test_only_reviewed_code_names_the_paid_status() -> None:
    found, _ = scan_tree()
    assert found["paid"] == PAID_MENTIONS


def test_only_the_payment_service_writes_the_ledger() -> None:
    found, _ = scan_tree()
    assert found["ledger"] == LEDGER_WRITERS


def test_order_status_is_written_only_by_placement_and_the_transition_engine() -> None:
    found, _ = scan_tree()
    assert found["orders"] == ORDER_STATUS_WRITERS


def test_every_paid_move_checks_a_pin_first() -> None:
    _, calls = scan_tree()
    for function in PIN_CHECKED:
        assert "verify_payment_pin" in calls.get(function, set()), function
    # verify and review_late reach the paid move only through _confirm.
    for function in ("PaymentService.verify", "PaymentService.review_late"):
        assert "_confirm" in calls[function], function
        assert "transition" not in calls[function], function


# Self-test: the search catches planted writers ------------------------------------------------

PLANTED = '''
"""A docstring that mentions paid_staff_verified is ignored."""

from velora.orders import lifecycle

STATUS = "paid_staff_verified"


async def on_screenshot(conn, tenant, order_id, now):
    await lifecycle.transition(conn, tenant, order_id, to=STATUS, actor=None, now=now)


async def sneaky(conn):
    await conn.execute("UPDATE velora.orders SET status = 'paid_staff_verified'")
    await conn.execute("insert into velora.payments (status) values ('verified')")
'''


def test_the_search_finds_planted_writers() -> None:
    found = scan(PLANTED, "velora.rogue").found
    assert found["paid"] == {"velora.rogue:<module>", "velora.rogue:sneaky"}
    assert found["ledger"] == {"velora.rogue:sneaky"}
    assert found["orders"] == {"velora.rogue:sneaky"}
