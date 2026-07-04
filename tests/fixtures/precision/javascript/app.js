// Constructs that MUST NOT be tagged, with positive controls (#97).
const crypto = require("crypto");

function safe(db, id) {
  crypto.createHash("sha256").update(id).digest("hex"); // NOT weak_crypto
  db.query("SELECT * FROM t WHERE id = ?", [id]); // NOT sql (parameterized)
  /\d+/.exec(id); // NOT command_exec (RegExp.exec)
}

function dangerous(db, id) {
  crypto.createHash("md5"); // weak_crypto (control)
  db.query("SELECT * FROM t WHERE id = " + id); // sql (control)
}
