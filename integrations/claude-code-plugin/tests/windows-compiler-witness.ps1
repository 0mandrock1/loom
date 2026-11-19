param([switch]$Worker)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not $Worker -or [Console]::In.ReadLine() -cne $env:LOOM_PRIVATE_COMPILATION_ID) { throw 'Unavailable' }
$lib = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..', 'lib'))
. ([IO.Path]::Combine($lib, 'windows-native-bootstrap.ps1'))
. ([IO.Path]::Combine($lib, 'windows-session-protection.ps1')) -DefinitionsOnly
$native = New-LoomBootstrapApi
Invoke-LoomCompilerScope $native $false {
    param($directory)
    $pipeName = 'loom-compiler-' + $env:LOOM_PRIVATE_COMPILATION_ID
    $pipe = [IO.Pipes.NamedPipeServerStream]::new($pipeName, [IO.Pipes.PipeDirection]::Out)
    try {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = [IO.Path]::Combine([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory(), 'csc.exe')
        $start.Arguments = '/noconfig /target:library /out:"' + [IO.Path]::Combine($directory, 'witness.dll') + '" @"\\.\pipe\' + $pipeName + '"'
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $compiler = [Diagnostics.Process]::Start($start)
        # Announce only after the REAL compiler opens its response pipe. Keeping
        # it open without EOF blocks compilation until the native job timer.
        $pipe.WaitForConnection()
        if ($compiler.HasExited) { throw 'Compiler did not remain blocked on its response pipe' }
        $r = @{ pid = $compiler.Id; started = $compiler.StartTime.ToUniversalTime().Ticks.ToString(); directory = $directory }
        $path = [IO.Path]::Combine($directory, 'witness.json')
        [IO.File]::WriteAllText($path + '.new', (ConvertTo-Json -Compress $r))
        [IO.File]::Move($path + '.new', $path)
        [Threading.Thread]::Sleep(30000)
    } finally { $pipe.Dispose() }
}
