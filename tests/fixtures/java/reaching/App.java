package app;

public class App {
    void confirmed(javax.servlet.http.HttpServletRequest req) throws Exception {
        String q = req.getParameter("cmd");
        Runtime.getRuntime().exec(q);
    }

    void refuted(javax.servlet.http.HttpServletRequest req) throws Exception {
        String q = req.getParameter("cmd");
        Runtime.getRuntime().exec("ls");
    }
}
