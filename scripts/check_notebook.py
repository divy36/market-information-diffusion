"""Execute the dependency-free walkthrough cells in order without modifying it."""
import json
import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    notebook = root / "notebooks/02_portfolio_walkthrough.ipynb"
    cells = json.loads(notebook.read_text())["cells"]
    namespace = {"__name__": "__main__"}
    previous = Path.cwd()
    count = 0
    try:
        os.chdir(root)
        for index, cell in enumerate(cells):
            if cell["cell_type"] == "code":
                exec(compile("".join(cell["source"]), f"{notebook.name}:cell-{index}", "exec"), namespace)
                count += 1
    finally:
        os.chdir(previous)
    print(f"Executed {count} notebook code cells successfully.")


if __name__ == "__main__":
    main()
