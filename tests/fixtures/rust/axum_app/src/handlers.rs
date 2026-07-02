use std::process::Command;

pub async fn create_report(cmd: String) -> String {
    let output = run_report(&cmd);
    output
}

fn run_report(cmd: &str) -> String {
    let child = Command::new(cmd);
    format!("{:?}", child)
}
