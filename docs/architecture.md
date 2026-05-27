# Architecture

How the moving parts fit together. The goal of this document is to
give you enough of a mental model that you can find any line of code
from a runtime symptom — and trust the framework to handle the
plumbing you don't see.

> **What this doc covers:** boot sequence, DI mechanisms, CQRS layer,
> pipeline runtime, web layer, observability stack, layout invariants.
> **When to read it:** the second day on the codebase, or while
> tracing a runtime symptom back to source. **Where else to look:**
> - First-day tour: [`overview.md`](overview.md).
> - Stage DAG details: [`pipeline.md`](pipeline.md).
> - HTTP shape: [`api-reference.md`](api-reference.md).

---

## 1. Two frameworks, one application

flydocs builds on two complementary Firefly Framework libraries.

| Library                       | Provides                                                                                  |
| ----------------------------- | ----------------------------------------------------------------------------------------- |
| `fireflyframework-pyfly`      | Application lifecycle, dependency injection, CQRS, REST controllers, EDA, observability, security, actuator. |
| `fireflyframework-agentic`    | Multimodal LLM agents (`FireflyAgent`), prompt templates + registry, pipeline DAG runtime (`PipelineEngine`), tool registry. |

Pyfly owns the **service runtime** (boot, DI, HTTP, EDA, health,
W3C trace context). Agentic owns the **AI runtime** (prompts, agents, pipeline). flydocs
is the glue that turns a `POST /api/v1/extract` into a pipeline of
multimodal LLM calls, with the results validated, judged, and rule-checked
on the way out.

---

## 2. Boot sequence

```
uvicorn flydocs.main:app
         │
         ▼
   flydocs.main         ← builds PyFlyApplication(FlydocsApplication)
         │                    ▲
         │                    └── reads fireflyframework-pyfly config + env vars
         ▼
   PyFlyApplication._lifespan (FastAPI lifespan hook)
         │
         ▼
   await pyfly_app.startup()
         │
         ├─▶ scan_packages    – discovers @configuration, @service, @command_handler,
         │                      @query_handler, @rest_controller, @controller_advice
         │
         ├─▶ build DI graph   – resolves every @bean in IDPCoreConfiguration
         │                      (settings → repository, webhook,
         │                       database_health, prompt_catalog →
         │                       extractor, splitter, judge, rule_engine,
         │                       …, orchestrator). The EDA EventPublisher
         │                       is provided by fireflyframework-pyfly's
         │                       EdaAutoConfiguration.
         │
         ├─▶ register routes  – fireflyframework-pyfly mounts every @rest_controller on FastAPI
         │
         ├─▶ start actuator   – /actuator/health, /actuator/metrics
         │
         └─▶ start observability – structlog + Prometheus + OTLP exporters
```

By the time the first request lands, every cross-cutting service the
controller depends on (orchestrator, command bus, query bus, settings,
repositories) is already wired and warm.

---

## 3. Dependency injection — the four mechanisms

`fireflyframework-pyfly` has four complementary ways to put a class in
the container. flydocs uses all four, and the choice is significant.

### 3a. `@configuration` + `@bean`

Used for everything that is **not a domain object** — repositories,
the prompt catalog, the LLM stages, the orchestrator, the health
indicators. Each `@bean` method becomes a singleton; its parameter
annotations are how the container injects dependencies. (The EDA
`EventPublisher` is provided by `pyfly.eda.auto_configuration.EdaAutoConfiguration`
upstream; flydocs just declares it as a constructor parameter
where needed.)

```python
# core/configuration.py
@configuration
class IDPCoreConfiguration:
    @bean
    def settings(self) -> IDPSettings:
        return get_settings()

    @bean
    def prompt_catalog(self) -> PromptCatalog:
        return PromptCatalog.from_resources()

    @bean
    def extractor(self, settings: IDPSettings, prompts: PromptCatalog) -> MultimodalExtractor:
        return MultimodalExtractor(
            template=prompts.extract,
            model=settings.model,
            fallback_model=settings.fallback_model,
        )
```

This is the **single place** outside the stereotype decorators where
beans live. Anything that needs construction wiring goes here.

### 3b. Stereotype decorators (`@service`, `@command_handler`, …)

Used for domain classes that the framework should scan from a package.
The class is itself a bean; the decorator tags it for the container's
discovery pass.

```python
# core/services/extract/extract_handler.py
@command_handler
@service
class ExtractHandler(CommandHandler[ExtractCommand, ExtractionResult]):
    def __init__(self, orchestrator: PipelineOrchestrator, settings: IDPSettings) -> None:
        self._orchestrator = orchestrator
        self._settings = settings
```

The constructor's type annotations drive injection.

### 3c. `@rest_controller`

A specialised stereotype that also tells `fireflyframework-pyfly` to
mount the class onto the FastAPI app and register every
`@get_mapping`/`@post_mapping` route method.

```python
# web/controllers/extract_controller.py
@rest_controller
@request_mapping("/api/v1")
class ExtractController:
    def __init__(self, commands: CommandBus, settings: IDPSettings) -> None:
        self._commands = commands
        self._settings = settings

    @post_mapping("/extract")
    async def extract(self, request: Valid[Body[ExtractionRequest]]) -> ExtractionResult:
        return await self._commands.send(ExtractCommand(request=request))
```

### 3d. `@controller_advice`

Used by the global exception advice that maps domain errors to RFC 7807
problem responses. One class, scanned once, gets its `@exception_handler`
methods registered with the FastAPI app.

---

## 4. The CQRS layer

Controllers never talk to handlers directly. They go through the
`CommandBus` / `QueryBus`:

```
controller ──CommandBus.send(cmd)──▶ fireflyframework-pyfly dispatches by Generic ──▶ handler.do_handle(cmd)
controller ──QueryBus.query(q)──▶ fireflyframework-pyfly dispatches by Generic ──▶ handler.do_handle(q)
```

Commands and queries are **frozen dataclasses** parameterised by their
return type:

```python
@dataclass(frozen=True)
class ExtractCommand(Command[ExtractionResult]):
    request: ExtractionRequest

@dataclass(frozen=True)
class GetExtractionQuery(Query[Extraction | None]):
    extraction_id: str
```

The handler's class declaration carries the same Generic args
(`CommandHandler[ExtractCommand, ExtractionResult]`), and
`fireflyframework-pyfly` uses type introspection to wire the bus.

Why bother? Because the controller stays a thin HTTP adapter: no DB
access, no LLM calls, no domain logic. The handler is the unit of work,
and the bus is a clean seam for cross-cutting concerns (tracing,
metrics, future retries).

---

## 5. The pipeline runtime

The orchestrator does not run stages by hand. Each request builds a
fresh DAG using `fireflyframework-agentic`'s `PipelineBuilder` and runs
it through `PipelineEngine`:

```python
builder = PipelineBuilder("flydocs")
builder.add_node("load",      CallableStep(self._step_load),      timeout_seconds=10)
builder.add_node("split",     CallableStep(self._step_split),     timeout_seconds=60)
builder.add_node("extract",   CallableStep(self._step_extract),   timeout_seconds=240)
builder.add_node("judge",     CallableStep(self._step_judge),     timeout_seconds=180)
builder.add_node("rules",     CallableStep(self._step_rules),     timeout_seconds=180)
builder.chain("load", "split", "extract", "judge", "rules")
engine = builder.build()
await engine.run(context=ctx)
```

Each step reads/writes shared state in `context.metadata`. The engine:

- enforces a per-stage timeout,
- emits `on_node_start` / `on_node_complete` / `on_node_error` events
  (we plug a structured-log handler into them),
- groups concurrent failures into the context so the orchestrator can
  decide whether to keep running or short-circuit.

Per-doc fan-out (one document, multiple `document_types[]` — multi-doc
files with a splitter pass) is implemented with `asyncio.gather`
_inside_ the stage. The pipeline itself stays a flat chain;
concurrency is a stage concern.

---

## 6. Prompts as data

Every LLM stage's system + user prompt lives in a YAML file under
`src/flydocs/resources/prompts/`. At boot, `PromptCatalog` reads
each file via `PromptLoader.from_file`, instantiates a `PromptTemplate`,
and registers it with the framework-wide `PromptRegistry`.

The catalog is a normal `fireflyframework-pyfly` bean. The LLM services receive their
template through constructor injection — they never import a template
global. Two consequences:

- prompt text edits don't touch Python, and
- you can register an additional version of a template (e.g. for an
  A/B test) and select it at request time through `options.model` or a
  custom resolution rule, with no code change to the service.

See [prompts.md](prompts.md).

---

## 7. The web layer

`fireflyframework-pyfly`'s FastAPI adapter wraps controllers in a thin
shell. Three things worth knowing:

1. **`Valid[Body[...]]`** runs pydantic validation against the DTO and
   returns RFC 7807 problem details on failure (mapped by the
   `@controller_advice`).
2. **`Header[str]`** binds HTTP headers (we use it for
   `Idempotency-Key`).
3. **`PathVar[str]`** binds path parameters.

Controllers return plain DTOs; FastAPI serialises them. No manual
`JSONResponse(...)`.

---

## 8. Observability + ops

`fireflyframework-pyfly`'s `@enable_core_stack` brings the
observability stack in already-configured:

- **Structured logs** (structlog JSON). The pipeline stamps a
  `correlation_id` on every line.
- **OpenTelemetry traces**. Configure the exporter via standard
  `OTEL_EXPORTER_OTLP_*` env vars.
- **Prometheus metrics** at `/actuator/metrics` — CQRS handler latency
  histograms, HTTP latency, runtime metrics.
- **Actuator endpoints**: `/actuator/health`, `/actuator/health/liveness`,
  `/actuator/health/readiness`, `/actuator/info`.

Logs, traces, and metrics share a `correlation_id`, so correlating an
API log line to a worker log line is just a grep.

---

## 9. Layout invariants

These are intentional constraints. Breaking them tends to surface as
subtle bugs at boot.

| Rule                                                                          | Why                                                                                  |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `interfaces/` is the only thing the public HTTP API speaks.                   | SQLAlchemy / framework types must not leak into responses.                           |
| `core/services/` is pure async Python. No imports from `web/`.                | Keeps the domain layer testable without spinning up FastAPI.                         |
| `web/` is `@rest_controller` beans only.                                      | Handlers and services don't depend on FastAPI either.                                |
| `core/configuration.py` is the **single** place where extra `@bean`s live.    | All wiring in one file — easier to audit the graph.                                  |
| Commands and queries are frozen dataclasses.                                  | Pyfly introspects Generic args for bus routing; frozen makes equality free.          |
| Bbox stays normalised to `[0, 1]`.                                            | The `clamp_bbox` helper is the single enforcement point.                             |
| Document bytes never hit the DB.                                              | `extractions` rows store base64 only because the worker re-renders the request — keep it that way unless you wire blob storage. |
