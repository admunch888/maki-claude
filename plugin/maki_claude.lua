-- Claude subscription for maki. The provider itself is `providers/claude` in
-- this package, a maki dynamic provider script. This file installs it into the
-- config directory and adds /claude for a status line.

local SCRIPT_NAME = "claude"
local PROVIDERS_DIR = "providers"
local SCRIPT_MODE = "755"
local JOB_WAIT_MS = 20000
local TITLE = "claude"
local LOGIN_HINT = "run `maki auth login claude` in a terminal, then restart maki"

local function notify(msg, level)
  maki.notify(msg, level or "info", { title = TITLE })
end

local function package_root()
  local src = debug.info(1, "s") or ""
  src = src:match('^%[string "(.-)"%]$') or src:gsub("^[=@]", "")
  return maki.fs.dirname(maki.fs.dirname(src))
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
local function install_script()
  local dst, err = script_install_path()
  if not dst then
    return nil, err
  end
  local source = maki.fs.joinpath(package_root(), PROVIDERS_DIR, SCRIPT_NAME)
  local content, read_err = maki.fs.read(source)
  if not content then
    return nil, "cannot read " .. source .. ": " .. tostring(read_err)
  end
  if maki.fs.read(dst) == content then
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

local function status_line(s)
  if not s.logged_in then
    return "not logged in, " .. LOGIN_HINT
  end
  local who = s.email and (" as " .. s.email) or ""
  if s.expires_in_s <= 0 then
    return "logged in" .. who .. ", token expired, it refreshes on the next request"
  end
  return ("logged in%s, token valid for %d min"):format(who, math.floor(s.expires_in_s / 60))
end

local function status()
  maki.async.run(function()
    local script, err = script_install_path()
    if not script then
      notify(err, "error")
      return
    end
    local out, run_err = run({ script, "status" })
    if not out then
      notify(run_err, "error")
      return
    end
    local s = maki.json.decode(out)
    if type(s) ~= "table" then
      notify("unexpected output from `claude status`: " .. out, "error")
      return
    end
    notify(status_line(s), s.logged_in and "info" or "warn")
  end)
end

maki.api.register_command({
  name = "claude",
  description = "Claude subscription: login state and token expiry",
  handler = status,
})

local path, err, changed = install_script()
if not path then
  maki.log.error("maki-claude: " .. err)
  notify(err, "error")
  return
end
if changed then
  maki.log.info("maki-claude: installed provider script at " .. path)
  notify("provider script installed at " .. path .. ", restart maki, then " .. LOGIN_HINT)
end
