"""In-memory fakes of the AWS clients used by the Lambdas, for unit tests without AWS or moto.

FakeTable mimics the boto3 DynamoDB *Table resource* closely enough to catch real bugs:
- rejects float values (boto3 requires Decimal),
- evaluates ConditionExpression / UpdateExpression strings (SET, REMOVE, ADD, if_not_exists, list_append,
  attribute_exists, attribute_not_exists, begins_with, comparisons, AND/OR/NOT, BETWEEN, IN),
- evaluates boto3 Key()/Attr() condition objects in query/scan,
- keeps sparse GSIs (GSI1, GSI2) sorted by their sort key, honours Limit before FilterExpression.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

_MISSING = object()


def _client_error(code: str, op: str, message: str = "") -> Exception:
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": message or code}}, op)


def _check_no_float(value: Any, path: str = "") -> None:
    if isinstance(value, float):
        raise TypeError(f"Float types are not supported. Use Decimal types instead. ({path})")
    if isinstance(value, dict):
        for k, v in value.items():
            _check_no_float(v, f"{path}.{k}")
    elif isinstance(value, list | tuple | set):
        for v in value:
            _check_no_float(v, path)


def _check_unused(values: dict | None, names: dict | None, *exprs: Any) -> None:
    """DynamoDB rejects ExpressionAttributeValues/Names that no expression uses."""
    text = " ".join(e for e in exprs if isinstance(e, str))
    for mapping in (values, names):
        unused = {k for k in (mapping or {}) if not re.search(re.escape(k) + r"(?!\w)", text)}
        if unused:
            raise _client_error("ValidationException", "Expression", f"Unused expression attributes: {sorted(unused)}")


# --------------------------------------------------------------------------------------------- expressions
_TOKEN = re.compile(r"\s*(#\w+|:\w+|[A-Za-z_]\w*|\d+|<>|<=|>=|[=<>(),.\[\]+\-])")


def _tokenize(expr: str) -> list[str]:
    tokens, pos = [], 0
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"Cannot parse expression near: {expr[pos:]!r}")
        tokens.append(m.group(1))
        pos = m.end()
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
    return tokens


class _Parser:
    def __init__(self, expr: str, names: dict[str, str] | None, values: dict[str, Any] | None) -> None:
        self.t = _tokenize(expr)
        self.i = 0
        self.names = names or {}
        self.values = values or {}

    # -- token helpers
    def peek(self, k: int = 0) -> str | None:
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self, expected: str | None = None) -> str:
        tok = self.peek()
        if tok is None or (expected is not None and tok.upper() != expected.upper()):
            raise ValueError(f"Expected {expected!r}, got {tok!r}")
        self.i += 1
        return tok

    def at_kw(self, *kws: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.upper() in kws

    # -- paths / operands (return callables evaluated against an item)
    def path(self) -> list[str | int]:
        parts: list[str | int] = [self._name(self.take())]
        while self.peek() in (".", "["):
            if self.take() == ".":
                parts.append(self._name(self.take()))
            else:
                parts.append(int(self.take()))
                self.take("]")
        return parts

    def _name(self, tok: str) -> str:
        if tok.startswith("#"):
            if tok not in self.names:
                raise ValueError(f"Missing ExpressionAttributeNames entry {tok}")
            return self.names[tok]
        return tok

    def operand(self) -> Callable[[dict[str, Any]], Any]:
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if tok.startswith(":"):
            self.take()
            if tok not in self.values:
                raise ValueError(f"Missing ExpressionAttributeValues entry {tok}")
            value = self.values[tok]
            return lambda item, v=value: copy.deepcopy(v)
        if tok.lower() == "size" and self.peek(1) == "(":
            self.take(), self.take("(")
            p = self.path()
            self.take(")")
            return lambda item, p=p: Decimal(len(_get(item, p))) if _get(item, p) is not _MISSING else _MISSING
        p = self.path()
        return lambda item, p=p: _get(item, p)

    # -- update value: term (+|- term)?
    def update_value(self) -> Callable[[dict[str, Any]], Any]:
        left = self.update_term()
        if self.peek() in ("+", "-"):
            op = self.take()
            right = self.update_term()

            def arith(item: dict[str, Any], left=left, right=right, op=op) -> Any:
                a, b = left(item), right(item)
                if a is _MISSING or b is _MISSING:
                    raise _client_error("ValidationException", "UpdateItem", "Operand missing in arithmetic")
                return a + b if op == "+" else a - b

            return arith
        return left

    def update_term(self) -> Callable[[dict[str, Any]], Any]:
        tok = (self.peek() or "").lower()
        if tok == "if_not_exists" and self.peek(1) == "(":
            self.take(), self.take("(")
            p = self.path()
            self.take(",")
            default = self.update_value()
            self.take(")")
            return lambda item, p=p, d=default: v if (v := _get(item, p)) is not _MISSING else d(item)
        if tok == "list_append" and self.peek(1) == "(":
            self.take(), self.take("(")
            a = self.update_value()
            self.take(",")
            b = self.update_value()
            self.take(")")
            return lambda item, a=a, b=b: list(_or_empty(a(item))) + list(_or_empty(b(item)))
        return self.operand()

    # -- conditions
    def condition(self) -> Callable[[dict[str, Any]], bool]:
        left = self.conjunction()
        while self.at_kw("OR"):
            self.take()
            right = self.conjunction()
            left = _either(left, right)
        return left

    def conjunction(self) -> Callable[[dict[str, Any]], bool]:
        left = self.unary()
        while self.at_kw("AND"):
            self.take()
            right = self.unary()
            left = _both(left, right)
        return left

    def unary(self) -> Callable[[dict[str, Any]], bool]:
        if self.at_kw("NOT"):
            self.take()
            inner = self.unary()
            return lambda item: not inner(item)
        if self.peek() == "(":
            self.take("(")
            inner = self.condition()
            self.take(")")
            return inner
        tok = (self.peek() or "").lower()
        if tok in ("attribute_exists", "attribute_not_exists") and self.peek(1) == "(":
            self.take(), self.take("(")
            p = self.path()
            self.take(")")
            exists = tok == "attribute_exists"
            return lambda item, p=p: (_get(item, p) is not _MISSING) == exists
        if tok in ("begins_with", "contains") and self.peek(1) == "(":
            self.take(), self.take("(")
            a = self.operand()
            self.take(",")
            b = self.operand()
            self.take(")")
            if tok == "begins_with":
                return lambda item: isinstance(x := a(item), str) and x.startswith(b(item))
            return lambda item: (x := a(item)) is not _MISSING and b(item) in x
        left = self.operand()
        if self.at_kw("BETWEEN"):
            self.take()
            low = self.operand()
            self.take("AND")
            high = self.operand()
            return lambda item: _cmp(low(item), "<=", left(item)) and _cmp(left(item), "<=", high(item))
        if self.at_kw("IN"):
            self.take(), self.take("(")
            options = [self.operand()]
            while self.peek() == ",":
                self.take()
                options.append(self.operand())
            self.take(")")
            return lambda item: left(item) is not _MISSING and any(left(item) == o(item) for o in options)
        op = self.take()
        if op not in ("=", "<>", "<", "<=", ">", ">="):
            raise ValueError(f"Unsupported comparator {op!r}")
        right = self.operand()
        return lambda item: _cmp(left(item), op, right(item))

    def done(self) -> None:
        if self.peek() is not None:
            raise ValueError(f"Unexpected token {self.peek()!r}")


def _either(a: Callable[[Any], bool], b: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda item: a(item) or b(item)


def _both(a: Callable[[Any], bool], b: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda item: a(item) and b(item)


def _or_empty(v: Any) -> Any:
    return [] if v is _MISSING else v


def _cmp(a: Any, op: str, b: Any) -> bool:
    if a is _MISSING or b is _MISSING:
        return op == "<>" and not (a is _MISSING and b is _MISSING)
    if op == "=":
        return a == b
    if op == "<>":
        return a != b
    if type(a) is not type(b) and not (isinstance(a, Decimal | int) and isinstance(b, Decimal | int)):
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _get(item: Any, path: list[str | int]) -> Any:
    cur = item
    for part in path:
        if isinstance(part, int):
            if not isinstance(cur, list) or part >= len(cur):
                return _MISSING
            cur = cur[part]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return _MISSING
            cur = cur[part]
    return cur


def _set(item: dict[str, Any], path: list[str | int], value: Any) -> None:
    cur: Any = item
    for part in path[:-1]:
        cur = cur[part]
    cur[path[-1]] = value


def _remove(item: dict[str, Any], path: list[str | int]) -> None:
    cur: Any = item
    for part in path[:-1]:
        if _get(cur, [part]) is _MISSING:
            return
        cur = cur[part]
    if isinstance(path[-1], int):
        if isinstance(cur, list) and path[-1] < len(cur):
            cur.pop(path[-1])
    elif isinstance(cur, dict):
        cur.pop(path[-1], None)


def eval_condition(expr: Any, item: dict[str, Any], names: dict | None = None, values: dict | None = None) -> bool:
    if expr is None:
        return True
    if not isinstance(expr, str):
        return _eval_boto_condition(expr, item)
    parser = _Parser(expr, names, values)
    fn = parser.condition()
    parser.done()
    return bool(fn(item))


def apply_update(expr: str, item: dict[str, Any], names: dict | None = None, values: dict | None = None) -> None:
    parser = _Parser(expr, names, values)
    snapshot = copy.deepcopy(item)  # every operand reads the pre-update item, like DynamoDB
    actions: list[Callable[[], None]] = []
    while parser.peek() is not None:
        clause = parser.take().upper()
        if clause not in ("SET", "REMOVE", "ADD", "DELETE"):
            raise ValueError(f"Unknown update clause {clause}")
        while True:
            path = parser.path()
            if clause == "SET":
                parser.take("=")
                fn = parser.update_value()
                actions.append(lambda p=path, f=fn: _set(item, p, f(snapshot)))
            elif clause == "REMOVE":
                actions.append(lambda p=path: _remove(item, p))
            else:
                fn = parser.operand()

                def add_or_delete(p=path, f=fn, kind=clause) -> None:
                    cur, val = _get(snapshot, p), f(snapshot)
                    if kind == "ADD":
                        if isinstance(val, set):
                            _set(item, p, (set() if cur is _MISSING else set(cur)) | val)
                        else:
                            _set(item, p, (Decimal(0) if cur is _MISSING else cur) + val)
                    elif cur is not _MISSING:
                        _set(item, p, set(cur) - val)

                actions.append(add_or_delete)
            if parser.peek() == ",":
                parser.take()
                continue
            break
    for action in actions:
        action()


def _eval_boto_condition(cond: Any, item: dict[str, Any]) -> bool:
    kind = type(cond).__name__
    vals = cond.get_expression()["values"]

    def val(x: Any) -> Any:
        return _get(item, [x.name]) if hasattr(x, "name") and type(x).__name__ in ("Key", "Attr") else x

    if kind == "And":
        return all(_eval_boto_condition(c, item) for c in vals)
    if kind == "Or":
        return any(_eval_boto_condition(c, item) for c in vals)
    if kind == "Not":
        return not _eval_boto_condition(vals[0], item)
    if kind == "AttributeExists":
        return val(vals[0]) is not _MISSING
    if kind == "AttributeNotExists":
        return val(vals[0]) is _MISSING
    if kind == "BeginsWith":
        a = val(vals[0])
        return isinstance(a, str) and a.startswith(vals[1])
    if kind == "Contains":
        a = val(vals[0])
        return a is not _MISSING and vals[1] in a
    if kind == "Between":
        a = val(vals[0])
        return _cmp(vals[1], "<=", a) and _cmp(a, "<=", vals[2])
    if kind == "In":
        return val(vals[0]) in vals[1]
    ops = {
        "Equals": "=",
        "NotEquals": "<>",
        "LessThan": "<",
        "LessThanEquals": "<=",
        "GreaterThan": ">",
        "GreaterThanEquals": ">=",
    }
    if kind in ops:
        return _cmp(val(vals[0]), ops[kind], vals[1])
    raise NotImplementedError(f"Condition {kind} not supported by FakeTable")


# --------------------------------------------------------------------------------------------- table
class FakeTable:
    key_schema = ("PK", "SK")
    indexes = {"GSI1": ("GSI1PK", "GSI1SK"), "GSI2": ("GSI2PK", "GSI2SK")}

    def __init__(self, name: str = "af-dev-arquifusion") -> None:
        self.name = name
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.meta = _Meta(_FakeClient(self))

    # -- helpers
    def _key(self, key: dict[str, Any]) -> tuple[str, str]:
        return key["PK"], key["SK"]

    def all(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(i) for _, i in sorted(self.items.items())]

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self.items.get((pk, sk))
        return copy.deepcopy(item) if item else None

    # -- boto3 Table API
    def put_item(
        self,
        Item: dict[str, Any],
        ConditionExpression: Any = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,
        **_: Any,
    ) -> dict:  # noqa: N803
        self.calls.append(("put_item", {"Item": Item}))
        _check_no_float(Item, "Item")
        _check_no_float(ExpressionAttributeValues or {}, "ExpressionAttributeValues")
        _check_unused(ExpressionAttributeValues, ExpressionAttributeNames, ConditionExpression)
        current = self.items.get(self._key(Item), {})
        if not eval_condition(ConditionExpression, current, ExpressionAttributeNames, ExpressionAttributeValues):
            raise _client_error("ConditionalCheckFailedException", "PutItem")
        self.items[self._key(Item)] = copy.deepcopy(Item)
        return {}

    def get_item(self, Key: dict[str, Any], **_: Any) -> dict:  # noqa: N803
        item = self.items.get(self._key(Key))
        return {"Item": copy.deepcopy(item)} if item else {}

    def delete_item(
        self,
        Key: dict[str, Any],
        ConditionExpression: Any = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,
        **_: Any,
    ) -> dict:  # noqa: N803
        current = self.items.get(self._key(Key), {})
        if not eval_condition(ConditionExpression, current, ExpressionAttributeNames, ExpressionAttributeValues):
            raise _client_error("ConditionalCheckFailedException", "DeleteItem")
        self.items.pop(self._key(Key), None)
        return {}

    def update_item(
        self,
        Key: dict[str, Any],
        UpdateExpression: str | None = None,  # noqa: N803
        ConditionExpression: Any = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,  # noqa: N803
        ReturnValues: str = "NONE",
        **_: Any,
    ) -> dict:  # noqa: N803
        self.calls.append(("update_item", {"Key": Key, "UpdateExpression": UpdateExpression}))
        _check_no_float(ExpressionAttributeValues or {}, "ExpressionAttributeValues")
        _check_unused(ExpressionAttributeValues, ExpressionAttributeNames, UpdateExpression, ConditionExpression)
        existing = self.items.get(self._key(Key))
        current = copy.deepcopy(existing) if existing else dict(Key)
        if not eval_condition(ConditionExpression, existing or {}, ExpressionAttributeNames, ExpressionAttributeValues):
            raise _client_error("ConditionalCheckFailedException", "UpdateItem")
        if UpdateExpression:
            apply_update(UpdateExpression, current, ExpressionAttributeNames, ExpressionAttributeValues)
        _check_no_float(current, "Item")
        self.items[self._key(Key)] = current
        return {"Attributes": copy.deepcopy(current)} if ReturnValues in ("ALL_NEW", "UPDATED_NEW") else {}

    def query(
        self,
        KeyConditionExpression: Any,
        IndexName: str | None = None,  # noqa: N803
        FilterExpression: Any = None,
        ScanIndexForward: bool = True,  # noqa: N803
        Limit: int | None = None,
        ExclusiveStartKey: dict | None = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,
        **_: Any,
    ) -> dict:  # noqa: N803
        self.calls.append(("query", {"IndexName": IndexName}))
        pk_name, sk_name = self.indexes[IndexName] if IndexName else self.key_schema
        rows = [
            i
            for i in self.items.values()
            if pk_name in i and sk_name in i and eval_condition(KeyConditionExpression, i)
        ]
        rows.sort(key=lambda i: (i[sk_name], i["PK"], i["SK"]), reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            marker = (ExclusiveStartKey[sk_name], ExclusiveStartKey["PK"], ExclusiveStartKey["SK"])
            rows = [
                i
                for i in rows
                if ((i[sk_name], i["PK"], i["SK"]) > marker) == ScanIndexForward
                and (i[sk_name], i["PK"], i["SK"]) != marker
            ]
        last_key = None
        if Limit is not None and len(rows) > Limit:
            rows = rows[:Limit]
            last = rows[-1]
            last_key = {k: last[k] for k in {"PK", "SK", pk_name, sk_name}}
        out = [
            copy.deepcopy(i)
            for i in rows
            if eval_condition(FilterExpression, i, ExpressionAttributeNames, ExpressionAttributeValues)
        ]
        result: dict[str, Any] = {"Items": out, "Count": len(out)}
        if last_key:
            result["LastEvaluatedKey"] = last_key
        return result

    def scan(self, FilterExpression: Any = None, **_: Any) -> dict:  # noqa: N803
        out = [copy.deepcopy(i) for i in self.items.values() if eval_condition(FilterExpression, i)]
        return {"Items": out, "Count": len(out)}

    def batch_writer(self, **_: Any) -> _BatchWriter:
        return _BatchWriter(self)


class _Meta:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client


class _FakeClient:
    """Table.meta.client, which accepts resource-style values and transacts atomically."""

    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def transact_write_items(self, TransactItems: list[dict[str, Any]], **_: Any) -> dict:  # noqa: N803
        from botocore.exceptions import ClientError

        self.table.calls.append(("transact_write_items", {"count": len(TransactItems)}))

        parsed = []
        for action in TransactItems:
            ((kind, spec),) = action.items()
            assert spec.get("TableName") == self.table.name, spec.get("TableName")
            key = spec.get("Key") or {k: v for k, v in spec.get("Item", {}).items() if k in ("PK", "SK")}
            parsed.append((kind, spec, key, spec.get("ExpressionAttributeValues"), spec.get("Item")))
        reasons = []
        for _kind, spec, key, values, _ in parsed:
            existing = self.table.items.get(self.table._key(key)) or {}
            names = spec.get("ExpressionAttributeNames")
            _check_unused(values, names, spec.get("UpdateExpression"), spec.get("ConditionExpression"))
            ok = eval_condition(spec.get("ConditionExpression"), existing, names, values)
            reasons.append({"Code": "None"} if ok else {"Code": "ConditionalCheckFailed"})
        if any(r["Code"] != "None" for r in reasons):
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
                    "CancellationReasons": reasons,
                },
                "TransactWriteItems",
            )
        for kind, spec, key, values, item in parsed:
            names = spec.get("ExpressionAttributeNames")
            if kind == "Update":
                current = copy.deepcopy(self.table.items.get(self.table._key(key)) or dict(key))
                apply_update(spec["UpdateExpression"], current, names, values)
                _check_no_float(current, "Item")
                self.table.items[self.table._key(key)] = current
            elif kind == "Put":
                self.table.items[self.table._key(key)] = copy.deepcopy(item)
            elif kind == "Delete":
                self.table.items.pop(self.table._key(key), None)
        return {}


class _BatchWriter:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def __enter__(self) -> _BatchWriter:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self.table.put_item(Item=Item)

    def delete_item(self, Key: dict[str, Any]) -> None:  # noqa: N803
        self.table.delete_item(Key=Key)


# --------------------------------------------------------------------------------------------- other clients
class FakeSsm:
    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, str] = {k: v if isinstance(v, str) else json.dumps(v) for k, v in (params or {}).items()}

    def get_parameter(self, Name: str, WithDecryption: bool = False) -> dict:  # noqa: N803
        if Name not in self.params:
            raise _client_error("ParameterNotFound", "GetParameter")
        return {"Parameter": {"Name": Name, "Value": self.params[Name]}}

    def put_parameter(self, Name: str, Value: str, **_: Any) -> dict:  # noqa: N803
        self.params[Name] = Value
        return {"Version": 1}

    def json(self, name: str) -> Any:
        return json.loads(self.params[name])


class Recorder:
    """Generic fake client: records every call as (method, kwargs) and returns canned responses."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses or {}

    def __getattr__(self, method: str) -> Callable[..., Any]:
        if method.startswith("__"):
            raise AttributeError(method)

        def call(**kwargs: Any) -> Any:
            self.calls.append((method, kwargs))
            response = self.responses.get(method, {})
            return response(**kwargs) if callable(response) else copy.deepcopy(response)

        return call

    def of(self, method: str) -> list[dict[str, Any]]:
        return [kw for m, kw in self.calls if m == method]


# --------------------------------------------------------------------------------------------- fixtures data
def demo_fleet_raw() -> dict[str, Any]:
    """contracts/examples/fleet-config.json (tests only: the file is not packaged with the Lambdas)."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "fleet-config.json"
    return json.loads(path.read_text(encoding="utf-8"))
