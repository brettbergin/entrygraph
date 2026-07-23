Rails.application.routes.draw do
  root 'pages#home'
  get 'profile', to: 'users#show'

  resources :posts, only: [:index, :show, :create] do
    resources :comments, only: [:index]
  end

  namespace :admin do
    resources :reports, only: [:show]
  end

  draw(:api)

  # a split file drawn inside a scope inherits its path and controller module;
  # the long option list also pushes `module:` past the short arg-preview cut
  scope(path: 'v2', constraints: { id: /\d+/ }, defaults: { format: :json }, module: :v2, as: :v2) do
    draw(:v2)
  end
end
