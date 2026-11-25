"""First-time Google onboarding and the signed-in user's own profile."""

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import AfterValidator, Field

from ..core.db import with_identity
from ..core.http import (
    Body,
    CtxDep,
    GuardedRoute,
    HttpError,
    IdentityDep,
    InviteCode,
    NameField,
    TokenField,
    access,
    domain_errors,
)
from ..core.security import hash_password, hash_token, new_token
from .auth import AUTH_LIMIT, start_session
from .consent import TERMS_VERSION
from .invites import RegisterConsents

router = APIRouter(route_class=GuardedRoute)
PHONE_RE = re.compile(r"\+?[0-9 .-]{8,20}")


def _phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not PHONE_RE.fullmatch(value):
        raise ValueError("invalid phone")
    return value


PhoneField = Annotated[str | None, AfterValidator(_phone)]
Band = Annotated[float, Field(ge=0, le=9, multiple_of=0.5)]
Referral = Literal["facebook", "tiktok", "instagram", "youtube", "friend", "teacher", "search", "other"]
Skill = Literal["listening", "reading", "writing", "speaking"]
Style = Literal["video", "exercise", "flashcard", "tutor"]


class OnboardingBody(Body):
    ticket: TokenField
    role: Literal["student", "teacher"]
    displayName: NameField
    # Asked again, with more choices, on the setup screens after sign-up.
    referralSource: Referral | None = None
    phone: PhoneField = None
    # Student only.
    inviteCode: InviteCode | None = None
    currentBand: Band | None = None
    targetBand: Band | None = None
    ageGroup: Literal["18_plus", "under_18"] | None = None
    consents: RegisterConsents | None = None


class ProfileBody(Body):
    displayName: NameField
    phone: PhoneField = None
    currentBand: Band | None = None
    targetBand: Band | None = None


@router.post("/api/onboarding", status_code=201)
@access("public", limit=AUTH_LIMIT)
def onboarding(body: OnboardingBody, ctx: CtxDep, response: Response) -> dict[str, Any]:
    # The account is Google-only until a password is set through the reset flow.
    password_hash = hash_password(new_token())
    ticket = hash_token(body.ticket)
    if body.role == "teacher":
        with ctx.pool.connection() as conn, domain_errors():
            conn.execute(
                "select app.onboard_teacher(%s, %s, %s, %s, %s, %s)",
                (ticket, password_hash, body.displayName, body.phone, body.referralSource, ctx.now()),
            )
        response.status_code = 202
        return {"status": "pending"}

    if body.inviteCode is None:
        raise HttpError(400, "invite_required")
    if body.ageGroup != "18_plus":
        raise HttpError(403, "age_restricted")
    if body.consents is None or not body.consents.core_learning:
        raise HttpError(400, "consent_required")
    try:
        with ctx.pool.connection() as conn, domain_errors():
            row = conn.execute(
                """select app.onboard_student(
                     %s, %s, %s, %s, %s, %s, %s::numeric, %s::numeric, %s, %s, %s) as id""",
                (
                    ticket,
                    body.inviteCode,
                    password_hash,
                    body.displayName,
                    body.phone,
                    body.referralSource,
                    body.currentBand,
                    body.targetBand,
                    body.consents.ai_processing,
                    TERMS_VERSION,
                    ctx.now(),
                ),
            ).fetchone()
    except HttpError as err:
        # Name the bad invite code so the form can point at that field.
        if err.status == 404:
            raise HttpError(404, "invite_invalid") from err
        raise
    assert row is not None  # noqa: S101
    return {**start_session(ctx, response, row["id"]), "status": "active"}


class SetupBody(Body):
    """First-run setup. Teachers send only the name and age; students send everything."""

    displayName: NameField
    age: Annotated[int, Field(ge=18, le=100)]
    occupation: Literal["school", "university", "working", "other"] | None = None
    referralSource: Referral | None = None
    targetBand: Annotated[float, Field(ge=5, le=9, multiple_of=0.5)] | None = None
    cefrLevel: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None = None
    skillRank: list[Skill] | None = None
    studyMinutes: Literal[10, 20, 30, 45, 60] | None = None
    studyTime: Literal["morning", "noon", "evening", "night"] | None = None
    studyStyles: list[Style] | None = None
    reminders: bool | None = None


STUDENT_FIELDS = (
    "occupation",
    "referralSource",
    "targetBand",
    "cefrLevel",
    "skillRank",
    "studyMinutes",
    "studyTime",
    "studyStyles",
    "reminders",
)

# The uploaded photo wins over the Google picture; the query string busts the browser cache.
ME_COLUMNS = """id, email, display_name as "displayName", role,
  case when photo_updated_at is not null
    then '/api/me/photo?v=' || (extract(epoch from photo_updated_at) * 1000)::bigint
    else avatar_url end as "avatarUrl",
  phone, referral_source as "referralSource", current_band::float as "currentBand",
  target_band::float as "targetBand", created_at as "createdAt",
  case auth_provider when 'google' then 'google' else 'email' end as "signInMethod",
  age, occupation, cefr_level as "cefrLevel", skill_rank as "skillRank",
  study_minutes as "studyMinutes", study_time as "studyTime", study_styles as "studyStyles",
  reminders, onboarded_at as "onboardedAt\""""
PHOTO_MAX = 300 * 1024


@router.get("/api/me")
@access(("student", "teacher", "admin"))
def me(ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        return tx.execute(f"select {ME_COLUMNS} from users where id = %s", (identity.user_id,)).fetchone()  # noqa: S608


@router.patch("/api/me")
@access(("student", "teacher", "admin"))
def update_me(body: ProfileBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    with with_identity(ctx.pool, identity) as tx:
        tx.execute(
            "select app.update_profile(%s, %s, %s::numeric, %s::numeric)",
            (body.displayName, body.phone, body.currentBand, body.targetBand),
        )
        return tx.execute(f"select {ME_COLUMNS} from users where id = %s", (identity.user_id,)).fetchone()  # noqa: S608


@router.post("/api/me/setup")
@access(("student", "teacher"))
def setup(body: SetupBody, ctx: CtxDep, identity: IdentityDep) -> Any:
    if identity.role == "student":
        if any(getattr(body, f) is None for f in STUDENT_FIELDS):
            raise HttpError(400, "setup_incomplete")
        assert body.skillRank is not None and body.studyStyles is not None  # noqa: S101
        if sorted(body.skillRank) != sorted(("listening", "reading", "writing", "speaking")):
            raise HttpError(400, "setup_incomplete")
        styles = sorted(set(body.studyStyles))
        values = body.model_dump()
    else:
        styles = None
        values = {**body.model_dump(), **dict.fromkeys(STUDENT_FIELDS)}
    with with_identity(ctx.pool, identity) as tx:
        tx.execute(
            """select app.complete_setup(%s, %s::smallint, %s, %s, %s::numeric, %s, %s::text[],
                 %s::smallint, %s, %s::text[], %s, %s)""",
            (
                body.displayName,
                body.age,
                values["occupation"],
                values["referralSource"],
                values["targetBand"],
                values["cefrLevel"],
                values["skillRank"],
                values["studyMinutes"],
                values["studyTime"],
                styles,
                values["reminders"],
                ctx.now(),
            ),
        )
        return tx.execute(f"select {ME_COLUMNS} from users where id = %s", (identity.user_id,)).fetchone()  # noqa: S608


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9"


@router.put("/api/me/photo")
@access(("student", "teacher", "admin"))
async def put_photo(request: Request, ctx: CtxDep, identity: IdentityDep) -> Any:
    """The browser crops and re-encodes the picture to a small JPEG before sending it."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "image/jpeg":
        raise HttpError(415, "not_jpeg")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > PHOTO_MAX:
        raise HttpError(413, "file_too_large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > PHOTO_MAX:
            raise HttpError(413, "file_too_large")
    if len(data) < 100 or not _is_jpeg(bytes(data)):
        raise HttpError(415, "not_jpeg")
    await run_in_threadpool(_save_photo, ctx, identity, bytes(data))
    return await run_in_threadpool(me, ctx, identity)


def _save_photo(ctx: CtxDep, identity: IdentityDep, data: bytes) -> None:
    with with_identity(ctx.pool, identity) as tx:
        tx.execute("select app.set_photo(%s, %s)", (data, ctx.now()))


@router.get("/api/me/photo")
@access(("student", "teacher", "admin"))
def get_photo(ctx: CtxDep, identity: IdentityDep) -> Response:
    with with_identity(ctx.pool, identity) as tx:
        row = tx.execute("select app.my_photo() as jpeg").fetchone()
    if row is None or row["jpeg"] is None:
        raise HttpError(404, "not_found")
    return Response(
        bytes(row["jpeg"]),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
