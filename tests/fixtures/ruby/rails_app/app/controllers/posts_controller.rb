class PostsController < ApplicationController
  def index
    @posts = Post.all
  end

  def show
    id = params[:id]
    system("render_post #{id}")
  end

  def create
    @post = Post.new(post_params)
  end

  private

  def post_params
    params.require(:post).permit(:title, :body)
  end
end
