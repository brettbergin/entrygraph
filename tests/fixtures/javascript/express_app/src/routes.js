import express from 'express';
import { runReport } from './services';

const app = express();

function getUser(req, res) {
  res.json({ id: req.params.id });
}

function createReport(req, res) {
  const out = runReport(req.body.name);
  res.send(out);
}

function health(req, res) {
  res.json({ ok: true });
}

app.get('/users/:id', getUser);
app.post('/reports', createReport);
app.get('/health', health);

export default app;
