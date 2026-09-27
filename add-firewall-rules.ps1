New-NetFirewallRule -DisplayName "Vite Dev 5174" -Direction Inbound -Protocol TCP -LocalPort 5174 -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "FastAPI Backend 8001" -Direction Inbound -Protocol TCP -LocalPort 8001 -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null
Write-Host ""
Write-Host "בוצע! עכשיו אפשר לגשת לאפליקציה מהטלפון." -ForegroundColor Green
Write-Host ""
Write-Host "אפשר לסגור את החלון הזה."
Start-Sleep -Seconds 8
