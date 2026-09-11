-- Claude subscription for maki. The provider itself is `providers/claude` in
-- this package, a maki dynamic provider script. This file installs it into the
-- config directory, keeps its loopback proxy running as a job for the life of
-- this maki process, and adds the /claude command.

local SCRIPT_NAME = "claude"
local PROVIDERS_DIR = "providers"
local PROXY_JOB = "maki-claude-proxy"
local SCRIPT_MODE = "755"
local JOB_WAIT_MS = 20000
local TITLE = "claude"
local USAGE_LINE = "usage: /claude status | usage | login | logout | install"
local LOGIN_HINT = "run `maki auth login claude` in a terminal, then restart maki"
local MAX_USAGE_LINES = 12

local function notify(msg, level)
  maki.notify(msg, level or "info", { title = TITLE })
end

local function package_root()
  local src = debug.info(1, "s") or ""
  src = src:match('^%[string "(.-)"%]$') or src:gsub("^[=@]", "")
  return maki.fs.dirname(maki.fs.dirname(src))
end

local function script_source_path()
  return maki.fs.joinpath(package_root(), PROVIDERS_DIR, SCRIPT_NAME)
end

local function script_install_path()
  local dir = maki.env.config_dir()
  if not dir then
    return nil, "cannot locate the maki config directory"
  end
  return maki.fs.joinpath(dir, PROVIDERS_DIR, SCRIPT_NAME)
end

-- Plugin scope: at load there is no task for a job to belong to.
local function run(argv)
  local job = maki.fn.jobstart(argv, { scope = "plugin" })
  local result = maki.fn.jobwait(job, JOB_WAIT_MS)
  if not result then
    maki.fn.jobstop(job)
    return nil, "timed out: " .. table.concat(argv, " ")
  end
  if result.exit_code ~= 0 then
    local err = result.stderr:match("^%s*(.-)%s*$")
    return nil, err ~= "" and err or ("exit code " .. tostring(result.exit_code))
  end
  return result.stdout
end

-- Copies the script from the package into the config providers directory when
-- the two differ. Returns the installed path and whether it changed.
local function install_script(force)
  local dst, err = script_install_path()
  if not dst then
    return nil, err
  end
  local source = script_source_path()
  local content, read_err = maki.fs.read(source)
  if not content then
    return nil, "cannot read " .. source .. ": " .. tostring(read_err)
  end
  if not force and maki.fs.read(dst) == content then
    return dst, nil, false
  end
  local ok, mk_err = maki.fs.mkdir(maki.fs.dirname(dst), { parents = true })
  if not ok then
    return nil, mk_err
  end
  local written, write_err = maki.fs.atomic_write(dst, content)
  if not written then
    return nil, write_err
  end
  local _, chmod_err = run({ "chmod", SCRIPT_MODE, dst })
  if chmod_err then
    return nil, "chmod failed: " .. chmod_err
  end
  return dst, nil, true
end

local function start_proxy(script)
  if maki.fn.jobfind(PROXY_JOB) then
    return true
  end
  if maki.fn.executable("python3") ~= 1 then
    return nil, "python3 is not on PATH, the Claude provider needs it"
  end
  local ok, err = pcall(maki.fn.jobstart, { script, "serve" }, {
    scope = "plugin",
    name = PROXY_JOB,
    on_stdout = function(_, line)
      maki.log.info("claude proxy: " .. line)
    end,
    on_stderr = function(_, line)
      maki.log.warn("claude proxy: " .. line)
    end,
    on_exit = function(_, code)
      maki.log.warn("claude proxy exited with code " .. tostring(code))
    end,
  })
  if not ok then
    return nil, tostring(err)
  end
  return true
end

local function script_json(subcommand)
  local script, err = script_install_path()
  if not script then
    return nil, err
  end
  local out, run_err = run({ script, subcommand })
  if not out then
    return nil, run_err
  end
  local data, decode_err = maki.json.decode(out)
  if type(data) ~= "table" then
    return nil, "unexpected output from `claude " .. subcommand .. "`: " .. tostring(decode_err or out)
  end
  return data
end

local function describe_expiry(seconds)
  if type(seconds) ~= "number" then
    return ""
  end
  if seconds <= 0 then
    return "token expired, it refreshes on the next request"
  end
  if seconds < 3600 then
    return ("token valid for %d min"):format(math.floor(seconds / 60))
  end
  return ("token valid for %.1f h"):format(seconds / 3600)
end

local function status_line(s)
  local parts = {}
  if s.logged_in then
    parts[#parts + 1] = "logged in" .. (s.email and (" as " .. s.email) or "")
    parts[#parts + 1] = describe_expiry(s.expires_in_s)
  else
    parts[#parts + 1] = "not logged in, " .. LOGIN_HINT
  end
  if type(s.proxy) == "table" then
    parts[#parts + 1] = ("proxy :%d %s"):format(s.proxy.port, s.proxy.alive and "up" or "down")
  else
    parts[#parts + 1] = "proxy not started"
  end
  return table.concat(parts, ", ")
end

local function show_lines(title, lines)
  local ok = pcall(function()
    local buf = maki.ui.buf()
    for _, line in ipairs(lines) do
      buf:line(line)
    end
    local win = maki.ui.open_win(buf, {
      title = title,
      width = "60%",
      height = math.min(#lines, MAX_USAGE_LINES) + 1,
      footer = { { "esc", "close" } },
    })
    maki.async.run(function()
      while true do
        local ev = win:recv()
        if not ev or (ev.type == "key" and (ev.key == "esc" or ev.key == "q")) then
          break
        end
      end
      if win:is_open() then
        win:close()
      end
    end)
  end)
  if not ok then
    notify(table.concat(lines, "; "))
  end
end

local function usage_lines(u)
  local lines = {}
  local function add(label, percent, resets_at)
    if type(percent) ~= "number" then
      return
    end
    local reset = ""
    if type(resets_at) == "string" then
      local stamp = resets_at:gsub(":%d%d[%.%d]*[%+%-Z].*$", ""):gsub("T", " ")
      reset = "  resets " .. stamp
    end
    lines[#lines + 1] = ("%-28s %3d%%%s"):format(label, math.floor(percent + 0.5), reset)
  end
  for _, limit in ipairs(u.limits or {}) do
    local label = limit.kind or "limit"
    local scope = type(limit.scope) == "table" and limit.scope.model
    if type(scope) == "table" and scope.display_name then
      label = label .. " (" .. scope.display_name .. ")"
    end
    add(label, limit.percent, limit.resets_at)
  end
  if #lines == 0 then
    local windows = {
      { "session (5h)", u.five_hour },
      { "week (all models)", u.seven_day },
      { "week (Sonnet)", u.seven_day_sonnet },
      { "week (Opus)", u.seven_day_opus },
    }
    for _, w in ipairs(windows) do
      if type(w[2]) == "table" then
        add(w[1], w[2].utilization, w[2].resets_at)
      end
    end
  end
  if type(u.extra_usage) == "table" and u.extra_usage.is_enabled then
    add("extra usage credits", u.extra_usage.utilization)
  end
  if #lines == 0 then
    lines[1] = "no usage windows reported"
  end
  return lines
end

local function status()
  maki.async.run(function()
    local s, err = script_json("status")
    if not s then
      notify(err, "error")
      return
    end
    notify(status_line(s), s.logged_in and "info" or "warn")
  end)
end

local function usage()
  maki.async.run(function()
    local u, err = script_json("usage")
    if not u then
      notify(err, "error")
      return
    end
    show_lines("Claude subscription usage", usage_lines(u))
  end)
end

local function logout()
  maki.async.run(function()
    local script, err = script_install_path()
    if not script then
      notify(err, "error")
      return
    end
    local out, run_err = run({ script, "logout" })
    if not out then
      notify(run_err, "error")
      return
    end
    notify(out:match("^%s*(.-)%s*$"))
  end)
end

local function install()
  maki.async.run(function()
    local path, err = install_script(true)
    if not path then
      notify(err, "error")
      return
    end
    local job = maki.fn.jobfind(PROXY_JOB)
    if job then
      maki.fn.jobstop(job)
    end
    local ok, start_err = start_proxy(path)
    if not ok then
      notify(start_err, "error")
      return
    end
    notify("provider script installed at " .. path .. ", restart maki to pick it up")
  end)
end

maki.api.register_command({
  name = "claude",
  description = "Claude subscription: /claude status | usage | login | logout | install",
  nargs = "?",
  handler = function(opts)
    local sub = opts.fargs[1]
    if sub == "status" or sub == nil then
      status()
    elseif sub == "usage" then
      usage()
    elseif sub == "login" then
      notify(LOGIN_HINT)
    elseif sub == "logout" then
      logout()
    elseif sub == "install" then
      install()
    else
      maki.ui.flash(USAGE_LINE)
    end
  end,
})

local function setup()
  local path, err, changed = install_script(false)
  if not path then
    maki.log.error("maki-claude: " .. err)
    notify(err, "error")
    return
  end
  local ok, start_err = start_proxy(path)
  if not ok then
    maki.log.error("maki-claude: " .. start_err)
    notify(start_err, "error")
    return
  end
  if changed then
    maki.log.info("maki-claude: installed provider script at " .. path)
    notify("provider script installed at " .. path .. ", restart maki, then " .. LOGIN_HINT)
  end
end

local ok, err = pcall(setup)
if not ok then
  maki.log.error("maki-claude setup failed: " .. tostring(err))
end
