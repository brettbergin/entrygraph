package app

import (
	"net/http"
	"os/exec"
)

func Confirmed(w http.ResponseWriter, r *http.Request) {
	q := r.FormValue("cmd")
	exec.Command(q)
}

func Refuted(w http.ResponseWriter, r *http.Request) {
	_ = r.FormValue("cmd")
	exec.Command("ls")
}
