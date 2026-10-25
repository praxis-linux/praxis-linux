//! The services behind the Canvas in the packaged app: the model proxy and the agent.
//!
//! The AppImage carries them under `services/` in its resources, with a Python of
//! their own. When the Canvas starts and finds them there, it starts both, with a
//! random token that only the agent and this Canvas know, and stops them when it
//! exits. In development they are started by `scripts/dev.py` instead, and nothing
//! here runs.

use std::fs::{self, File};
use std::io::Read;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Where the agent listens. The Canvas's CSP names this address.
pub const AGENT_PORT: u16 = 7729;

/// How long the proxy may take to start. It loads a lot of Python; slow disks are slow.
const PROXY_START: Duration = Duration::from_secs(120);

/// Variables the AppImage sets for its own libraries, which a Python of its own must not inherit.
const APPIMAGE_VARIABLES: [&str; 6] = [
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "GIO_MODULE_DIR",
    "GSETTINGS_SCHEMA_DIR",
];

/// The services this Canvas started, if it started any.
#[derive(Default)]
pub struct Services {
    children: Arc<Mutex<Vec<Child>>>,
    // Set when the Canvas exits, so an agent not yet started is never started.
    stopping: Arc<AtomicBool>,
    token: Option<String>,
}

impl Services {
    /// The secret the agent was started with, which the Canvas sends with each request.
    pub fn token(&self) -> Option<String> {
        self.token.clone()
    }

    /// Start the proxy and the agent from `bundle`, logging to `logs`.
    ///
    /// Nothing is started if there is no bundle, or if something already answers on
    /// the agent's port: then that agent is the one the Canvas talks to.
    pub fn start(bundle: &Path, logs: &Path) -> Services {
        let python = bundle.join("python").join("bin").join("python3");
        if !python.exists() || answers(AGENT_PORT) {
            return Services::default();
        }
        match Self::spawn(bundle, &python, logs) {
            Ok(services) => services,
            Err(error) => {
                eprintln!("Praxis: could not start the agent: {error}");
                Services::default()
            }
        }
    }

    fn spawn(bundle: &Path, python: &Path, logs: &Path) -> std::io::Result<Services> {
        fs::create_dir_all(logs)?;
        let token = random_token()?;
        let proxy_port = free_port()?;
        let proxy_url = format!("http://127.0.0.1:{proxy_port}");

        let mut proxy = service(python, logs, "proxy")?;
        proxy
            .args(["-m", "litellm.proxy.proxy_cli", "--config"])
            .arg(bundle.join("litellm_config.yaml"))
            .args(["--host", "127.0.0.1", "--port", &proxy_port.to_string()])
            // The proxy's packages are kept apart from the agent's: their pins differ.
            .env("PYTHONPATH", bundle.join("litellm"));
        if std::env::var_os("OLLAMA_API_BASE").is_none() {
            proxy.env("OLLAMA_API_BASE", "http://127.0.0.1:11434");
        }

        let mut agent = service(python, logs, "agent")?;
        agent
            .args([
                "-m",
                "uvicorn",
                "praxis_agent.server:create_app",
                "--factory",
            ])
            .args(["--host", "127.0.0.1", "--port", &AGENT_PORT.to_string()])
            .env("PRAXIS_LITELLM_URL", &proxy_url)
            .env("PRAXIS_AGENT_TOKEN", &token)
            .env("PYDANTIC_AI_NO_BANNER", "1");

        let services = Services {
            children: Arc::new(Mutex::new(vec![proxy.spawn()?])),
            stopping: Arc::default(),
            token: Some(token),
        };
        // The agent comes up in a second, the proxy in several: the agent is started
        // once the proxy answers, so its first request finds a model. The window does
        // not wait; the Canvas waits for the agent.
        let (children, stopping) = (services.children.clone(), services.stopping.clone());
        std::thread::spawn(move || {
            let deadline = Instant::now() + PROXY_START;
            while !answers(proxy_port) && Instant::now() < deadline {
                if stopping.load(Ordering::SeqCst) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(250));
            }
            let Ok(mut started) = children.lock() else {
                return;
            };
            if stopping.load(Ordering::SeqCst) {
                return;
            }
            match agent.spawn() {
                Ok(child) => started.push(child),
                Err(error) => eprintln!("Praxis: could not start the agent: {error}"),
            }
            drop(started);
            // The agent is told to stop when the thread that started it ends, should the
            // Canvas die without stopping it: so this thread lives as long as the Canvas.
            while !stopping.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_millis(500));
            }
        });
        Ok(services)
    }

    /// Stop what was started: politely first, so the agent can close its MCP servers.
    pub fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        if let Ok(mut children) = self.children.lock() {
            stop_all(&mut children);
        }
    }
}

/// A command for one service: the bundled Python, a clean environment, and a log file.
fn service(python: &Path, logs: &Path, name: &str) -> std::io::Result<Command> {
    let log = File::create(logs.join(format!("{name}.log")))?;
    let mut command = Command::new(python);
    for variable in APPIMAGE_VARIABLES {
        command.env_remove(variable);
    }
    command
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Its own process group, so stopping it stops what it started too.
        command.process_group(0);
    }
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: prctl(2) only sets this process's parent-death signal; it allocates
        // nothing and is safe to call between fork and exec.
        unsafe {
            command.pre_exec(|| {
                // If the Canvas dies without stopping it, the service stops too, and the
                // next Canvas does not find an agent whose token it does not know.
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                Ok(())
            });
        }
    }
    Ok(command)
}

fn stop_all(children: &mut Vec<Child>) {
    for child in children.iter() {
        signal_group(child, false);
    }
    for child in children.iter_mut() {
        for _ in 0..30 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        signal_group(child, true);
        let _ = child.wait();
    }
    children.clear();
}

#[cfg(unix)]
fn signal_group(child: &Child, kill: bool) {
    let signal = if kill { libc::SIGKILL } else { libc::SIGTERM };
    if let Ok(pid) = i32::try_from(child.id()) {
        // SAFETY: kill(2) with a negative pid signals that process group; no memory is shared.
        unsafe {
            libc::kill(-pid, signal);
        }
    }
}

#[cfg(not(unix))]
fn signal_group(_child: &Child, _kill: bool) {}

/// Whether something answers on 127.0.0.1:`port`.
fn answers(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(300)).is_ok()
}

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

/// 32 random bytes from the system, as hex.
fn random_token() -> std::io::Result<String> {
    let mut bytes = [0u8; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Where the bundled services are, if this is the packaged app.
pub fn bundle_dir(resources: Option<PathBuf>) -> Option<PathBuf> {
    resources
        .map(|dir| dir.join("services"))
        .filter(|dir| dir.is_dir())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_token_is_64_hex_characters_and_never_the_same_twice() {
        let a = random_token().unwrap();
        let b = random_token().unwrap();
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn nothing_is_started_without_a_bundle() {
        let empty = std::env::temp_dir().join("praxis-no-bundle");
        let services = Services::start(&empty, &empty.join("logs"));
        assert!(services.token().is_none());
        assert!(services.children.lock().unwrap().is_empty());
    }

    #[test]
    fn the_bundle_is_the_services_folder_in_the_resources() {
        let resources = std::env::temp_dir().join("praxis-resources-test");
        fs::create_dir_all(resources.join("services")).unwrap();
        assert_eq!(
            bundle_dir(Some(resources.clone())),
            Some(resources.join("services"))
        );
        assert_eq!(
            bundle_dir(Some(std::env::temp_dir().join("praxis-none"))),
            None
        );
        assert_eq!(bundle_dir(None), None);
    }
}
