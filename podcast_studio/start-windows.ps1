$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Run setup-windows.ps1 first.' }
if (Test-Path '.env') {
  Get-Content '.env' | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z_0-9]*)=(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1],$matches[2].Trim('"'), 'Process')
    }
  }
}
$server = Start-Process -FilePath (Resolve-Path '.venv\Scripts\python.exe') -ArgumentList '-m','uvicorn','app:app','--app-dir','backend','--host','127.0.0.1','--port','8765' -PassThru -NoNewWindow
try {
  Start-Sleep -Seconds 2
  $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 5
  if (-not $health.ok) { throw 'Backend health check failed' }
  Push-Location frontend
  try { npm run tauri dev } finally { Pop-Location }
} finally {
  if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
