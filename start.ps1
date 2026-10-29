param([int]$Port = 8765, [switch]$RunExample, [switch]$AutoReleaseLocal, [switch]$Demo)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $rsiArguments = @('server.py', '--port', $Port)
    if ($Demo) { $rsiArguments += @('--mode', 'demo', '--seed') }
    if ($RunExample) { $rsiArguments += '--run-example' }
    if ($AutoReleaseLocal) { $rsiArguments += '--auto-release-local' }
    python @rsiArguments
} finally {
    Pop-Location
}
