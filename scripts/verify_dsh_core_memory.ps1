[CmdletBinding()]
param(
    [switch]$RuntimeOnly,
    [string]$InstallDir = $env:DSH_INSTALL_DIR,
    [string]$NodeExecutable = $env:DSH_NODE_EXECUTABLE,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 120
)

# Windows PowerShell 5.1 compatible. Use the running Desktop Electron for
# ASAR-aware reads, without opening a DSH profile or starting its Web app.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    throw 'DSH_INSTALL_DIR is required; run from the installed DSH environment or pass -InstallDir.'
}
if ([string]::IsNullOrWhiteSpace($NodeExecutable)) {
    throw 'DSH_NODE_EXECUTABLE is required; pass the installed Electron executable with -NodeExecutable.'
}
if (-not (Test-Path -LiteralPath $NodeExecutable -PathType Leaf)) {
    throw 'The specified installed Electron executable does not exist.'
}

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$testDirectory = Join-Path $workspaceRoot 'integrations\dsh-core-memory\test'
$runtimeTest = Join-Path $testDirectory 'runtime.test.cjs'
if (-not (Test-Path -LiteralPath $runtimeTest -PathType Leaf)) {
    throw 'The installed-runtime test file is missing.'
}
$testFiles = @()
if (-not $RuntimeOnly) {
    $testFiles += @(Get-ChildItem -LiteralPath $testDirectory -File |
        Where-Object { $_.Name -match '\.test\.(?:cjs|mjs|js)$' -and $_.Name -ne 'runtime.test.cjs' } |
        Sort-Object Name | ForEach-Object { $_.FullName })
}
$testFiles += $runtimeTest

# Pass paths through a base64 JSON payload, avoiding PowerShell/JS quoting
# ambiguities and preserving Chinese workspace names.
$payload = @{
    installDir = $InstallDir
    tests = @($testFiles)
} | ConvertTo-Json -Compress
$payloadBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
$script = @'
(async () => {
  const payload = JSON.parse(Buffer.from('__PAYLOAD__', 'base64').toString('utf8'));
  if (!process.versions.electron) throw new Error('The executable is not the installed Electron runtime.');
  process.env.DSH_INSTALL_DIR = payload.installDir;
  const { pathToFileURL } = require('node:url');
  for (const filename of payload.tests) await import(pathToFileURL(filename).href);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
'@
$script = $script.Replace('__PAYLOAD__', $payloadBase64)
$scriptBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))

$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $NodeExecutable
$start.Arguments = '-e "eval(Buffer.from(''' + $scriptBase64 + ''',''base64'').toString())"'
$start.WorkingDirectory = $workspaceRoot
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.StandardOutputEncoding = [Text.Encoding]::UTF8
$start.StandardErrorEncoding = [Text.Encoding]::UTF8

$previousRunAsNode = $env:ELECTRON_RUN_AS_NODE
$process = $null
$timeoutMs = $TimeoutSeconds * 1000
$clock = [Diagnostics.Stopwatch]::StartNew()
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $process = [Diagnostics.Process]::Start($start)
    # Drain both pipes concurrently while the bounded process wait runs.
    $stderr = $process.StandardError.ReadToEndAsync()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $remainingMs = [Math]::Max(0, $timeoutMs - [int]$clock.ElapsedMilliseconds)
    $timedOut = -not $process.WaitForExit($remainingMs)
    if (-not $timedOut) {
        # A descendant inheriting a pipe must not extend the overall budget.
        $remainingMs = [Math]::Max(0, $timeoutMs - [int]$clock.ElapsedMilliseconds)
        $streams = [Threading.Tasks.Task[]]@($stdout, $stderr)
        $timedOut = -not [Threading.Tasks.Task]::WaitAll($streams, $remainingMs)
    }
    if ($timedOut) {
        # This object is the exact process created above; never use a name/PID
        # search or terminate any pre-existing DSH application or fixture server.
        if (-not $process.HasExited) {
            try { $process.Kill() }
            catch [InvalidOperationException] { } # It exited during the check.
        }
        [void]$process.WaitForExit(5000)
        throw 'DSH core-memory verification timed out.'
    }
    [Console]::Out.Write($stdout.Result)
    [Console]::Error.Write($stderr.Result)
    $result = $process.ExitCode
}
finally {
    if ($null -ne $process) {
        try {
            if (-not $process.HasExited) {
                try { $process.Kill() }
                catch [InvalidOperationException] { }
                [void]$process.WaitForExit(5000)
            }
        }
        finally {
            $process.Dispose()
        }
    }
    $clock.Stop()
    $env:ELECTRON_RUN_AS_NODE = $previousRunAsNode
}
exit $result
