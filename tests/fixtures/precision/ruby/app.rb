# Constructs that MUST NOT be tagged (negative half of the catalog suite, #97),
# with a tagged positive control per category to prove the sink stays alive.
class WidgetsController
  def create
    Widgets::CreateService.new(params).execute(declared_params) # NOT sql
    Widget.where(id: params[:id])                               # NOT sql (parameterized)
    Digest::SHA256.hexdigest(params[:token])                    # NOT weak_crypto
    Open3.popen3(["ls", "-l"])                                  # NOT command_exec (constant argv)
    system("ls -l")                                             # NOT command_exec (constant string)
  end

  def dangerous
    conn.execute("SELECT * FROM widgets WHERE id = #{params[:id]}") # sql (control)
    system("ls #{params[:dir]}")                                    # command_exec (control)
    Open3.capture3(user_cmd)                                        # command_exec (control)
  end
end
