$ErrorActionPreference = 'Stop'
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Unavailable' }
$lib = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..', 'lib'))
. ([IO.Path]::Combine($lib, 'windows-native-bootstrap.ps1'))
. ([IO.Path]::Combine($lib, 'windows-compiler-job.ps1'))
. ([IO.Path]::Combine($lib, 'windows-session-protection.ps1')) -DefinitionsOnly
$native = New-LoomBootstrapApi
$receipt = [IO.Path]::Combine($env:LOOM_PRIVATE_COMPILER_BASE, $env:LOOM_PRIVATE_COMPILATION_ID, 'witness.json')
$script:announced = $false
Invoke-LoomGuardedWorker $native ([IO.Path]::Combine($PSScriptRoot, 'windows-compiler-witness.ps1')) {
    [void](Invoke-LoomCompilerScope $native $true $null)
} {
    param($worker)
    if (-not $script:announced -and [IO.File]::Exists($receipt)) {
        $r = [IO.File]::ReadAllText($receipt) | ConvertFrom-Json
        $guardian = [Diagnostics.Process]::GetCurrentProcess()
        $r | Add-Member -NotePropertyName guardian -NotePropertyValue @{ pid = $guardian.Id; started = $guardian.StartTime.ToUniversalTime().Ticks.ToString() }
        $r | Add-Member -NotePropertyName worker -NotePropertyValue @{ pid = $worker.Id; started = $worker.StartTime.ToUniversalTime().Ticks.ToString() }
        [Console]::Out.WriteLine((ConvertTo-Json -Compress -Depth 4 $r))
        [Console]::Out.Flush()
        $script:announced = $true
    }
}
