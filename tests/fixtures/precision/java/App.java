// Constructs that MUST NOT be tagged, with positive controls (#97).
package app;

public class App {
    void safe(Runnable task, java.util.concurrent.Executor executor) throws Exception {
        executor.execute(task);                       // NOT sql (Executor)
        java.lang.invoke.MethodHandles.lookup();      // NOT jndi (no-arg)
        statement.evaluate();                         // NOT template_injection (JUnit)
    }

    void dangerous(String id, javax.naming.InitialContext ctx) throws Exception {
        stmt.execute("DELETE FROM t WHERE id = " + id); // sql (control)
        ctx.lookup(id);                                  // jndi (control)
        parser.parseExpression(id);                      // template_injection (control)
    }
}
