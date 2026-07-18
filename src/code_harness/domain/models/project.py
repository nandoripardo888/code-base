from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    root: str
