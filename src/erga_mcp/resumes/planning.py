from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from erga_mcp.portfolio.inventory import ProjectCandidate, _score, select_projects
from erga_mcp.resumes.quality import build_project_identity_profile

_ACTIVE_STATUSES = frozenset({"planning", "review", "ready"})


@dataclass(frozen=True)
class TailoringPlanOption:
    id: str
    label: str
    description: str
    project_ids: tuple[str, ...] = ()
    project_titles: tuple[str, ...] = ()
    emphasis: str = "balanced"
    allow_ai_synthesis: bool | None = None
    recommended: bool = False


@dataclass(frozen=True)
class TailoringPlanQuestion:
    id: str
    prompt: str
    options: tuple[TailoringPlanOption, ...]


@dataclass(frozen=True)
class TailoringPlanAnswer:
    question_id: str
    option_id: str


@dataclass(frozen=True)
class TailoringPreferences:
    project_ids: tuple[str, ...]
    emphasis: str
    allow_ai_synthesis: bool


@dataclass(frozen=True)
class TailoringPlan:
    id: str
    job_url: str
    company: str
    role: str
    job_snapshot: str
    job_description: str
    questions: tuple[TailoringPlanQuestion, ...]
    answers: tuple[TailoringPlanAnswer, ...]
    status: str
    catalogue_candidate_count: int
    created_at: datetime
    updated_at: datetime

    @property
    def current_question(self) -> TailoringPlanQuestion | None:
        if self.status != "planning" or len(self.answers) >= len(self.questions):
            return None
        return self.questions[len(self.answers)]

    def as_public_dict(self) -> dict[str, object]:
        current = self.current_question
        return {
            "id": self.id,
            "job_url": self.job_url,
            "company": self.company,
            "role": self.role,
            "questions": [asdict(question) for question in self.questions],
            "answers": [asdict(answer) for answer in self.answers],
            "current_question": asdict(current) if current is not None else None,
            "status": self.status,
            "catalogue_candidate_count": self.catalogue_candidate_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def as_storage_dict(self) -> dict[str, object]:
        payload = self.as_public_dict()
        payload.pop("current_question", None)
        payload["job_snapshot"] = self.job_snapshot
        payload["job_description"] = self.job_description
        return payload


def _portfolio_option(
    option_id: str,
    label: str,
    description: str,
    candidates: tuple[ProjectCandidate, ...],
    *,
    emphasis: str,
    recommended: bool = False,
) -> TailoringPlanOption:
    return TailoringPlanOption(
        id=option_id,
        label=label,
        description=description,
        project_ids=tuple(candidate.id for candidate in candidates),
        project_titles=tuple(candidate.title for candidate in candidates),
        emphasis=emphasis,
        recommended=recommended,
    )


def _portfolio_options(
    candidates: tuple[ProjectCandidate, ...],
    job_description: str,
    *,
    project_count: int,
) -> tuple[TailoringPlanOption, ...]:
    if project_count == 0:
        return (
            TailoringPlanOption(
                id="master_projects",
                label="🛡️ Keep current projects",
                description="No complete approved catalogue replacement is available yet.",
                recommended=True,
            ),
        )
    matching = tuple(
        candidate for candidate in candidates if _score(candidate, job_description) > 0
    )
    balanced = select_projects(candidates, job_description, max_projects=project_count)
    if len(balanced) < project_count:
        balanced = tuple(dict.fromkeys((*balanced, *matching, *candidates)))[:project_count]
    profiles = {candidate.id: build_project_identity_profile(candidate) for candidate in matching}
    role_fit = tuple(
        sorted(
            matching,
            key=lambda item: (
                -_score(item, job_description),
                -profiles[item.id].quality_score,
                item.id,
            ),
        )[:project_count]
    )
    quality_first = tuple(
        sorted(
            matching,
            key=lambda item: (
                -profiles[item.id].quality_score,
                -_score(item, job_description),
                item.id,
            ),
        )[:project_count]
    )
    raw = (
        _portfolio_option(
            "balanced",
            "⚖️ Balanced",
            "Best mix of role fit, strong approved copy, and distinct project stories.",
            balanced,
            emphasis="balanced",
            recommended=True,
        ),
        _portfolio_option(
            "role_fit",
            "🎯 Closest match",
            "Prioritize the projects with the strongest direct overlap with this posting.",
            role_fit,
            emphasis="technical_depth",
        ),
        _portfolio_option(
            "quality_first",
            "💎 Strongest copy",
            "Favor the strongest approved outcome bullets among role-relevant projects.",
            quality_first,
            emphasis="outcome_impact",
        ),
    )
    unique: list[TailoringPlanOption] = []
    seen: set[tuple[str, ...]] = set()
    for option in raw:
        if len(option.project_ids) != project_count or option.project_ids in seen:
            continue
        unique.append(option)
        seen.add(option.project_ids)
    if len(unique) == 1 and len(candidates) > project_count:
        alternative = tuple(
            (*balanced[:-1], next(item for item in candidates if item not in balanced))
        )
        unique.append(
            _portfolio_option(
                "alternate",
                "🎯 Alternate mix",
                "Keep the leading projects and swap the final story for broader coverage.",
                alternative,
                emphasis="technical_depth",
            )
        )
    return tuple(unique[:3])


def build_tailoring_plan(
    *,
    job_url: str,
    company: str,
    role: str,
    job_snapshot: str,
    job_description: str,
    candidates: tuple[ProjectCandidate, ...],
    project_count: int,
    now: datetime | None = None,
) -> TailoringPlan:
    """Create a deterministic, review-only plan before any résumé generation."""
    if project_count < 1:
        raise ValueError("project_count must be positive")
    timestamp = now or datetime.now(UTC)
    effective_project_count = min(project_count, len(candidates))
    portfolio_options = _portfolio_options(
        candidates,
        job_description,
        project_count=effective_project_count,
    )
    questions: list[TailoringPlanQuestion] = []
    if len(portfolio_options) > 1:
        questions.append(
            TailoringPlanQuestion(
                id="portfolio",
                prompt="Which project story should this résumé tell?",
                options=portfolio_options,
            )
        )
    questions.append(
        TailoringPlanQuestion(
            id="copy_strategy",
            prompt="How aggressively should Erga tailor the project bullets?",
            options=(
                TailoringPlanOption(
                    id="synthesize",
                    label="✨ Evidence-backed tailoring",
                    description=(
                        "Draft role-specific bullets, but only from approved evidence and "
                        "Git facts."
                    ),
                    allow_ai_synthesis=True,
                    recommended=True,
                ),
                TailoringPlanOption(
                    id="preserve",
                    label="🛡️ Preserve master copy",
                    description=(
                        "Prefer existing approved master bullets and deterministic ordering."
                    ),
                    allow_ai_synthesis=False,
                ),
            ),
        )
    )
    answers: tuple[TailoringPlanAnswer, ...] = ()
    if len(portfolio_options) == 1:
        answers = (TailoringPlanAnswer("portfolio", portfolio_options[0].id),)
        questions.insert(
            0,
            TailoringPlanQuestion(
                id="portfolio",
                prompt="Only one complete approved project set is available.",
                options=portfolio_options,
            ),
        )
    return TailoringPlan(
        id=f"plan_{uuid4().hex}",
        job_url=job_url,
        company=company,
        role=role,
        job_snapshot=job_snapshot,
        job_description=job_description,
        questions=tuple(questions),
        answers=answers,
        status="planning",
        catalogue_candidate_count=len(candidates),
        created_at=timestamp,
        updated_at=timestamp,
    )


def answer_tailoring_plan(
    plan: TailoringPlan,
    *,
    question_id: str,
    option_id: str,
    now: datetime | None = None,
) -> TailoringPlan:
    if plan.status not in _ACTIVE_STATUSES:
        raise ValueError("tailoring plan can no longer be edited")
    question = plan.current_question
    if question is None or question.id != question_id:
        raise ValueError("answer must target the current question")
    if option_id not in {option.id for option in question.options}:
        raise ValueError("answer option does not exist")
    answers = (*plan.answers, TailoringPlanAnswer(question_id, option_id))
    return replace(
        plan,
        answers=answers,
        status="review" if len(answers) == len(plan.questions) else "planning",
        updated_at=now or datetime.now(UTC),
    )


def reopen_previous_question(plan: TailoringPlan, *, now: datetime | None = None) -> TailoringPlan:
    if plan.status not in _ACTIVE_STATUSES or not plan.answers:
        raise ValueError("tailoring plan has no prior answer to edit")
    return replace(
        plan,
        answers=plan.answers[:-1],
        status="planning",
        updated_at=now or datetime.now(UTC),
    )


def approve_tailoring_plan(plan: TailoringPlan, *, now: datetime | None = None) -> TailoringPlan:
    if plan.status != "review" or len(plan.answers) != len(plan.questions):
        raise ValueError("answer every tailoring-plan question before generation")
    return replace(plan, status="ready", updated_at=now or datetime.now(UTC))


def set_tailoring_plan_status(
    plan: TailoringPlan, status: str, *, now: datetime | None = None
) -> TailoringPlan:
    if status not in {"cancelled", "completed", "ready"}:
        raise ValueError("unsupported tailoring-plan status")
    return replace(plan, status=status, updated_at=now or datetime.now(UTC))


def tailoring_plan_preferences(plan: TailoringPlan) -> TailoringPreferences:
    if len(plan.answers) != len(plan.questions):
        raise ValueError("tailoring plan is incomplete")
    answer_by_question = {answer.question_id: answer.option_id for answer in plan.answers}
    option_by_question = {
        question.id: {option.id: option for option in question.options}
        for question in plan.questions
    }
    portfolio = option_by_question["portfolio"][answer_by_question["portfolio"]]
    copy_strategy = option_by_question["copy_strategy"][answer_by_question["copy_strategy"]]
    return TailoringPreferences(
        project_ids=portfolio.project_ids,
        emphasis=portfolio.emphasis,
        allow_ai_synthesis=bool(copy_strategy.allow_ai_synthesis),
    )


def tailoring_plan_from_storage(payload: object) -> TailoringPlan:
    if not isinstance(payload, dict):
        raise ValueError("stored tailoring plan must be an object")
    questions = tuple(
        TailoringPlanQuestion(
            id=str(question["id"]),
            prompt=str(question["prompt"]),
            options=tuple(
                TailoringPlanOption(
                    id=str(option["id"]),
                    label=str(option["label"]),
                    description=str(option["description"]),
                    project_ids=tuple(str(value) for value in option.get("project_ids", [])),
                    project_titles=tuple(str(value) for value in option.get("project_titles", [])),
                    emphasis=str(option.get("emphasis", "balanced")),
                    allow_ai_synthesis=option.get("allow_ai_synthesis"),
                    recommended=bool(option.get("recommended", False)),
                )
                for option in question["options"]
            ),
        )
        for question in payload["questions"]
    )
    return TailoringPlan(
        id=str(payload["id"]),
        job_url=str(payload["job_url"]),
        company=str(payload["company"]),
        role=str(payload["role"]),
        job_snapshot=str(payload["job_snapshot"]),
        job_description=str(payload["job_description"]),
        questions=questions,
        answers=tuple(
            TailoringPlanAnswer(
                question_id=str(answer["question_id"]),
                option_id=str(answer["option_id"]),
            )
            for answer in payload["answers"]
        ),
        status=str(payload["status"]),
        catalogue_candidate_count=int(payload["catalogue_candidate_count"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
    )
