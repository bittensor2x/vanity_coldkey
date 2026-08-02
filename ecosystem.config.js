/**
 * PM2 config — one process; Python spawns CPU workers internally.
 *
 * Change CPU here: 4 | 8 | 16 | 32 | 64
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
      instances: 1, // keep 1 — Python spawns CPU workers
      exec_mode: "fork",
      autorestart: true,
      // Exit 0 = matched or clean stop; do not loop forever
      stop_exit_codes: [0],
      max_restarts: 10,
      min_uptime: "10s",
      kill_timeout: 15000,
      env: {
        CPU: "16", // 4 | 8 | 16 | 32 | 64
        PREFIX: "Ev3R",
        SUFFIX: "rDEND",
        // CASE_SENSITIVE: "1",
      },

    },
  ],
};
