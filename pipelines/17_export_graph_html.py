from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.graph_visualizer import export_graph_html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--title", default="HfO2-FerroKG Knowledge Graph")
    args = parser.parse_args()
    print(export_graph_html(output_path=args.output, title=args.title))


if __name__ == "__main__":
    main()
