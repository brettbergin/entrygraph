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
end
