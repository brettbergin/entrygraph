class UsersController
  def confirmed
    id = params[:id]
    system("id #{id}")
  end

  def refuted
    _ignored = params[:id]
    fixed = "whoami"
    system("run #{fixed}")
  end
end
