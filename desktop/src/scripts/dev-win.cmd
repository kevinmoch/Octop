@echo off
rem Windows dev launcher that only needs Node.js (no pnpm / bash / sed).
rem Usage: double-click, or run `scripts\dev-win.cmd` from PowerShell.
setlocal
cd /d "%~dp0.."
node scripts\run-dev.mjs %*
