// Exercises taint sinks added in #135 (Rust catalog lift to full): filesystem
// mutation, outbound network, raw SQL, msgpack/cbor deserialization, and async
// tokio::fs access — each fed from an env-var source so it produces a
// source->sink path.

fn write_dir() {
    let dir = std::env::var("DIR").unwrap();
    std::fs::create_dir_all(&dir).unwrap();
}

fn fetch_url() {
    let url = std::env::var("URL").unwrap();
    let client = reqwest::Client::new();
    client.get(&url);
}

fn run_query(conn: &Connection) {
    let name = std::env::var("NAME").unwrap();
    conn.execute(&format!("select * from t where n = {}", name), []);
}

fn load_msgpack() {
    let data = std::env::var("DATA").unwrap();
    let _v: u32 = rmp_serde::from_slice(data.as_bytes()).unwrap();
}

async fn read_async() {
    let path = std::env::var("PATH").unwrap();
    tokio::fs::read(&path).await.unwrap();
}

fn main() {}
