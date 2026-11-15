/**
 * AutoClip AI Studio — Node.js Development & Production Runner
 * Orchestrates backend server, port monitoring, and studio UI.
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BACKEND_PORT = process.env.PORT || 8765;
const BACKEND_HOST = process.env.HOST || '0.0.0.0';

console.log('---------------------------------------------------------');
console.log('  ⚡ AutoClip Pro Studio — Node.js Orchestrator');
console.log(`  🚀 Target: http://localhost:${BACKEND_PORT}`);
console.log('---------------------------------------------------------');

// Check if Python virtual environment exists
const pythonExe = process.platform === 'win32'
  ? path.join(__dirname, '.venv', 'Scripts', 'python.exe')
  : path.join(__dirname, '.venv', 'bin', 'python');

function checkHealth() {
  return new Promise((resolve) => {
    http.get(`http://127.0.0.1:${BACKEND_PORT}/api/health`, (res) => {
      resolve(res.statusCode === 200);
    }).on('error', () => resolve(false));
  });
}

async function main() {
  const isRunning = await checkHealth();
  if (isRunning) {
    console.log(`[Node.js] Backend is already running on port ${BACKEND_PORT}.`);
    console.log(`[Node.js] Studio Web UI ready at: http://localhost:${BACKEND_PORT}`);
    return;
  }

  console.log(`[Node.js] Spawning Python Uvicorn backend...`);
  const backend = spawn(pythonExe, [
    '-m', 'uvicorn',
    'autoclip_app.app:app',
    '--host', BACKEND_HOST,
    '--port', String(BACKEND_PORT)
  ], {
    cwd: __dirname,
    stdio: 'inherit',
    env: process.env
  });

  backend.on('error', (err) => {
    console.error(`[Node.js] Failed to spawn backend:`, err.message);
  });

  backend.on('close', (code) => {
    console.log(`[Node.js] Backend process exited with code ${code}`);
  });

  // Wait for health
  let attempts = 0;
  const interval = setInterval(async () => {
    attempts++;
    const ok = await checkHealth();
    if (ok) {
      clearInterval(interval);
      console.log(`[Node.js] Backend verified healthy!`);
      console.log(`[Node.js] Studio Web UI active at: http://localhost:${BACKEND_PORT}`);
    } else if (attempts > 30) {
      clearInterval(interval);
      console.error(`[Node.js] Backend startup timed out.`);
    }
  }, 1000);
}

main();
