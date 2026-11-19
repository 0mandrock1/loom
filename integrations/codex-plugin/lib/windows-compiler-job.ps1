# Native timer callbacks contain emitted IL only; they need no PowerShell
# runspace, managed timer, Node event loop or compiler to stop a blocked worker.
$script:loomTimerRoots = New-Object 'System.Collections.Generic.List[System.Delegate]'
function Start-LoomNativeTimer([type]$native, [string]$method, [IntPtr]$context, [uint32]$milliseconds) {
    Write-LoomProtectionPhase 'native-timer'
    $callbackType = $native.Assembly.GetType('LoomNativeTimerCallback', $true)
    $callback = [Delegate]::CreateDelegate($callbackType, $native.GetMethod($method))
    $script:loomTimerRoots.Add($callback)
    $pointer = [Runtime.InteropServices.Marshal]::GetFunctionPointerForDelegate($callback)
    $timer = [IntPtr]::Zero
    # A one-shot callback on the native timer thread performs one short API call.
    if (-not $native::CreateTimerQueueTimer([ref]$timer, [IntPtr]::Zero, $pointer, $context, $milliseconds, 0, 0x28)) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Write-LoomProtectionPhase 'native-timer' $nativeCode
        throw 'Unavailable'
    }
    # Timers and delegate roots live through OS exit; no stale handle reuse.
}

function Start-LoomGuardianDeadline([type]$native) {
    Write-LoomProtectionPhase 'guardian-deadline'
    $deadline = 0L
    if (-not [long]::TryParse($env:LOOM_PRIVATE_DEADLINE_UTC, [ref]$deadline)) { throw 'Unavailable' }
    $remaining = $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($remaining -le 0) { throw 'Unavailable' }
    # The startup timestamp only shortens this relative native timer.
    $remaining = [Math]::Min(1500L, $remaining)
    # An outer self-job covers worker creation as well as compilation. Even
    # death between Process.Start and inner assignment closes this handle.
    $outer = New-LoomCompilerJob $native
    if (-not $native::AssignProcessToJobObject($outer, $native::GetCurrentProcess())) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Write-LoomProtectionPhase 'native-job' $nativeCode
        throw 'Unavailable'
    }
    Start-LoomNativeTimer $native 'StopJob' $outer ([uint32]$remaining)
    return [Diagnostics.Stopwatch]::StartNew(), [int]$remaining
}

function New-LoomCompilerJob([type]$native) {
    Write-LoomProtectionPhase 'native-job'
    $handle = $native::CreateJobObjectW([IntPtr]::Zero, [IntPtr]::Zero)
    if ($handle -eq [IntPtr]::Zero) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Write-LoomProtectionPhase 'native-job' $nativeCode
        throw 'Unavailable'
    }
    $size = if ([IntPtr]::Size -eq 8) { 144 } else { 112 }
    $limits = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)
    try {
        [Runtime.InteropServices.Marshal]::Copy((New-Object byte[] $size), 0, $limits, $size)
        [Runtime.InteropServices.Marshal]::WriteInt32($limits, 16, 0x2000)
        if (-not $native::SetInformationJobObject($handle, 9, $limits, $size)) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-LoomProtectionPhase 'native-job' $nativeCode
            throw 'Unavailable'
        }
        return $handle
    } catch {
        [void]$native::CloseHandle($handle)
        throw
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($limits) }
}

function Receive-LoomWorkerPhases($state, [bool]$finish = $false) {
    while ($null -ne $state.Pending -and ($finish -or $state.Pending.IsCompleted)) {
        try { $line = $state.Pending.GetAwaiter().GetResult() }
        catch { $state.Unexpected = $true; $state.Pending = $null; break }
        if ($null -eq $line) { $state.Pending = $null; break }
        $code = 0
        if ($line -cmatch '^LSP1:(startup|bootstrap|worker-bootstrap|guardian-deadline|native-job|native-timer|worker-start|worker-assign|compiler-private|compile|request|native-op|cleanup|worker-exit|helper-exit):(-?[0-9]{1,10})$' -and [int]::TryParse($Matches[2], [ref]$code)) {
            Write-LoomProtectionPhase $Matches[1] $code
            if ($code -ne 0) { $state.Failed = $true }
        } else { $state.Unexpected = $true }
        try { $state.Pending = $state.Reader.ReadLineAsync() }
        catch { $state.Unexpected = $true; $state.Pending = $null }
    }
}

function Invoke-LoomGuardedWorker([type]$native, [string]$workerScript, [scriptblock]$cleanup, [scriptblock]$observe = $null) {
    [Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $clock, $budget = Start-LoomGuardianDeadline $native
    if ($budget -le 150) { throw 'Unavailable' }
    $job = New-LoomCompilerJob $native
    # The guardian is outside this inner job. OS death still closes the last
    # noninherited handle. The earlier timer leaves time for exact cleanup.
    $workerTime = $budget - 150 - $clock.ElapsedMilliseconds
    if ($workerTime -le 0) { throw 'Unavailable' }
    Start-LoomNativeTimer $native 'StopJob' $job ([uint32]$workerTime)
    $worker = $null
    $assigned = $false
    $workerPhases = $null
    $exitObserved = $false
    try {
        $inputBuffer = New-Object char[] 4097
        $length = 0
        while ($length -lt $inputBuffer.Length) {
            $read = [Console]::In.Read($inputBuffer, $length, $inputBuffer.Length - $length)
            if ($read -eq 0) { break }
            $length += $read
        }
        if ($length -gt 4096) { throw 'Unavailable' }
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = [IO.Path]::Combine($PSHOME, 'powershell.exe')
        # Script paths are fixed plugin/test assets, never request values.
        if ($workerScript.Contains('"')) { throw 'Unavailable' }
        $start.Arguments = '-NoLogo -NoProfile -NonInteractive -File "' + $workerScript + '" -Worker'
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        Write-LoomProtectionPhase 'worker-start'
        $worker = [Diagnostics.Process]::Start($start)
        # The fixed worker blocks on its nonce line before compiler/temp work.
        # Refused assignment never releases that line and never uses breakaway.
        Write-LoomProtectionPhase 'worker-assign'
        if (-not $native::AssignProcessToJobObject($job, $worker.Handle)) {
            $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            Write-LoomProtectionPhase 'worker-assign' $nativeCode
            throw 'Unavailable'
        }
        $assigned = $true
        $stdout = $worker.StandardOutput.ReadToEndAsync()
        $workerPhases = @{ Reader = $worker.StandardError; Pending = $worker.StandardError.ReadLineAsync(); Unexpected = $false; Failed = $false }
        $worker.StandardInput.WriteLine($env:LOOM_PRIVATE_COMPILATION_ID)
        $worker.StandardInput.Write($inputBuffer, 0, $length)
        $worker.StandardInput.Close()
        do {
            Receive-LoomWorkerPhases $workerPhases
            if ($null -ne $observe) { & $observe $worker }
            $wait = $native::WaitForSingleObject($worker.Handle, 10)
            if ($wait -ne 0 -and $wait -ne 258) { throw 'Unavailable' }
        } while ($wait -eq 258)
        $worker.WaitForExit()
        Receive-LoomWorkerPhases $workerPhases $true
        Write-LoomProtectionPhase 'worker-exit' $worker.ExitCode
        $exitObserved = $true
        $text = $stdout.GetAwaiter().GetResult()
        if ($worker.ExitCode -ne 0 -or $workerPhases.Unexpected -or $workerPhases.Failed -or $text.Length -gt 1024) { throw 'Unavailable' }
    } finally {
        if ($null -ne $worker -and -not $assigned -and -not $worker.HasExited) { $worker.Kill() }
        if (-not $native::TerminateJobObject($job, 1)) { throw 'Unavailable' }
        if ($null -ne $worker) {
            $worker.WaitForExit()
            if (-not $exitObserved) {
                if ($null -ne $workerPhases) { Receive-LoomWorkerPhases $workerPhases $true }
                Write-LoomProtectionPhase 'worker-exit' $worker.ExitCode
            }
            $worker.Dispose()
            $worker = $null
        }
        # Wait for every compiler descendant before deleting its owned files.
        $accounting = [Runtime.InteropServices.Marshal]::AllocHGlobal(48)
        try {
            do {
                if (-not $native::QueryInformationJobObject($job, 1, $accounting, 48, [IntPtr]::Zero)) { throw 'Unavailable' }
                $active = [Runtime.InteropServices.Marshal]::ReadInt32($accounting, 40)
                if ($active -gt 0) { [Threading.Thread]::Sleep(1) }
            } while ($active -gt 0)
            Write-LoomProtectionPhase 'cleanup'
            & $cleanup
        } finally {
            [Runtime.InteropServices.Marshal]::FreeHGlobal($accounting)
        }
        # Keep the job handle through timer callbacks and process exit. Closing
        # it now could let a later timer operate on a reused handle value.
    }
    [Console]::Out.Write($text)
}
