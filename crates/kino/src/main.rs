use anyhow::Result;
use kino::launch::{self, Options};

#[tokio::main]
async fn main() -> Result<()> {
    if std::env::args().any(|a| a == "--version") {
        println!("kino-server {}", kino::backup::app_version());
        return Ok(());
    }
    let flag = |name: &str| std::env::var(name).is_ok_and(|v| v == "1");
    if flag("KINO_EXIT_WITH_STDIN") {
        // Started by the desktop app: its end closes our input, and we end too.
        std::thread::spawn(|| {
            let _ = std::io::copy(&mut std::io::stdin(), &mut std::io::sink());
            std::process::exit(0);
        });
    }
    let port: u16 = std::env::var("KINO_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let options = Options {
        port,
        // The desktop app shares its pairing token so the phone remote's link works here.
        pairing_token: std::env::var("KINO_PAIRING_TOKEN").unwrap_or_default(),
        legacy: std::env::var("KINO_LEGACY").ok(),
        native_player: flag("KINO_NATIVE_PLAYER"),
        notifications: flag("KINO_NOTIFICATIONS"),
        startup_tasks: flag("KINO_STARTUP_TASKS"),
        window: None,
        notifier: None,
        remote: None,
    };
    launch::serve(launch::bind(port).await?, options).await
}
