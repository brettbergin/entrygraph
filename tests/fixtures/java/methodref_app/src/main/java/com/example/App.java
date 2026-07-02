package com.example;

public class App {
    void setup(Router r) {
        r.get("/run", this::handle);
    }

    void handle() throws Exception {
        Runtime.getRuntime().exec("sh");
    }
}
