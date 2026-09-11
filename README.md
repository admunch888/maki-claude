<h1><img src="assets/claude-logo.svg" alt="Claude" width="40" align="absmiddle"> maki-claude</h1>

Use a Claude Pro or Max subscription in [maki](https://github.com/tontinton/maki).
The package adds a `claude` provider that logs in with the same OAuth flow as
Claude Code. A subscription token is accepted when the system prompt opens with
the Claude Code identity line as its own block, which maki sends as the
provider's system prefix; nothing else about the request has to change.

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

Start maki once so the plugin installs the provider script to
`~/.config/maki/providers/claude`, then restart after login so maki discovers
it.

## Setup

```
maki auth login claude
```

Login opens the browser. If the browser is on another machine, paste the
final redirect URL into the terminal. Tokens are stored in the maki state
directory with mode 0600 and refresh on their own.

Pick a model with `/model`; the catalog is under the `claude/` prefix, for
example `claude/claude-opus-5`.

- `/claude` shows the login state and token expiry.
- `maki auth logout claude` removes the stored tokens.
- maki's own usage display shows the subscription quota.

## Permissions

The package requests exactly what it calls: `fs_read` and `fs_write` to
install the script, `run` to mark it executable and call it for `/claude`.
Every request to Anthropic is made by maki itself.

## Development

Clone the repository and run the checks from its root. The Rust tests load
the package through the real maki Lua host and need `cargo-nextest`.

```sh
git clone https://github.com/bzzimmy/maki-claude.git
cd maki-claude
just check && just lint && just test
```
