package com.example;

public class ReportService {

    private final ReportRunner reportRunner = new ReportRunner();

    public String buildReport(String name) {
        return reportRunner.executeShell(name);
    }
}
