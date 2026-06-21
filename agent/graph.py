"""Graph assembly: per-opportunity subgraph (fan-out via Send) + main graph
with the Human Approval interrupt.

Safety invariant enforced by topology: the only node that can schedule a send
is `scheduler`, and `scheduler` is reachable ONLY from `approval`'s "approved"
route — i.e. nothing reaches a send path without passing through Human Approval.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent import nodes
from agent.state import OppState, OutreachState


# ---------------------------------------------------------------------------
# Per-opportunity subgraph (no interrupt; safe to fan out in parallel)
# ---------------------------------------------------------------------------
def build_opp_subgraph():
    g = StateGraph(OppState)
    g.add_node("parse", nodes.parse_node)
    g.add_node("research", nodes.research_node)
    g.add_node("gap", nodes.gap_node)
    g.add_node("score", nodes.score_node)
    g.add_node("summary", nodes.summary_node)
    g.add_node("email_writer", nodes.email_writer_node)
    g.add_node("bump_retry", nodes.bump_retry_node)
    g.add_node("gmail_draft", nodes.gmail_draft_node)
    g.add_node("archived", nodes.archived_node)
    g.add_node("needs_review", nodes.needs_review_node)
    g.add_node("parked", nodes.parked_node)
    g.add_node("quality_needs_review", nodes.quality_needs_review_node)

    g.add_edge(START, "parse")
    # Funding Gate (conditional edge, decided in Python).
    g.add_conditional_edges("parse", nodes.funding_route, {
        "archived": "archived", "needs_review": "needs_review", "research": "research",
    })
    g.add_conditional_edges("research", nodes.research_route, {
        "needs_review": "needs_review", "gap": "gap",
    })
    g.add_edge("gap", "score")
    g.add_conditional_edges("score", nodes.score_route, {
        "summary": "summary", "parked": "parked",
    })
    g.add_conditional_edges("summary", nodes.summary_route, {
        "needs_review": "needs_review", "email_writer": "email_writer",
    })
    g.add_edge("email_writer", "quality_gate_router")
    # Quality Review (conditional edge): pass -> draft, fail -> retry once -> needs_review.
    g.add_node("quality_gate_router", lambda s: {})  # pass-through anchor for routing
    g.add_conditional_edges("quality_gate_router", nodes.quality_route, {
        "gmail_draft": "gmail_draft", "retry": "bump_retry",
        "needs_review": "quality_needs_review",
    })
    g.add_edge("bump_retry", "email_writer")

    for terminal in ("gmail_draft", "archived", "needs_review", "parked",
                     "quality_needs_review"):
        g.add_edge(terminal, END)
    return g.compile()


OPP_SUBGRAPH = build_opp_subgraph()


def process_opp_node(state: dict) -> dict:
    """Run the subgraph for one opportunity; merge only pipeline channels back.

    Static inputs (profile/templates/documents) are intentionally NOT returned,
    so parallel branches never collide on a reducer-less channel.
    """
    result = OPP_SUBGRAPH.invoke(state, {"recursion_limit": 50})
    return {k: result[k] for k in nodes.PIPELINE_KEYS if k in result}


# ---------------------------------------------------------------------------
# Main graph
# ---------------------------------------------------------------------------
def fan_out(state: dict):
    """Dispatch: emit one Send per discovered opportunity (parallel fan-out)."""
    discovered = state.get("discovered_opportunities", []) or []
    if not discovered:
        return "approval"
    sends = []
    for item in discovered:
        sends.append(Send("process_opp", {
            "opportunity": item,
            "user_profile": state.get("user_profile", {}),
            "uploaded_documents": state.get("uploaded_documents", {}),
            "email_templates": state.get("email_templates", {}),
            "email_retries": 0,
            # Propagate the submission mode so per-opportunity routing
            # (funding/score/quality gates) honours reactive vs proactive.
            "run_mode": state.get("run_mode"),
        }))
    return sends


def build_graph(checkpointer=None):
    g = StateGraph(OutreachState)
    g.add_node("intake", nodes.intake_node)
    g.add_node("discover", nodes.discover_node)
    g.add_node("process_opp", process_opp_node)
    g.add_node("approval", nodes.approval_node)
    g.add_node("scheduler", nodes.scheduler_node)
    g.add_node("finalize", nodes.finalize_node)

    g.add_edge(START, "intake")
    g.add_edge("intake", "discover")
    g.add_conditional_edges("discover", fan_out, ["process_opp", "approval"])
    g.add_edge("process_opp", "approval")
    g.add_conditional_edges("approval", nodes.route_after_approval, {
        "scheduler": "scheduler", "approval": "approval", "finalize": "finalize",
    })
    g.add_edge("scheduler", "approval")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)
