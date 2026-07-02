const cp = require('child_process');
const { exec } = require('child_process');
const express = require('express');

function runDefault(input) {
  cp.execSync(input);
}

function runNamed(input) {
  exec(input);
}

const app = express();
app.get('/a', (req, res) => runDefault(req.query.cmd));
app.get('/b', (req, res) => runNamed(req.query.cmd));
