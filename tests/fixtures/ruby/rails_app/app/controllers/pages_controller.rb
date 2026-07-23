class PagesController < ApplicationController
  def home
    render plain: "welcome"
  end
end
