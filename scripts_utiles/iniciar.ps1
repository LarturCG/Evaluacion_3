$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$secretDirectory = Join-Path $projectRoot "secrets"
New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null

function New-SecretFile([string]$name, [int]$byteCount) {
    $path = Join-Path $secretDirectory $name
    if (-not (Test-Path $path)) {
        $bytes = New-Object byte[] $byteCount
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $generator.GetBytes($bytes)
        }
        finally {
            $generator.Dispose()
        }
        $value = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
        [IO.File]::WriteAllText($path, $value, [Text.Encoding]::ASCII)
    }
}

New-SecretFile "postgres_admin_password.txt" 32
New-SecretFile "db_password.txt" 32
New-SecretFile "jwt_secret.txt" 48
New-SecretFile "bank_operator_password.txt" 32

Push-Location $projectRoot
try {
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose no pudo iniciar los servicios"
    }
}
finally {
    Pop-Location
}
