class V2::WidgetsController < ApplicationController
  def index
    render json: { widgets: [] }
  end
end
