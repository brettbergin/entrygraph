use axum::routing::post;
use axum::Router;

mod handlers;

use handlers::create_report;

#[tokio::main]
async fn main() {
    let app = Router::new().route("/reports", post(create_report));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();
    axum::serve(listener, app).await.unwrap();
}
