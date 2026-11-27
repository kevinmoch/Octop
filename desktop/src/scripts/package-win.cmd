@echo off
rem Build and package with Node only (no pnpm, bash, sed or .bin shims).
rem Usage: scripts\package-win.cmd         installer
rem        scripts\package-win.cmd --dir   unpacked directory
setlocal
cd /d "%~dp0.."
pushd "%~dp0..\..\..\dashboard"
call npm run build
if errorlevel 1 (
  popd
  exit /b 1
)
popd
node node_modules\electron-vite\bin\electron-vite.js build
if errorlevel 1 exit /b %errorlevel%
node node_modules\electron-builder\cli.js --config electron-builder.config.ts %*
if errorlevel 1 exit /b %errorlevel%
node scripts\verify-package.mjs
