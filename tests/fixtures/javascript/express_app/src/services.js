import child_process from 'child_process';

class ReportRunner {
  constructor(name) {
    this.name = name;
  }

  start() {
    return this.render();
  }

  render() {
    return child_process.execSync('generate-report ' + this.name);
  }
}

export function runReport(name) {
  const runner = new ReportRunner(name);
  return runner.start();
}
