class CommentsController < ApplicationController
  def index
    @comments = Comment.where(post_id: params[:post_id])
  end
end
