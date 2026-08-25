from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from erga_mcp.applications.role_profile import (
    JobRequirement,
    RoleProfile,
    concepts_and_terms,
    role_profile_from_text,
)
from erga_mcp.models import Evidence
from erga_mcp.portfolio.inventory import ProjectCandidate
from erga_mcp.resumes.artifacts import latex_to_text

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
    gap_strategy: str | None = None
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
    gap_strategy: str = "honest"


@dataclass(frozen=True)
class RoleAlignment:
    requirement_id: str
    requirement: str
    kind: str
    priority: str
    status: str
    supporting_evidence_ids: tuple[str, ...]
    supporting_project_ids: tuple[str, ...]

    def as_public_dict(self) -> dict[str, object]:
        return asdict(self)


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
    role_alignment: tuple[RoleAlignment, ...] = ()
    project_selection_mode: str = "automatic_strength"
    project_ids: tuple[str, ...] = ()
    project_titles: tuple[str, ...] = ()
    project_emphasis: str = "balanced"

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
            "project_selection": {
                "mode": self.project_selection_mode,
                "project_ids": list(self.project_ids),
                "project_titles": list(self.project_titles),
                "emphasis": self.project_emphasis,
            },
            "role_alignment": [item.as_public_dict() for item in self.role_alignment],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def as_storage_dict(self) -> dict[str, object]:
        payload = self.as_public_dict()
        payload.pop("current_question", None)
        payload["job_snapshot"] = self.job_snapshot
        payload["job_description"] = self.job_description
        return payload


def _supports_requirement(value: str, requirement: JobRequirement, role: str) -> bool:
    return bool(
        RoleProfile(role=role, requirements=(requirement,)).match(value).matched_requirement_ids
    )


def _role_alignment(
    profile: RoleProfile,
    *,
    evidence: tuple[Evidence, ...],
    candidates: tuple[ProjectCandidate, ...],
) -> tuple[RoleAlignment, ...]:
    aligned: list[RoleAlignment] = []
    for requirement in profile.requirements:
        evidence_ids = tuple(
            item.id
            for item in evidence
            if item.approved and _supports_requirement(item.text, requirement, profile.role)
        )
        project_ids = tuple(
            item.id
            for item in candidates
            if _supports_requirement(
                latex_to_text(item.latex) + " " + " ".join(item.tags),
                requirement,
                profile.role,
            )
        )
        requirement_features = set(requirement.features)
        partial = any(
            requirement_features & concepts_and_terms(value)
            for value in (
                *(item.text for item in evidence if item.approved),
                *(latex_to_text(item.latex) + " " + " ".join(item.tags) for item in candidates),
            )
        )
        aligned.append(
            RoleAlignment(
                requirement_id=requirement.id,
                requirement=requirement.text,
                kind=requirement.kind,
                priority=requirement.priority,
                status=(
                    "supported"
                    if evidence_ids or project_ids
                    else "partial"
                    if partial
                    else "unsupported"
                ),
                supporting_evidence_ids=evidence_ids,
                supporting_project_ids=project_ids,
            )
        )
    return tuple(aligned)


def build_tailoring_plan(
    *,
    job_url: str,
    company: str,
    role: str,
    job_snapshot: str,
    job_description: str,
    candidates: tuple[ProjectCandidate, ...],
    project_count: int,
    evidence: tuple[Evidence, ...] = (),
    role_profile: RoleProfile | None = None,
    now: datetime | None = None,
) -> TailoringPlan:
    """Create a deterministic, review-only plan before any résumé generation."""
    if project_count < 1:
        raise ValueError("project_count must be positive")
    timestamp = now or datetime.now(UTC)
    profile = role_profile or role_profile_from_text(job_description)
    role_alignment = _role_alignment(profile, evidence=evidence, candidates=candidates)
    questions: list[TailoringPlanQuestion] = []
    material_gaps = tuple(
        item
        for item in role_alignment
        if item.priority == "required" and item.status != "supported"
    )
    if material_gaps:
        questions.append(
            TailoringPlanQuestion(
                id="evidence_gaps",
                prompt=(
                    f"Erga found {len(material_gaps)} required requirement"
                    f"{'s' if len(material_gaps) != 1 else ''} without strong approved evidence. "
                    "How should the résumé handle that gap?"
                ),
                options=(
                    TailoringPlanOption(
                        id="honest_adjacent",
                        label="🧭 Emphasize adjacent proof",
                        description=(
                            "Emphasize the closest supported work without implying the missing "
                            "qualification."
                        ),
                        gap_strategy="honest",
                        recommended=True,
                    ),
                    TailoringPlanOption(
                        id="preserve_on_gaps",
                        label="🛡️ Keep claims conservative",
                        description=(
                            "Preserve approved copy and avoid model-authored bullets for this role."
                        ),
                        gap_strategy="preserve",
                    ),
                ),
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
    return TailoringPlan(
        id=f"plan_{uuid4().hex}",
        job_url=job_url,
        company=company,
        role=role,
        job_snapshot=job_snapshot,
        job_description=job_description,
        questions=tuple(questions),
        answers=(),
        status="planning",
        catalogue_candidate_count=len(candidates),
        created_at=timestamp,
        updated_at=timestamp,
        role_alignment=role_alignment,
        project_selection_mode=("automatic_strength" if candidates else "master_projects"),
    )


def migrate_tailoring_plan_project_selection(
    plan: TailoringPlan, *, now: datetime | None = None
) -> TailoringPlan:
    """Replace the legacy portfolio-choice question with automatic strength selection."""
    if plan.project_selection_mode != "legacy_question" and not any(
        question.id == "portfolio" for question in plan.questions
    ):
        return plan

    questions = tuple(question for question in plan.questions if question.id != "portfolio")
    answer_by_question = {answer.question_id: answer for answer in plan.answers}
    answers = tuple(
        answer_by_question[question.id]
        for question in questions
        if question.id in answer_by_question
    )
    if plan.status in _ACTIVE_STATUSES:
        status = "review" if len(answers) == len(questions) else "planning"
    else:
        status = plan.status
    return replace(
        plan,
        questions=questions,
        answers=answers,
        status=status,
        project_selection_mode=(
            "automatic_strength" if plan.catalogue_candidate_count else "master_projects"
        ),
        project_ids=(),
        project_titles=(),
        project_emphasis="balanced",
        updated_at=now or datetime.now(UTC),
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
    copy_strategy = option_by_question["copy_strategy"][answer_by_question["copy_strategy"]]
    gap_option = (
        option_by_question["evidence_gaps"][answer_by_question["evidence_gaps"]]
        if "evidence_gaps" in answer_by_question
        else None
    )
    gap_strategy = gap_option.gap_strategy if gap_option and gap_option.gap_strategy else "honest"
    return TailoringPreferences(
        project_ids=plan.project_ids,
        emphasis=plan.project_emphasis,
        allow_ai_synthesis=bool(copy_strategy.allow_ai_synthesis) and gap_strategy != "preserve",
        gap_strategy=gap_strategy,
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
                    gap_strategy=(
                        str(option["gap_strategy"])
                        if option.get("gap_strategy") is not None
                        else None
                    ),
                    recommended=bool(option.get("recommended", False)),
                )
                for option in question["options"]
            ),
        )
        for question in payload["questions"]
    )
    raw_project_selection = payload.get("project_selection")
    project_selection = raw_project_selection if isinstance(raw_project_selection, dict) else {}
    project_ids = tuple(str(value) for value in project_selection.get("project_ids", []))
    project_titles = tuple(str(value) for value in project_selection.get("project_titles", []))
    project_emphasis = str(project_selection.get("emphasis", "balanced"))
    project_selection_mode = str(project_selection.get("mode", "legacy_question"))
    if not project_ids:
        answer_by_question = {
            str(answer.get("question_id")): str(answer.get("option_id"))
            for answer in payload.get("answers", [])
            if isinstance(answer, dict)
        }
        portfolio_answer = answer_by_question.get("portfolio")
        portfolio_question = next(
            (question for question in questions if question.id == "portfolio"), None
        )
        portfolio_option = (
            next(
                (option for option in portfolio_question.options if option.id == portfolio_answer),
                None,
            )
            if portfolio_question is not None and portfolio_answer is not None
            else None
        )
        if portfolio_option is not None:
            project_ids = portfolio_option.project_ids
            project_titles = portfolio_option.project_titles
            project_emphasis = portfolio_option.emphasis
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
        role_alignment=tuple(
            RoleAlignment(
                requirement_id=str(item["requirement_id"]),
                requirement=str(item["requirement"]),
                kind=str(item["kind"]),
                priority=str(item["priority"]),
                status=str(item["status"]),
                supporting_evidence_ids=tuple(
                    str(value) for value in item.get("supporting_evidence_ids", [])
                ),
                supporting_project_ids=tuple(
                    str(value) for value in item.get("supporting_project_ids", [])
                ),
            )
            for item in payload.get("role_alignment", [])
        ),
        project_selection_mode=project_selection_mode,
        project_ids=project_ids,
        project_titles=project_titles,
        project_emphasis=project_emphasis,
    )
