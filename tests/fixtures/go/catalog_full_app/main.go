// Exercises taint sinks added in #135 (Go catalog lift to full): syscall exec,
// gob deserialization, encoding/xml XXE, and plugin dynamic-load — each fed from
// an env-var source so it produces a source->sink path.
package main

import (
	"encoding/gob"
	"encoding/xml"
	"os"
	"plugin"
	"syscall"
)

func runSyscall() {
	cmd := os.Getenv("CMD")
	syscall.Exec("/bin/sh", []string{cmd}, nil)
}

func loadPlugin() {
	name := os.Getenv("PLUGIN")
	plugin.Open(name)
}

func parseXML() {
	data := os.Getenv("XML")
	var out map[string]interface{}
	xml.Unmarshal([]byte(data), &out)
}

func decodeGob(r gob.GobDecoder) {
	dec := gob.NewDecoder(nil)
	dec.Decode(nil)
}

func main() {}
