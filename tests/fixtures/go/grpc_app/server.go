package server

type Server struct {
	Ingester *Ingester
}

type Ingester struct{}

func (i *Ingester) Push(req int) {}
func (i *Ingester) Query(req int) {}
func (i *Ingester) helper() {} // unexported: not an RPC method

func (s *Server) Register(grpcServer int) {
	RegisterIngesterServer(grpcServer, s.Ingester)
}
