"""Analysis and conditional model routing for standalone and embedded runtimes."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    run_id: str
    code_build: dict
    primary: dict
    secondary: dict
    research: dict
    decision: dict
    final: dict
    patch: dict
    review: dict


def build(runtime, *, parallel_analysis=None, complete_pair_on_error=False):
    """Build a graph over a host's approved bundle, stage methods and admission policy.

    Standalone callers run sequentially. An embedding host may admit independent
    readers concurrently and retain a successful sibling when the other fails.
    """
    graph = StateGraph(State)
    graph.add_node("code_build", lambda s: {"code_build": runtime.run_code_build(s)})
    def primary(state):
        return {"primary": runtime.assess(state, "primary", "fast_primary")}

    def secondary(state):
        return {"secondary": runtime.assess(state, "secondary", "fast_secondary")}

    graph.add_node("primary", primary)
    graph.add_node("secondary", secondary)
    graph.add_node("research", lambda s: {"research": runtime.research(s)})
    graph.add_node("decision", lambda s: {"decision": runtime.decide(s)})
    graph.add_node("synthesis", lambda s: {"final": runtime.assess(s, "synthesis", "fast_primary", compare=True)})
    graph.add_node("deep", lambda s: {"final": runtime.assess(s, "deep", "deep_reasoner", compare="secondary" in s)})
    graph.add_node("plan", lambda s: {"final": runtime.plan(s)})
    graph.add_node("patch", lambda s: {"patch": runtime.propose(s, "patch", "editor")})
    graph.add_node("review", lambda s: {"review": runtime.propose(s, "review", "reviewer")})

    def after_primary(s):
        mode = runtime.bundle(s)["mode"]
        if not s["primary"]["ok"] and not (complete_pair_on_error and mode in {"balanced", "verified"}):
            return END
        if mode in {"balanced", "verified"}:
            return "secondary"
        return {"plan": "plan", "edit": "patch"}.get(mode, END)

    def after_secondary(s):
        if not s["primary"]["ok"] or not s["secondary"]["ok"]:
            return END
        if runtime.needs_research(s):
            return "research"
        return "decision" if runtime.bundle(s)["mode"] == "verified" else "synthesis"

    def after_decision(s):
        decision = s["decision"]
        if not decision["ok"] or decision["result"]["confidence"] < 0.75:
            return END
        return {"neither": "deep", "combine": "synthesis"}.get(decision["result"]["choice"], END)

    def start(state):
        mode = runtime.bundle(state)["mode"]
        if mode in {"balanced", "verified"} and parallel_analysis and parallel_analysis(state):
            return ["paired_primary", "paired_secondary"]
        return {"deep": "deep", "build": "code_build"}.get(mode, "primary")

    if parallel_analysis is not None:
        graph.add_node("paired_primary", primary)
        graph.add_node("paired_secondary", secondary)
        graph.add_node("pair_complete", lambda _: {})
        graph.add_edge(["paired_primary", "paired_secondary"], "pair_complete")
        graph.add_conditional_edges("pair_complete", after_secondary)
    graph.add_conditional_edges(START, start)
    graph.add_conditional_edges("primary", after_primary)
    graph.add_conditional_edges("secondary", after_secondary)
    graph.add_conditional_edges("decision", after_decision)
    graph.add_edge("research", "synthesis")
    graph.add_edge("patch", "review")
    for name in ("synthesis", "plan", "deep", "review", "code_build"):
        graph.add_edge(name, END)
    return graph.compile()
