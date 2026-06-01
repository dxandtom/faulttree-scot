"""ScotWorker — v4.0 graph executor.

The Worker is a long-lived async task inside one scot-execute run. Code-like
nodes execute in-process. Interactions that need model-like execution
(``llm_text`` or prompt-only SKILL.md) or user input flow through a worker
bridge. The production bridge satisfies model-like calls with the configured
SCOT worker API agent by default, or falls back to ACP ``_scot/*`` JSON-RPC
requests to the host when no API is configured. Local skill/tool runners
(builtin / command / python, including SKILL.md entries with ``scripts:``)
still execute in-process through the :class:`SkillInvoker` adapter.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

import asyncio
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from adapters import NodeResult
from ref_expr import RefResolver
from plan_schema import build_nodes_index
from wire_validation import (
    SkillResponseValidationError,
    validate_skill_response_data,
)
import error_kinds as EK

from working_memory import WorkingMemory
from transition_engine import TransitionEngine
from usage_meter import UsageMeter
from trace_recorder import TraceRecorder
from reflection_policy import ReflectionPolicy
from worker_bridge import (
    EVENT_STARTED,
    EVENT_STEP_DONE,
    WorkerCanceled,
    BridgeReplyError,
)


def _debug(level: str, msg: str, **fields: Any) -> None:
    mode = os.environ.get("SCOT_DEBUG")
    if not mode:
        return
    if mode.lower() in {"text", "plain"}:
        suffix = " ".join(f"{k}={v!r}" for k, v in fields.items())
        sys.stderr.write(f"[scot-execute] {level}: {msg} {suffix}\n")
    else:
        line = json.dumps(
            {"ts": time.time(), "level": level, "msg": msg, **fields},
            ensure_ascii=False,
            default=str,
        )
        sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _wrap_exception(
    exc: BaseException,
    *,
    error_kind: str = EK.EXCEPTION,
    where: str = "",
) -> NodeResult:
    """Build a failed NodeResult with full traceback preserved."""
    tb = traceback.format_exc()
    _debug(
        "error",
        "exception",
        where=where,
        error_kind=error_kind,
        error=f"{type(exc).__name__}: {exc}",
        traceback=tb,
    )
    return NodeResult(
        status="failed",
        error=str(exc),
        error_kind=error_kind,
        raw_error=tb,
    )


def _safe_nonneg_int(value: Any, *, where: str = "") -> int:
    """Coerce an untrusted harness-reported number to ``max(0, int(x))``.

    Thin wrapper around :func:`wire_validation.safe_nonneg_int` that
    adds the worker-specific SCOT_DEBUG breadcrumb. The validation
    rule itself lives in scot-shared so the reference harness driver
    can apply the same coercion without re-importing worker.
    """
    from wire_validation import safe_nonneg_int  # local: scot-shared on path
    n = safe_nonneg_int(value)
    if n == 0 and value not in (None, 0, "0", "") and os.environ.get("SCOT_DEBUG"):
        _debug(
            "warning",
            "non-int usage field coerced to 0",
            value=value,
            where=where,
        )
    return n


# Jinja2 interpolation block: ``{{ ... }}``. We only rewrite ``$ref``
# tokens *inside* these blocks so literal "$memory.X" appearing in
# surrounding prose (e.g. routine_view embedded in a system prompt) is
# left untouched.
_JINJA_VAR_BLOCK_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.DOTALL)
# Template-side $ref tokens: same shape parse_ref() accepts. Used
# inside ``{{ ... }}`` blocks where the leading ``$`` would otherwise
# be a Jinja2 syntax error.
_TEMPLATE_REF_RE = re.compile(
    r"\$(?:input|memory|output|step\.[A-Za-z0-9_\-]+)"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*"
)


def _resolve_template_refs(
    template: str,
    *,
    ref_resolver: RefResolver,
    memory: dict,
    step_results: dict,
    runtime_input: dict,
    output_buf: dict,
) -> tuple[str, dict]:
    """Pre-substitute ``$ref`` tokens inside Jinja2 ``{{ ... }}`` blocks.

    Jinja2 cannot lex ``{{ $memory.X }}`` — ``$`` is invalid. Plan
    authors expect the same ref syntax to work in ``prompt_template``
    that already works in ``inputs``. We scan each ``{{ ... }}`` block,
    resolve each ``$ref`` token inside via :class:`RefResolver`, and
    rewrite it to a unique safe identifier whose binding rides in via
    the returned extra-context dict. Literal ``$memory.X`` appearing in
    text *outside* a Jinja2 block is left intact.
    """
    extra: dict[str, Any] = {}
    counter = [0]

    def _rewrite_block(block_match: "re.Match[str]") -> str:
        inner = block_match.group(1)

        def _repl(ref_match: "re.Match[str]") -> str:
            expr = ref_match.group(0)
            var = f"_scot_ref_{counter[0]}"
            counter[0] += 1
            try:
                value = ref_resolver.resolve_value(
                    expr, memory, step_results, runtime_input, output_buf
                )
            except Exception:
                value = None
            # When a whole-dict / whole-list ref ends up in a prompt
            # (e.g. ``{{ $memory.alert_context }}`` resolving to a
            # dict), Jinja2's default repr renders it as Python literal
            # syntax with single quotes and trailing commas — useless
            # for downstream LLMs and definitely not valid JSON. Coerce
            # dict / list values to ``json.dumps`` so the rendered
            # prompt carries a real JSON block. Scalars (bool, int,
            # str, None) and nested-path leaf values are unaffected;
            # ``{{ $memory.decision.has_drop }}`` still renders the
            # bare bool.
            if isinstance(value, (dict, list)):
                try:
                    value = json.dumps(value, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass  # un-serializable → fall back to Jinja2 default
            extra[var] = value
            return var

        rewritten_inner = _TEMPLATE_REF_RE.sub(_repl, inner)
        return "{{ " + rewritten_inner + " }}"

    return _JINJA_VAR_BLOCK_RE.sub(_rewrite_block, template), extra


def _render_template(
    template: str,
    context: dict,
    *,
    ref_resolver: RefResolver | None = None,
    memory: dict | None = None,
    step_results: dict | None = None,
    runtime_input: dict | None = None,
    output_buf: dict | None = None,
) -> str:
    """Render a Jinja2 template, auto-resolving ``$ref`` tokens.

    Plan authors can write ``{{ $memory.X.field }}`` /
    ``{{ $step.<id>.field }}`` / ``{{ $input.X }}`` / ``{{ $output.X }}``
    directly in ``prompt_template`` — same syntax the plan uses in
    ``inputs``. If ``ref_resolver`` is omitted, only ``context`` keys
    are bound (legacy callers / unit tests).
    """
    extra: dict[str, Any] = {}
    if ref_resolver is not None:
        template, extra = _resolve_template_refs(
            template,
            ref_resolver=ref_resolver,
            memory=memory or {},
            step_results=step_results or {},
            runtime_input=runtime_input or {},
            output_buf=output_buf or {},
        )
    full_ctx = {**(context or {}), **extra}
    try:
        from jinja2 import Template
        return Template(template).render(**full_ctx)
    except ImportError:
        out = template
        for k, v in full_ctx.items():
            out = out.replace("{{ " + k + " }}", str(v))
            out = out.replace("{{" + k + "}}", str(v))
        return out


_COMMON_USER_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "service_name": ("服务名称", "服务名", "服务", "业务服务", "service"),
    "target_service": ("目标服务", "服务名称", "服务名", "服务"),
    "resource_id": ("资源标识", "资源id", "资源ID", "资源编号", "资源", "ciid", "vid"),
    "resource_name": ("资源名称", "资源名", "资源标识", "资源", "ci名称"),
    "resource": ("资源", "资源标识", "资源名称"),
    "time_window": ("时间窗口", "时间范围", "时间段", "窗口", "时间"),
    "start_time": ("开始时间", "起始时间", "startTime", "start"),
    "end_time": ("结束时间", "终止时间", "endTime", "end"),
    "ci_type": ("ci类型", "CI类型", "资源类型", "类型"),
    "ci_name": ("ci名称", "CI名称", "资源名称", "资源名"),
    "ci_names": ("ci名称", "CI名称", "资源名称列表", "资源名列表"),
    "ci_id": ("ci标识", "CI标识", "ciid", "资源标识"),
    "ci_ids": ("ci标识", "CI标识", "ciid列表", "资源标识列表"),
    "metric_codes": ("指标编码", "指标", "metricCodes", "metrics"),
    "metric_ids": ("指标id", "指标ID", "metricIds"),
    "tag_type": ("tag类型", "Tag类型", "tagType", "类型"),
    "vid_list": ("vid列表", "VID列表", "资源标识", "资源标识列表"),
}


def _coerce_user_answer_for_outputs(answer: Any, outputs_spec: dict) -> dict:
    """Shape a free-form ``_scot/input`` reply into declared output keys."""
    if not isinstance(outputs_spec, dict) or not outputs_spec:
        return answer if isinstance(answer, dict) else {"text": answer}
    if not isinstance(answer, dict):
        answer = {"text": answer}

    output_keys = [str(k) for k in outputs_spec.keys()]
    if all(k in answer for k in output_keys):
        return answer

    text = answer.get("text") if isinstance(answer, dict) else None
    if not isinstance(text, str) or not text.strip():
        return _bind_single_user_output(answer, output_keys)

    parsed = _parse_user_text_for_output_keys(text, output_keys)
    if not parsed and set(answer.keys()) == {"text"} and len(output_keys) == 1 \
            and "text" not in outputs_spec:
        parsed = {output_keys[0]: text}
    if not parsed:
        return _bind_single_user_output(answer, output_keys)

    merged = dict(answer)
    for key, value in parsed.items():
        merged.setdefault(key, value)
    return merged


def _bind_single_user_output(answer: dict, output_keys: list[str]) -> dict:
    if len(output_keys) == 1:
        # A common harness shape is {"value": {structured fields...}} while
        # the plan declares one aggregate output such as
        # {"value": "$memory.context"} or {"context": "$memory.context"}.
        # If the structured answer does not already expose that output key,
        # bind the whole answer to the lone declared key instead of letting
        # _write_outputs store None.
        only_key = output_keys[0]
        if only_key not in answer:
            merged = dict(answer)
            merged[only_key] = dict(answer)
            return merged
    return answer


def _parse_user_text_for_output_keys(text: str, output_keys: list[str]) -> dict:
    raw = text.strip()
    if not raw:
        return {}

    jsonish = _parse_jsonish_user_text(raw)
    if isinstance(jsonish, dict):
        return {
            key: jsonish[key]
            for key in output_keys
            if key in jsonish
        }

    pairs, unlabeled = _parse_label_value_pairs(raw)
    out: dict[str, Any] = {}
    for label, value in pairs:
        key = _match_user_label_to_output_key(label, output_keys)
        if key and key not in out:
            out[key] = value

    if out:
        return out

    if len(unlabeled) == len(output_keys):
        return dict(zip(output_keys, unlabeled))
    return {}


def _parse_jsonish_user_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _parse_label_value_pairs(raw: str) -> tuple[list[tuple[str, str]], list[str]]:
    pairs: list[tuple[str, str]] = []
    unlabeled: list[str] = []
    for part in re.split(r"[;\n；]+", raw):
        item = part.strip().strip(",，")
        if not item:
            continue
        match = re.match(r"^\s*([^:=：]+?)\s*[:=：]\s*(.+?)\s*$", item)
        if match:
            pairs.append((match.group(1).strip(), match.group(2).strip()))
        else:
            unlabeled.append(item)
    return pairs, unlabeled


def _match_user_label_to_output_key(label: str, output_keys: list[str]) -> str | None:
    normalized_label = _normalize_user_label(label)
    for key in output_keys:
        aliases = {key, key.replace("_", ""), key.replace("_", " ")}
        aliases.update(_COMMON_USER_FIELD_ALIASES.get(key, ()))
        aliases.update(_COMMON_USER_FIELD_ALIASES.get(_snake_case_key(key), ()))
        if normalized_label in {_normalize_user_label(a) for a in aliases}:
            return key
    return None


def _snake_case_key(key: str) -> str:
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return key.replace("-", "_").lower()


def _normalize_user_label(label: str) -> str:
    return re.sub(r"[\s_\-:：=,，。；;]+", "", str(label)).lower()


def _augment_result_data_for_declared_outputs(
    result_data: dict,
    outputs_spec: dict,
) -> dict:
    """Add conservative aliases for common skill result key variants."""
    if not isinstance(result_data, dict) or not isinstance(outputs_spec, dict):
        return result_data
    augmented = dict(result_data)
    present_keys = [str(k) for k in result_data.keys()]
    for key in outputs_spec.keys():
        key = str(key)
        if key in augmented:
            continue
        alias = _find_result_key_alias(key, present_keys)
        if alias is not None:
            augmented[key] = result_data[alias]
    return augmented


def _find_result_key_alias(wanted: str, present_keys: list[str]) -> str | None:
    wanted_snake = _snake_case_key(wanted)
    direct_aliases = {
        f"{wanted}_result",
        f"{wanted}_data",
        f"{wanted_snake}_result",
        f"{wanted_snake}_data",
    }
    if wanted_snake.endswith("_data"):
        direct_aliases.add("data")
    if wanted_snake.endswith("_result"):
        direct_aliases.add("result")

    for present in present_keys:
        if present in direct_aliases or _snake_case_key(present) in direct_aliases:
            return present
        present_snake = _snake_case_key(present)
        for suffix in ("_result", "_data"):
            if present_snake.endswith(suffix) and present_snake[:-len(suffix)] == wanted_snake:
                return present
    return None


@dataclass
class _State:
    """Mutable per-run execution state."""
    current: str | None
    memory: "WorkingMemory"
    step_results: dict
    retry_counts: dict
    output_buf: dict
    # v4.0 Saga compensation: stack of (succeeded_node_id, compensation_node_id)
    # in completion order. On terminal FAILED / NEEDS_REPAIR the worker
    # walks this LIFO and dispatches each compensation node before
    # returning the failure to the harness.
    compensation_stack: list = field(default_factory=list)
    # Set while running compensations so dispatched nodes don't push
    # further compensations (avoid infinite unwind loops).
    in_compensation: bool = False


@dataclass
class _NodeExecution:
    trace_entry: Any
    result: NodeResult
    resolved_inputs: dict


@dataclass
class _RunCtx:
    plan: dict
    nodes_by_id: dict
    run_id: str
    runtime_input: dict
    usage: "UsageMeter"
    trace: "TraceRecorder"
    # Live execution state reference. Populated by _run_plan; used by
    # llm_text / ask_user dispatch to feed memory / step_results /
    # output_buf into _render_template so ``$ref`` tokens inside
    # ``prompt_template`` resolve like they do in ``inputs``.
    state: Any = None


@dataclass
class ExecResult:
    """Final result of a run. External interactions happen before this."""
    status: str                     # SUCCESS | FAILED | NEEDS_REPAIR
    final_answer: Any = None
    working_memory: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    output_buffer: dict = field(default_factory=dict)
    repair_request: dict | None = None
    budget_usage: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "final_answer": self.final_answer,
            "working_memory": self.working_memory,
            "output_buffer": self.output_buffer,
            "trace": self.trace,
            "repair_request": self.repair_request,
            "budget_usage": self.budget_usage,
            "error": self.error,
        }


class _BranchFailed(Exception):
    """Internal signal that a parallel branch reported a fatal NodeResult."""

    def __init__(self, branch_id: str, result: NodeResult,
                 failed_node_id: str | None) -> None:
        super().__init__(f"branch '{branch_id}' failed at '{failed_node_id}'")
        self.branch_id = branch_id
        self.result = result
        self.failed_node_id = failed_node_id


class ScotWorker:
    """Lightweight deterministic executor for SCOT Plan JSON (v4.0)."""

    def __init__(
        self,
        invoker=None,
        event_bridge=None,
        dry_run: bool = False,
    ) -> None:
        self.invoker = invoker
        self.event_bridge = event_bridge
        self.dry_run = dry_run
        self.ref_resolver = RefResolver()
        self.transition_engine = TransitionEngine()
        # Reflection rebuilt per-run() so plan.meta.reflection takes effect.
        self.reflection = ReflectionPolicy()

    async def run(
        self,
        plan: dict,
        runtime_context: dict | None = None,
        run_id: str | None = None,
    ) -> ExecResult:
        runtime_context = runtime_context or {}
        runtime_input = self._build_runtime_input(runtime_context)
        run_id = run_id or f"run_{uuid4().hex[:10]}"
        self._configure_reflection_for_plan(plan)

        nodes_by_id = build_nodes_index(plan.get("nodes"))
        if plan.get("entry") not in nodes_by_id:
            return ExecResult(
                status="FAILED",
                error=f"entry '{plan.get('entry')}' missing",
                budget_usage=UsageMeter().snapshot(),
            )

        usage = UsageMeter()
        trace = TraceRecorder()
        state = _State(
            current=plan["entry"],
            memory=WorkingMemory(
                memory_init=plan.get("memory_init", {}),
                initial_memory=runtime_context.get("initial_memory", {}),
                runtime_input=runtime_input,
            ),
            step_results={},
            retry_counts={},
            output_buf={},
        )
        ctx = _RunCtx(
            plan=plan, nodes_by_id=nodes_by_id, run_id=run_id,
            runtime_input=runtime_input, usage=usage, trace=trace,
            state=state,
        )

        if self.event_bridge is not None:
            self.event_bridge.notify(
                EVENT_STARTED, plan_id=plan.get("plan_id"), run_id=run_id,
            )

        return await self._run_main_loop(state, ctx)

    # -------- run helpers --------------------------------------------------

    def _configure_reflection_for_plan(self, plan: dict) -> None:
        plan_reflection = (plan.get("meta") or {}).get("reflection") or {}
        self.reflection = ReflectionPolicy(plan_reflection=plan_reflection)

    async def _run_main_loop(self, state: _State, ctx: _RunCtx) -> ExecResult:
        while state.current is not None:
            node = ctx.nodes_by_id.get(state.current)
            if node is None:
                return await self._exec_failed(state, ctx, f"node '{state.current}' not found")

            transition = node.get("transition") or {}
            if transition.get("parallel_after"):
                next_or_pause = await self._dispatch_parallel(node, state, ctx)
                if isinstance(next_or_pause, ExecResult):
                    return next_or_pause
                state.current = next_or_pause
                continue

            if node.get("kind") == "for_each":
                outcome = await self._execute_for_each(node, state, ctx)
            else:
                outcome = await self._execute_node(node, state, ctx)
            if outcome.result.status == "success":
                self._advance_after_success(node, outcome.result, state, ctx,
                                             entry=outcome.trace_entry)
                continue
            if (outcome.result.error_kind or "").lower() == "canceled":
                # Cancellation is run-terminal — do not run reflection.
                # The host explicitly asked us to stop, so honor that;
                # _exec_failed still runs Saga compensations on the way
                # out for best-effort rollback of side effects already
                # committed.
                return await self._exec_failed(
                    state, ctx,
                    outcome.result.error or "canceled by host",
                )
            decision = await self._apply_reflection(
                node, outcome.result, outcome.trace_entry, state, ctx
            )
            if decision is not None:
                return decision

        return self._exec_success(state, ctx)

    async def _execute_node(
        self, node: dict, state: _State, ctx: _RunCtx
    ) -> _NodeExecution:
        attempt = state.retry_counts.get(node["id"], 0) + 1
        trace_ctx = ctx.trace.start(node, attempt=attempt)
        ctx.usage.record_node()
        resolved_inputs = self.ref_resolver.resolve(
            node.get("inputs", {}),
            state.memory.as_dict(),
            state.step_results,
            ctx.runtime_input,
            state.output_buf,
        )
        if self.dry_run:
            result = NodeResult(status="success", data={"_dry_run": True})
        else:
            try:
                result = await self._dispatch(node, resolved_inputs, ctx)
            except WorkerCanceled as exc:
                # Host sent ``$/cancelRequest`` (or the bridge tore down
                # mid-request). Run-terminal in the sense that the
                # worker must NOT loop back through reflection (no
                # retry / fallback / NEEDS_REPAIR). Tag the NodeResult
                # with error_kind="canceled" so the main loop / branch
                # chain short-circuit to a FAILED ExecResult
                # immediately. Compensations DO still run on the way
                # out (Saga rollback is best-effort) — see
                # references/acp_protocol.md for the documented
                # cancellation semantic.
                result = NodeResult(
                    status="failed",
                    error=f"worker canceled: {exc}",
                    error_kind=EK.CANCELED,
                )
            except BridgeReplyError as exc:
                result = NodeResult(
                    status="failed",
                    error=str(exc),
                    error_kind=exc.error_kind or EK.HARNESS,
                )
        entry = ctx.trace.finish(trace_ctx, resolved_inputs, result)
        if self.event_bridge is not None:
            self.event_bridge.notify(
                EVENT_STEP_DONE, node_id=node["id"], status=result.status,
                outputs=result.data if isinstance(result.data, dict) else {"value": result.data},
            )
        return _NodeExecution(
            trace_entry=entry, result=result, resolved_inputs=resolved_inputs
        )

    def _advance_after_success(
        self, node: dict, result: NodeResult, state: _State, ctx: _RunCtx,
        entry=None,
    ) -> None:
        missing = self._write_outputs(
            node, result.data, state.memory, state.output_buf)
        if missing and entry is not None:
            keys_present = (
                sorted(result.data.keys())
                if isinstance(result.data, dict) else []
            )
            entry.notes.append(
                f"outputs_missing_keys: declared {sorted(missing)} not in "
                f"result_data (got keys: {keys_present}); wrote None"
            )
        state.step_results[node["id"]] = (
            result.data if isinstance(result.data, dict) else {"value": result.data}
        )
        state.retry_counts.pop(node["id"], None)
        # Saga compensation: remember nodes whose effect we know how to
        # undo. Skip while we're already running compensations to avoid
        # nested unwind loops. Skip ``noop`` since it has no effect.
        if not state.in_compensation:
            comp_id = (node.get("policy") or {}).get("compensation_node")
            if comp_id and node.get("kind") != "noop":
                state.compensation_stack.append((node["id"], comp_id))
        state.current = self.transition_engine.next_node(
            node, success=True,
            memory=state.memory.as_dict(),
            step_results=state.step_results,
            runtime_input=ctx.runtime_input,
        )

    async def _apply_reflection(
        self, node: dict, result: NodeResult, entry,
        state: _State, ctx: _RunCtx,
    ) -> ExecResult | None:
        action, reason = await self.reflection.evaluate(
            node, result, state.retry_counts.get(node["id"], 0), ctx.usage
        )
        entry.notes.append(f"reflection: {action} — {reason}")

        if action == "retry":
            state.retry_counts[node["id"]] = state.retry_counts.get(node["id"], 0) + 1
            return None

        if action == "fallback":
            fallback = self.transition_engine.next_node(
                node, success=False,
                memory=state.memory.as_dict(),
                step_results=state.step_results,
                runtime_input=ctx.runtime_input,
            )
            if fallback:
                state.current = fallback
                return None
            action = "needs_repair"

        if action == "needs_repair":
            await self._run_compensations(state, ctx, reason="needs_repair")
            return ExecResult(
                status="NEEDS_REPAIR",
                repair_request=self._build_repair_request(
                    ctx.plan, node, result, ctx.trace, state.memory,
                    ctx.runtime_input),
                working_memory=state.memory.snapshot(),
                output_buffer=state.output_buf,
                trace=ctx.trace.export(),
                budget_usage=ctx.usage.snapshot(),
                error=result.error,
            )

        await self._run_compensations(state, ctx, reason="cancel")
        return ExecResult(
            status="FAILED",
            error=result.error or "canceled by reflection",
            working_memory=state.memory.snapshot(),
            output_buffer=state.output_buf,
            trace=ctx.trace.export(),
            budget_usage=ctx.usage.snapshot(),
        )

    async def _exec_failed(self, state: _State, ctx: _RunCtx, error: str) -> ExecResult:
        # Run any pending compensations before surfacing the failure so
        # half-completed Saga state is unwound. Best-effort: a failing
        # compensation is logged in trace but doesn't override the
        # original error message.
        await self._run_compensations(state, ctx, reason="exec_failed")
        return ExecResult(
            status="FAILED",
            error=error,
            working_memory=state.memory.snapshot(),
            output_buffer=state.output_buf,
            trace=ctx.trace.export(),
            budget_usage=ctx.usage.snapshot(),
        )

    def _exec_success(self, state: _State, ctx: _RunCtx) -> ExecResult:
        final = state.output_buf.get("final_answer")
        if final is None and "final_answer" in state.memory:
            final = state.memory.get("final_answer")
        if final is None:
            final = _derive_terminal_answer(ctx.trace.export())
        return ExecResult(
            status="SUCCESS",
            final_answer=final,
            working_memory=state.memory.snapshot(),
            output_buffer=state.output_buf,
            trace=ctx.trace.export(),
            budget_usage=ctx.usage.snapshot(),
        )

    # -------- parallel dispatch -------------------------------------------

    async def _dispatch_parallel(
        self, node: dict, state: _State, ctx: _RunCtx,
    ) -> str | ExecResult | None:
        """Spawn each branch chain concurrently; advance to join_at on
        success, or build a NEEDS_REPAIR / FAILED ExecResult on any branch
        failure (per ``policy.parallel_fail``)."""
        transition = node.get("transition") or {}
        branches: list[str] = list(transition.get("parallel_after") or [])
        join_at = transition.get("join_at")
        fail_mode = ((node.get("policy") or {}).get("parallel_fail")
                     or "fail_fast").lower()

        # Surface barrier in trace so the timeline shows the fan-out.
        trace_ctx = ctx.trace.start(node, attempt=1)
        ctx.usage.record_node()
        if self.event_bridge is not None:
            self.event_bridge.notify(
                EVENT_STEP_DONE, node_id=node["id"], status="parallel_started",
                outputs={"branches": branches, "join_at": join_at,
                         "parallel_fail": fail_mode},
            )

        async def run_branch(branch_id: str) -> None:
            await self._run_branch_chain(branch_id, join_at, state, ctx)

        try:
            if fail_mode == "best_effort":
                results = await asyncio.gather(
                    *(run_branch(b) for b in branches), return_exceptions=True
                )
                for r in results:
                    if isinstance(r, _BranchFailed):
                        ctx.trace.finish(
                            trace_ctx, {"branches": branches},
                            NodeResult(
                                status="failed",
                                error=f"branch '{r.branch_id}' failed at "
                                      f"'{r.failed_node_id}': {r.result.error}",
                                error_kind=r.result.error_kind or EK.BRANCH,
                            ),
                        )
                        return await self._handle_branch_failure(node, r, state, ctx)
                    if isinstance(r, BaseException):
                        raise r
            else:  # fail_fast
                await asyncio.gather(*(run_branch(b) for b in branches))
        except _BranchFailed as bf:
            ctx.trace.finish(
                trace_ctx, {"branches": branches},
                NodeResult(
                    status="failed",
                    error=f"branch '{bf.branch_id}' failed at "
                          f"'{bf.failed_node_id}': {bf.result.error}",
                    error_kind=bf.result.error_kind or EK.BRANCH,
                ),
            )
            return await self._handle_branch_failure(node, bf, state, ctx)

        ctx.trace.finish(
            trace_ctx, {"branches": branches},
            NodeResult(status="success", data={"join_at": join_at}),
        )
        state.step_results[node["id"]] = {"join_at": join_at, "branches": branches}
        return join_at

    async def _run_branch_chain(
        self, branch_id: str, join_at: str | None,
        state: _State, ctx: _RunCtx,
    ) -> None:
        """Walk a parallel branch from `branch_id` until `join_at`. Raises
        :class:`_BranchFailed` if any node in the chain fails fatally."""
        current = branch_id
        last_failed_node: str | None = None
        while current and current != join_at:
            node = ctx.nodes_by_id.get(current)
            if node is None:
                raise _BranchFailed(
                    branch_id,
                    NodeResult(status="failed",
                               error=f"node '{current}' not found in branch",
                               error_kind=EK.SCHEMA),
                    current,
                )
            outcome = await self._execute_node(node, state, ctx)
            if outcome.result.status == "success":
                self._advance_after_success(
                    node, outcome.result, state, ctx, entry=outcome.trace_entry
                )
                current = state.current
                state.current = None  # branch's "current" is local
                continue
            last_failed_node = current
            if (outcome.result.error_kind or "").lower() == "canceled":
                # Cancellation is run-terminal — short-circuit the
                # branch without going through reflection, mirroring
                # the main loop.
                raise _BranchFailed(branch_id, outcome.result, last_failed_node)
            decision = await self._apply_reflection(
                node, outcome.result, outcome.trace_entry, state, ctx
            )
            if decision is not None:
                # retry exhausted / needs_repair / cancel →
                # propagate as a branch failure
                raise _BranchFailed(branch_id, outcome.result, last_failed_node)
            # decision is None: either retry (state.current untouched) or
            # fallback (state.current pointed at the next_on_fail target by
            # _apply_reflection). The retry path keeps `current` the same so
            # the loop re-executes the failing node; the fallback path needs
            # `current` to jump to the new target. Mirror the success branch's
            # discipline of clearing state.current after consuming it.
            if state.current is not None and state.current != current:
                current = state.current
                state.current = None
            # otherwise: retry — loop body re-executes the same node id

    async def _handle_branch_failure(
        self, fan_out_node: dict, failure: _BranchFailed,
        state: _State, ctx: _RunCtx,
    ) -> ExecResult:
        # Cancellation inside a parallel branch is still run-terminal —
        # mirror _run_main_loop's behaviour: do NOT build a
        # repair_request, do NOT escalate to NEEDS_REPAIR. The host
        # explicitly asked us to stop, so the right answer is FAILED.
        # Compensations still run (Saga rollback is best-effort
        # cancel-and-unwind, the documented cancellation semantic in
        # references/acp_protocol.md).
        is_canceled = (failure.result.error_kind or "").lower() == "canceled"
        await self._run_compensations(
            state, ctx,
            reason="parallel_branch_canceled" if is_canceled
                   else "parallel_branch_failed",
        )
        if is_canceled:
            return ExecResult(
                status="FAILED",
                error=(
                    f"parallel branch '{failure.branch_id}' canceled at "
                    f"'{failure.failed_node_id}': {failure.result.error}"
                ),
                working_memory=state.memory.snapshot(),
                output_buffer=state.output_buf,
                trace=ctx.trace.export(),
                budget_usage=ctx.usage.snapshot(),
            )
        return ExecResult(
            status="NEEDS_REPAIR",
            repair_request=self._build_repair_request(
                ctx.plan, fan_out_node, failure.result, ctx.trace,
                state.memory, ctx.runtime_input,
            ),
            working_memory=state.memory.snapshot(),
            output_buffer=state.output_buf,
            trace=ctx.trace.export(),
            budget_usage=ctx.usage.snapshot(),
            error=(
                f"parallel branch '{failure.branch_id}' failed at "
                f"'{failure.failed_node_id}': {failure.result.error}"
            ),
        )

    # -------- compensation (Saga unwind) ---------------------------------

    async def _run_compensations(
        self, state: _State, ctx: _RunCtx, *, reason: str,
    ) -> None:
        """Walk the compensation stack LIFO and dispatch each entry.

        Best-effort: a failing compensation is recorded in the trace
        (``notes`` of its trace entry) but does not abort the unwind —
        we keep popping so partial Saga state still gets undone as much
        as possible. The caller-provided original failure is what the
        harness ultimately sees in RESULT.
        """
        if not state.compensation_stack or state.in_compensation:
            return
        state.in_compensation = True
        try:
            while state.compensation_stack:
                origin_id, comp_id = state.compensation_stack.pop()
                comp_node = ctx.nodes_by_id.get(comp_id)
                if comp_node is None:
                    if self.event_bridge is not None:
                        self.event_bridge.notify(
                            EVENT_STEP_DONE,
                            node_id=f"_compensate:{comp_id}",
                            status="skipped_missing_node",
                            outputs={"origin": origin_id, "reason": reason},
                        )
                    continue
                # Dispatch the compensation node like any other. We don't
                # recurse on its compensation_node (in_compensation guard
                # in _advance_after_success skips the push) and we don't
                # apply reflection on failure — just record + move on.
                outcome = await self._execute_node(comp_node, state, ctx)
                outcome.trace_entry.notes.append(
                    f"compensation for '{origin_id}' "
                    f"(reason={reason}); status={outcome.result.status}"
                )
                if outcome.result.status == "success":
                    self._write_outputs(
                        comp_node, outcome.result.data,
                        state.memory, state.output_buf,
                    )
                    state.step_results[comp_node["id"]] = (
                        outcome.result.data
                        if isinstance(outcome.result.data, dict)
                        else {"value": outcome.result.data}
                    )
        finally:
            state.in_compensation = False

    # -------- dispatch -----------------------------------------------------

    def _template_state_kwargs(self, ctx: _RunCtx | None) -> dict:
        """Build the ``ref_resolver`` + state dicts for ``_render_template``.

        Returns an empty dict for callers without a live ctx (early
        unit tests). When a real run is in progress, this threads
        the resolver and live memory / step_results / output_buf
        through so ``$ref`` tokens in ``prompt_template`` resolve.
        """
        if ctx is None or ctx.state is None:
            return {}
        state = ctx.state
        return {
            "ref_resolver": self.ref_resolver,
            "memory": state.memory.as_dict(),
            "step_results": state.step_results,
            "runtime_input": ctx.runtime_input,
            "output_buf": state.output_buf,
        }

    async def _dispatch(self, node: dict, resolved_inputs: dict,
                          ctx: _RunCtx) -> NodeResult:
        kind = node.get("kind")

        if kind == "noop":
            return NodeResult(status="success", data={})

        if kind == "ask_user":
            return await self._dispatch_ask_user(node, resolved_inputs, ctx)

        if kind == "llm_text":
            return await self._dispatch_llm_text(node, resolved_inputs, ctx)

        if kind == "script":
            return await self._dispatch_script(node, resolved_inputs)

        if kind == "agent_skill":
            return await self._dispatch_agent_skill(
                node, resolved_inputs, ctx
            )

        # for_each is dispatched directly from _run_main_loop because it
        # needs the live state (memory + step_results) to bind iteration
        # vars; reaching it here means the main loop missed it.
        if kind == "for_each":
            return NodeResult(
                status="failed",
                error="for_each must be dispatched from the main loop",
                error_kind=EK.EXCEPTION,
            )

        return NodeResult(status="failed", error=f"unknown kind: {kind}",
                          error_kind=EK.SCHEMA)

    async def _dispatch_ask_user(
        self, node: dict, resolved_inputs: dict, ctx: _RunCtx | None = None,
    ) -> NodeResult:
        prompt = node.get("prompt_template") or node.get("purpose") \
            or "Please provide input."
        try:
            prompt = _render_template(
                prompt, resolved_inputs,
                **self._template_state_kwargs(ctx),
            )
        except Exception:
            pass
        if self.event_bridge is None:
            return NodeResult(
                status="failed",
                error="ask_user requires an event_bridge but none was attached",
                error_kind=EK.NOT_FOUND,
            )
        reply = await self.event_bridge.request_input(
            node_id=node["id"], node_name=node.get("name", ""),
            prompt=prompt, outputs_spec=node.get("outputs") or {},
        )
        # ``_scot/input`` reply shape: {"value": ...} where value is a
        # dict or a plain scalar. We accept ``data`` / ``text`` as
        # fallback keys for hosts that confuse the reply shape.
        value = reply.get("value", reply.get("data") or reply.get("text"))
        coerced = _coerce_user_answer_for_outputs(value, node.get("outputs") or {})
        return NodeResult(status="success", data=coerced)

    async def _dispatch_llm_text(
        self, node: dict, resolved_inputs: dict, ctx: _RunCtx,
    ) -> NodeResult:
        if self.event_bridge is None:
            return NodeResult(
                status="failed",
                error="llm_text requires an event_bridge but none was attached",
                error_kind=EK.NOT_FOUND,
            )
        template = _default_llm_template(node, resolved_inputs)
        try:
            user_prompt = _render_template(
                template, resolved_inputs,
                **self._template_state_kwargs(ctx),
            )
        except Exception as e:
            return _wrap_exception(
                e, where=f"llm_text/{node.get('id')}/render_template"
            )
        timeout = (node.get("policy") or {}).get("timeout_ms")
        max_attempts = max(1, 1 + _safe_nonneg_int(
            (node.get("policy") or {}).get("max_retry"),
            where=f"llm_text/{node.get('id')}/policy.max_retry",
        ))
        total_tokens = 0
        last_text = ""
        last_data: dict[str, Any] = {}
        validation_errors: list[str] = []
        response_schema = node.get("response_schema") or node.get("output_schema") or {}

        for attempt_idx in range(max_attempts):
            effective_prompt = user_prompt
            if validation_errors:
                effective_prompt = (
                    user_prompt
                    + "\n\nPrevious response did not match the declared output "
                    + "contract: "
                    + "; ".join(validation_errors[-3:])
                    + ". Return ONLY a valid JSON object matching the declared "
                    + f"keys: {sorted((node.get('outputs') or {}).keys())}."
                )
            try:
                reply = await self._with_timeout(
                    self.event_bridge.request_llm(
                        node_id=node["id"], node_name=node.get("name", ""),
                        system_prompt="You are a helpful assistant.",
                        user_prompt=effective_prompt,
                        model_hint=node.get("model_hint", "fast"),
                        outputs_spec=node.get("outputs") or {},
                        response_schema=response_schema
                        if isinstance(response_schema, dict) else {},
                    ),
                    timeout_ms=timeout,
                )
            except asyncio.TimeoutError:
                return NodeResult(status="failed", error="llm_text timed out",
                                  error_kind=EK.TIMEOUT)
            ctx.usage.record_llm_call()
            tokens = _safe_nonneg_int(reply.get("tokens_used"),
                                        where=f"llm_text/{node.get('id')}")
            total_tokens += tokens
            ctx.usage.record_tokens(tokens)
            last_text = reply.get("text", "")
            last_data = _coerce_llm_text_outputs(node, last_text)
            validation_errors = _validate_llm_text_outputs(node, last_data)
            if not validation_errors:
                return NodeResult(
                    status="success", data=last_data,
                    tokens_used=total_tokens,
                    llm_calls=attempt_idx + 1, raw=last_text,
                )

        return NodeResult(
            status="failed",
            data=last_data,
            error=(
                "llm_text response did not match declared outputs: "
                + "; ".join(validation_errors)
            ),
            error_kind=EK.SCHEMA,
            tokens_used=total_tokens,
            llm_calls=max_attempts,
            raw=last_text,
        )

    async def _dispatch_script(
        self, node: dict, resolved_inputs: dict,
    ) -> NodeResult:
        """v4.0 ``script``: local-runner-backed capability.

        Authoring contract: the catalog entry MUST have a local runner
        (builtin / command / python). If the invoker hints ``waiting_skill``
        (i.e. the catalog entry is actually prompt-only), that's a plan
        / catalog mismatch — surface as a schema error so the planner can
        switch the node to ``agent_skill`` instead.
        """
        if self.invoker is None:
            return NodeResult(status="failed", error="no SkillInvoker",
                              error_kind=EK.NOT_FOUND)
        name = node.get("name", "")
        timeout = (node.get("policy") or {}).get("timeout_ms")
        try:
            result = await self._with_timeout(
                self.invoker.invoke(name, resolved_inputs),
                timeout_ms=timeout,
            )
        except asyncio.TimeoutError:
            return NodeResult(status="failed", error="script timed out",
                              error_kind=EK.TIMEOUT)
        except Exception as e:
            return _wrap_exception(
                e, where=f"script/{node.get('id')}/invoker.invoke({name})"
            )
        if result.status == "waiting_skill":
            return NodeResult(
                status="failed",
                error=(
                    f"node '{node.get('id')}' has kind=script but catalog "
                    f"entry '{name}' is prompt-only — change the node to "
                    "kind=agent_skill, or register a local runner for the "
                    "catalog entry"
                ),
                error_kind=EK.SCHEMA,
            )
        return result

    async def _dispatch_agent_skill(
        self, node: dict, resolved_inputs: dict, ctx: _RunCtx,
    ) -> NodeResult:
        """v4.0 ``agent_skill``: prompt-only catalog skill.

        Goes through the bridge's ``request_skill``. We still call the invoker
        first to pick up the catalog metadata (skill_md_path, schemas) needed
        by the worker API agent or the outer-harness fallback; if the invoker
        can run it locally that's a plan / catalog mismatch.
        """
        if self.invoker is None:
            return NodeResult(status="failed", error="no SkillInvoker",
                              error_kind=EK.NOT_FOUND)
        name = node.get("name", "")
        try:
            preview = await self.invoker.invoke(name, resolved_inputs)
        except Exception as e:
            return _wrap_exception(
                e, where=f"agent_skill/{node.get('id')}/invoker.invoke({name})"
            )
        if preview.status != "waiting_skill":
            return NodeResult(
                status="failed",
                error=(
                    f"node '{node.get('id')}' has kind=agent_skill but "
                    f"catalog entry '{name}' is locally executable — change "
                    "the node to kind=script, or register the entry as "
                    "prompt-only"
                ),
                error_kind=EK.SCHEMA,
            )
        result = await self._emit_skill_request(node, name, resolved_inputs,
                                                  preview)
        # The skill reply may declare its own llm_calls / tokens_used (the
        # worker agent or host might have spawned a multi-step sub-skill).
        # Mirror llm_text's accounting: charge at least one call so the skill
        # node shows up in the usage snapshot, and pass through reported
        # tokens after coercion.
        reported_calls = _safe_nonneg_int(
            getattr(result, "llm_calls", 0),
            where=f"agent_skill/{node.get('id')}/llm_calls",
        )
        for _ in range(max(1, reported_calls)):
            ctx.usage.record_llm_call()
        ctx.usage.record_tokens(_safe_nonneg_int(
            getattr(result, "tokens_used", 0),
            where=f"agent_skill/{node.get('id')}/tokens_used",
        ))
        return result

    async def _emit_skill_request(
        self, node: dict, name: str, resolved_inputs: dict,
        invoker_preview: NodeResult,
    ) -> NodeResult:
        if self.event_bridge is None:
            return NodeResult(
                status="failed",
                error=(
                    f"skill '{name}' is prompt-only and needs a worker API "
                    "agent or outer harness, but no event_bridge is attached"
                ),
                error_kind=EK.NOT_FOUND,
            )
        expected_keys = list((node.get("outputs") or {}).keys())
        output_schema = (invoker_preview.data or {}).get("output_schema")
        reply = await self.event_bridge.request_skill(
            node_id=node["id"], node_name=name, skill_name=name,
            purpose=node.get("purpose", ""),
            inputs=resolved_inputs,
            expected_keys=expected_keys,
            skill_md_path=(invoker_preview.data or {}).get("skill_md_path"),
            input_schema=(invoker_preview.data or {}).get("input_schema"),
            output_schema=output_schema,
        )
        raw_data = reply.get("data")
        # Worker-side bottom-line schema check: a prompt-only skill that
        # returns the wrong shape used to silently flow into _write_outputs
        # and write None to $memory. With validate_skill_response_data we
        # surface this as a schema-class node failure that reflection /
        # NEEDS_REPAIR can route. The reference harness driver applies
        # the same rules pre-reply for faster feedback, but this worker
        # check is the canonical line of defense — never trust the
        # harness reply unverified.
        try:
            data = validate_skill_response_data(
                data=raw_data,
                expected_keys=expected_keys,
                output_schema=output_schema if isinstance(
                    output_schema, dict
                ) else None,
                where=f"agent_skill/{node['id']}/skill={name!r}",
            )
        except SkillResponseValidationError as exc:
            return NodeResult(
                status="failed",
                error=str(exc),
                error_kind=EK.SCHEMA,
                tokens_used=_safe_nonneg_int(
                    reply.get("tokens_used"),
                    where=f"agent_skill/{node['id']}/reply.tokens_used",
                ),
                llm_calls=_safe_nonneg_int(
                    reply.get("llm_calls"),
                    where=f"agent_skill/{node['id']}/reply.llm_calls",
                ),
                raw={"code": exc.code, "details": exc.details},
            )
        return NodeResult(
            status="success", data=data,
            tokens_used=_safe_nonneg_int(
                reply.get("tokens_used"),
                where=f"agent_skill/{node['id']}/reply.tokens_used",
            ),
            llm_calls=_safe_nonneg_int(
                reply.get("llm_calls"),
                where=f"agent_skill/{node['id']}/reply.llm_calls",
            ),
        )

    async def _execute_for_each(
        self, node: dict, state: _State, ctx: _RunCtx,
    ) -> _NodeExecution:
        """Wrap _run_for_each with the same trace + budget bookkeeping
        that ``_execute_node`` provides for non-iterative kinds."""
        attempt = state.retry_counts.get(node["id"], 0) + 1
        trace_ctx = ctx.trace.start(node, attempt=attempt)
        ctx.usage.record_node()
        try:
            result = await self._run_for_each(node, state, ctx)
        except Exception as e:
            result = _wrap_exception(
                e, where=f"for_each/{node.get('id')}"
            )
        # for_each has no "resolved_inputs" of its own — the spec lives
        # under node.for_each and the bindings are per-iteration. Pass
        # an empty dict to keep _NodeExecution shape stable.
        entry = ctx.trace.finish(trace_ctx, {}, result)
        if self.event_bridge is not None:
            self.event_bridge.notify(
                EVENT_STEP_DONE, node_id=node["id"], status=result.status,
                outputs={
                    "iterations": len((result.data or {}).get("iterations", [])),
                    "failed_indices": (result.data or {}).get("failed_indices", []),
                },
            )
        return _NodeExecution(trace_entry=entry, result=result,
                               resolved_inputs={})

    async def _run_for_each(
        self, node: dict, state: _State, ctx: _RunCtx,
    ) -> NodeResult:
        spec = node.get("for_each") or {}
        items_ref = str(spec.get("items") or "").strip()
        as_var = str(spec.get("as_var") or spec.get("as") or "item").strip()
        template = node.get("template") or {}

        items = self.ref_resolver.resolve_value(
            items_ref,
            memory=state.memory.as_dict(),
            step_results=state.step_results,
            runtime_input=ctx.runtime_input,
            output_buf=state.output_buf,
        )
        if not isinstance(items, list):
            return NodeResult(
                status="failed",
                error=(
                    f"for_each.items={items_ref!r} did not resolve to a "
                    f"list (got {type(items).__name__})"
                ),
                error_kind=EK.SCHEMA,
            )

        iterations: list[Any] = []
        failed_indices: list[int] = []
        errors: list[str] = []
        for i, item in enumerate(items):
            # Each iteration executes a template node, so it should count
            # toward max_plan_steps just like a top-level node would. The
            # outer for_each wrapper already recorded one step in
            # _execute_for_each; per-iteration accounting reports the
            # real fan-out cost (no enforcement — the harness can compare
            # against plan.meta.budget after the run).
            ctx.usage.record_node()

            iter_runtime_input = dict(ctx.runtime_input)
            iter_runtime_input[as_var] = item
            iter_runtime_input[f"{as_var}_index"] = i

            iter_node = dict(template)
            iter_node["id"] = f"{node['id']}:iter:{i}"
            iter_node.setdefault("name", template.get("name", ""))
            iter_node.setdefault("purpose",
                                  f"{node.get('purpose', '')} [iter {i}]")
            # Resolve inputs against the augmented runtime_input scope so
            # ``$input.<as_var>`` picks up the loop binding.
            iter_inputs = self.ref_resolver.resolve(
                iter_node.get("inputs", {}) or {},
                memory=state.memory.as_dict(),
                step_results=state.step_results,
                runtime_input=iter_runtime_input,
                output_buf=state.output_buf,
            )
            iter_result = await self._dispatch(
                iter_node, iter_inputs,
                _RunCtx(plan=ctx.plan, nodes_by_id=ctx.nodes_by_id,
                         run_id=ctx.run_id, runtime_input=iter_runtime_input,
                         usage=ctx.usage, trace=ctx.trace, state=state),
            )
            if iter_result.status == "success":
                iterations.append(iter_result.data)
            else:
                iterations.append({"_error": iter_result.error})
                failed_indices.append(i)
                errors.append(
                    f"iter[{i}] {as_var}={item!r}: "
                    f"{iter_result.error or 'failed'}"
                )

        data = {
            "iterations": iterations,
            "count": len(items),
            "failed_indices": failed_indices,
        }
        if failed_indices:
            return NodeResult(
                status="failed",
                error=f"{len(failed_indices)}/{len(items)} iterations failed: "
                      + "; ".join(errors[:3]),
                error_kind=EK.TRANSIENT,
                data=data,
            )
        return NodeResult(status="success", data=data)

    async def _with_timeout(self, coro, timeout_ms):
        if timeout_ms and timeout_ms > 0:
            return await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
        return await coro

    # -------- helpers ------------------------------------------------------

    def _write_outputs(
        self, node: dict, result_data: Any, memory: WorkingMemory,
        output_buf: dict,
    ) -> list[str]:
        outputs_spec = node.get("outputs") or {}
        if not outputs_spec:
            return []
        if not isinstance(result_data, dict):
            result_data = {"value": result_data}
        else:
            result_data = _augment_result_data_for_declared_outputs(
                result_data, outputs_spec)
        missing: list[str] = []
        self.ref_resolver.write(outputs_spec, result_data,
                                memory.as_dict(), output_buf,
                                missing=missing)
        return missing

    def _build_runtime_input(self, runtime_context: dict) -> dict:
        ri = dict(runtime_context.get("user_inputs") or {})
        if "user_request" in runtime_context:
            ri["user_request"] = runtime_context["user_request"]
        for k in ("user_request", "user_inputs", "initial_memory"):
            if k in runtime_context:
                ri.setdefault(k, runtime_context[k])
        return ri

    def _build_repair_request(self, plan, failed_node, result, trace,
                               memory, runtime_input) -> dict:
        return {
            "original_request": runtime_input.get("user_request", ""),
            "failure_trace": {
                "failed_node": failed_node.get("id"),
                "node_kind": failed_node.get("kind"),
                "node_name": failed_node.get("name"),
                "error": result.error,
                "error_kind": result.error_kind,
                "trace_tail": trace.export()[-5:],
            },
            "repair_hints": _build_repair_hints(failed_node, result),
            "memory_snapshot": memory.snapshot(),
            "plan_id": plan.get("plan_id"),
        }


def _build_repair_hints(node, result) -> list[str]:
    hints: list[str] = []
    ek = (result.error_kind or "").lower()
    if ek == "not_found":
        hints.append(f"Skill or tool '{node.get('name')}' was not found in the catalog. "
                     "Replace with an available skill, or split the step into ones that exist.")
    elif ek == "schema":
        hints.append(f"Inputs to '{node.get('name')}' did not match the expected schema. "
                     "Check parameter names/types against the catalog.")
    elif ek == "timeout":
        hints.append("Step timed out. Consider raising timeout_ms or splitting the step.")
    elif ek == "exception":
        hints.append(f"Underlying exception: {result.error}. Consider an alternative approach.")
    if not hints:
        hints.append("Reconsider the step ordering and the chosen skill for this node.")
    return hints


def _default_llm_template(node: dict, resolved_inputs: dict) -> str:
    explicit = node.get("prompt_template")
    if explicit:
        return explicit
    if "text" in resolved_inputs:
        return "{{ text }}"
    if "user_request" in resolved_inputs:
        return "{{ user_request }}"
    for key, value in resolved_inputs.items():
        if isinstance(value, str):
            return "{{ " + key + " }}"
    return "{{ user_request }}"


def _derive_terminal_answer(trace_entries: list[dict]) -> Any:
    last_llm_text: Any = None
    last_node_id: str | None = None
    for entry in reversed(trace_entries):
        if entry.get("status") != "success":
            continue
        node_id = entry.get("node_id")
        kind = entry.get("kind") or entry.get("node_kind")
        output_data = entry.get("output_data") or {}
        if last_node_id is None:
            last_node_id = node_id
        if kind == "llm_text" and isinstance(output_data, dict):
            text = output_data.get("text")
            if text not in (None, ""):
                last_llm_text = text
                break
    if last_llm_text is not None:
        return last_llm_text
    return {
        "warning": (
            "plan did not declare an explicit $output.final_answer; "
            "returning structured trace summary instead of guessing"
        ),
        "terminal_node": last_node_id,
        "trace_node_count": len(trace_entries),
    }


def _validate_llm_text_outputs(node: dict, data: dict) -> list[str]:
    """Validate a fulfilled ``llm_text`` reply against the node contract.

    This intentionally stays smaller than full JSON Schema. The worker's
    load-bearing guarantee is that declared output keys are present before
    downstream refs can consume them. Optional ``response_schema`` /
    ``output_schema`` fields can further mark required keys and primitive
    types for worker API / fallback replies and local retry.
    """
    outputs_spec = node.get("outputs") or {}
    if not isinstance(outputs_spec, dict):
        outputs_spec = {}
    errors: list[str] = []
    for key in outputs_spec.keys():
        if key not in data:
            errors.append(f"missing declared output key {key!r}")

    schema = node.get("response_schema") or node.get("output_schema") or {}
    if not isinstance(schema, dict) or not schema:
        return errors

    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else None
    required = schema.get("required") if isinstance(schema.get("required"), list) else None
    if properties is None:
        properties = {
            str(k): v for k, v in schema.items()
            if k not in {"type", "required", "properties", "additionalProperties"}
        }
    if required is None:
        required = list(outputs_spec.keys())

    for key in required:
        if key not in data:
            errors.append(f"missing response_schema required key {key!r}")

    for key, spec in (properties or {}).items():
        if key not in data:
            continue
        expected_type = _schema_type_name(spec)
        if expected_type and not _matches_schema_type(data[key], expected_type):
            errors.append(
                f"key {key!r} expected {expected_type}, got "
                f"{type(data[key]).__name__}"
            )
    return errors


def _schema_type_name(spec: Any) -> str | None:
    if isinstance(spec, str):
        return spec.lower()
    if isinstance(spec, dict):
        t = spec.get("type")
        return str(t).lower() if isinstance(t, str) else None
    return None


def _matches_schema_type(value: Any, expected: str) -> bool:
    if value is None:
        return False
    if expected in {"string", "str"}:
        return isinstance(value, str)
    if expected in {"boolean", "bool"}:
        return isinstance(value, bool)
    if expected in {"integer", "int"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected in {"number", "float"}:
        return (isinstance(value, (int, float)) and not isinstance(value, bool))
    if expected in {"array", "list"}:
        return isinstance(value, list)
    if expected in {"object", "dict"}:
        return isinstance(value, dict)
    return True


def _coerce_llm_text_outputs(node: dict, text: str) -> dict:
    """Map raw LLM text to declared outputs.

    Two shape rules, in this order:

    1. If the LLM text parses as a JSON **object**, splay its top-level
       keys onto ``data``. A plan that declares multi-key
       ``outputs: {"a": ..., "b": ...}`` and asks the LLM to return
       ``{"a": ..., "b": ...}`` then gets ``a`` / ``b`` written into
       ``$memory`` cleanly. The whole dict is also kept available at
       ``data["_json"]`` for callers that want the whole payload.
    2. **Single-key shortcut**: when ``outputs_spec`` declares exactly
       one key and that key is not yet in ``data`` (from rule 1), bind
       the whole payload there — parsed dict if the JSON parsed
       cleanly (so downstream ``$memory.X.field == true`` indexing
       works), otherwise the raw text. This is the canonical
       "the LLM returned a structured decision blob" case.

    The raw string is always preserved at ``data["text"]`` for
    ``final_answer`` extraction / debugging — independent of parse
    success.
    """
    data: dict = {"text": text}
    outputs_spec = node.get("outputs") or {}
    parsed = _try_parse_json_payload(text)

    # Rule 1: splay top-level keys of a JSON object onto data so the
    # multi-key outputs_spec path picks them up via
    # _augment_result_data_for_declared_outputs.
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            if k in ("text",):
                continue  # never let the LLM clobber our raw-text slot
            data.setdefault(k, v)

    # Rule 2: single-key shortcut.
    if isinstance(outputs_spec, dict) and len(outputs_spec) == 1:
        only_key = next(iter(outputs_spec.keys()))
        if only_key not in data:
            data.setdefault(
                only_key, parsed if parsed is not None else text,
            )
    return data


def _try_parse_json_payload(text: Any) -> Any:
    """Return ``json.loads(text)`` if it parses cleanly as object / array.

    Plain strings, numbers, booleans, malformed JSON, or wrapped
    markdown all return ``None`` — callers fall back to the raw text.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if len(stripped) < 2:
        return None
    if not ((stripped[0] == "{" and stripped[-1] == "}")
            or (stripped[0] == "[" and stripped[-1] == "]")):
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None
