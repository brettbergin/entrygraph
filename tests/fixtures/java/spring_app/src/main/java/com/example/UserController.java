package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class UserController {

    private final ReportService reportService = new ReportService();

    @GetMapping("/users/{id}")
    public String getUser(String id) {
        return "user:" + id;
    }

    @PostMapping("/reports")
    public String createReport(String name) {
        return reportService.buildReport(name);
    }
}
