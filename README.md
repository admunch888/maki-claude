# maki-claude

Use a Claude Pro or Max subscription in [maki](https://github.com/tontinton/maki).
The package adds a `claude` provider that logs in with the same OAuth flow as
Claude Code and keeps every maki tool usable on that login, the way
[pi-claude](https://github.com/bzzimmy/pi-claude) does for pi.

## How it works

A subscription token is only accepted for requests that look like Claude Code:
the system prompt starts with the Claude Code identity line, the request
carries the OAuth beta and Claude Code client headers, and tool names match
what Claude Code sends. Tools outside that core set are billed as extra usage
unless they are `mcp__` prefixed.

The package covers all three. `providers/claude` is a Python 3 script that
maki loads as a [dynamic provider](https://tontinton.github.io/maki/docs/providers/#dynamic-providers).
It handles login and token refresh, declares the identity line, and runs a
loopback proxy that renames tools on the wire: core tools take their Claude
Code casing (`bash` becomes `Bash`), every other tool becomes
`mcp__maki__<name>`, and tool calls coming back are renamed to what maki
registered, including the names inside a `batch` call. `plugin/maki_claude.lua`
installs the script, runs the proxy as a job for the life of the maki process,
and adds `/claude` for a status line.

The proxy listens on `127.0.0.1` only, holds no credentials, and exits with
maki.

## Requirements

- maki 0.5.3 or newer
- Python 3.8 or newer on `PATH` as `python3`
- Linux or macOS

## Install

Add the package in `~/.config/maki/init.lua`:

```lua
maki.pack.add({
  { src = "https://github.com/bzzimmy/maki-claude", version = "main" },
})
```

Start maki once so the plugin installs the script to
`~/.config/maki/providers/claude`. maki discovers providers at startup, so
quit, log in, and start again:

```sh
maki auth login claude
maki
```

Login opens the browser. If the browser is on another machine, paste the
final redirect URL into the terminal. For the code-paste flow with no browser,
call the script directly:

```sh
~/.config/maki/providers/claude login --manual --no-browser
```

Tokens live in the maki state directory with mode 0600 and refresh on their
own. Pick a model with `/model`; the catalog is under the `claude/` prefix, for
example `claude/claude-opus-5`.

## Commands

`/claude` shows the login state, token expiry, and whether the proxy is up.
`maki auth logout claude` removes the stored tokens. maki's own usage display
works with the subscription: the provider base URL names the Anthropic host,
so maki polls the quota through the proxy.

## Permissions

`fs_read` and `fs_write` to install the script, `run` to start the proxy and
call the script. No `net` or `env`: every request to Anthropic goes through
the script.

## Debugging

The proxy logs to `maki.log`. The script also runs on its own:

```sh
~/.config/maki/providers/claude status
MAKI_CLAUDE_PROXY_PORT=47999 ~/.config/maki/providers/claude serve
```

`resolve`, `reload`, and `refresh` print the auth JSON maki reads. They find
the proxy through the parent maki process, so outside maki set
`MAKI_CLAUDE_PROXY_PORT` to a `serve` you started yourself.

## Limits

- Fast mode and the 1M context window depend on what the subscription allows.
- `code_execution` binds tools by their maki names, so a model that copies a
  wire name such as `Bash` into Python gets a sandbox error.
- Costs shown by maki are Anthropic list prices; a subscription is not billed
  per token.
- No Windows: maki only runs script providers with an executable extension
  there.

## Development

```sh
git clone https://github.com/bzzimmy/maki-claude.git
cd maki-claude
just check && just lint && just test
```

The Rust tests load the package through the real maki Lua host and need
`cargo-nextest`. The Python tests run the proxy against a fake upstream.

## Credit

Tool aliasing follows [pi-claude](https://github.com/bzzimmy/pi-claude); the
OAuth flow follows the Anthropic login in [pi](https://github.com/badlogic/pi-mono).

## License

MIT
