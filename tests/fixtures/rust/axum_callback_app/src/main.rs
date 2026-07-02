use axum::routing::post;
use axum::Router;

fn register(app: Router) {
    app.route("/run", post(handler));
}

async fn handler() {
    std::process::Command::new("sh");
}
