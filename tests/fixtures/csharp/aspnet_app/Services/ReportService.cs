using System.Diagnostics;

namespace AspNetApp.Services;

public class ReportService
{
    public void Generate(string cmd)
    {
        Process.Start(cmd);
    }
}
