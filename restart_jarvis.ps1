param(
    [switch]$NoBrowser
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Join-Path $projectRoot 'frontend'

function Stop-JarvisPort {
    param([int]$Port)

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
}

if (-not (Test-Path (Join-Path $projectRoot 'main.py'))) {
    throw 'main.py was not found. Run this script from the JARVIS project folder.'
}

if (-not (Test-Path (Join-Path $frontendRoot 'artisan'))) {
    throw 'Laravel frontend was not found.'
}

Stop-JarvisPort -Port 8100
Stop-JarvisPort -Port 9999
Start-Sleep -Milliseconds 500

# Clean up browser profile on restart (prevents bloat)
$profileDir = Join-Path $projectRoot 'data\jarvis_browser_profile'
if (Test-Path $profileDir) {
    $size = (Get-ChildItem $profileDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 1)
    Remove-Item $profileDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Browser profile cleaned ($sizeMB MB freed)"
}

Start-Process -FilePath 'python' -ArgumentList 'main.py' -WorkingDirectory $projectRoot -WindowStyle Hidden
Start-Process -FilePath 'php' -ArgumentList 'artisan', 'serve', '--host=127.0.0.1', '--port=9999' -WorkingDirectory $frontendRoot -WindowStyle Hidden

Start-Sleep -Seconds 2

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:9999'
}

Write-Host 'JARVIS restarted. Open http://127.0.0.1:9999 if the browser did not open.'
