from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from docker.errors import DockerException
from pydantic import ValidationError

from fixjury import __version__, queue
from fixjury.agents.metadata import UNKNOWN, describe_all
from fixjury.agents.registry import UnknownAgentError, build_adapter, known_agents
from fixjury.api.app import create_app as create_api_app
from fixjury.bench import BenchError, BenchSummary, drain, enqueue_plan, load_plan
from fixjury.db import make_engine, make_session_maker, session_scope
from fixjury.evaluation.contracts import EvaluationConfiguration
from fixjury.evaluation.gates import (
    GateError,
    GateOutcome,
    evaluate_gates,
    evidence_mismatch,
    load_policy,
)
from fixjury.evaluation.service import EvaluationError, build_request
from fixjury.evaluation.store import evaluations_for_run, load_evaluation
from fixjury.export import MANIFEST_FILENAME, export_rows
from fixjury.judge.service import (
    JUDGE_FAILURES,
    JudgedEvaluation,
    judge_configuration,
)
from fixjury.judge.service import (
    evaluate_run as judge_run,
)
from fixjury.leaderboard import (
    DIMENSIONS,
    LeaderboardError,
    build_leaderboard,
)
from fixjury.leaderboard import (
    as_payload as leaderboard_payload,
)
from fixjury.leaderboard import (
    render_markdown as render_leaderboard,
)
from fixjury.metrics.analytics import AnalyticsError, SnapshotRef, build_analytics
from fixjury.metrics.compute import VerdictSource
from fixjury.metrics.service import (
    MetricsError,
    analytics_sources,
    compute_snapshot,
    latest_snapshots,
    snapshot_by_id,
)
from fixjury.models import (
    CostBasis,
    EvaluationVerdict,
    MetricSnapshot,
    ReviewBatch,
    Run,
    RunStatus,
    Verdict,
)
from fixjury.report.charts import render_charts, render_regression_split
from fixjury.report.dashboard import DashboardError, build_dashboard
from fixjury.report.markdown import render_agent_table, render_calibration_table, render_report
from fixjury.report.quality import render_analytics, render_gate_report
from fixjury.review.app import create_app
from fixjury.review.pack import pack_batches
from fixjury.run import RunError, ensure_task, run_task
from fixjury.sandbox.network import reap_networks
from fixjury.tasks.cli import app as tasks_app
from fixjury.tasks.loader import TaskFileError, read_index, read_task, task_path
from fixjury.verify.runner import VerificationError
from fixjury.verify.service import VerifyError, verify_run
from fixjury.worker import WorkerError, acknowledge, redis_client, work_once

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(tasks_app, name="tasks")


async def _reap_stale_networks() -> None:
    """Remove run networks a crashed process left behind, and never fail the command."""

    try:
        reaped = await reap_networks()
    except (DockerException, OSError) as exc:
        typer.echo(f"warning: could not reap stale run networks: {exc}", err=True)
        return
    if reaped:
        typer.echo(f"reaped {reaped} stale run network(s)")


@app.callback()
def main() -> None:
    """FixJury — agent-agnostic reliability layer for autonomous code repair."""


@app.command()
def version() -> None:
    """Print the installed version."""

    typer.echo(__version__)


@app.command()
def run(
    task: Annotated[str, typer.Option("--task", help="Frozen task id, for example T-001.")],
    agent: Annotated[str, typer.Option("--agent", help="Adapter name.")] = "baseline",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    attempt: Annotated[
        int, typer.Option("--attempt", min=1, help="Attempt number for this agent.")
    ] = 1,
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    keep_workspace: Annotated[
        bool,
        typer.Option("--keep-workspace", help="Leave the checkout on disk for inspection."),
    ] = False,
) -> None:
    """Run one agent on one frozen task and record the result in ``runs``."""

    document = read_task(task_path(corpus, task))
    try:
        build_adapter(agent)
    except UnknownAgentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> Run:
        engine = make_engine()
        await _reap_stale_networks()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await run_task(
                    document,
                    session,
                    agent=agent,
                    attempt=attempt,
                    image=image,
                    keep_workspace=keep_workspace,
                )
        finally:
            await engine.dispose()

    try:
        row = asyncio.run(go())
    except RunError as exc:
        typer.echo(f"run failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    diff_bytes = len(row.diff or "")
    typer.echo(
        f"run {row.id} {row.status.value} agent={row.agent} model={row.model} "
        f"tokens={row.tokens_in}/{row.tokens_out} diff={diff_bytes}B "
        f"sha256={row.diff_sha256 or '-'}"
    )
    if row.status is not RunStatus.finished:
        raise typer.Exit(code=1)


@app.command()
def verify(
    run_id: Annotated[str, typer.Option("--run", help="Run id (UUID) to verify.")],
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    keep_workspace: Annotated[
        bool,
        typer.Option("--keep-workspace", help="Leave the checkout on disk for inspection."),
    ] = False,
) -> None:
    """Verify a stored run in a fresh container and record its verdict."""

    try:
        identifier = uuid.UUID(run_id)
    except ValueError as exc:
        typer.echo(f"not a run id: {run_id!r}", err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> Verdict:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await verify_run(
                    session,
                    identifier,
                    corpus_root=corpus,
                    image=image,
                    keep_workspace=keep_workspace,
                )
        finally:
            await engine.dispose()

    try:
        verdict = asyncio.run(go())
    except (VerifyError, VerificationError) as exc:
        typer.echo(f"verification failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(
        f"verdict run={verdict.run_id} automated={verdict.automated_pass} "
        f"tests={verdict.tests_pass} lint={verdict.lint_pass} types={verdict.type_pass} "
        f"policy={verdict.policy_pass} heldout={verdict.heldout_pass} "
        f"tools={verdict.tool_versions}"
    )
    if not verdict.automated_pass:
        raise typer.Exit(code=1)


judge_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(judge_app, name="judge")


@judge_app.command("evaluate")
def judge_evaluate(
    run_id: Annotated[str, typer.Option("--run", help="Run id (UUID) to judge.")],
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Judge mode: single, adaptive (default) or multi."),
    ] = None,
) -> None:
    """Judge a verified run and store the evaluation it produces."""

    try:
        identifier = uuid.UUID(run_id)
    except ValueError as exc:
        typer.echo(f"not a run id: {run_id!r}", err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> JudgedEvaluation:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await judge_run(
                    session,
                    run_id=identifier,
                    corpus_root=corpus,
                    config=judge_configuration(mode=mode),
                )
        finally:
            await engine.dispose()

    try:
        judged = asyncio.run(go())
    except JUDGE_FAILURES as exc:
        typer.echo(f"judge failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    result = judged.result
    judges = ", ".join(
        f"{assessment.judge_id}={assessment.response.verdict.value}"
        f"({assessment.response.confidence:.2f})"
        for assessment in judged.assessments
    )
    typer.echo(
        f"evaluation {judged.evaluation.id} verdict={result.verdict.value} "
        f"score={result.score.overall:.2f} confidence={result.confidence:.2f} "
        f"judges=[{judges}] findings={len(result.findings)}"
    )
    for limitation in result.limitations:
        typer.echo(f"  limitation: {limitation}")
    if result.verdict is not EvaluationVerdict.ACCEPTED:
        raise typer.Exit(code=1)


queue_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add(
    task: Annotated[str, typer.Option("--task", help="Frozen task id.")],
    agent: Annotated[str, typer.Option("--agent", help="Adapter name.")] = "baseline",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Attempt number.")] = 1,
) -> None:
    """Queue one run; duplicates are refused by (task, agent, attempt)."""

    document = read_task(task_path(corpus, task))

    async def go() -> Run:
        engine = make_engine()
        client = redis_client()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                await ensure_task(session, document)
                return await queue.enqueue(
                    session, client, task_id=document.id, agent=agent, attempt=attempt
                )
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    try:
        row = asyncio.run(go())
    except queue.QueueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"queued run {row.id} task={row.task_id} agent={row.agent} attempt={row.attempt}")


@queue_app.command("status")
def queue_status() -> None:
    """Print the depth of both queue lists."""

    async def go() -> dict[str, int]:
        client = redis_client()
        try:
            return await queue.depth(client)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    counts = asyncio.run(go())
    typer.echo(f"queued={counts['queued']} processing={counts['processing']}")


@queue_app.command("resume")
def queue_resume() -> None:
    """Put runs left in ``processing`` back into ``queued`` after a crash."""

    async def go() -> int:
        client = redis_client()
        try:
            return await queue.resume(client)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    recovered = asyncio.run(go())
    typer.echo(f"requeued {recovered} run(s)")


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Process one run and exit.")] = False,
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
    wait_s: Annotated[
        float, typer.Option("--wait", help="Seconds to block on an empty queue.")
    ] = 5.0,
) -> None:
    """Claim runs from the queue and execute them, one at a time."""

    async def go() -> None:
        engine = make_engine()
        client = redis_client()
        await _reap_stale_networks()
        try:
            while True:
                async with session_scope(make_session_maker(engine)) as session:
                    try:
                        row = await work_once(
                            client,
                            session,
                            corpus_root=corpus,
                            image=image,
                            wait_s=wait_s,
                        )
                    except WorkerError as exc:
                        # A duplicate delivery or a missing row: log it and keep
                        # serving the queue instead of exiting the worker.
                        typer.echo(f"dropped a delivery: {exc}", err=True)
                        continue
                if row is not None:
                    # Committed: the id can leave ``processing`` without losing the run.
                    await acknowledge(client, row.id)
                    typer.echo(
                        f"run {row.id} {row.status.value} agent={row.agent} "
                        f"attempt={row.attempt} diff={len(row.diff or '')}B"
                    )
                elif once:
                    typer.echo("queue is empty")
                if once:
                    return
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    asyncio.run(go())


metrics_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(metrics_app, name="metrics")


@metrics_app.command("compute")
def metrics_compute(
    agent: Annotated[str, typer.Option("--agent", help="Agent to summarise.")] = "baseline",
    cost_basis: Annotated[
        str, typer.Option("--cost-basis", help="list_price or amortized.")
    ] = "list_price",
    verdict_source: Annotated[
        str,
        typer.Option(
            "--verdict-source",
            help="Which verdict acceptance reads: judge (V1.1) or human (the V1 series).",
        ),
    ] = "judge",
) -> None:
    """Compute one snapshot from runs + verdicts + evaluations and store it."""

    try:
        basis = CostBasis(cost_basis)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CostBasis)
        typer.echo(f"cost basis must be one of {allowed}", err=True)
        raise typer.Exit(code=2) from exc
    try:
        source = VerdictSource(verdict_source)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in VerdictSource)
        typer.echo(f"verdict source must be one of {allowed}", err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> MetricSnapshot:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await compute_snapshot(
                    session, agent=agent, cost_basis=basis, verdict_source=source
                )
        finally:
            await engine.dispose()

    try:
        snapshot = asyncio.run(go())
    except MetricsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"snapshot {snapshot.id} agent={snapshot.agent} basis={snapshot.cost_basis.value} "
        f"verdict_source={source.value}"
    )
    typer.echo(render_agent_table(snapshot.payload))


@metrics_app.command("calibration")
def metrics_calibration(
    agent: Annotated[str | None, typer.Option("--agent", help="Filter by agent.")] = None,
) -> None:
    """Print how the judge behaved against the held-out labels, from stored snapshots."""

    async def go() -> list[MetricSnapshot]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await latest_snapshots(session, agent=agent)
        finally:
            await engine.dispose()

    snapshots = asyncio.run(go())
    if not snapshots:
        typer.echo("no snapshots yet: run `fixjury metrics compute` first", err=True)
        raise typer.Exit(code=1)
    section = render_calibration_table([snapshot.payload for snapshot in snapshots])
    if not section:
        typer.echo(
            "no judged pairs in these snapshots: calibrate against `fixjury judge evaluate` first",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo("\n".join(section).lstrip())


@metrics_app.command("show")
def metrics_show(
    agent: Annotated[str | None, typer.Option("--agent", help="Filter by agent.")] = None,
) -> None:
    """Print the latest snapshot per agent."""

    async def go() -> list[MetricSnapshot]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await latest_snapshots(session, agent=agent)
        finally:
            await engine.dispose()

    snapshots = asyncio.run(go())
    if not snapshots:
        typer.echo("no snapshots yet: run `fixjury metrics compute` first")
        raise typer.Exit(code=1)
    for snapshot in snapshots:
        typer.echo(render_agent_table(snapshot.payload))


@app.command()
def report(
    out: Annotated[Path, typer.Option("--out", help="Directory for the report.")] = Path(
        "docs/report"
    ),
    basis: Annotated[str, typer.Option("--basis", help="Cost basis to label.")] = "list_price",
    snapshot: Annotated[
        str | None, typer.Option("--snapshot", help="Reproduce the report from one snapshot id.")
    ] = None,
    charts: Annotated[
        bool, typer.Option("--charts", help="Also write the charts next to the report.")
    ] = False,
) -> None:
    """Render the markdown report from the latest snapshots, or from one snapshot."""

    async def go() -> list[MetricSnapshot]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                if snapshot is not None:
                    found = await snapshot_by_id(session, uuid.UUID(snapshot))
                    if found is None:
                        raise SystemExit(f"no snapshot {snapshot}")
                    return [found]
                return await latest_snapshots(session, agent=None)
        finally:
            await engine.dispose()

    snapshots = asyncio.run(go())
    if not snapshots:
        typer.echo("no snapshots yet: run `fixjury metrics compute` first", err=True)
        raise typer.Exit(code=1)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "README.md"
    target.write_text(
        render_report([snapshot.payload for snapshot in snapshots], basis=basis),
        encoding="utf-8",
    )
    snapshots.sort(key=lambda snapshot: snapshot.agent)
    if charts:
        payloads = [snapshot.payload for snapshot in snapshots]
        drawn = render_charts(payloads, out)
        drawn["regressions"] = render_regression_split(payloads, out)
        typer.echo(f"charts: {', '.join(sorted(path.name for path in drawn.values()))}")
    typer.echo(f"wrote {target} from {len(snapshots)} snapshot(s)")


@app.command()
def leaderboard(
    dimensions: Annotated[
        str, typer.Option("--dimensions", help="Comma-separated: overall, difficulty.")
    ] = "overall,difficulty",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    out: Annotated[
        Path | None, typer.Option("--out", help="Directory for leaderboard.md and .json.")
    ] = None,
    snapshot: Annotated[
        str | None, typer.Option("--snapshot", help="Rank one snapshot id only.")
    ] = None,
) -> None:
    """Rank the agents from stored snapshots, with sample sizes and intervals."""

    selected = tuple(item.strip() for item in dimensions.split(",") if item.strip())
    unknown = [item for item in selected if item not in DIMENSIONS]
    if unknown:
        recorded = ", ".join(DIMENSIONS)
        typer.echo(
            f"unknown dimension(s) {', '.join(unknown)}; the corpus records {recorded}",
            err=True,
        )
        raise typer.Exit(code=2)

    async def go() -> list[MetricSnapshot]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                if snapshot is not None:
                    found = await snapshot_by_id(session, uuid.UUID(snapshot))
                    if found is None:
                        raise SystemExit(f"no snapshot {snapshot}")
                    return [found]
                return await latest_snapshots(session, agent=None)
        finally:
            await engine.dispose()

    snapshots = asyncio.run(go())
    if not snapshots:
        typer.echo("no snapshots yet: run `fixjury metrics compute` first", err=True)
        raise typer.Exit(code=1)
    payloads = [snapshot.payload for snapshot in snapshots]
    try:
        version = read_index(corpus).corpus_version
        boards = [
            build_leaderboard(payloads, corpus_version=version, dimension=dimension)
            for dimension in selected
        ]
    except (LeaderboardError, TaskFileError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    text = "\n\n".join(render_leaderboard(board) for board in boards)
    typer.echo(text)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        target = out / "README.md"
        target.write_text(text + "\n", encoding="utf-8")
        (out / "leaderboard.json").write_text(
            json.dumps([leaderboard_payload(board) for board in boards], indent=2) + "\n",
            encoding="utf-8",
        )
        typer.echo(f"wrote {target} and leaderboard.json")


gates_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(gates_app, name="gates")

#: What a gate outcome means to a shell: the codes docs/USAGE.md documents.
GATE_EXIT_CODES: dict[GateOutcome, int] = {
    GateOutcome.PASS: 0,
    GateOutcome.FAIL: 1,
    GateOutcome.INCONCLUSIVE: 2,
}


@gates_app.command("check")
def gates_check(
    run_id: Annotated[str, typer.Option("--run", help="Run id whose evaluation is gated.")],
    policy: Annotated[
        str, typer.Option("--policy", help="Policy name in the config file.")
    ] = "default",
    config: Annotated[Path, typer.Option("--config", help="Quality policy file.")] = Path(
        "config/quality.yaml"
    ),
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
) -> None:
    """Apply the quality gates to a run's stored evaluation (PASS / FAIL / INCONCLUSIVE)."""

    try:
        identifier = uuid.UUID(run_id)
    except ValueError as exc:
        typer.echo(f"not a run id: {run_id!r}", err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> tuple[str, GateOutcome]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                evaluations = await evaluations_for_run(session, identifier)
                if not evaluations:
                    raise GateError(
                        f"run {identifier} has no evaluation: judge it before gating it"
                    )
                newest = evaluations[0]
                stored = await load_evaluation(session, newest.id)
                if stored is None or stored.result is None:
                    # A failed evaluation recorded a reason, not a verdict; there is nothing
                    # to gate and calling it a pass would publish an outage as a success.
                    raise GateError(
                        f"evaluation {newest.id} has no result: "
                        f"{newest.error or 'a failed evaluation cannot pass a gate'}"
                    )
                request = await build_request(
                    session,
                    run_id=identifier,
                    corpus_root=corpus,
                    config=EvaluationConfiguration.model_validate(newest.config),
                )
                mismatch = evidence_mismatch(stored.result, request.checks)
                if mismatch is not None:
                    raise GateError(mismatch)
                report = evaluate_gates(
                    load_policy(config, name=policy),
                    request=request,
                    result=stored.result,
                    evaluation_run_id=str(newest.id),
                )
                return render_gate_report(report), report.outcome
        finally:
            await engine.dispose()

    try:
        text, outcome = asyncio.run(go())
    except (GateError, EvaluationError, ValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(text)
    # The documented contract: PASS 0, FAIL 1, INCONCLUSIVE 2. A script gating a pipeline has
    # to tell a failed rule from a rule nobody could evaluate.
    raise typer.Exit(code=GATE_EXIT_CODES[outcome])


@app.command()
def analytics(
    agent: Annotated[str | None, typer.Option("--agent", help="Filter by agent.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="First day, YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="Last day, YYYY-MM-DD.")] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the report to this directory.")
    ] = None,
) -> None:
    """Evaluation history, agent standing, cost and judge statistics from snapshots."""

    try:
        start = date.fromisoformat(since) if since else None
        end = date.fromisoformat(until) if until else None
    except ValueError as exc:
        typer.echo(f"a date must look like YYYY-MM-DD: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if (since and start is None) or (until and end is None):
        typer.echo("a date must look like YYYY-MM-DD", err=True)
        raise typer.Exit(code=2)

    async def go() -> list[SnapshotRef]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await analytics_sources(session, agent=agent)
        finally:
            await engine.dispose()

    sources = asyncio.run(go())
    try:
        report = build_analytics(sources, agent=agent, since=start, until=end)
    except AnalyticsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    text = render_analytics(report)
    typer.echo(text)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        target = out / "README.md"
        target.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"wrote {target}")


@app.command()
def agents() -> None:
    """List the registered integrations and what the platform can honestly say about them."""

    described = describe_all(known_agents())
    typer.echo("| Agent | Version | Kind | Image | Attribution |")
    typer.echo("| --- | --- | --- | --- | --- |")
    for item in described:
        typer.echo(
            f"| {item.name} | {item.version} | {item.kind} | {item.image or '-'} "
            f"| {item.attribution} |"
        )
    if any(not item.known for item in described):
        typer.echo("")
        typer.echo(
            f"`{UNKNOWN}` means the platform cannot attribute the run: no version or image is "
            "declared, and it is recorded as unknown rather than guessed."
        )


@app.command()
def export(
    out: Annotated[Path, typer.Option("--out", help="Directory for the JSONL files.")] = Path(
        "docs/export"
    ),
) -> None:
    """Export raw runs, verdicts and rejections as JSONL, with a hashed manifest."""

    async def go() -> dict[str, object]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await export_rows(session, out)
        finally:
            await engine.dispose()

    manifest = asyncio.run(go())
    files = manifest.get("files")
    summary = (
        ", ".join(f"{name} {entry['rows']} row(s)" for name, entry in files.items())
        if isinstance(files, dict)
        else ""
    )
    typer.echo(f"wrote {summary} and {MANIFEST_FILENAME} under {out}")


@app.command()
def dashboard(
    out: Annotated[Path, typer.Option("--out", help="Directory for the static export.")] = Path(
        "docs/dashboard"
    ),
    basis: Annotated[str, typer.Option("--basis", help="Cost basis to label.")] = "list_price",
    snapshot: Annotated[
        str | None, typer.Option("--snapshot", help="Build the page from one snapshot id.")
    ] = None,
) -> None:
    """Build the static metrics page (Next.js export) from the latest snapshots."""

    async def go() -> list[MetricSnapshot]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                if snapshot is not None:
                    found = await snapshot_by_id(session, uuid.UUID(snapshot))
                    if found is None:
                        raise SystemExit(f"no snapshot {snapshot}")
                    return [found]
                return await latest_snapshots(session, agent=None)
        finally:
            await engine.dispose()

    snapshots = asyncio.run(go())
    if not snapshots:
        typer.echo("no snapshots yet: run `fixjury metrics compute` first", err=True)
        raise typer.Exit(code=1)
    snapshots.sort(key=lambda snapshot: snapshot.agent)
    payloads = [snapshot.payload for snapshot in snapshots]
    try:
        target = build_dashboard(out, payloads, basis=basis)
    except DashboardError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"wrote {target} from {len(snapshots)} snapshot(s)")


@app.command()
def api(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port.")] = 8000,
) -> None:
    """Serve the read-only JSON API; loopback unless told otherwise."""

    engine = make_engine()
    application = create_api_app(maker=make_session_maker(engine))
    typer.echo(f"API on http://{host}:{port}/api/v1")
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def bench(
    agents: Annotated[str, typer.Option("--agents", help="Comma-separated adapter names.")],
    tasks: Annotated[
        str, typer.Option("--tasks", help="Comma-separated task ids; default: the corpus index.")
    ] = "",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    attempt: Annotated[int, typer.Option("--attempt", min=1, help="Attempt number.")] = 1,
    max_seconds: Annotated[
        float | None, typer.Option("--max-seconds", help="Stop when the budget is spent.")
    ] = None,
    verify: Annotated[
        bool, typer.Option("--verify", help="Verify every finished run as it lands.")
    ] = False,
    judge: Annotated[
        bool,
        typer.Option(
            "--judge",
            help="Judge every verified run (requires --verify: the judge reads the verdict).",
        ),
    ] = False,
    image: Annotated[str | None, typer.Option("--image", help="Sandbox image override.")] = None,
) -> None:
    """Queue a task x agent grid, run it, and report what happened."""

    if judge and not verify:
        # A judge needs the check evidence verification records; without it every pair
        # would fail as unevaluable, which looks like a judge problem and is a flag problem.
        typer.echo(
            "--judge needs --verify: the judge evaluates the verdict, it cannot run without it",
            err=True,
        )
        raise typer.Exit(code=2)

    selected_agents = tuple(item.strip() for item in agents.split(",") if item.strip())
    selected_tasks = tuple(item.strip() for item in tasks.split(",") if item.strip())
    try:
        plan = load_plan(
            corpus_root=corpus, agents=selected_agents, tasks=selected_tasks, attempt=attempt
        )
        for name in plan.agents:
            build_adapter(name)
    except (BenchError, UnknownAgentError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def go() -> BenchSummary:
        engine = make_engine()
        client = redis_client()
        maker = make_session_maker(engine)
        await _reap_stale_networks()
        try:
            recovered = await queue.resume(client)
            if recovered:
                typer.echo(f"requeued {recovered} orphaned run(s)")
            async with session_scope(maker) as session:
                queued, skipped = await enqueue_plan(session, client, plan=plan, corpus_root=corpus)
            typer.echo(f"queued {len(queued)} run(s) for {plan.pairs} pair(s)")
            if skipped:
                typer.echo(f"skipped {len(skipped)} pair(s) that already ran: {', '.join(skipped)}")
            return await drain(
                maker,
                client,
                plan=plan,
                corpus_root=corpus,
                image=image,
                max_seconds=max_seconds,
                verify=verify,
                judge=judge,
            )
        finally:
            await client.aclose()  # type: ignore[attr-defined]
            await engine.dispose()

    summary = asyncio.run(go())
    typer.echo(
        f"bench done in {summary.elapsed_s}s: finished={summary.finished} "
        f"failed={summary.failed} verified={summary.verified} judged={summary.judged} "
        f"judge_failed={summary.judge_failed} "
        f"budget_stop={summary.stopped_on_budget} statuses={summary.statuses}"
    )
    if summary.failed:
        raise typer.Exit(code=1)


review_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(review_app, name="review")


@review_app.command("pack")
def review_pack(
    reviewer: Annotated[
        str, typer.Option("--reviewer", help="Who reviews these batches.")
    ] = "owner",
    pass_no: Annotated[int, typer.Option("--pass", min=1, help="Review pass number.")] = 1,
    seed: Annotated[int, typer.Option("--seed", help="Base seed for the stored order.")] = 0,
    tasks: Annotated[str, typer.Option("--tasks", help="Comma-separated task ids.")] = "",
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
    min_runs: Annotated[int, typer.Option("--min-runs", min=2, help="Runs per batch.")] = 2,
) -> None:
    """Pack accepted patches into blind batches, one per task."""

    selected = tuple(item.strip() for item in tasks.split(",") if item.strip()) or None

    async def go() -> list[ReviewBatch]:
        engine = make_engine()
        try:
            async with session_scope(make_session_maker(engine)) as session:
                return await pack_batches(
                    session,
                    reviewer=reviewer,
                    pass_no=pass_no,
                    seed=seed,
                    tasks=selected,
                    min_runs=min_runs,
                )
        finally:
            await engine.dispose()

    batches = asyncio.run(go())
    if not batches:
        typer.echo("nothing to pack: no task has enough accepted patches")
        raise typer.Exit(code=1)
    for batch in batches:
        typer.echo(f"batch {batch.id} task={batch.task_id} patches={len(batch.run_ids)}")


@review_app.command("serve")
def review_serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port.")] = 8099,
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("benchmarks"),
) -> None:
    """Serve the blind review page."""

    engine = make_engine()
    application = create_app(maker=make_session_maker(engine), corpus_root=corpus)
    typer.echo(f"review page on http://{host}:{port}")
    uvicorn.run(application, host=host, port=port, log_level="warning")
