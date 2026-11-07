# Security policy

## Supported versions

`0.1.x` is the current line. Security fixes land on `main` first.

## Reporting a vulnerability

Please use **GitHub private security advisories**:

<https://github.com/Tayhon-mvg/astra-code/security/advisories/new>

Do not open a public issue for a secret leak, sandbox escape, SSRF, or command
injection. Include a minimal reproduction and the Astra version (`astra --version`).

## Trust model

Astra is a local terminal agent. It sends prompts and the context you give it to
the model provider you configured — including file contents and command output
the model asks to read. Free tiers may train on prompts; check the provider policy.

The agent is **not** a security boundary:

- `--yes` / `approval = "auto"` approves every tool call, including shell commands.
- An approved shell command runs with your user privileges. Secrets are stripped
  from its environment, but it can still read files your user can read.
- `read_file`, `write_file`, `edit_file` and `run_command` are approval-gated by
  default. `.env`, `auth.toml`, private keys and credential files require an extra
  confirmation; their output is redacted.
- `~/.astra/auth.toml` and `~/.omniroute/storage.sqlite` are blocked from the file
  tools. They are still ordinary user files on disk; rely on OS permissions.
- `fetch_url` blocks loopback/private/link-local addresses and validates redirects,
  but DNS rebinding and provider-side SSRF are outside its control.
- Prompt injection is real: file contents, web pages and tool output are treated
  as untrusted data by the system prompt, but a model can still be tricked. Review
  commands and diffs before approving them.

## Secrets

- Keys live in `~/.astra/auth.toml` (best-effort 0600) or environment variables.
- `~/.astra` is best-effort 0700; sessions/history/config are best-effort 0600.
- Shell commands receive an environment without `*_API_KEY`, `*_TOKEN`, `*_SECRET`,
  `DATABASE_URL`, `AWS_*`, `AZURE_*` and `OMNIROUTE_*`.
- Shell/`fetch_url` output and sensitive-file reads pass through secret redaction
  (`sk-***`, `Bearer ***`, `API_KEY=***`).
- Never commit `.env`, `auth.toml`, `*.pem`, `*.key` or session files; they are
  ignored by `.gitignore`.

## OmniRoute

OmniRoute is a separate local project. Astra imports its key only with an explicit
`astra auth set omniroute --from-omniroute`. Astra docs recommend binding OmniRoute
to `127.0.0.1` (`OMNIROUTE_SERVER_HOST=127.0.0.1`); the default `0.0.0.0` may expose
the inference plane to your network.
