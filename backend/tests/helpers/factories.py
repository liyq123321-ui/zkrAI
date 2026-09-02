"""Reusable valid contract payloads for tests."""

from app.domain.types import (
    AcceptanceCriterion,
    AgentSpecProposal,
    ProjectSpecPayload,
    ReviewVerdict,
    SemanticReview,
    WorkBreakdown,
    WorkItemKind,
    WorkItemProposal,
)
from app.schemas.workflow import ProjectBrief


def make_complete_brief() -> ProjectBrief:
    return ProjectBrief(
        motivation="Coordinate delivery of a new workflow",
        final_objective="Turn an approved project brief into executable work",
        known_scope=["workflow orchestration", "agent specifications"],
        exclusions=["production deployment"],
        reference_materials=["brief-1"],
        expected_deliverables=["approved specification", "agent work items"],
        time_constraints="Complete in one quarter",
        staffing_constraints="One backend team",
        final_approver="approver-1",
        project_manager_ids=["pm-1"],
        root_owner_ids=["owner-1"],
    )


def make_valid_spec() -> ProjectSpecPayload:
    acceptance_criteria = [
        AcceptanceCriterion(
            requirement_ids=["FR-001"],
            criterion="The workflow accepts a complete brief",
            verification_method="API integration test",
            expected_result="A session is created with INTAKE phase",
        ),
        AcceptanceCriterion(
            requirement_ids=["NFR-001"],
            criterion="Repeated commands are handled idempotently",
            verification_method="Replay test",
            expected_result="The same command produces the same result",
        ),
    ]
    return ProjectSpecPayload(
        background_and_goals=["Make project planning reproducible"],
        users_and_scenarios=["A project manager submits a brief", "Agents prepare delivery plans"],
        functional_requirements=[
            {
                "requirement_id": "FR-001",
                "statement": "The system creates a workflow session from a brief",
                "priority": "MUST",
            }
        ],
        non_functional_requirements=[
            {
                "requirement_id": "NFR-001",
                "statement": "Commands are idempotent by command identifier",
                "priority": "MUST",
            }
        ],
        system_boundaries=["The workflow API and its persistence layer"],
        exclusions=["External deployment automation"],
        fixed_parts=["State transitions are validated"],
        configurable_parts=["Agent model selection"],
        extension_points=["Additional review providers"],
        core_objects=["Project", "Session", "SpecVersion", "WorkItem"],
        main_flows=["Submit brief, clarify, specify, review, decompose"],
        exceptional_flows=["Pause when blocking questions remain"],
        permissions_and_responsibilities=["Approver approves specifications"],
        deliverable_requirements=["Versioned project specification"],
        acceptance_criteria=acceptance_criteria,
        risks=["Ambiguous source requirements"],
        assumptions=["Actors have stable identifiers"],
        open_questions=[],
        source_refs=["artifact:brief-1"],
    )


def make_passing_semantic_review() -> SemanticReview:
    return SemanticReview(verdict=ReviewVerdict.PASS, findings=[])


def make_valid_breakdown() -> WorkBreakdown:
    milestone = WorkItemProposal(
        local_key="m-api",
        parent_key=None,
        kind=WorkItemKind.MILESTONE,
        title="Build the workflow API",
        objective="Deliver the API surface for project workflows",
        dependency_keys=[],
    )
    domain_task = WorkItemProposal(
        local_key="t-domain",
        parent_key="m-api",
        kind=WorkItemKind.TASK,
        title="Define domain contracts",
        objective="Implement strict domain models",
        dependency_keys=[],
    )
    api_task = WorkItemProposal(
        local_key="t-api",
        parent_key="m-api",
        kind=WorkItemKind.TASK,
        title="Implement session API",
        objective="Expose workflow session commands",
        dependency_keys=["t-domain"],
    )
    domain_agent_spec = AgentSpecProposal(
        work_item_key="t-domain",
        objective="Implement the strict domain contracts",
        scope=["domain models"],
        exclusions=["HTTP routes"],
        context_refs=["artifact:brief-1"],
        inputs=["approved project specification"],
        outputs=[{"name": "models", "format": "python", "required": True}],
        fixed_constraints=["Pydantic"],
        configurable_parts=[],
        extension_points=[],
        acceptance_criteria=[
            {
                "requirement_ids": ["FR-001"],
                "criterion": "The domain models reject invalid input",
                "verification_method": "Unit test",
                "expected_result": "Invalid input is rejected",
            }
        ],
        required_skills=["backend-development"],
        allowed_tools=["pytest"],
        allowed_paths=["app/domain"],
        responsible_role="Backend Engineer",
        suggested_assignee="domain-agent",
        dependency_keys=[],
        test_obligations=["Unit test"],
        risks=[],
        open_questions=[],
    )
    api_agent_spec = AgentSpecProposal(
        work_item_key="t-api",
        objective="Implement the session API",
        scope=["session routes"],
        exclusions=["frontend"],
        context_refs=["artifact:brief-1"],
        inputs=["approved project specification"],
        outputs=[{"name": "router", "format": "python", "required": True}],
        fixed_constraints=["FastAPI"],
        configurable_parts=[],
        extension_points=[],
        acceptance_criteria=[
            {
                "requirement_ids": ["FR-001"],
                "criterion": "The session route works",
                "verification_method": "HTTP test",
                "expected_result": "Returns 201",
            }
        ],
        required_skills=["backend-development"],
        allowed_tools=["pytest"],
        allowed_paths=["app/api"],
        responsible_role="Backend Engineer",
        suggested_assignee="backend-agent",
        dependency_keys=["t-domain"],
        test_obligations=["API integration test"],
        risks=[],
        open_questions=[],
    )
    return WorkBreakdown(
        milestones=[milestone],
        tasks=[domain_task, api_task],
        agent_specs=[domain_agent_spec, api_agent_spec],
    )

