import express from 'express';
import cp from 'child_process';

const app = express();

function fromHeader(req, res) {
  const cmd = req.headers['x-cmd'];
  cp.execSync(cmd);
}

function fromCookie(req, res) {
  const cmd = req.cookies.session;
  cp.execSync(cmd);
}

app.get('/header', fromHeader);
app.get('/cookie', fromCookie);

export default app;
