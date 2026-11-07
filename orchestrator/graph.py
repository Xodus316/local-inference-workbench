"""Sequential analysis and conditional model routing on LangGraph."""
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


def build(runtime):
    graph = StateGraph(State)
    graph.add_node("code_build", lambda s: {"code_build": runtime.run_code_build(s)})
    graph.add_node("primary", lambda s: {"primary": runtime.assess(s, "primary", "fast_primary")})
    graph.add_node("secondary", lambda s: {"secondary": runtime.assess(s, "secondary", "fast_secondary")})
    graph.add_node("research", lambda s: {"research": runtime.research(s)})
    graph.add_node("decision", lambda s: {"decision": runtime.decide(s)})
    graph.add_node("synthesis", lambda s: {"final": runtime.assess(s, "synthesis", "fast_primary", compare=True)})
    graph.add_node("deep", lambda s: {"final": runtime.assess(s, "deep", "deep_reasoner", compare="secondary" in s)})
    graph.add_node("plan", lambda s: {"final": runtime.plan(s)})
    graph.add_node("patch", lambda s: {"patch": runtime.propose(s, "patch", "editor")})
    graph.add_node("review", lambda s: {"review": runtime.propose(s, "review", "reviewer")})

    def after_primary(s):
        if not s["primary"]["ok"]:
            return END
        mode = runtime.bundle(s)["mode"]
        if mode in {"balanced", "verified"}:
            return "secondary"
        return {"plan": "plan", "edit": "patch"}.get(mode, END)

    def after_secondary(s):
        if not s["secondary"]["ok"]:
            return END
        if runtime.needs_research(s):
            return "research"
        return "decision" if runtime.bundle(s)["mode"] == "verified" else "synthesis"

    def after_decision(s):
        decision = s["decision"]
        if not decision["ok"] or decision["result"]["confidence"] < 0.75:
            return END
        return {"neither": "deep", "combine": "synthesis"}.get(decision["result"]["choice"], END)

    graph.add_conditional_edges(START, lambda s: {"deep": "deep", "build": "code_build"}.get(runtime.bundle(s)["mode"], "primary"))
    graph.add_conditional_edges("primary", after_primary)
    graph.add_conditional_edges("secondary", after_secondary)
    graph.add_conditional_edges("decision", after_decision)
    graph.add_edge("research", "synthesis")
    graph.add_edge("patch", "review")
    for name in ("synthesis", "plan", "deep", "review", "code_build"):
        graph.add_edge(name, END)
    return graph.compile()
