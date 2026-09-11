maki-claude puts a Claude subscription behind maki's dynamic provider mechanism.
It loads through the maki pack system, not as a builtin. Two parts:

- `providers/claude`: a Python 3 script, the maki dynamic provider. OAuth
  login, token storage, and refresh. Standard library only, one file, no
  `.py` extension because maki runs it by name. `info` declares the Claude
  Code identity line as the system prefix; that is the only thing the
  subscription token requires of a request, verified against Opus, Sonnet,
  and Fable with flat maki tool names and no extra headers.
- `plugin/maki_claude.lua`: installs the script into the config `providers/`
  directory and registers `/claude` for a status line. Keep it to that:
  anything else the script can do is reachable through `maki auth`.

## Code guidelines

- No trivial comments, minimal bloat, no unnecessary state.
- Constants at the top of each file, in both languages.
- Lua: fallible runtime operations return the `(value, err)` pair and never
  throw. Setup at load logs and returns on the first failure instead of
  failing the package.
- Python: user-facing failures raise `Fail`; `main` prints them to stderr and
  exits non-zero, which is how maki surfaces script errors.
- `plugin.toml` grants exactly what the Lua calls. Keep it aligned by hand.

## Testing

Cheapest first:

- `just check` runs `cargo check --tests` and byte-compiles the script.
- `just lint`
- `just test-py` runs the Python tests.
- `just test` runs both suites; the Rust part needs `cargo-nextest`.

The Rust tests load the package through `PluginHost::load_package`, passing the
repo root. Loading installs the script, so the test points `HOME` and the XDG
variables at a temporary directory before creating the host. Assert
Lua-visible effects: the registered command and the installed file.

Dev-dependencies pin a revision of maki. Move the pin when the host changes
what the plugin uses.

## Layout

- `providers/claude`: the provider script.
- `plugin/`: the Lua entry file.
- `plugin.toml`: `min_maki_version` and the `[permissions]` request.
- `tests/plugin.rs`: host harness. `tests/test_provider.py`: script tests.
- `justfile`: check, lint, test, test-py, fmt-lua.

## Docs

The README is the canonical home for install and usage. Follow the maki docs
voice: plain words, no em-dashes, no contractions, state facts once.
