"""Thin Typer adapter: validate inputs, invoke application commands, show facts."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from csv import Error as CsvError
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError

from services.application.ingest import (
    DEFAULT_BUILD_REVISION,
    IngestFile,
    IngestResult,
    ReprocessSource,
)
from services.application.ingest import reprocess_source as run_reprocess
from services.application.process import ProcessRun, RetryRun, RunResult
from services.application.process import ingest_and_process as run_ingest
from services.application.process import process_run as run_process
from services.application.process import retry_run as run_retry
from services.cli.contracts import IngestInput, ProcessingInput, ReprocessInput
from services.domain.runs import RunState
from services.infrastructure.runtime import (
    DEFAULT_DATABASE_URL,
    Runtime,
    RuntimeConfigurationError,
    Settings,
    build_runtime,
)

RuntimeFactory = Callable[[Settings], AbstractContextManager[Runtime]]
BatchSize = Annotated[
    int, typer.Option(envvar="BATCH_SIZE", help="Records per parse batch.")
]


@contextmanager
def operator_errors() -> Generator[None]:
    try:
        yield
    except ValidationError as error:
        # Do not expose inputs (particularly database credentials) in diagnostics.
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        typer.echo(f"Error: {details}", err=True)
        raise typer.Exit(2) from None
    except KeyError as error:
        typer.echo(f"Error: Requested record was not found: {error}", err=True)
        raise typer.Exit(1) from None
    except (
        ValueError,
        LookupError,
        OSError,
        CsvError,
        NotImplementedError,
        RuntimeConfigurationError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from None


def show_ingest(result: IngestResult, *, reprocess: bool = False) -> None:
    if reprocess:
        typer.echo("New reprocess run created from frozen source.")
    elif result.duplicate_upload:
        typer.echo(
            "Exact file already ingested; current run reused, new occurrence recorded."
        )
    elif result.run_reused:
        typer.echo("Idempotency replay; original occurrence and run reused.")
    else:
        typer.echo("New source and run created.")
    typer.echo(f"Source: {result.source_file_id}")
    typer.echo(f"Occurrence: {result.source_occurrence_id}")
    typer.echo(f"Run: {result.run_id}")
    if checkpoint := result.resume_from_checkpoint:
        typer.echo(
            f"Resume checkpoint: {checkpoint.stage}, "
            f"record {checkpoint.record_ordinal} "
            "(at submission)"
        )


def show_run(result: RunResult) -> None:
    typer.echo(f"Run: {result.run_id}")
    state = "Processed" if result.state is RunState.STAGED else result.state.value
    typer.echo(f"State: {state}")
    typer.echo(f"Raw records: {result.parse.record_count}")
    typer.echo(f"Candidates normalised: {result.normalise.record_count}")
    typer.echo(f"Outcomes: {result.classification_counts}")
    typer.echo(f"Canonical revisions promoted: {result.promoted_count}")


def create_app(runtime_factory: RuntimeFactory = build_runtime) -> typer.Typer:
    cli = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @cli.callback()
    def configure(
        ctx: typer.Context,
        database_url: Annotated[
            str, typer.Option(envvar="DATABASE_URL")
        ] = DEFAULT_DATABASE_URL,
        source_root: Annotated[str, typer.Option(envvar="SOURCE_ROOT")] = "var/sources",
        build_revision: Annotated[
            str, typer.Option(envvar="APPLICATION_BUILD_REVISION")
        ] = DEFAULT_BUILD_REVISION,
    ) -> None:
        """Freeze CSV sources and manage complete processing runs."""
        with operator_errors():
            ctx.obj = Settings.model_validate(
                {
                    "database_url": database_url,
                    "source_root": source_root,
                    "build_revision": build_revision,
                }
            )

    @cli.command()
    def ingest(
        ctx: typer.Context,
        source: Path,
        actor_label: Annotated[str, typer.Option()] = "operator",
        idempotency_key: Annotated[str | None, typer.Option()] = None,
        process: Annotated[
            bool, typer.Option("--process", help="Request complete processing.")
        ] = False,
        batch_size: BatchSize = 1000,
    ) -> None:
        """Freeze a file; optionally process or resume its existing run."""
        with operator_errors():
            boundary = IngestInput(
                source=source,
                actor_label=actor_label,
                idempotency_key=idempotency_key,
                process=process,
                batch_size=batch_size,
            )
            processed: RunResult | None = None
            with runtime_factory(cast(Settings, ctx.obj)) as runtime:
                with boundary.source.open("rb") as content:
                    result = run_ingest(
                        IngestFile(
                            content,
                            boundary.source.name,
                            str(boundary.source),
                            boundary.actor_label,
                            boundary.idempotency_key,
                        ),
                        runtime.uow_factory,
                        runtime.source_store,
                        runtime.clock,
                        process=boundary.process,
                        batch_size=boundary.batch_size,
                        build_revision=runtime.build_revision,
                    )
                if boundary.process:
                    processed = run_process(
                        ProcessRun(result.run_id, boundary.batch_size),
                        runtime.uow_factory,
                        runtime.source_store,
                        runtime.clock,
                    )
            show_ingest(result)
            if processed is not None:
                show_run(processed)
            else:
                typer.echo("No processing requested.")

    @cli.command()
    def process(ctx: typer.Context, run_id: str, batch_size: BatchSize = 1000) -> None:
        """Process the frozen source for a run."""
        with operator_errors():
            boundary = ProcessingInput.model_validate(
                {"run_id": run_id, "batch_size": batch_size}
            )
            with runtime_factory(cast(Settings, ctx.obj)) as runtime:
                result = run_process(
                    ProcessRun(boundary.run_id, boundary.batch_size),
                    runtime.uow_factory,
                    runtime.source_store,
                    runtime.clock,
                )
            show_run(result)

    @cli.command()
    def retry(ctx: typer.Context, run_id: str, batch_size: BatchSize = 1000) -> None:
        """Resume a run from its durable pipeline boundary."""
        with operator_errors():
            boundary = ProcessingInput.model_validate(
                {"run_id": run_id, "batch_size": batch_size}
            )
            with runtime_factory(cast(Settings, ctx.obj)) as runtime:
                result = run_retry(
                    RetryRun(boundary.run_id, boundary.batch_size),
                    runtime.uow_factory,
                    runtime.source_store,
                    runtime.clock,
                )
            show_run(result)

    @cli.command()
    def reprocess(
        ctx: typer.Context,
        source_file_id: str,
        occurrence_id: Annotated[
            str, typer.Option(help="Requesting source occurrence UUID.")
        ],
    ) -> None:
        """Create a successor run; use process RUN_ID to process it."""
        with operator_errors():
            boundary = ReprocessInput.model_validate(
                {
                    "source_file_id": source_file_id,
                    "source_occurrence_id": occurrence_id,
                }
            )
            with runtime_factory(cast(Settings, ctx.obj)) as runtime:
                result = run_reprocess(
                    ReprocessSource(
                        boundary.source_file_id, boundary.source_occurrence_id
                    ),
                    runtime.uow_factory(),
                    runtime.clock,
                    build_revision=runtime.build_revision,
                )
            show_ingest(result, reprocess=True)
            typer.echo("No processing requested.")

    return cli


app = create_app()

if __name__ == "__main__":
    app()
