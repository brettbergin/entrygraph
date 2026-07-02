using Microsoft.AspNetCore.Mvc;
using AspNetApp.Services;

namespace AspNetApp.Controllers;

[ApiController]
public class ReportsController : ControllerBase
{
    private readonly ReportService _reports;

    public ReportsController()
    {
        _reports = new ReportService();
    }

    [HttpPost("/reports")]
    public string Create(string command)
    {
        _reports.Generate(command);
        return "ok";
    }
}
