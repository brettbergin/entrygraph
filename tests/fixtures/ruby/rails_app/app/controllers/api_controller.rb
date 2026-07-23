class ApiController < ApplicationController
  def ping
    render json: { ok: true }
  end
end
