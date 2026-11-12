"""The blind review page (PLAN §11, task 6.2).

A reviewer opens a batch, sees the task, the patch and the check results, and
records accept or reject with reasons from the taxonomy. Nothing on the page names
the agent, the model, the cost or the timing — that is what makes the verdict
blind, and ``review_batches.order_seed`` is how the blindness stays auditable.

The page writes only ``human_*`` fields. An agent, an adapter or a background job
never writes a verdict.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from fixjury.db import SessionMaker, session_scope
from fixjury.models import HumanVerdict, RejectionCode, ReviewBatch, Run, Verdict
from fixjury.review.pack import ordered_items, rubric
from fixjury.tasks.loader import read_task, task_path

TEMPLATES = Path(__file__).parent / "templates"
REASONS = tuple(code.value for code in RejectionCode)


def create_app(*, maker: SessionMaker, corpus_root: Path) -> FastAPI:
    app = FastAPI(title="FixJury review", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        progress: list[dict[str, object]] = []
        async with session_scope(maker) as active:
            batches = list((await active.scalars(select(ReviewBatch))).all())
            for batch in batches:
                verdicts = list(
                    (
                        await active.scalars(
                            select(Verdict).where(Verdict.run_id.in_(batch.run_ids))
                        )
                    ).all()
                )
                done = sum(1 for verdict in verdicts if verdict.human_verdict is not None)
                progress.append(
                    {
                        "id": batch.id,
                        "task_id": batch.task_id,
                        "total": len(batch.run_ids),
                        "done": done,
                        "pass_no": batch.pass_no,
                    }
                )
        return templates.TemplateResponse(request, "index.html", {"batches": progress})

    @app.get("/batch/{batch_id}", response_class=HTMLResponse)
    async def show_batch(request: Request, batch_id: str) -> HTMLResponse:
        async with session_scope(maker) as active:
            batch = await active.get(ReviewBatch, batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="batch not found")
            runs = {
                run.id: run
                for run in (
                    await active.scalars(select(Run).where(Run.id.in_(batch.run_ids)))
                ).all()
            }
            verdicts = {
                verdict.run_id: verdict
                for verdict in (
                    await active.scalars(select(Verdict).where(Verdict.run_id.in_(batch.run_ids)))
                ).all()
            }
            items = ordered_items(batch, runs, verdicts)
            pending = next(
                (item for item in items if verdicts[item.run_id].human_verdict is None), None
            )
            if pending is None:
                return templates.TemplateResponse(
                    request, "done.html", {"batch_id": batch.id, "total": len(items)}
                )
            document = read_task(task_path(corpus_root, batch.task_id))
            remaining = sum(1 for item in items if verdicts[item.run_id].human_verdict is None)
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "batch_id": batch.id,
                "item": pending,
                "document": document,
                "rubric": rubric(document),
                "reasons": REASONS,
                "remaining": remaining,
            },
        )

    @app.post("/batch/{batch_id}/verdict")
    async def record_verdict(batch_id: str, request: Request) -> RedirectResponse:
        # Parsed by hand: a form parser would be one more dependency for one form.
        fields = parse_qs((await request.body()).decode("utf-8"))
        run_id = fields.get("run_id", [""])[0]
        verdict = fields.get("verdict", [""])[0]
        reasons = fields.get("reasons", [])
        reviewer = fields.get("reviewer", ["owner"])[0]
        try:
            seconds = int(fields.get("seconds", ["0"])[0] or 0)
        except ValueError:
            seconds = 0
        try:
            chosen = HumanVerdict(verdict)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="verdict must be accept or reject") from exc
        if chosen is HumanVerdict.reject and not reasons:
            raise HTTPException(status_code=400, detail="a rejection needs a reason")

        async with session_scope(maker) as active:
            batch = await active.get(ReviewBatch, batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="batch not found")
            row = await active.get(Verdict, uuid.UUID(run_id))
            if row is None or row.run_id not in batch.run_ids:
                raise HTTPException(status_code=404, detail="run is not in this batch")
            if row.human_verdict is not None:
                raise HTTPException(status_code=409, detail="this patch is already reviewed")
            row.human_verdict = chosen
            row.human_reasons = list(reasons)
            row.human_reviewer = reviewer
            row.review_seconds = max(0, seconds)
            row.blind = True
            row.batch_id = batch.id
            row.reviewed_at = datetime.now(UTC)
            await active.flush()
        return RedirectResponse(url=f"/batch/{batch_id}", status_code=303)

    return app
