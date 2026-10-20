# Опрашивает HikCentral раз в минуту и пересылает события прохода на шлюз.
# Запуск: powershell -ExecutionPolicy Bypass -File hikcentral_poll.ps1
$HikHost   = "127.0.0.1"
$AppKey    = "16342390"
$AppSecret = "ВСТАВЬТЕ_СЕКРЕТ_СЮДА"
$Gateway   = "http://hik-getaway.mado.uz/api/v1/getaway/hikvision"
# Должен совпадать с GATEWAY_TOKEN на сервере шлюза
$GatewayToken = "ВСТАВЬТЕ_ТОКЕН_ШЛЮЗА_СЮДА"
$Path      = "/artemis/api/acs/v1/door/events"
$StateFile = Join-Path $PSScriptRoot "last_time.txt"

# HikCentral использует самоподписанный сертификат
Add-Type @"
using System.Net; using System.Security.Cryptography.X509Certificates;
public class TrustAll : ICertificatePolicy {
  public bool CheckValidationResult(ServicePoint s, X509Certificate c, WebRequest r, int p) { return true; }
}
"@
[System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAll
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

function Get-Signature($stringToSign) {
  $h = New-Object System.Security.Cryptography.HMACSHA256
  $h.Key = [Text.Encoding]::UTF8.GetBytes($AppSecret)
  [Convert]::ToBase64String($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($stringToSign)))
}

function Get-IsoTime($d) { $d.ToString("yyyy-MM-ddTHH:mm:ssK") }

function Get-Events($start, $end, $page) {
  $body = @{ pageNo = $page; pageSize = 100; startTime = $start; endTime = $end } | ConvertTo-Json
  $sts = "POST`n*/*`napplication/json`nx-ca-key:$AppKey`n$Path"
  $headers = @{
    "Accept" = "*/*"
    "X-Ca-Key" = $AppKey
    "X-Ca-Signature" = (Get-Signature $sts)
    "X-Ca-Signature-Headers" = "x-ca-key"
  }
  Invoke-RestMethod -Method Post -Uri "https://$HikHost$Path" -Headers $headers `
    -ContentType "application/json" -Body $body
}

while ($true) {
  try {
    $now = Get-Date
    if (Test-Path $StateFile) { $from = [datetime](Get-Content $StateFile) } else { $from = $now.AddMinutes(-5) }
    $start = Get-IsoTime $from; $end = Get-IsoTime $now
    $page = 1
    do {
      $res = Get-Events $start $end $page
      $list = $res.data.list
      foreach ($e in $list) {
        $json = $e | ConvertTo-Json -Depth 10
        Invoke-RestMethod -Method Post -Uri $Gateway -ContentType "application/json; charset=utf-8" `
          -Headers @{ "X-Gateway-Token" = $GatewayToken } `
          -Body ([Text.Encoding]::UTF8.GetBytes($json)) | Out-Null
      }
      Write-Host "$(Get-Date -f s) страница $page, событий: $(@($list).Count)"
      $page++
    } while ($list -and @($list).Count -ge 100)
    $now.ToString("o") | Set-Content $StateFile
  } catch {
    Write-Host "$(Get-Date -f s) ОШИБКА: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds 60
}
