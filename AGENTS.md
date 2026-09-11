maki-claude puts a Claude subscription behind maki's dynamic provider mechanism.
It loads through the maki pack system, not as a builtin. Two parts:

- `providers/claude`: a Python 3 script, the maki dynamic provider. Login,
  token refresh, and the loopback proxy that renames tools on the wire and
  adds the Claude Code headers. Standard library only, one file, no `.py`
  extension because maki runs it by name.
- `plugin/maki_claude.lua`: installs the script into the config `providers/`
  directory, runs `claude serve` as a plugin-scoped job, registers `/claude`.

## Code guidelines

- No trivial comments, minimal bloat, no unnecessary state.
- Constants at the top of each file, in both languages.
- Lua: fallible runtime operations return the `(value, err)` pair and never
  throw. Setup at load is wrapped in `pcall` so a broken environment logs
  instead of failing the package.
- Python: user-facing failures raise `Fail`; `main` prints them to stderr and
  exits non-zero, which is how maki surfaces script errors.
- The proxy must never read or store tokens. Auth arrives from maki in the
  `authorization` header and is forwarded as is.
- `plugin.toml` grants exactly what the Lua calls. Keep it aligned by hand.

## Testing

Cheapest first:

- `just check` runs `cargo check --tests` and byte-compiles the script.
- `just lint`
- `just test-py` runs the Python tests, including the proxy end to end
  against a fake upstream.
- `just test` runs both suites; the Rust part needs `cargo-nextest`.

The Rust tests load the package through `PluginHost::load_package`, passing the
repo root. Loading installs the script and starts the proxy, so the test points
`HOME` and the XDG variables at a temporary directory before creating the host.
Assert Lua-visible effects: the registered command, the installed file, the
proxy answering on the port it wrote.

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
