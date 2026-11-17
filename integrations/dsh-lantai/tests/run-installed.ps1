# Desktop ASAR requires the installed Electron runtime, not node from PATH.
$ErrorActionPreference = 'Stop'
if (-not $env:DSH_NODE_EXECUTABLE -or -not $env:DSH_INSTALL_DIR) {
    throw 'Run from a DSH shell with DSH_NODE_EXECUTABLE and DSH_INSTALL_DIR set.'
}
$env:ELECTRON_RUN_AS_NODE = '1'
$runner = Join-Path $PSScriptRoot 'run-installed.cjs'
$runInfo = New-Object System.Diagnostics.ProcessStartInfo
$runInfo.FileName = $env:DSH_NODE_EXECUTABLE
$runInfo.Arguments = '"' + $runner + '"'
$runInfo.UseShellExecute = $false
$runInfo.RedirectStandardOutput = $true
$runInfo.RedirectStandardError = $true
$runInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$runInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
$runProcess = [System.Diagnostics.Process]::Start($runInfo)
$runError = $runProcess.StandardError.ReadToEndAsync()
while (($line = $runProcess.StandardOutput.ReadLine()) -ne $null) { Write-Output $line }
$runProcess.WaitForExit()
Write-Output $runError.Result
exit $runProcess.ExitCode
