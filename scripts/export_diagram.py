"""Emit a Mermaid diagram of the compiled LangGraph (main graph + subgraph).

Usage:
    python -m scripts.export_diagram            # prints + writes docs/graph.mmd
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.graph import OPP_SUBGRAPH, build_graph  # noqa: E402


def main() -> None:
    main_graph = build_graph(checkpointer=None)

    main_mmd = main_graph.get_graph().draw_mermaid()
    sub_mmd = OPP_SUBGRAPH.get_graph().draw_mermaid()

    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "graph_main.mmd").write_text(main_mmd, encoding="utf-8")
    (out_dir / "graph_subgraph.mmd").write_text(sub_mmd, encoding="utf-8")

    print("=== MAIN GRAPH ===\n")
    print(main_mmd)
    print("\n=== PER-OPPORTUNITY SUBGRAPH ===\n")
    print(sub_mmd)
    print(f"\nWritten to {out_dir/'graph_main.mmd'} and {out_dir/'graph_subgraph.mmd'}")


if __name__ == "__main__":
    main()
