require 'sinatra'
require_relative './services/runner'

# Route reaches Kernel#system through Services::Runner#run_report (2 hops):
#   report_route (block) -> run_report -> render_and_execute -> system(...)
get '/reports' do
  runner = Services::Runner.new
  runner.run_report(params[:cmd])
end

post '/health' do
  "ok"
end
