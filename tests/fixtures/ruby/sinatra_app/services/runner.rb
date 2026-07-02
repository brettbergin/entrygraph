module Services
  class Base
  end

  # A uniquely-named service so the route -> service edge resolves FUZZY,
  # then the service method reaches Kernel#system (the command_exec sink).
  class Runner < Base
    def run_report(cmd)
      render_and_execute(cmd)
    end

    def render_and_execute(cmd)
      system("generate-report #{cmd}")
    end
  end
end
