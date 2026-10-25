mod services;

use services::Services;
use tauri::plugin::{Builder as PluginBuilder, TauriPlugin};
use tauri::{Manager, RunEvent, Runtime, State, Url};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Under WSL, WebKit's DMA-BUF renderer draws an empty window.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() && in_wsl() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    tauri::Builder::default()
        .plugin(navigation_guard())
        .setup(|app| {
            // In the packaged app, the Canvas starts the agent it talks to.
            let paths = app.path();
            let started = match services::bundle_dir(paths.resource_dir().ok()) {
                Some(bundle) => {
                    let logs = paths
                        .app_log_dir()
                        .unwrap_or_else(|_| std::env::temp_dir().join("praxis"));
                    Services::start(&bundle, &logs)
                }
                None => Services::default(),
            };
            app.manage(started);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![agent_token])
        .build(tauri::generate_context!())
        .expect("error while building Praxis Canvas")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                app.state::<Services>().stop();
            }
        });
}

fn in_wsl() -> bool {
    std::env::var_os("WSL_DISTRO_NAME").is_some()
        || std::fs::read_to_string("/proc/sys/kernel/osrelease")
            .is_ok_and(|release| release.to_lowercase().contains("microsoft"))
}

/// The secret the agent was started with, if this Canvas started it.
#[tauri::command]
fn agent_token(services: State<'_, Services>) -> Option<String> {
    services.token()
}

/// Keeps the Canvas window on the Canvas.
///
/// What the agent writes is shown as text, links included, but should anything
/// still try to take the window elsewhere, a remote page would inherit the
/// Canvas's title and window with no way back. Navigation anywhere but the
/// Canvas's own pages is refused.
fn navigation_guard<R: Runtime>() -> TauriPlugin<R> {
    PluginBuilder::new("navigation-guard")
        .on_navigation(|_webview, url| is_canvas_page(url, cfg!(debug_assertions)))
        .build()
}

/// Whether `url` is one of the Canvas's own pages. In development the Canvas is
/// served by Vite on localhost:1420.
fn is_canvas_page(url: &Url, development: bool) -> bool {
    match url.scheme() {
        // The bundled Canvas on Linux and macOS.
        "tauri" => url.host_str() == Some("localhost"),
        // The bundled Canvas on Windows, and the development server.
        "http" => match url.host_str() {
            Some("tauri.localhost") => true,
            Some("localhost") => development && url.port() == Some(1420),
            _ => false,
        },
        // The blank page a webview starts on.
        "about" => url.as_str() == "about:blank",
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::is_canvas_page;
    use tauri::Url;

    fn allowed(url: &str, development: bool) -> bool {
        is_canvas_page(&Url::parse(url).unwrap(), development)
    }

    #[test]
    fn the_bundled_canvas_is_allowed() {
        assert!(allowed("tauri://localhost/", false));
        assert!(allowed("tauri://localhost/index.html", false));
        assert!(allowed("http://tauri.localhost/", false));
        assert!(allowed("about:blank", false));
    }

    #[test]
    fn the_development_server_only_in_development() {
        assert!(allowed("http://localhost:1420/", true));
        assert!(!allowed("http://localhost:1420/", false));
        assert!(!allowed("http://localhost:7729/intent", true));
    }

    #[test]
    fn anywhere_else_is_refused() {
        for url in [
            "https://evil.example/login",
            "http://evil.example/",
            "https://tauri.localhost.evil.example/",
            "tauri://evil.example/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<h1>hi</h1>",
            "http://127.0.0.1:7729/",
        ] {
            assert!(!allowed(url, true), "{url} should be refused");
        }
    }
}
