# Prompts

flydocs keeps every LLM prompt **outside the Python source**. Each
stage's system + user template lives as a YAML file under
`src/flydocs/resources/prompts/`, loaded at boot by a single
`PromptCatalog` bean and registered with the framework-wide
`PromptRegistry`. This makes prompt edits a configuration change
rather than a code change, gives every template a stable
`name + version` identity, and keeps the LLM stages themselves
template-agnostic — they take a `PromptTemplate` through the DI
container and never import a prompt global.

---

## 1. Anatomy of a YAML template

```yaml
name: flydocs/extract
version: "1.0.0"
description: >-
  Schema-driven multimodal extraction. One LLM call returns every
  declared field with its value, confidence, page and normalised
  bounding box.
required_variables:
  - schema_json
  - media_type
  - page_count
  - intention
system_template: |
  You are an expert document understanding system specialised in
  intelligent document processing.

  For every field group and field in the schema, return:
  - ``value``      -- the extracted value, ...
  ...
user_template: |
  This document is {{ media_type }} with {{ page_count }} page(s).

  Extraction intention: {{ intention }}
  ...
```

Five fields drive the loader:

| Field                | Meaning                                                                                  |
| -------------------- | ---------------------------------------------------------------------------------------- |
| `name`               | Unique key in the `PromptRegistry`. Conventionally `flydocs/<stage>`.                |
| `version`            | Semantic version. Bump when the contract (variables / output shape) changes.             |
| `description`        | Human-readable summary. Surfaces in `PromptInfo` and the framework's prompt browser.     |
| `required_variables` | Names the renderer must receive. Missing required vars raise `PromptValidationError`.    |
| `system_template`    | Jinja2 string — the agent's static instructions.                                         |
| `user_template`      | Jinja2 string — the first element of the multimodal payload (followed by document bytes).|

The YAML body is passed verbatim as kwargs to `PromptTemplate(...)`,
so any field the framework adds in the future is automatically
supported.

---

## 2. How they're loaded

`PromptCatalog.from_resources()` (called by the `prompt_catalog` bean
in `IDPCoreConfiguration`):

```python
@classmethod
def from_resources(cls, *, registry: PromptRegistry | None = None) -> PromptCatalog:
    registry = registry or prompt_registry
    templates: dict[str, PromptTemplate] = {}
    prompts_dir = _resources_dir()
    for stage, filename in _PROMPT_FILES.items():
        template = PromptLoader.from_file(prompts_dir / filename)
        registry.register(template)
        templates[stage] = template
    return cls(templates, registry=registry)
```

Two things happen for every YAML file:

1. **`PromptLoader.from_file`** reads the YAML and constructs a
   `PromptTemplate`. Jinja2 syntax errors surface here (before the
   service starts taking traffic).
2. **`registry.register(template)`** indexes the template by
   `name + version` in the framework-wide `PromptRegistry`. Anything
   else in the codebase can look the template up by name without
   importing the catalog.

The catalog is then handed to every LLM stage's bean through DI:

```python
@bean
def extractor(self, settings: IDPSettings, prompts: PromptCatalog) -> MultimodalExtractor:
    return MultimodalExtractor(
        template=prompts.extract,
        model=settings.model,
        fallback_model=settings.fallback_model,
    )
```

The extractor itself just renders:

```python
prompt = self._template.render(
    schema_json=schema_json,
    media_type=media_type,
    page_count=page_count,
    intention=intention,
)
agent = FireflyAgent(name=..., model=..., instructions=prompt.system, output_type=...)
result = await agent.run([prompt.user, BinaryContent(data=..., media_type=...)])
```

---

## 3. The shipped catalog

| Catalog name             | YAML file                       | Stage it powers                                                |
| ------------------------ | ------------------------------- | -------------------------------------------------------------- |
| `extract`                | `extract.yaml`                  | Multimodal field extraction + bounding boxes.                  |
| `splitter`               | `splitter.yaml`                 | Multi-document page-range identification.                       |
| `content_authenticity`   | `content_authenticity.yaml`     | Content-integrity audit.                                        |
| `visual_authenticity`    | `visual_authenticity.yaml`      | Caller-defined visual validators.                               |
| `judge`                  | `judge.yaml`                    | Cross-check extraction vs. source document.                     |
| `rule_engine`            | `rule_engine.yaml`              | Per-level business-rule evaluation.                             |

All registered at version `1.0.0`. Bump the version when you change a
template's contract (variables or output shape).

Look one up at runtime:

```python
catalog: PromptCatalog = container.resolve(PromptCatalog)
template = catalog.extract                                  # named accessor
template = catalog.get("flydocs/extract", version="1.0.0")
```

---

## 4. Editing a prompt safely

1. **Identify the contract.** `required_variables` lists what every
   caller must supply. Don't drop a variable without updating every
   call site.
2. **Keep the output contract.** The `extract` template specifies
   `value`, `confidence`, `page`, `bbox{xmin,ymin,xmax,ymax}`, `notes`.
   The post-processor reads those exact field names — renaming them
   breaks `normalise_doc`.
3. **Bump the version** when you change semantics. The registry
   indexes by `name + version`; an older version can coexist for an
   A/B test or a controlled rollout.
4. **Run the real-LLM smoke test** after the change. Use whichever
   provider your `FLYDOCS_MODEL` is set to — `fireflyframework-genai`
   reads the matching env var automatically:

   ```bash
   ANTHROPIC_API_KEY=… task test:llm   # for anthropic:* models
   # or
   OPENAI_API_KEY=…    task test:llm   # for openai:* models
   ```

   It pretty-prints every field + judge verdict + rule output, so a
   prompt regression is hard to miss.

---

## 5. Adding a new prompt

If you're adding a new pipeline stage, the prompt comes with it.

1. Create `src/flydocs/resources/prompts/<stage>.yaml` with the
   five required fields above.
2. Register it in `PromptCatalog._PROMPT_FILES`:

   ```python
   _PROMPT_FILES: dict[str, str] = {
       ...,
       "<stage>": "<stage>.yaml",
   }
   ```

3. Add a named accessor on `PromptCatalog`:

   ```python
   @property
   def <stage>(self) -> PromptTemplate:
       return self._templates["<stage>"]
   ```

4. Wire the bean in `IDPCoreConfiguration` — your new service takes
   `template: PromptTemplate` in its constructor, and the bean method
   pulls the right template from `prompts.<stage>`.

The framework's `PromptRegistry` is a process-wide singleton, so any
other module that needs to look the template up by `name + version`
gets it for free.

---

## 6. Why this design

- **Operability** — prompts ship in the same wheel as the code, but
  edits land as a diff in `.yaml` files, not Python. Reviews are
  short.
- **Reproducibility** — every prompt has a stable identifier
  (`name + version`). A change tracked through git history and version
  bumps is auditable per incident.
- **Decoupling** — the LLM stages don't import templates; they take a
  `PromptTemplate` through the container. Swapping templates per
  tenant or per experiment is a wiring change, not a code change.
- **Validation at boot** — Jinja2 syntax errors and missing
  `required_variables` references show up before the first request.
