# CI/CD

flydesk-idp ships two GitHub Actions workflows: one PR gate and one
multi-arch image publisher. Both live under `.github/workflows/`.

```
.github/workflows/
├── pr-gate.yaml          ← runs on every pull request
└── docker-publish.yaml   ← runs on push to main and on SemVer tags
```

The image is published to **GitHub Container Registry (GHCR)** as
`ghcr.io/firefly-operationos/flydesk-idp` (the owner is normalised to
lower-case because GHCR rejects upper-case namespaces).

---

## 1. PR gate — `.github/workflows/pr-gate.yaml`

Runs on `pull_request` against `main`/`develop` and via
`workflow_dispatch`. Concurrency is keyed on the branch ref so a new
push cancels the previous run.

| Job | What it does |
| --- | --- |
| **lint** | `ruff check` + `ruff format --check` against the working tree. |
| **typecheck** *(advisory)* | `pyright src/flydesk_idp`. `continue-on-error: true` so it doesn't block merges yet — flip the flag once the codebase is fully typed. |
| **unit** | `pytest -q tests/unit` — 94 tests, in-memory SQLite + in-memory EDA bus, no real Postgres. |
| **docker-build** | `docker buildx build --platform linux/amd64 --load` against the real `Dockerfile`. Smoke-tests that the image still builds after the PR's changes. No push. |

All four jobs check out the **sibling firefly-framework repos** into
`./vendor/` and rewrite the path sources in `pyproject.toml`:

```bash
git clone --depth=1 --branch main \
  https://github.com/fireflyframework/fireflyframework-pyfly.git \
  ./vendor/pyfly
git clone --depth=1 --branch main \
  https://github.com/fireflyframework/fireflyframework-agentic.git \
  ./vendor/fireflyframework-agentic

sed -i \
  -e 's|path = "\.\./\.\./fireflyframework/fireflyframework-pyfly"|path = "./vendor/pyfly"|' \
  -e 's|path = "\.\./\.\./fireflyframework/fireflyframework-agentic"|path = "./vendor/fireflyframework-agentic"|' \
  pyproject.toml
```

This is the same trick `docker-compose.yml` uses with BuildKit
`additional_contexts` and the `Dockerfile`'s `--build-context`
parameters — the source of truth for the path-source rewrite is one
sed command in the workflow.

> **Private framework repos?** If
> `fireflyframework/fireflyframework-pyfly` ever becomes private, add a
> classic PAT with `repo:read` scope as the `FIREFLY_GH_TOKEN` secret
> and pass it to `git clone` via
> `https://x-access-token:${FIREFLY_GH_TOKEN}@github.com/...`.

---

## 2. Multi-arch publish — `.github/workflows/docker-publish.yaml`

Triggers:

| Trigger | Tags applied |
| --- | --- |
| `push` to `main` | `main`, `sha-<short>`, `latest` |
| `push` of a SemVer tag (`v1.2.3`) | `v1.2.3`, `v1.2`, `v1`, `sha-<short>` |
| `workflow_dispatch` | `manual-<run_id>` |

Platforms: **`linux/amd64`** and **`linux/arm64`**. QEMU sets up
cross-arch emulation; buildx publishes a single multi-arch manifest
list so consumers pull the right variant transparently.

```bash
# Both arches land on the same tag:
docker pull ghcr.io/firefly-operationos/flydesk-idp:latest

# Force a specific arch (useful for cross-arch testing on a workstation):
docker pull --platform linux/amd64 ghcr.io/firefly-operationos/flydesk-idp:latest
docker pull --platform linux/arm64 ghcr.io/firefly-operationos/flydesk-idp:latest
```

### Caching

The job uses the GitHub Actions cache backend:

```yaml
cache-from: type=gha
cache-to: type=gha,mode=max
```

This makes warm builds finish in roughly the time it takes to run
`uv sync` plus the final image assembly — usually under three
minutes on a clean main push.

### Supply chain

Two opt-in features that are on by default:

- **Provenance**: `provenance: true` on `docker/build-push-action`
  records an SLSA-style attestation that the image came from this
  workflow run. This one is emitted by buildkit and travels with the
  manifest — works on every org plan.
- **SBOM**: `sbom: true` attaches a CycloneDX SBOM to the manifest.
  `cosign verify-attestation` can read it.

There is also an *advisory* `actions/attest-build-provenance` step
that uploads a separate signed attestation document to the GitHub
attestation store. That feature requires the *Build & Validate
Attestations* setting, which is gated behind a paid plan (or making
the repository public). The step is marked
`continue-on-error: true`, so a free-tier org simply logs the 403 and
the publish workflow stays green — the buildkit-emitted provenance
above is still applied.

The job's `permissions:` block grants the workflow `packages: write`
(GHCR push), `id-token: write` and `attestations: write` (the
attestation API call, when the feature is enabled).

### Package visibility

GHCR packages created by Actions are **private by default**. To pull
the image without a token, flip the visibility in the package
settings:

1. Go to <https://github.com/orgs/firefly-operationOS/packages/container/flydesk-idp/settings>.
2. Under *Danger Zone*, click **Change visibility** → **Public**.

Or pull with auth:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <user> --password-stdin
docker pull ghcr.io/firefly-operationos/flydesk-idp:latest
```

The GitHub Action's built-in token has `packages: read` (and `write`
on the publish workflow) by default, so cross-repo CI pulls work
without an extra secret.

### Image labels

`docker/metadata-action` stamps the OCI annotations every registry
listing relies on:

```
org.opencontainers.image.title       flydesk-idp
org.opencontainers.image.description Firefly Desk -- Intelligent Document Processing service…
org.opencontainers.image.source      https://github.com/firefly-operationOS/flydesk-idp
org.opencontainers.image.licenses    Apache-2.0
org.opencontainers.image.vendor      Firefly Software Solutions Inc
```

---

## 3. Local pre-commit

`.pre-commit-config.yaml` runs three hook groups:

| Group | Hooks |
| --- | --- |
| `pre-commit-hooks` | `check-merge-conflict`, `detect-private-key`, `end-of-file-fixer`, `trailing-whitespace`, `check-yaml`, `check-toml`, `check-added-large-files` (1 MiB ceiling) |
| `ruff-pre-commit` | `ruff-check --fix`, `ruff-format` |
| `local` | `no-anthropic-keys` — grep for `sk-ant-…` in any staged text file so a stray key never makes it into a commit |

Install once after cloning:

```bash
uv run pre-commit install --hook-type pre-commit
```

The CI gate runs the same `ruff` rules, so a clean pre-commit run is
a good predictor of a green PR.

---

## 4. Releasing a new version

1. Land everything you want on `main`.
2. Tag the head with SemVer (`v0.2.0`, `v0.2.1`, …).
3. Push the tag:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

4. `docker-publish.yaml` fires, builds for both arches, pushes
   `ghcr.io/firefly-operationos/flydesk-idp:v0.2.0`, `:v0.2`, `:v0`,
   and `:sha-<short>`, plus `:latest` if the tag points at the head
   of the default branch.

The summary panel on the workflow run prints the final image digest
and the full tag list — paste it into the release notes.

---

## 5. Consuming the image in production

```yaml
# k8s deployment snippet
spec:
  containers:
    - name: api
      image: ghcr.io/firefly-operationos/flydesk-idp:v0.2.0
      env:
        - name: FLYDESK_IDP_DATABASE_URL
          value: postgresql+asyncpg://idp:idp@postgres:5432/flydesk_idp
        - name: FLYDESK_IDP_EDA_ADAPTER
          value: postgres  # durable outbox + LISTEN/NOTIFY, no extra broker
      livenessProbe:
        httpGet:
          path: /actuator/health/liveness
          port: 8400
      readinessProbe:
        httpGet:
          path: /actuator/health/readiness
          port: 8400
```

`/actuator/health/readiness` reflects the DB + EDA bus state via the
`database_health` and `eda_health` indicators registered upstream in
`pyfly.data.relational.health` and `pyfly.eda.health`. A failing
indicator returns 503 and Kubernetes stops routing traffic to the
pod automatically.
