$ErrorActionPreference = 'Stop'
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Unavailable' }
$lib = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..', 'lib'))
. ([IO.Path]::Combine($lib, 'windows-native-bootstrap.ps1'))
. ([IO.Path]::Combine($lib, 'windows-compiler-job.ps1'))
. ([IO.Path]::Combine($lib, 'windows-session-protection.ps1')) -DefinitionsOnly
$native = New-LoomBootstrapApi
Invoke-LoomGuardedCompiler $native {
    param($compilerNative, $compilerJob, $compilerDeadline, $directory)
    $pipeName = 'loom-compiler-' + $env:LOOM_PRIVATE_COMPILATION_ID
    $pipe = [IO.Pipes.NamedPipeServerStream]::new($pipeName, [IO.Pipes.PipeDirection]::Out, 1,
        [IO.Pipes.PipeTransmissionMode]::Byte, [IO.Pipes.PipeOptions]::Asynchronous)
    try {
        $connected = $pipe.BeginWaitForConnection($null, $null)
        $state = @{ Announced = $false }
        Invoke-LoomCompilerProcess $compilerNative $compilerJob $compilerDeadline $directory (
            '/noconfig /target:library /out:witness.dll @"\\.\pipe\' + $pipeName + '"') {
            param($compilerHandle, $compilerPid)
            if (-not $state.Announced -and $connected.IsCompleted) {
                $pipe.EndWaitForConnection($connected)
                if ($compilerNative::WaitForSingleObject($compilerHandle, 0) -ne 258) { throw 'Unavailable' }
                $compiler = [Diagnostics.Process]::GetProcessById($compilerPid)
                try {
                    $guardian = [Diagnostics.Process]::GetCurrentProcess()
                    $receipt = @{ pid = $compilerPid; started = $compiler.StartTime.ToUniversalTime().Ticks.ToString();
                        directory = $directory; guardian = @{ pid = $guardian.Id; started = $guardian.StartTime.ToUniversalTime().Ticks.ToString() } }
                    [Console]::Out.WriteLine((ConvertTo-Json -Compress -Depth 4 $receipt))
                    [Console]::Out.Flush()
                    $state.Announced = $true
                } finally { $compiler.Dispose() }
            }
        }
    } finally { $pipe.Dispose() }
}
