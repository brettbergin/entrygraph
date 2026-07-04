<?php
// Constructs that MUST NOT be tagged, with positive controls (#97).
class Repo {
    public function safe($pdo, $id) {
        $stmt = $pdo->prepare('SELECT * FROM users WHERE id = :id'); // NOT sql (bound params)
        $pdo->query('SELECT COUNT(*) FROM users');                   // NOT sql (constant)
    }
    public function dangerous($pdo, $name) {
        $pdo->query("SELECT * FROM users WHERE name = '$name'");     // sql (control)
        $pdo->prepare($sql);                                         // sql (control, variable stmt)
    }
}
