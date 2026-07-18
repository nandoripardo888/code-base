from argparse import ArgumentParser
from pathlib import Path
from shutil import copytree


def main() -> None:
    parser = ArgumentParser(description="Copy the deterministic test repository fixture.")
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    source = Path(__file__).parents[1] / "tests" / "fixtures" / "sample_repository"
    copytree(source, arguments.destination, dirs_exist_ok=False)


if __name__ == "__main__":
    main()
