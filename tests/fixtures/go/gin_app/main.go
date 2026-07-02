// Package main wires up a gin router and a net/http fallback handler.
package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// Server holds shared handler state.
type Server struct {
	Name string
	port int
}

func main() {
	r := gin.Default()
	r.GET("/reports/:name", reportHandler)
	r.POST("/users", createUser)

	http.HandleFunc("/health", healthHandler)
	http.ListenAndServe(":8080", r)
}

// reportHandler is a gin route handler that reaches os/exec through the
// service layer: reportHandler -> RunReport -> execReport -> exec.Command.
func reportHandler(c *gin.Context) {
	name := c.Param("name")
	out := RunReport(name)
	c.String(http.StatusOK, out)
}

func createUser(c *gin.Context) {
	c.Status(http.StatusCreated)
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

// Run starts the server (method with a pointer receiver, for the extractor test).
func (s *Server) Run() error {
	return nil
}
