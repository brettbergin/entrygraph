package main

import (
	"os/exec"
	"strings"
)

// RunReport is the first service hop from the route handler. It delegates to
// execReport, which reaches os/exec.Command — a command_exec sink.
func RunReport(name string) string {
	cleaned := strings.TrimSpace(name)
	return execReport(cleaned)
}

// execReport builds and runs a shell command; the reachability sink.
func execReport(name string) string {
	cmd := exec.Command("generate-report", name)
	out, _ := cmd.Output()
	return string(out)
}
