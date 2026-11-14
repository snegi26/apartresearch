"""Inspect solver that routes generation through the Constitutional Guardian."""

from __future__ import annotations

from inspect_ai.model import ChatMessageAssistant, ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

from epistemic_virtue.guardian import EMBEDDED_CONTEXT, guarded_generate
from epistemic_virtue.models import InspectModelClient, get_client


@solver
def guardian_gate(guardian: str, system: str, max_revisions: int = 2) -> Solver:
    """Draft with the evaluated model, then revise until ``guardian`` passes it.

    The final draft is always scored, including when the Guardian never passed
    it; the outcome and every verdict are recorded under ``metadata["guardian"]``.
    Do not use the evaluated model as its own Guardian.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        actor = InspectModelClient()  # the model under evaluation
        reviews, drafts, final = [], [], {}
        async for event in guarded_generate(
            actor,
            get_client(guardian),
            actor_system=system,
            actor_prompt=state.input_text,
            intent=state.input_text,
            context=EMBEDDED_CONTEXT,
            max_revisions=max_revisions,
        ):
            if event["type"] == "draft":
                drafts.append(event["text"])
            elif event["type"] == "verdict":
                reviews.append({k: event[k] for k in ("round", "verdict", "error")})
            else:
                final = event

        state.messages.append(ChatMessageAssistant(content=final["response"]))
        state.output = ModelOutput.from_content(model=str(state.model), content=final["response"])
        state.metadata["guardian"] = {
            "model": guardian,
            "outcome": final["outcome"],
            "rounds": final["rounds"],
            "first_draft": drafts[0],
            "reviews": reviews,
        }
        return state

    return solve
