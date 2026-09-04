"""Concrete, hand-written implementation plans for workflow boundary tests."""


def implementation_plan(requirement_ids=None):
    return {
        "overview": "Validate session commands before persistence and return stable resource identifiers.",
        "interface_notes": "The session API is the boundary consumed by the web client.",
        "interfaces": [{
            "name": "Create session", "kind": "HTTP", "definition": "POST /sessions",
            "inputs": [{"name": "request_id", "data_type": "string", "required": True,
                        "description": "Client-generated identifier used to detect repeated requests."}],
            "outputs": [{"name": "session_id", "data_type": "string", "required": True,
                         "description": "Stable identifier of the persisted session."}],
            "error_handling": ["Return 422 for a blank request identifier before inserting a session."],
        }],
        "data_structures": [{"name": "SessionCommand", "definition": "{request_id: string, brief: object}",
                             "validation_rules": ["request_id must be non-blank and unique within a request scope"]}],
        "design_decisions": [{"decision": "Use a transaction for creation and its idempotency receipt.",
                              "reason": "Repeated commands must resolve to the original resources.", "status": "PROPOSED"}],
        "steps": [{
            "step_id": "S1", "title": "Validate and persist the command",
            "requirement_ids": requirement_ids or ["FR-001", "NFR-001"],
            "implementation_method": "Validate the request model, load a receipt by request_id, return its session if present, otherwise insert the session and receipt in one transaction.",
            "expected_output": "Session creation handler and a persisted idempotency receipt.",
            "verification_method": "Submit one request twice and assert the same session_id and exactly one database row; submit a blank id and assert 422 with no inserted rows.",
        }],
    }
