param(
    [int]$Port = 8000,
    [string]$Database = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    if ($Database) {
        python app.py --port $Port --db $Database
    } else {
        python app.py --port $Port
    }
} finally {
    Pop-Location
}
