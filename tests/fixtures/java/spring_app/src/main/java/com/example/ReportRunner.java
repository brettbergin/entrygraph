package com.example;

import java.io.IOException;

public class ReportRunner {

    public String executeShell(String name) {
        try {
            Process p = Runtime.getRuntime().exec("generate-report " + name);
            return "started:" + p.hashCode();
        } catch (IOException e) {
            return "error";
        }
    }
}
