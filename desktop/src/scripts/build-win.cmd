@echo off
rem Build the Electron shell with Node only (no pnpm, bash, sed or .bin shims).
setlocal
cd /d "%~dp0.."
node node_modules\electron-vite\bin\electron-vite.js build
