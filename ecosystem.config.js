/**
 * PM2 config — one process; Python auto-detects CPU cores and spawns
 * that many workers internally.
 *
 *   pm2 start ecosystem.config.js
 *   pm2 logs vanity-coldkey
 *   pm2 restart vanity-coldkey
 *   pm2 stop vanity-coldkey
 */
module.exports = {
  apps: [
    {
      name: "vanity-coldkey",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "search.py",
      interpreter: "none",
      instances: 1, // keep 1 — Python auto-spawns one worker per CPU core
      exec_mode: "fork",
      autorestart: true,
      // Exit 0 = matched or clean stop; do not loop forever
      stop_exit_codes: [0],
      max_restarts: 10,
      min_uptime: "10s",
      kill_timeout: 15000,
      env: {
        PREFIX: "Ev3R",
        SUFFIX: "rDEND",
        // CASE_SENSITIVE: "1",
      },
    },
  ],
};
