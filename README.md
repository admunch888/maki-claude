<h1><img src="assets/claude-logo.svg" alt="Claude" width="40" align="absmiddle"> maki-claude</h1>

Use a Claude Pro or Max subscription in [maki](https://github.com/tontinton/maki).
The package adds a `claude` provider that logs in with the same OAuth flow as
Claude Code and keeps every maki tool usable on that login, the way
[pi-claude](https://github.com/bzzimmy/pi-claude) does for pi.

## How it works

A subscription token is only accepted for requests that look like Claude Code:
the system prompt starts with the Claude Code identity line, the request
carries the OAuth beta and Claude Code client headers, and tool names match
what Claude Code sends. The provider script declares the identity line and
runs a loopback proxy that adds the headers and renames tools on the wire:
core tools take their Claude Code casing (`bash` becomes `Bash`), every other
tool becomes `mcp__maki__<name>`, and tool calls coming back are renamed to
what maki registered. The proxy listens on `127.0.0.1` only, holds no
credentials, and exits with maki.

## Requirements

- maki 0.5.3 or newer
- Python 3.8 or newer on `PATH` as `python3`
- A Claude Pro or Max subscription
- Linux or macOS

## Install

Add the package in `~/.config/maki/init.lua`:

```lua
maki.pack.add({
  { src = "https://github.com/bzzimmy/maki-claude", version = "main" },
})
```

Start maki once so the plugin installs the provider script, then restart
after login so maki discovers it.

## Setup

```
maki auth login claude
```

Login opens the browser. If the browser is on another machine, paste the
final redirect URL into the terminal. Without a browser, call the script
directly:

```
~/.config/maki/providers/claude login --manual --no-browser
```

Tokens are stored in the maki state directory with mode 0600 and refresh on
their own. Pick a model with `/model`; the catalog is under the `claude/`
prefix, for example `claude/claude-opus-5`.

- `/claude` shows the login state, token expiry, and whether the proxy is up.
- `maki auth logout claude` removes the stored tokens.
- maki's own usage display shows the subscription quota.

## Permissions

The package requests exactly what it calls: `fs_read` and `fs_write` to
install the script, `run` to start the proxy and call the script. Every
request to Anthropic goes through the script.

## Development

Clone the repository and run the checks from its root. The Rust tests load
the package through the real maki Lua host and need `cargo-nextest`; the
Python tests run the proxy against a fake upstream.

```sh
git clone https://github.com/bzzimmy/maki-claude.git
cd maki-claude
just check && just lint && just test
```
