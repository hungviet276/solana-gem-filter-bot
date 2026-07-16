from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gem_filter.api.auth import (
    UserContext,
    current_user,
    get_session,
    hash_password,
    issue_token,
    require_roles,
    verify_password,
)
from gem_filter.api.schemas import (
    BacktestCreateIn,
    BlacklistIn,
    CloseTokenIn,
    LoginIn,
    RuleCreateIn,
    SimulationIn,
    SourceCreateIn,
    SourceUpdateIn,
    SubscriptionIn,
    TelegramUpdateIn,
    WhaleProfileIn,
)
from gem_filter.application.processor import ProcessingService, TelegramEnvelope, _rule_from_payload
from gem_filter.config import Settings, get_settings
from gem_filter.domain.models import (
    MarketSnapshot,
    MultipleEvent,
    TokenCall,
    TokenStatus,
    WhaleEvent,
)
from gem_filter.domain.rules import DEFAULT_RULE, evaluate
from gem_filter.domain.state import transition
from gem_filter.infrastructure.database import (
    AlertRecord,
    AuditLog,
    BacktestRun,
    Base,
    ContractBlacklist,
    MarketSnapshotRecord,
    MultipleEventRecord,
    OutboxEvent,
    RawTelegramEvent,
    RuleVersion,
    ScoreEvaluationRecord,
    TelegramSource,
    TokenCallRecord,
    TokenStateHistory,
    UserAccount,
    UserSubscription,
    WhaleEventRecord,
    WhaleProfile,
    create_engine_and_session,
)
from gem_filter.infrastructure.market import create_market_provider
from gem_filter.infrastructure.runtime_store import PostgresRuntimeStore
from gem_filter.observability import configure_logging, correlation_id_var

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")


async def _bootstrap(session: AsyncSession, settings: Settings) -> None:
    default_rule = await session.scalar(
        select(RuleVersion).where(RuleVersion.version == DEFAULT_RULE.version)
    )
    if default_rule is None:
        config = {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(DEFAULT_RULE).items()
            if key != "version"
        }
        session.add(
            RuleVersion(
                version=DEFAULT_RULE.version,
                status="PUBLISHED",
                config=config,
                shadow=False,
                published_at=datetime.now(tz=UTC),
                created_by="bootstrap",
            )
        )
    source_ids = settings.source_id_set
    if not source_ids and settings.app_env in {"dev", "test"}:
        source_ids = {0}
    for channel_id in source_ids:
        source = await session.scalar(
            select(TelegramSource).where(TelegramSource.channel_id == channel_id)
        )
        if source is None:
            session.add(
                TelegramSource(
                    name=f"source-{channel_id}",
                    channel_id=channel_id,
                    connection_mode=settings.telegram_source_mode,
                    parser_version="gemtools-parser-1.0.0",
                    enabled=True,
                )
            )
    account = await session.scalar(
        select(UserAccount).where(UserAccount.email == settings.bootstrap_admin_email)
    )
    if account is None:
        salt, digest = hash_password(settings.bootstrap_admin_password.get_secret_value())
        session.add(
            UserAccount(
                email=settings.bootstrap_admin_email,
                password_salt=salt,
                password_hash=digest,
                role="Admin",
                active=True,
            )
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, sessions = create_engine_and_session(settings)
    if settings.auto_create_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    market = create_market_provider(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.runtime_store = PostgresRuntimeStore(sessions)
    app.state.market = market
    app.state.processor = ProcessingService(sessions, market, settings)
    try:
        async with sessions() as session, session.begin():
            await _bootstrap(session, settings)
        yield
    finally:
        await market.close()
        await engine.dispose()


app = FastAPI(
    title="Solana Gem Filter Bot",
    version="1.0.0",
    description=(
        "Read-only analytics, paper tracking, backtest and Telegram alerting. No trade execution."
    ),
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))[:64]
    token = correlation_id_var.set(correlation_id)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    finally:
        correlation_id_var.reset(token)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    try:
        async with request.app.state.sessions() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Dependencies unavailable"
        ) from exc
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/auth/login")
async def login(
    body: LoginIn, request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, Any]:
    account = await session.scalar(
        select(UserAccount).where(UserAccount.email == body.email, UserAccount.active.is_(True))
    )
    if account is None or not verify_password(
        body.password, account.password_salt, account.password_hash
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return {
        "access_token": issue_token(
            account.email,
            account.role,
            request.app.state.settings.auth_signing_secret.get_secret_value(),
        ),
        "token_type": "bearer",
        "role": account.role,
    }


@app.post("/api/v1/ingestion/telegram", status_code=status.HTTP_202_ACCEPTED)
async def ingest_telegram(
    body: TelegramUpdateIn,
    request: Request,
    internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> dict[str, Any]:
    expected = request.app.state.settings.internal_api_token.get_secret_value()
    if internal_token is None or not hmac.compare_digest(internal_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token"
        )
    try:
        return await request.app.state.processor.ingest(
            TelegramEnvelope(
                source_channel_id=body.source_channel_id,
                source_message_id=body.source_message_id,
                reply_to_message_id=body.reply_to_message_id,
                event_ts=body.event_ts,
                raw_text=body.raw_text,
                edited=body.edited,
                revision=body.revision,
                raw_payload=body.raw_payload,
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@app.post("/api/v1/webhooks/helius", status_code=status.HTTP_202_ACCEPTED)
async def helius_webhook(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    if settings.helius_webhook_secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Helius integration disabled"
        )
    expected = settings.helius_webhook_secret.get_secret_value()
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Helius authorization header"
        )
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc
    first = (
        payload[0]
        if isinstance(payload, list) and payload
        else payload
        if isinstance(payload, dict)
        else {}
    )
    signature = first.get("signature") if isinstance(first, dict) else None
    event_id = str(signature or hashlib.sha256(raw_body).hexdigest())
    event_ts = first.get("timestamp") if isinstance(first, dict) else None
    if isinstance(event_ts, (int, float)):
        skew = abs(datetime.now(tz=UTC).timestamp() - float(event_ts))
        if skew > settings.webhook_max_skew_seconds:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Stale webhook timestamp"
            )
    async with request.app.state.sessions() as session, session.begin():
        if not await request.app.state.runtime_store.claim_once(
            "helius", event_id, 86_400, session=session
        ):
            return {"status": "duplicate"}
        session.add(
            OutboxEvent(
                topic="HELIUS_EVENT",
                idempotency_key=f"helius:{event_id}",
                payload={"event_id": event_id, "events": payload},
            )
        )
    return {"status": "accepted"}


@app.get("/api/v1/tokens")
async def list_tokens(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(current_user)],
    token_status: str | None = Query(default=None, alias="status"),
    rule_version: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    statement = (
        select(TokenCallRecord).order_by(TokenCallRecord.call_ts.desc()).limit(limit).offset(offset)
    )
    if token_status:
        statement = statement.where(TokenCallRecord.status == token_status)
    if rule_version:
        statement = statement.where(TokenCallRecord.rule_version == rule_version)
    rows = (await session.scalars(statement)).all()
    total = await session.scalar(select(func.count()).select_from(TokenCallRecord))
    return {"total": total or 0, "items": [_token_json(row) for row in rows]}


@app.get("/api/v1/tokens/{token_id}")
async def token_detail(
    token_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(current_user)],
) -> dict[str, Any]:
    token = await session.get(TokenCallRecord, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    whales = (
        await session.scalars(
            select(WhaleEventRecord)
            .where(WhaleEventRecord.token_call_id == token.id)
            .order_by(WhaleEventRecord.event_ts)
        )
    ).all()
    multiples = (
        await session.scalars(
            select(MultipleEventRecord)
            .where(MultipleEventRecord.token_call_id == token.id)
            .order_by(MultipleEventRecord.event_ts)
        )
    ).all()
    snapshots = (
        await session.scalars(
            select(MarketSnapshotRecord)
            .where(MarketSnapshotRecord.token_call_id == token.id)
            .order_by(MarketSnapshotRecord.provider_ts.desc())
            .limit(100)
        )
    ).all()
    scores = (
        await session.scalars(
            select(ScoreEvaluationRecord)
            .where(ScoreEvaluationRecord.token_call_id == token.id)
            .order_by(ScoreEvaluationRecord.evaluated_at.desc())
            .limit(100)
        )
    ).all()
    alerts = (
        await session.scalars(
            select(AlertRecord)
            .where(AlertRecord.token_call_id == token.id)
            .order_by(AlertRecord.created_at)
        )
    ).all()
    return jsonable_encoder(
        {
            "token": _token_json(token),
            "whales": [_row_json(row) for row in whales],
            "multiples": [_row_json(row) for row in multiples],
            "snapshots": [_row_json(row, exclude={"raw_payload"}) for row in snapshots],
            "scores": [_row_json(row) for row in scores],
            "alerts": [_row_json(row) for row in alerts],
        }
    )


@app.post("/api/v1/tokens/{token_id}/close")
async def close_token(
    token_id: uuid.UUID,
    body: CloseTokenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin", "Analyst"))],
) -> dict[str, str]:
    async with session.begin():
        token = await session.get(TokenCallRecord, token_id, with_for_update=True)
        if token is None:
            raise HTTPException(status_code=404, detail="Token not found")
        previous = token.status
        token.status = transition(TokenStatus(token.status), TokenStatus.CLOSED).value
        token.state_reason = body.reason
        session.add(
            TokenStateHistory(
                token_call_id=token.id,
                from_status=previous,
                to_status=token.status,
                reason=body.reason,
                actor=user.email,
            )
        )
        session.add(
            AuditLog(
                actor=user.email,
                action="TOKEN_CLOSE",
                entity_type="token_call",
                entity_id=str(token.id),
                reason=body.reason,
                before={"status": previous},
                after={"status": token.status},
            )
        )
    return {"status": "closed"}


@app.post("/api/v1/tokens/{token_id}/reparse", status_code=202)
async def reparse_token(
    token_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin", "Analyst"))],
) -> dict[str, str]:
    async with session.begin():
        token = await session.get(TokenCallRecord, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Token not found")
        raw = await session.scalar(
            select(RawTelegramEvent)
            .where(
                RawTelegramEvent.source_id == token.source_id,
                RawTelegramEvent.source_message_id == token.source_message_id,
            )
            .order_by(RawTelegramEvent.revision.desc())
        )
        if raw is None:
            raise HTTPException(status_code=404, detail="Raw event not found")
        session.add(
            OutboxEvent(
                topic="REPARSE_RAW_EVENT",
                idempotency_key=f"reparse:{raw.id}:{uuid.uuid4()}",
                payload={"raw_event_id": str(raw.id), "requested_by": user.email},
            )
        )
        session.add(
            AuditLog(
                actor=user.email,
                action="REPARSE_REQUEST",
                entity_type="raw_event",
                entity_id=str(raw.id),
                reason="Manual reparse",
            )
        )
    return {"status": "queued"}


@app.post("/api/v1/rules", status_code=201)
async def create_rule(
    body: RuleCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, Any]:
    _rule_from_payload(body.config, body.version)
    async with session.begin():
        if await session.scalar(select(RuleVersion).where(RuleVersion.version == body.version)):
            raise HTTPException(status_code=409, detail="Rule version exists")
        record = RuleVersion(
            version=body.version,
            status="DRAFT",
            config=body.config,
            shadow=body.shadow,
            created_by=user.email,
        )
        session.add(record)
        await session.flush()
        session.add(
            AuditLog(
                actor=user.email,
                action="RULE_CREATE",
                entity_type="rule_version",
                entity_id=str(record.id),
                reason="Draft created",
                after={"version": body.version, "shadow": body.shadow},
            )
        )
    return {"id": str(record.id), "version": record.version, "status": record.status}


@app.get("/api/v1/rules")
async def list_rules(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(current_user)],
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(select(RuleVersion).order_by(RuleVersion.created_at.desc()))
    ).all()
    return jsonable_encoder([_row_json(row) for row in rows])


@app.post("/api/v1/rules/{rule_id}/simulate")
async def simulate_rule(
    rule_id: uuid.UUID,
    body: SimulationIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Analyst"))],
) -> dict[str, Any]:
    record = await session.get(RuleVersion, rule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    token = TokenCall(**body.token.model_dump(), rule_version=record.version)
    market = None
    if body.market:
        market = MarketSnapshot(
            captured_at=body.evaluation_time,
            provider_ts=body.market.provider_ts,
            provider="simulation",
            pair_address="simulation",
            dex="simulation",
            market_cap_usd=body.market.market_cap_usd,
            fdv_usd=body.market.fdv_usd,
            liquidity_usd=body.market.liquidity_usd,
            price_usd=body.market.price_usd,
        )
    whales = tuple(WhaleEvent(**item.model_dump()) for item in body.whales)
    outcomes = tuple(MultipleEvent(**item.model_dump()) for item in body.outcomes)
    result = evaluate(
        token,
        market,
        whales,
        outcomes,
        _rule_from_payload(record.config, record.version),
        evaluation_time=body.evaluation_time,
    )
    return jsonable_encoder(asdict(result))


@app.post("/api/v1/rules/{rule_id}/publish")
async def publish_rule(
    rule_id: uuid.UUID,
    reason: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, str]:
    async with session.begin():
        record = await session.get(RuleVersion, rule_id, with_for_update=True)
        if record is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        if record.shadow:
            raise HTTPException(
                status_code=409, detail="Shadow rule cannot be published until shadow is disabled"
            )
        await session.execute(
            update(RuleVersion).where(RuleVersion.status == "PUBLISHED").values(status="ARCHIVED")
        )
        record.status = "PUBLISHED"
        record.published_at = datetime.now(tz=UTC)
        session.add(
            AuditLog(
                actor=user.email,
                action="RULE_PUBLISH",
                entity_type="rule_version",
                entity_id=str(record.id),
                reason=reason,
                after={"version": record.version},
            )
        )
    return {"version": record.version, "status": record.status}


@app.post("/api/v1/rules/{rule_id}/rollback")
async def rollback_rule(
    rule_id: uuid.UUID,
    reason: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, str]:
    return await publish_rule(rule_id, f"ROLLBACK: {reason}", request, session, user)


@app.post("/api/v1/backtests", status_code=202)
async def create_backtest(
    body: BacktestCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin", "Analyst"))],
) -> dict[str, str]:
    async with session.begin():
        if not await session.scalar(
            select(RuleVersion).where(RuleVersion.version == body.rule_version)
        ):
            raise HTTPException(status_code=404, detail="Rule not found")
        run = BacktestRun(
            rule_version=body.rule_version,
            dataset_split=body.dataset_split,
            requested_by=user.email,
            parameters=jsonable_encoder(body.model_dump()),
        )
        session.add(run)
        await session.flush()
        session.add(
            OutboxEvent(
                topic="BACKTEST",
                idempotency_key=f"backtest:{run.id}",
                payload={"backtest_run_id": str(run.id)},
            )
        )
    return {"id": str(run.id), "status": run.status}


@app.get("/api/v1/backtests/{run_id}")
async def get_backtest(
    run_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(current_user)],
) -> dict[str, Any]:
    run = await session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return jsonable_encoder(_row_json(run))


@app.get("/api/v1/metrics/performance")
async def performance_metrics(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(current_user)],
    rule_version: str | None = None,
) -> dict[str, Any]:
    statement = select(TokenCallRecord)
    if rule_version:
        statement = statement.where(TokenCallRecord.rule_version == rule_version)
    calls = (await session.scalars(statement)).all()
    outcomes = (
        await session.execute(
            select(
                MultipleEventRecord.token_call_id, func.max(MultipleEventRecord.multiple)
            ).group_by(MultipleEventRecord.token_call_id)
        )
    ).all()
    max_by_token = {str(token_id): int(value) for token_id, value in outcomes}
    strong_ids = set(
        (
            await session.scalars(
                select(AlertRecord.token_call_id).where(AlertRecord.level == "STRONG")
            )
        ).all()
    )
    observed_strong = [token_id for token_id in strong_ids if str(token_id) in max_by_token]

    def precision(target: int) -> float | None:
        return (
            sum(max_by_token[str(token_id)] >= target for token_id in observed_strong)
            / len(observed_strong)
            if observed_strong
            else None
        )

    return {
        "sample_size": len(calls),
        "strong_observed_sample_size": len(observed_strong),
        "coverage": len(strong_ids) / len(calls) if calls else 0,
        "precision_x2": precision(2),
        "precision_x5": precision(5),
        "precision_x10": precision(10),
        "confidence_warning": "Mẫu Strong đủ quan sát dưới 50."
        if len(observed_strong) < 50
        else None,
    }


@app.get("/api/v1/exports/analysis")
async def export_analysis(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Analyst", "Auditor"))],
    export_format: str = Query(default="json", alias="format", pattern=r"^(json|csv)$"),
    rule_version: str | None = None,
    limit: int = Query(default=10_000, ge=1, le=50_000),
) -> Response:
    statement = select(TokenCallRecord).order_by(TokenCallRecord.call_ts).limit(limit)
    if rule_version:
        statement = statement.where(TokenCallRecord.rule_version == rule_version)
    tokens = (await session.scalars(statement)).all()
    token_ids = [token.id for token in tokens]
    whales = (
        await session.scalars(
            select(WhaleEventRecord).where(WhaleEventRecord.token_call_id.in_(token_ids))
        )
    ).all()
    outcomes = (
        await session.scalars(
            select(MultipleEventRecord).where(MultipleEventRecord.token_call_id.in_(token_ids))
        )
    ).all()
    scores = (
        await session.scalars(
            select(ScoreEvaluationRecord).where(ScoreEvaluationRecord.token_call_id.in_(token_ids))
        )
    ).all()
    if export_format == "json":
        payload = {
            "tokens": [_token_json(row) for row in tokens],
            "whale_events": [_row_json(row) for row in whales],
            "outcomes": [_row_json(row) for row in outcomes],
            "scores": [_row_json(row) for row in scores],
        }
        return Response(
            json.dumps(jsonable_encoder(payload), ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=gem-filter-analysis.json"},
        )
    latest_score: dict[uuid.UUID, ScoreEvaluationRecord] = {}
    for score in sorted(scores, key=lambda item: item.evaluated_at):
        latest_score[score.token_call_id] = score
    max_outcome: dict[uuid.UUID, int] = {}
    for outcome in outcomes:
        if outcome.token_call_id:
            max_outcome[outcome.token_call_id] = max(
                max_outcome.get(outcome.token_call_id, 1), outcome.multiple
            )
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "id",
            "contract",
            "ticker",
            "call_ts",
            "call_mc_usd",
            "status",
            "rule_version",
            "latest_score",
            "latest_decision",
            "max_multiple",
        ],
    )
    writer.writeheader()
    for token in tokens:
        score = latest_score.get(token.id)
        writer.writerow(
            {
                "id": token.id,
                "contract": token.contract,
                "ticker": token.ticker,
                "call_ts": token.call_ts.isoformat(),
                "call_mc_usd": token.call_mc_usd,
                "status": token.status,
                "rule_version": token.rule_version,
                "latest_score": score.score if score else None,
                "latest_decision": score.decision if score else None,
                "max_multiple": max_outcome.get(token.id, 1),
            }
        )
    return Response(
        stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=gem-filter-analysis.csv"},
    )


@app.get("/api/v1/sources")
async def list_sources(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Auditor"))],
) -> list[dict[str, Any]]:
    return jsonable_encoder(
        [_row_json(row) for row in (await session.scalars(select(TelegramSource))).all()]
    )


@app.post("/api/v1/sources", status_code=201)
async def create_source(
    body: SourceCreateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, Any]:
    async with session.begin():
        if await session.scalar(
            select(TelegramSource).where(TelegramSource.channel_id == body.channel_id)
        ):
            raise HTTPException(status_code=409, detail="Source exists")
        source = TelegramSource(**body.model_dump())
        session.add(source)
        await session.flush()
        session.add(
            AuditLog(
                actor=user.email,
                action="SOURCE_CREATE",
                entity_type="telegram_source",
                entity_id=str(source.id),
                reason="Source configured",
                after=body.model_dump(),
            )
        )
    return jsonable_encoder(_row_json(source))


@app.patch("/api/v1/sources/{source_id}")
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    async with session.begin():
        source = await session.get(TelegramSource, source_id, with_for_update=True)
        if source is None:
            raise HTTPException(status_code=404, detail="Source not found")
        before = _row_json(source)
        for key, value in changes.items():
            setattr(source, key, value)
        session.add(
            AuditLog(
                actor=user.email,
                action="SOURCE_UPDATE",
                entity_type="telegram_source",
                entity_id=str(source.id),
                reason="Source configuration changed",
                before=jsonable_encoder(before),
                after=jsonable_encoder(_row_json(source)),
            )
        )
    return jsonable_encoder(_row_json(source))


@app.get("/api/v1/subscriptions")
async def list_subscriptions(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(current_user)],
) -> list[dict[str, Any]]:
    statement = select(UserSubscription)
    if user.role not in {"Admin", "Auditor"}:
        statement = statement.where(UserSubscription.user_id == uuid.UUID(user.id))
    return jsonable_encoder([_row_json(row) for row in (await session.scalars(statement)).all()])


@app.put("/api/v1/subscriptions/{chat_id}")
async def upsert_subscription(
    chat_id: str,
    body: SubscriptionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(current_user)],
) -> dict[str, Any]:
    if chat_id != body.destination_chat_id:
        raise HTTPException(status_code=422, detail="Chat ID mismatch")
    async with session.begin():
        record = await session.scalar(
            select(UserSubscription).where(UserSubscription.destination_chat_id == chat_id)
        )
        values = body.model_dump()
        if record is None:
            record = UserSubscription(user_id=uuid.UUID(user.id), **values)
            session.add(record)
        elif record.user_id != uuid.UUID(user.id) and user.role != "Admin":
            raise HTTPException(status_code=403, detail="Not owner")
        else:
            for key, value in values.items():
                setattr(record, key, value)
        session.add(
            AuditLog(
                actor=user.email,
                action="SUBSCRIPTION_UPSERT",
                entity_type="subscription",
                entity_id=chat_id,
                reason="User preference change",
                after=values,
            )
        )
    return jsonable_encoder(_row_json(record))


@app.get("/api/v1/whales")
async def list_whales(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Analyst", "Auditor"))],
) -> list[dict[str, Any]]:
    return jsonable_encoder(
        [_row_json(row) for row in (await session.scalars(select(WhaleProfile))).all()]
    )


@app.post("/api/v1/whales", status_code=201)
async def create_whale(
    body: WhaleProfileIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, Any]:
    async with session.begin():
        if await session.scalar(
            select(WhaleProfile).where(WhaleProfile.cluster_id == body.cluster_id)
        ):
            raise HTTPException(status_code=409, detail="Cluster exists")
        record = WhaleProfile(**body.model_dump())
        session.add(record)
        await session.flush()
        session.add(
            AuditLog(
                actor=user.email,
                action="WHALE_PROFILE_CREATE",
                entity_type="whale_profile",
                entity_id=str(record.id),
                reason="Profile created",
                after=jsonable_encoder(body.model_dump()),
            )
        )
    return jsonable_encoder(_row_json(record))


@app.get("/api/v1/blacklist")
async def list_blacklist(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Analyst", "Auditor"))],
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(select(ContractBlacklist).where(ContractBlacklist.active.is_(True)))
    ).all()
    return jsonable_encoder([_row_json(row) for row in rows])


@app.post("/api/v1/blacklist", status_code=201)
async def add_blacklist(
    body: BlacklistIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, Any]:
    async with session.begin():
        record = await session.scalar(
            select(ContractBlacklist).where(ContractBlacklist.contract == body.contract)
        )
        if record is None:
            record = ContractBlacklist(
                contract=body.contract,
                reason=body.reason,
                created_by=user.email,
                active=True,
            )
            session.add(record)
            await session.flush()
        else:
            record.reason = body.reason
            record.created_by = user.email
            record.active = True
        session.add(
            AuditLog(
                actor=user.email,
                action="CONTRACT_BLACKLIST",
                entity_type="contract",
                entity_id=body.contract,
                reason=body.reason,
            )
        )
    return jsonable_encoder(_row_json(record))


@app.delete("/api/v1/blacklist/{contract}")
async def remove_blacklist(
    contract: str,
    reason: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[UserContext, Depends(require_roles("Admin"))],
) -> dict[str, str]:
    async with session.begin():
        record = await session.scalar(
            select(ContractBlacklist).where(ContractBlacklist.contract == contract)
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Contract not blacklisted")
        record.active = False
        session.add(
            AuditLog(
                actor=user.email,
                action="CONTRACT_UNBLACKLIST",
                entity_type="contract",
                entity_id=contract,
                reason=reason,
            )
        )
    return {"status": "removed"}


@app.get("/api/v1/audit-logs")
async def audit_logs(
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[UserContext, Depends(require_roles("Admin", "Auditor"))],
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit))
    ).all()
    return jsonable_encoder([_row_json(row) for row in rows])


def _row_json(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    hidden = exclude or set()
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in hidden
    }


def _token_json(row: TokenCallRecord) -> dict[str, Any]:
    return _row_json(row)


def run() -> None:
    uvicorn.run(
        "gem_filter.api.main:app",
        host="0.0.0.0",  # noqa: S104 - container listener; ingress controls exposure
        port=8080,
        proxy_headers=True,
    )


if __name__ == "__main__":
    run()
