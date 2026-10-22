# Container execution boundary

FR-8 of the PRD makes the container the **only** permission boundary for Codex (whose
reference argv bypasses approvals and sandboxing) and the outer boundary for dsh and Claude
Code. `morphd` checks the marker and refuses real runs outside it.

## Build

```sh
docker build -f deploy/container/Dockerfile -t morphd:dev .
```

To mount locally installed CLIs instead of installing npm versions:

```sh
docker build --build-arg INSTALL_CLIS=0 -f deploy/container/Dockerfile -t morphd:dev .
```

## Run

```sh
docker run --rm -it \
  --network morphd-egress \                # a network with default-deny egress + allowlist
  -e MORPHD_EGRESS_ENFORCED=1 \
  -e MORPHD_EGRESS_ALLOWLIST=api.deepseek.com,api.anthropic.com,api.openai.com \
  -e DEEPSEEK_API_KEY \
  -e ANTHROPIC_API_KEY \
  -e OPENAI_API_KEY \
  -e MORPHD_DSH_TARGET_HOME=/target/dsh-home \
  -v "$PWD/tasks:/data/tasks:ro" \
  -v "$PWD/runs:/var/lib/morphd:rw" \
  -v "$PWD/target:/target:rw" \
  morphd:dev run --runtime dsh --k 1 --split train \
    --budget-usd 5 --budget-calls 60 --model dsh=deepseek-flash
```

The egress allowlist is not enforced by this image. Enforce it at the Docker network,
Kubernetes NetworkPolicy, or host firewall layer, and verify DNS + fetch from inside the
container before the first paid run.

## Rules

- Mount only the sanitised task snapshot into `/data/tasks`; never mount a developer home.
- Never set `DSH_HOME` or `CODEX_HOME` to a default `~/.dsh` / `~/.codex` path. Each rollout
  receives its own home under the run directory.
- Do not mount model credentials as files; pass them as environment variables. Variant homes
  are scanned for `.credentials.yaml`, `auth.json` and key-shaped content.
- Do not run with `--privileged` or the host network.
- Keep `/var/lib/morphd` on a volume that is backed up according to the retention policy in
  `docs/runbook.md`.
