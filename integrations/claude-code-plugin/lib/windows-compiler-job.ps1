# Native timer callbacks contain emitted IL only; they need no PowerShell
# runspace, managed timer, Node event loop or compiler to stop a blocked worker.
$script:loomTimerRoots = [System.Collections.Generic.List[System.Delegate]]::new()
function Start-LoomNativeTimer([type]$native, [string]$method, [IntPtr]$context, [long]$deadline) {
    Write-LoomProtectionPhase 'native-timer'
    $callbackType = $native.Assembly.GetType('LoomNativeTimerCallback', $true)
    $callback = [Delegate]::CreateDelegate($callbackType, $native.GetMethod($method))
    $script:loomTimerRoots.Add($callback)
    $pointer = [Runtime.InteropServices.Marshal]::GetFunctionPointerForDelegate($callback)
    $timer = [IntPtr]::Zero
    # Callback and job setup consume the same absolute operation budget.
    $remaining = $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($remaining -le 0 -or $remaining -gt 1500) { throw 'Unavailable' }
    # A one-shot callback on the native timer thread performs one short API call.
    if (-not $native::CreateTimerQueueTimer([ref]$timer, [IntPtr]::Zero, $pointer, $context, ([uint32]$remaining), 0, 0x28)) {
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
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $remaining = $deadline - $now
    if ($remaining -le 0) { throw 'Unavailable' }
    # Anchor both native deadlines before creating or assigning either job.
    $remaining = [Math]::Min(1500L, $remaining)
    $deadline = $now + $remaining
    # An outer self-job covers worker creation as well as compilation. Even
    # death between Process.Start and inner assignment closes this handle.
    $outer = New-LoomCompilerJob $native
    if (-not $native::AssignProcessToJobObject($outer, $native::GetCurrentProcess())) {
        $nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Write-LoomProtectionPhase 'native-job' $nativeCode
        throw 'Unavailable'
    }
    Start-LoomNativeTimer $native 'StopJob' $outer $deadline
    return @{ Deadline = $deadline; Job = $outer }
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
        [Runtime.InteropServices.Marshal]::Copy(([byte[]]::new($size)), 0, $limits, $size)
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

# All callers supply fixed plugin/test arguments. Session JSON never selects
# the application, command line, environment, working directory or source.
function Invoke-LoomCompilerProcess([type]$native, [IntPtr]$job, [long]$deadline,
    [string]$directory, [string]$arguments, [scriptblock]$observe = $null) {
    $application = [IO.Path]::Combine([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory(), 'csc.exe')
    if ($application.Contains('"')) { throw 'Unavailable' }
    $command = [Text.StringBuilder]::new('"' + $application + '" ' + $arguments)
    $attributeList = $startup = $information = $handleList = $security = $environment = [IntPtr]::Zero
    $nul = $process = $thread = [IntPtr]::Zero
    $attributesInitialized = $false
    try {
        $pointerSize = [IntPtr]::Size
        $startupSize = if ($pointerSize -eq 8) { 104 } else { 68 }
        $extendedSize = $startupSize + $pointerSize
        $startup = [Runtime.InteropServices.Marshal]::AllocHGlobal($extendedSize)
        [Runtime.InteropServices.Marshal]::Copy([byte[]]::new($extendedSize), 0, $startup, $extendedSize)
        [Runtime.InteropServices.Marshal]::WriteInt32($startup, 0, $extendedSize)
        $information = [Runtime.InteropServices.Marshal]::AllocHGlobal(2 * $pointerSize + 8)
        [Runtime.InteropServices.Marshal]::Copy([byte[]]::new(2 * $pointerSize + 8), 0, $information, 2 * $pointerSize + 8)
        $securitySize = if ($pointerSize -eq 8) { 24 } else { 12 }
        $security = [Runtime.InteropServices.Marshal]::AllocHGlobal($securitySize)
        [Runtime.InteropServices.Marshal]::Copy([byte[]]::new($securitySize), 0, $security, $securitySize)
        [Runtime.InteropServices.Marshal]::WriteInt32($security, 0, $securitySize)
        [Runtime.InteropServices.Marshal]::WriteInt32($security, $(if ($pointerSize -eq 8) { 16 } else { 8 }), 1)
        $nul = $native::CreateFileW('NUL', [uint32]3221225472, 3, $security, 3, 0, [IntPtr]::Zero)
        if ($nul.ToInt64() -eq -1) { $nul = [IntPtr]::Zero; throw 'Unavailable' }
        # STARTUPINFOEX allows ONLY this harmless standard-I/O handle through.
        # Job and pinned-directory handles remain noninherited.
        $size = [IntPtr]::Zero
        $initialized = $native::InitializeProcThreadAttributeList([IntPtr]::Zero, 1, 0, [ref]$size)
        $sizeError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($initialized -or $sizeError -ne 122 -or $size.ToInt64() -le 0 -or $size.ToInt64() -gt 65536) { throw 'Unavailable' }
        $attributeList = [Runtime.InteropServices.Marshal]::AllocHGlobal($size)
        if (-not $native::InitializeProcThreadAttributeList($attributeList, 1, 0, [ref]$size)) { throw 'Unavailable' }
        $attributesInitialized = $true
        $handleList = [Runtime.InteropServices.Marshal]::AllocHGlobal($pointerSize)
        [Runtime.InteropServices.Marshal]::WriteIntPtr($handleList, $nul)
        if (-not $native::UpdateProcThreadAttribute($attributeList, 0, [IntPtr]0x20002,
            $handleList, [IntPtr]$pointerSize, [IntPtr]::Zero, [IntPtr]::Zero)) { throw 'Unavailable' }
        [Runtime.InteropServices.Marshal]::WriteIntPtr($startup, $startupSize, $attributeList)
        [Runtime.InteropServices.Marshal]::WriteInt32($startup, $(if ($pointerSize -eq 8) { 60 } else { 44 }), 0x100)
        $standardOffset = if ($pointerSize -eq 8) { 80 } else { 56 }
        for ($i = 0; $i -lt 3; $i++) { [Runtime.InteropServices.Marshal]::WriteIntPtr($startup, $standardOffset + $i * $pointerSize, $nul) }
        $variables = [Environment]::GetEnvironmentVariables()
        $variables['TEMP'] = $directory
        $variables['TMP'] = $directory
        $entries = @($variables.Keys | Sort-Object | ForEach-Object { [string]$_ + '=' + [string]$variables[$_] })
        $environment = [Runtime.InteropServices.Marshal]::StringToHGlobalUni(($entries -join "`0") + "`0`0")
        if ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge $deadline) { throw 'Unavailable' }
        Write-LoomProtectionPhase 'worker-start'
        # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT |
        # EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW. Never BREAKAWAY.
        if (-not $native::CreateProcessW($application, $command, [IntPtr]::Zero, [IntPtr]::Zero,
            $true, 0x08080404, $environment, $directory, $startup, $information)) { throw 'Unavailable' }
        $process = [Runtime.InteropServices.Marshal]::ReadIntPtr($information, 0)
        $thread = [Runtime.InteropServices.Marshal]::ReadIntPtr($information, $pointerSize)
        $pidValue = [Runtime.InteropServices.Marshal]::ReadInt32($information, 2 * $pointerSize)
        if ($process -eq [IntPtr]::Zero -or $thread -eq [IntPtr]::Zero -or $pidValue -le 0) { throw 'Unavailable' }
        Write-LoomProtectionPhase 'worker-assign'
        if (-not $native::AssignProcessToJobObject($job, $process)) { throw 'Unavailable' }
        # A fired timer cannot grant a late compiler a new execution window.
        if ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge $deadline) { throw 'Unavailable' }
        if ($native::ResumeThread($thread) -eq [uint32]::MaxValue) { throw 'Unavailable' }
        do {
            if ($null -ne $observe) { & $observe $process $pidValue }
            $wait = $native::WaitForSingleObject($process, 10)
            if ($wait -ne 0 -and $wait -ne 258) { throw 'Unavailable' }
        } while ($wait -eq 258)
        $exitCode = [uint32]0
        if (-not $native::GetExitCodeProcess($process, [ref]$exitCode)) { throw 'Unavailable' }
        if ($exitCode -gt [int]::MaxValue) { Write-LoomProtectionPhase 'worker-exit' -1 }
        else { Write-LoomProtectionPhase 'worker-exit' ([int]$exitCode) }
        if ($exitCode -ne 0) { throw 'Unavailable' }
    } finally {
        if ($process -ne [IntPtr]::Zero) {
            if ($native::WaitForSingleObject($process, 0) -ne 0) {
                # Covers assignment/resume failure too. Outer self-job bounds
                # this exact-handle wait if termination itself cannot complete.
                [void]$native::TerminateProcess($process, 1)
                if ($native::WaitForSingleObject($process, [uint32]::MaxValue) -ne 0) { throw 'Unavailable' }
            }
            [void]$native::CloseHandle($process)
        }
        if ($thread -ne [IntPtr]::Zero) { [void]$native::CloseHandle($thread) }
        if ($attributesInitialized) { $native::DeleteProcThreadAttributeList($attributeList) }
        foreach ($allocation in @($environment, $handleList, $attributeList, $information, $startup, $security)) {
            if ($allocation -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($allocation) }
        }
        if ($nul -ne [IntPtr]::Zero) { [void]$native::CloseHandle($nul) }
    }
}

function Stop-LoomCompilerJob([type]$native, [IntPtr]$job) {
    if (-not $native::TerminateJobObject($job, 1)) { throw 'Unavailable' }
    $accounting = [Runtime.InteropServices.Marshal]::AllocHGlobal(48)
    try {
        do {
            if (-not $native::QueryInformationJobObject($job, 1, $accounting, 48, [IntPtr]::Zero)) { throw 'Unavailable' }
            $active = [Runtime.InteropServices.Marshal]::ReadInt32($accounting, 40)
            if ($active -gt 0) { [Threading.Thread]::Sleep(1) }
        } while ($active -gt 0)
    } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($accounting) }
}

function Invoke-LoomGuardedCompiler([type]$native, [scriptblock]$compilerOperation) {
    $guardian = Start-LoomGuardianDeadline $native
    $deadline = $guardian.Deadline
    if (($deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) -le 150) { throw 'Unavailable' }
    $job = New-LoomCompilerJob $native
    Start-LoomNativeTimer $native 'StopJob' $job ($deadline - 150)
    $value = Invoke-LoomCompilerScope $native $false {
        param($directory)
        & $compilerOperation $native $job ($deadline - 150) $directory
    } {
        # Runs before any compile-scope handle release or file deletion.
        Stop-LoomCompilerJob $native $job
    }
    return @{ Value = $value; Guardian = $guardian }
    # Retain both job handles and native callback roots through process exit;
    # no timer may later operate on a recycled handle value.
}
