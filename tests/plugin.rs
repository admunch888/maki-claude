use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use maki_agent::tools::ToolRegistry;
use maki_lua::{PluginHost, PluginPermissions};

const PLUGIN_NAME: &str = "maki-claude";
const COMMAND: &str = "/claude";
const SCRIPT_REL: &str = "providers/claude";
const INSTALLED_REL: &str = "maki/providers/claude";
const PORT_FILE_DIR_REL: &str = "maki/maki-claude";
const PROXY_WAIT: Duration = Duration::from_secs(10);
const HEALTH_REQUEST: &str =
    "GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
const HEALTH_OK: &str = "HTTP/1.1 200";

/// The plugin writes into the maki config directory at load, so every test
/// process points HOME and the XDG bases at a directory of its own first.
fn isolated_home() -> &'static PathBuf {
    static HOME: OnceLock<PathBuf> = OnceLock::new();
    HOME.get_or_init(|| {
        let dir = std::env::temp_dir().join(format!("maki-claude-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for var in [
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        ] {
            // Set once, before any thread resolves paths.
            unsafe { std::env::set_var(var, &dir) };
        }
        dir
    })
}

fn plugin_host() -> (Arc<ToolRegistry>, PluginHost) {
    isolated_home();
    let reg = Arc::new(ToolRegistry::new());
    let host = PluginHost::new(Arc::clone(&reg)).unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    host.load_package(
        PLUGIN_NAME,
        root,
        PluginPermissions::from_approved(["fs_read", "fs_write", "run"]),
        Default::default(),
    )
    .unwrap();
    (reg, host)
}

fn wait_for_port_file(dir: &Path) -> u16 {
    let deadline = Instant::now() + PROXY_WAIT;
    loop {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if !name.starts_with("proxy-") || !name.ends_with(".json") {
                    continue;
                }
                let text = std::fs::read_to_string(entry.path()).unwrap_or_default();
                if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text)
                    && let Some(port) = json["port"].as_u64()
                {
                    return port as u16;
                }
            }
        }
        assert!(
            Instant::now() < deadline,
            "the proxy did not write a port file under {}",
            dir.display()
        );
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn health(port: u16) -> String {
    let deadline = Instant::now() + PROXY_WAIT;
    loop {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            stream.write_all(HEALTH_REQUEST.as_bytes()).unwrap();
            let mut reply = String::new();
            stream.read_to_string(&mut reply).unwrap();
            return reply;
        }
        assert!(
            Instant::now() < deadline,
            "the proxy never accepted on port {port}"
        );
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn command_registers() {
    let (_reg, host) = plugin_host();
    let snap = host.command_reader().load();
    assert!(
        snap.commands.iter().any(|c| c.name.as_ref() == COMMAND),
        "the /claude command should register"
    );
}

#[test]
fn load_installs_the_provider_script_and_starts_its_proxy() {
    let (_reg, _host) = plugin_host();
    let home = isolated_home();

    let installed = home.join(INSTALLED_REL);
    let expected = std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join(SCRIPT_REL)).unwrap();
    assert_eq!(
        std::fs::read(&installed).unwrap(),
        expected,
        "the installed script should match the package copy"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&installed).unwrap().permissions().mode();
        assert_eq!(
            mode & 0o111,
            0o111,
            "the installed script should be executable"
        );
    }

    let port = wait_for_port_file(&home.join(PORT_FILE_DIR_REL));
    let reply = health(port);
    assert!(
        reply.starts_with(HEALTH_OK),
        "unexpected health reply: {reply}"
    );
}
