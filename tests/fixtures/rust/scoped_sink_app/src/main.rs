fn run_inline(cmd: &str) {
    std::process::Command::new(cmd);
}

fn read_file(path: &str) {
    std::fs::read(path);
}
