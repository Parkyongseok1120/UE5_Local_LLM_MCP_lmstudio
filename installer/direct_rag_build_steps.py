"""Generate bounded collector commands for one resolved installer build."""

from __future__ import annotations

from pathlib import Path

from installer.direct_rag_build_model import BUILD_OUTPUTS, PROJECT_DETAIL_OUTPUTS, BuildStep
from installer.direct_rag_build_scope import BuildScope


class StepBuilder:
    def __init__(self, scope: BuildScope, stage: Path) -> None:
        self.scope = scope
        self.stage = stage
        self.steps: list[BuildStep] = []
        self.required: list[str] = []

    def add(
        self,
        name: str,
        script: str,
        *arguments: str,
        output: str | None = None,
    ) -> None:
        path = self.scope.root / "scripts" / script
        if not path.is_file():
            raise FileNotFoundError(f"Direct RAG build dependency is missing: {path}")
        self.steps.append(BuildStep(name, (str(self.scope.python), str(path), *arguments)))
        if output:
            self.required.append(output)

    def add_documents(
        self,
        guidelines_root: Path | None,
        game_design_root: Path | None,
    ) -> None:
        guidelines = (guidelines_root or self.scope.root / "RAG_Project_Guidelines").resolve()
        if guidelines.is_dir():
            self.add(
                "collect-guidelines",
                "collect_project_guidelines.py",
                "--root", str(guidelines),
                "--out", str(self.stage / "raw_guidelines.jsonl"),
                output="raw_guidelines.jsonl",
            )
        game_design = (game_design_root or self.scope.root / "Game_Design_Docs").resolve()
        if game_design.is_dir():
            self.add(
                "collect-game-design",
                "collect_game_design_docs.py",
                "--root", str(game_design),
                "--out", str(self.stage / "raw_game_design.jsonl"),
                output="raw_game_design.jsonl",
            )

    def add_projects(self) -> None:
        if self.scope.tier in {"standard", "full"} and self.scope.included_projects:
            arguments = [
                "--workspace", str(self.scope.root),
                "--out-dir", str(self.stage),
            ]
            for descriptor in self.scope.included_projects:
                arguments.extend(("--project", str(descriptor)))
            self.add("collect-project-set", "direct_rag_project_set.py", *arguments)
            self.required.extend(("raw_projects.jsonl", *PROJECT_DETAIL_OUTPUTS))
            return

        arguments = ["--out", str(self.stage / "raw_projects.jsonl")]
        for descriptor in (*self.scope.included_projects, *self.scope.unresolved_dry_roots):
            arguments.extend(("--root", str(descriptor)))
        self.add(
            "collect-projects",
            "collect_unreal_projects.py",
            *arguments,
            output="raw_projects.jsonl",
        )

    def add_engine(self) -> None:
        if self.scope.tier not in {"standard", "full"}:
            return
        assert self.scope.engine_source is not None
        self.add(
            "collect-engine-public-symbols",
            "collect_unreal_symbols.py",
            "--root", str(self.scope.engine_source),
            "--out", str(self.stage / "raw_symbols.jsonl"),
            "--sidecar-out", str(self.stage / "sidecar_symbols_meta.jsonl"),
            "--tier", "public",
            "--scope", "engine",
            output="raw_symbols.jsonl",
        )
        if self.scope.tier == "full":
            self.add(
                "collect-engine-source",
                "collect_unreal_source.py",
                "--root", str(self.scope.engine_source),
                "--out", str(self.stage / "raw_source.jsonl"),
                output="raw_source.jsonl",
            )

    def add_index(self) -> None:
        self.add(
            "build-index",
            "direct_rag_build_generation.py",
            "--out-dir", str(self.stage),
            "--workspace", str(self.scope.root),
            "--engine-version", self.scope.engine_version,
            "--engine-association", self.scope.engine_association,
        )
        self.required.extend(BUILD_OUTPUTS)


def create_build_steps(
    scope: BuildScope,
    stage: Path,
    *,
    guidelines_root: Path | None,
    game_design_root: Path | None,
) -> tuple[tuple[BuildStep, ...], tuple[str, ...]]:
    builder = StepBuilder(scope, stage)
    builder.add_documents(guidelines_root, game_design_root)
    builder.add_projects()
    builder.add_engine()
    builder.add_index()
    return tuple(builder.steps), tuple(dict.fromkeys(builder.required))


__all__ = ["create_build_steps"]
