class Program
{
    static void Main()
    {
        var app = Build();
        app.MapPost("/run", Handler);
    }

    static void Handler()
    {
        System.Diagnostics.Process.Start("sh", "-c");
    }

    static object Build() { return null; }
}
