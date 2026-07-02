package main

import (
	"net/http"
	"os/exec"
)

func handler(w http.ResponseWriter, r *http.Request) {
	exec.Command("sh", "-c", r.URL.Query().Get("cmd"))
}

func main() {
	http.HandleFunc("/run", handler)
	http.ListenAndServe(":8080", nil)
}
