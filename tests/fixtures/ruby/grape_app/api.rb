class API < Grape::API
  params do
    requires :name, type: String
    optional :age, type: Integer
  end
  post '/users' do
    { name: params[:name] }
  end

  get '/users/:id' do
    { id: params[:id], verbose: params[:verbose] }
  end
end
