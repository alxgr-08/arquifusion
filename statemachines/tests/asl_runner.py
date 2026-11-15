"""Minimal Amazon States Language interpreter for tests.

Runs the real statemachines/*.asl.json against Python callables, so tests check the data plumbing
(Parameters, ResultSelector, ResultPath, Choice, Map, Parallel, Catch, waitForTaskToken) without AWS.
Not supported on purpose: Retry back-off (errors are raised once), OutputPath/InputPath other than "$".
"""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Callable
from typing import Any

_PATH_PART = re.compile(r"\.([A-Za-z0-9_\-]+)|\[(\d+)\]")


_EXECUTIONS = itertools.count(1)
_ENTRIES = itertools.count(1)


def _entered_time() -> str:
    """$$.State.EnteredTime: strictly increasing, same format as Step Functions (ms precision, UTC)."""
    n = next(_ENTRIES)
    return f"2026-11-13T{n // 3600000 % 24:02d}:{n // 60000 % 60:02d}:{n // 1000 % 60:02d}.{n % 1000:03d}Z"


class Paused(Exception):
    """Raised by a wait handler to stop the execution while it 'waits' for a token."""


class StatesError(Exception):
    def __init__(self, name: str, cause: str = "") -> None:
        super().__init__(f"{name}: {cause}")
        self.name, self.cause = name, cause


def _get(data: Any, path: str) -> Any:
    if path == "$":
        return data
    if not path.startswith("$"):
        raise ValueError(f"Bad path {path}")
    cur = data
    for name, index in _PATH_PART.findall(path[1:]):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                raise StatesError("States.Runtime", f"Path {path} not found")
            cur = cur[name]
        else:
            if not isinstance(cur, list) or int(index) >= len(cur):
                raise StatesError("States.Runtime", f"Path {path} not found")
            cur = cur[int(index)]
    return cur


def _present(data: Any, path: str) -> bool:
    try:
        _get(data, path)
        return True
    except StatesError:
        return False


def _set(data: Any, path: str | None, value: Any) -> Any:
    if path is None:
        return data
    if path == "$":
        return value
    out = copy.deepcopy(data) if isinstance(data, dict) else {}
    cur = out
    parts = [n for n, _ in _PATH_PART.findall(path[1:])]
    for name in parts[:-1]:
        cur = cur.setdefault(name, {})
    cur[parts[-1]] = value
    return out


def resolve(template: Any, data: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        out = {}
        for key, value in template.items():
            if key.endswith(".$"):
                out[key[:-2]] = copy.deepcopy(_get(ctx, value[1:]) if value.startswith("$$") else _get(data, value))
            else:
                out[key] = resolve(value, data, ctx)
        return out
    if isinstance(template, list):
        return [resolve(v, data, ctx) for v in template]
    return template


def _choice_rule(rule: dict[str, Any], data: Any) -> bool:
    if "And" in rule:
        return all(_choice_rule(r, data) for r in rule["And"])
    if "Or" in rule:
        return any(_choice_rule(r, data) for r in rule["Or"])
    if "Not" in rule:
        return not _choice_rule(rule["Not"], data)
    var = rule["Variable"]
    if "IsPresent" in rule:
        return _present(data, var) == rule["IsPresent"]
    if not _present(data, var):
        return False
    value = _get(data, var)
    for op, cmp in (
        ("BooleanEquals", lambda a, b: a is b),
        ("StringEquals", lambda a, b: a == b),
        ("NumericEquals", lambda a, b: a == b),
        ("NumericLessThan", lambda a, b: a < b),
        ("NumericGreaterThanEquals", lambda a, b: a >= b),
    ):
        if op in rule:
            return cmp(value, rule[op])
        if op + "Path" in rule:
            return cmp(value, _get(data, rule[op + "Path"]))
    raise NotImplementedError(f"Choice rule {rule}")


class Runner:
    def __init__(
        self,
        definition: dict[str, Any],
        functions: dict[str, Callable[[Any], Any]],
        on_wait: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.definition = definition
        self.functions = functions
        self.on_wait = on_wait
        self.visited: list[str] = []
        self.waits: list[dict[str, Any]] = []
        self.tokens = 0

    def run(self, data: Any, execution_id: str | None = None) -> Any:
        if execution_id is None:
            execution_id = f"arn:aws:states:us-east-1:000000000000:execution:af-dev-incident:{next(_EXECUTIONS)}"
        return self._run_machine(self.definition, data, {"Execution": {"Id": execution_id}})

    def _run_machine(self, machine: dict[str, Any], data: Any, ctx: dict[str, Any]) -> Any:
        name = machine["StartAt"]
        for _ in range(1000):
            state = machine["States"][name]
            self.visited.append(name)
            ctx = {**ctx, "State": {"Name": name, "EnteredTime": _entered_time(), "RetryCount": 0}}
            try:
                data, nxt = self._step(name, state, data, ctx)
            except StatesError as err:
                handler = next(
                    (
                        c
                        for c in state.get("Catch", [])
                        if "States.ALL" in c["ErrorEquals"] or err.name in c["ErrorEquals"]
                    ),
                    None,
                )
                if handler is None:
                    raise
                data = _set(data, handler.get("ResultPath", "$"), {"Error": err.name, "Cause": err.cause})
                nxt = handler["Next"]
            if nxt is None:
                return data
            name = nxt
        raise RuntimeError("Too many transitions")

    def _invoke(self, resource: str, payload: Any) -> Any:
        fn = self.functions[resource]
        try:
            return copy.deepcopy(fn(copy.deepcopy(payload)))
        except Paused:
            raise
        except StatesError:
            raise
        except Exception as exc:  # what Lambda reports: the exception class name
            raise StatesError(type(exc).__name__, str(exc)) from exc

    def _step(self, name: str, state: dict[str, Any], data: Any, ctx: dict[str, Any]) -> tuple[Any, str | None]:
        kind = state["Type"]
        nxt = None if state.get("End") else state.get("Next")
        if kind == "Pass":
            result = resolve(state["Parameters"], data, ctx) if "Parameters" in state else state.get("Result", data)
            return _set(data, state.get("ResultPath", "$"), copy.deepcopy(result)), nxt
        if kind == "Succeed":
            return data, None
        if kind == "Fail":
            raise StatesError(state.get("Error", "States.Fail"), state.get("Cause", ""))
        if kind == "Wait":
            seconds = _get(data, state["SecondsPath"]) if "SecondsPath" in state else state.get("Seconds")
            self.waits.append({"state": name, "seconds": seconds})
            return data, nxt
        if kind == "Choice":
            for rule in state["Choices"]:
                if _choice_rule(rule, data):
                    return data, rule["Next"]
            if "Default" not in state:
                raise StatesError("States.NoChoiceMatched", name)
            return data, state["Default"]
        if kind == "Parallel":
            result = [self._run_machine(b, copy.deepcopy(data), ctx) for b in state["Branches"]]
        elif kind == "Map":
            items = _get(data, state["ItemsPath"])
            result = []
            for index, item in enumerate(items):
                item_ctx = {**ctx, "Map": {"Item": {"Value": item, "Index": index}}}
                item_input = resolve(state["ItemSelector"], data, item_ctx) if "ItemSelector" in state else item
                result.append(self._run_machine(state["ItemProcessor"], item_input, item_ctx))
        elif kind == "Task":
            result = self._task(name, state, data, ctx)
        else:
            raise NotImplementedError(kind)
        if "ResultSelector" in state:
            result = resolve(state["ResultSelector"], result, ctx)
        return _set(data, state.get("ResultPath", "$"), result), nxt

    def _task(self, name: str, state: dict[str, Any], data: Any, ctx: dict[str, Any]) -> Any:
        resource = state["Resource"]
        if resource == "arn:aws:states:::lambda:invoke.waitForTaskToken":
            self.tokens += 1
            task_ctx = {**ctx, "Task": {"Token": f"token-{self.tokens}"}}
            params = resolve(state["Parameters"], data, task_ctx)
            timeout = (
                _get(data, state["TimeoutSecondsPath"])
                if "TimeoutSecondsPath" in state
                else state.get("TimeoutSeconds")
            )
            if not isinstance(timeout, int) or timeout <= 0:
                raise StatesError("States.Runtime", f"{name}: TimeoutSecondsPath must be a positive integer")
            self._invoke(params["FunctionName"], params["Payload"])
            outcome = self.on_wait(
                {"state": name, "token": f"token-{self.tokens}", "payload": params["Payload"], "timeout": timeout}
            )
            self.waits.append({"state": name, "outcome": outcome})
            if outcome == "TIMEOUT":
                raise StatesError("States.Timeout", name)
            return outcome
        params = resolve(state.get("Parameters", {}), data, ctx) if "Parameters" in state else data
        if resource == "arn:aws:states:::lambda:invoke":
            return {"StatusCode": 200, "Payload": self._invoke(params["FunctionName"], params["Payload"])}
        return self._invoke(resource, params)


def check_definition(definition: dict[str, Any]) -> list[str]:
    """Static checks: every Next/Default/Catch target exists and every state is reachable."""
    problems: list[str] = []

    def walk(machine: dict[str, Any], where: str) -> None:
        states = machine["States"]
        if machine["StartAt"] not in states:
            problems.append(f"{where}: StartAt {machine['StartAt']} missing")
        targets = {machine["StartAt"]}
        for name, s in states.items():
            nexts = [
                s.get("Next"),
                s.get("Default"),
                *(c["Next"] for c in s.get("Choices", [])),
                *(c["Next"] for c in s.get("Catch", [])),
            ]
            for n in filter(None, nexts):
                targets.add(n)
                if n not in states:
                    problems.append(f"{where}.{name}: target {n} missing")
            terminal = s["Type"] in ("Succeed", "Fail", "Choice") or s.get("End")
            if not terminal and not s.get("Next"):
                problems.append(f"{where}.{name}: neither Next nor End")
            for i, branch in enumerate(s.get("Branches", [])):
                walk(branch, f"{where}.{name}[{i}]")
            if "ItemProcessor" in s:
                walk(s["ItemProcessor"], f"{where}.{name}.ItemProcessor")
        for name in set(states) - targets:
            problems.append(f"{where}.{name}: unreachable")

    walk(definition, "root")
    return problems
