# Durable session records use one native operation per helper invocation.
param([switch]$CleanupCompilation, [switch]$Worker, [switch]$DefinitionsOnly)
$ErrorActionPreference = 'Stop'
# Fixed ASCII diagnostics precede cmdlet and encoding initialization.
$loomDiagnosticStream = [Console]::OpenStandardError()
$loomDiagnosticStream.Write([byte[]](76, 83, 80, 49, 58, 101, 110, 116, 114, 121, 58, 48, 10), 0, 13)
$loomDiagnosticStream.Flush()
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
$loomDiagnosticStream.Write([byte[]](76, 83, 80, 49, 58, 101, 110, 99, 111, 100, 105, 110, 103, 45, 105, 110, 112, 117, 116, 58, 48, 10), 0, 22)
$loomDiagnosticStream.Flush()
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$loomDiagnosticStream.Write([byte[]](76, 83, 80, 49, 58, 101, 110, 99, 111, 100, 105, 110, 103, 45, 111, 117, 116, 112, 117, 116, 58, 48, 10), 0, 23)
$loomDiagnosticStream.Flush()
function Write-LoomProtectionPhase([string]$phase, [int]$code = 0) {
    if ($phase -cnotin @('entry', 'encoding-input', 'encoding-output', 'startup', 'bootstrap', 'worker-bootstrap', 'guardian-deadline', 'native-job', 'native-timer', 'worker-start', 'worker-assign', 'compiler-private', 'compile', 'request', 'native-op', 'cleanup', 'worker-exit', 'helper-exit')) { throw 'Unavailable' }
    if ($phase -cnotin @('worker-exit', 'helper-exit')) { $script:loomProtectionPhase = $phase }
    try {
        [Console]::Error.WriteLine('LSP1:' + $phase + ':' + $code.ToString([Globalization.CultureInfo]::InvariantCulture))
        [Console]::Error.Flush()
    } catch { $script:loomProtectionDiagnosticFailed = $true }
}
function Invoke-LoomCompilerScope([type]$native, [bool]$CleanupCompilation, [scriptblock]$Operation) {
$held = New-Object 'System.Collections.Generic.List[System.IntPtr]'
$compileDirectory = $null
$compilerHandleStart = -1
$result = $null
$succeeded = $false

try {
    Write-LoomProtectionPhase $(if ($CleanupCompilation) { 'cleanup' } else { 'compiler-private' })
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Unavailable' }
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User

    function Pin-Directory([string]$directory, [bool]$private, [bool]$allowMissing = $false) {
        # READ_CONTROL | FILE_READ_ATTRIBUTES; no FILE_SHARE_DELETE.
        $handle = $native::CreateFileW($directory, 0x20080, 3, [IntPtr]::Zero, 3, 0x02200000, [IntPtr]::Zero)
        if ($handle.ToInt64() -eq -1) {
            $openError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            if ($allowMissing -and ($openError -eq 2 -or $openError -eq 3)) { return $false }
            throw 'Unavailable'
        }
        $held.Add($handle)
        $information = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
        try {
            if (-not $native::GetFileInformationByHandle($handle, $information)) { throw 'Unavailable' }
            $attributes = [Runtime.InteropServices.Marshal]::ReadInt32($information)
            if (($attributes -band 0x400) -ne 0 -or ($attributes -band 0x10) -eq 0) { throw 'Unavailable' }
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($information) }
        if (-not $private) { return $true }
        $owner = $group = $dacl = $sacl = $sd = [IntPtr]::Zero
        if ($native::GetSecurityInfo($handle, 1, 5, [ref]$owner, [ref]$group, [ref]$dacl, [ref]$sacl, [ref]$sd) -ne 0) { throw 'Unavailable' }
        try {
            if ($owner -eq [IntPtr]::Zero -or $dacl -eq [IntPtr]::Zero -or $sd -eq [IntPtr]::Zero) { throw 'Unavailable' }
            $length = $native::GetSecurityDescriptorLength($sd)
            if ($length -lt 1 -or $length -gt 65536) { throw 'Unavailable' }
            $bytes = New-Object byte[] $length
            [Runtime.InteropServices.Marshal]::Copy($sd, $bytes, 0, $length)
            $actual = New-Object System.Security.AccessControl.RawSecurityDescriptor($bytes, 0)
            if (-not $sid.Equals($actual.Owner) -or ($actual.ControlFlags -band 0x1004) -ne 0x1004 -or $null -eq $actual.DiscretionaryAcl -or $actual.DiscretionaryAcl.Count -ne 1) { throw 'Unavailable' }
            $ace = $actual.DiscretionaryAcl[0]
            if ($ace -isnot [System.Security.AccessControl.CommonAce] -or $ace.IsCallback -or $ace.AceQualifier -ne [System.Security.AccessControl.AceQualifier]::AccessAllowed -or $ace.AceFlags -ne 3 -or $ace.AccessMask -ne 0x1f01ff -or -not $sid.Equals($ace.SecurityIdentifier)) { throw 'Unavailable' }
        } finally { if ($sd -ne [IntPtr]::Zero) { [void]$native::LocalFree($sd) } }
        return $true
    }

    function New-PrivateDirectory([string]$directory, [bool]$allowExisting) {
        $binary = $security.GetSecurityDescriptorBinaryForm()
        $descriptor = [Runtime.InteropServices.Marshal]::AllocHGlobal($binary.Length)
        $attributeSize = if ([IntPtr]::Size -eq 8) { 24 } else { 12 }
        $pointerOffset = if ([IntPtr]::Size -eq 8) { 8 } else { 4 }
        $attributes = [Runtime.InteropServices.Marshal]::AllocHGlobal($attributeSize)
        try {
            [Runtime.InteropServices.Marshal]::Copy($binary, 0, $descriptor, $binary.Length)
            [Runtime.InteropServices.Marshal]::WriteInt32($attributes, 0, $attributeSize)
            [Runtime.InteropServices.Marshal]::WriteIntPtr($attributes, $pointerOffset, $descriptor)
            [Runtime.InteropServices.Marshal]::WriteInt32($attributes, $pointerOffset + [IntPtr]::Size, 0)
            if (-not $native::CreateDirectoryW($directory, $attributes)) {
                $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                if (-not $allowExisting -or $errorCode -ne 183) { throw 'Unavailable' }
            }
        } finally {
            [Runtime.InteropServices.Marshal]::FreeHGlobal($attributes)
            [Runtime.InteropServices.Marshal]::FreeHGlobal($descriptor)
        }
    }

    $compilerBase = [IO.Path]::GetFullPath($env:LOOM_PRIVATE_COMPILER_BASE)
    $compilationId = $env:LOOM_PRIVATE_COMPILATION_ID
    if ($compilationId -notmatch '^[0-9a-f]{32}$') { throw 'Unavailable' }
    $root = [IO.Path]::GetPathRoot($compilerBase)
    if ($root -notmatch '^[A-Za-z]:\\$') { throw 'Unavailable' }
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetOwner($sid)
    $security.SetAccessRuleProtection($true, $false)
    # Compiler-created files must inherit owner-only authority themselves;
    # directory traversal checks may be bypassed by SeChangeNotifyPrivilege.
    $inherit = [System.Security.AccessControl.InheritanceFlags]'ObjectInherit, ContainerInherit'
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit,
        [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)))
    [void](Pin-Directory $root $false)
    $current = $root
    $missing = $false
    $parts = $compilerBase.Substring($root.Length).Split([char]'\')
    for ($i = 0; $i -lt $parts.Length; $i++) {
        $current = [IO.Path]::Combine($current, $parts[$i])
        if (-not $CleanupCompilation) { New-PrivateDirectory $current $true }
        if (-not (Pin-Directory $current ($i -eq $parts.Length - 1) $CleanupCompilation)) {
            $missing = $true
            break
        }
    }
    $candidate = [IO.Path]::Combine($compilerBase, $compilationId)
    if ($CleanupCompilation) {
        if (-not $missing) {
            $compilerHandleStart = $held.Count
            if (Pin-Directory $candidate $true $true) {
                # A receipt is written only after exclusive directory creation.
                # Without it cleanup refuses rather than deleting unknown data.
                $marker = [IO.File]::ReadAllText([IO.Path]::Combine($candidate, 'created'))
                if ($marker -ne ('loom-compiler-v1:' + $compilationId)) { throw 'Unavailable' }
                $compileDirectory = $candidate
            }
        }
        $succeeded = $true
        $result = $null
    } else {
    New-PrivateDirectory $candidate $false
    $compileDirectory = $candidate
    $compilerHandleStart = $held.Count
    [void](Pin-Directory $compileDirectory $true)
    [IO.File]::WriteAllText([IO.Path]::Combine($compileDirectory, 'created'), ('loom-compiler-v1:' + $compilationId))

    $result = & $Operation $compileDirectory
    $succeeded = $true
    }
} catch {
    Write-LoomProtectionPhase $script:loomProtectionPhase ([int]$_.Exception.HResult)
    $succeeded = $false
} finally {
    Write-LoomProtectionPhase 'cleanup'
    if ($compilerHandleStart -ge 0) {
        for ($i = $held.Count - 1; $i -ge $compilerHandleStart; $i--) {
            if (-not $native::CloseHandle($held[$i])) { $succeeded = $false }
            $held.RemoveAt($i)
        }
    }
    if ($null -ne $compileDirectory) {
        try {
            foreach ($file in [IO.Directory]::GetFileSystemEntries($compileDirectory)) {
                $attributes = [IO.File]::GetAttributes($file)
                if (($attributes -band 0x410) -ne 0) { throw 'Unavailable' }
                [IO.File]::Delete($file)
            }
            [IO.Directory]::Delete($compileDirectory, $false)
        } catch {
            Write-LoomProtectionPhase 'cleanup' ([int]$_.Exception.HResult)
            $succeeded = $false
        }
    }
    for ($i = $held.Count - 1; $i -ge 0; $i--) {
        if (-not $native::CloseHandle($held[$i])) { $succeeded = $false }
    }
}
if (-not $succeeded) { throw 'Unavailable' }
return $result
}

function Invoke-LoomRecordOperation([string]$compileDirectory) {
    # Compile fixed code before reading any session request.
    Write-LoomProtectionPhase 'compile'
    $source = [IO.File]::ReadAllText([IO.Path]::Combine($PSScriptRoot, 'windows-private-records.cs'))
    $parameters = New-Object System.CodeDom.Compiler.CompilerParameters
    $parameters.GenerateInMemory = $true
    $parameters.IncludeDebugInformation = $false
    $parameters.TreatWarningsAsErrors = $true
    $parameters.CompilerOptions = '/optimize+'
    $parameters.TempFiles = New-Object System.CodeDom.Compiler.TempFileCollection($compileDirectory, $false)
    [void]$parameters.ReferencedAssemblies.Add('System.dll')
    [void]$parameters.ReferencedAssemblies.Add('System.Core.dll')
    Microsoft.PowerShell.Utility\Add-Type -TypeDefinition $source -Language CSharp -CompilerParameters $parameters -ErrorAction Stop | Out-Null

    Write-LoomProtectionPhase 'request'
    $buffer = New-Object char[] 4097
    $count = 0
    while ($count -lt $buffer.Length) {
        $read = [Console]::In.Read($buffer, $count, $buffer.Length - $count)
        if ($read -eq 0) { break }
        $count += $read
    }
    if ($count -gt 4096) { throw 'Unavailable' }
    $request = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (New-Object string($buffer, 0, $count)) -ErrorAction Stop
    $expectedKeys = switch ($request.op) {
        'create' { 'base,op' }
        'inspect' { 'base,id,op' }
        'protect' { 'base,id,op' }
        'bind' { 'base,id,key,op' }
        'find' { 'base,key,op' }
        default { throw 'Unavailable' }
    }
    if ((($request.PSObject.Properties.Name | Sort-Object) -join ',') -ne $expectedKeys) { throw 'Unavailable' }
    if ($request.base -isnot [string] -or $request.base.Length -gt 2048 -or -not [IO.Path]::IsPathRooted($request.base)) { throw 'Unavailable' }
    Write-LoomProtectionPhase 'native-op'
    $store = New-Object WindowsPrivateRecords($request.base)
    $result = switch ($request.op) {
        'create' { $store.CreateRoot() }
        'inspect' { $store.IsProtected($request.id) }
        'protect' { $store.AppendProtection($request.id); $null }
        'bind' { $store.BindAlias($request.key, $request.id); $null }
        'find' { $store.FindAlias($request.key) }
    }
    return $result
}

if ($DefinitionsOnly) { return }
try {
    Write-LoomProtectionPhase 'startup'
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Unavailable' }
    if ($Worker -and [Console]::In.ReadLine() -cne $env:LOOM_PRIVATE_COMPILATION_ID) { throw 'Unavailable' }
    . ([IO.Path]::Combine($PSScriptRoot, 'windows-native-bootstrap.ps1'))
    . ([IO.Path]::Combine($PSScriptRoot, 'windows-compiler-job.ps1'))
    Write-LoomProtectionPhase $(if ($Worker) { 'worker-bootstrap' } else { 'bootstrap' })
    $native = New-LoomBootstrapApi
    if ($Worker) {
        $result = Invoke-LoomCompilerScope $native $false ${function:Invoke-LoomRecordOperation}
        [Console]::Out.Write((ConvertTo-Json -Compress -Depth 4 -InputObject @{ ok = $true; value = $result }))
    } elseif ($CleanupCompilation) {
        [void](Start-LoomGuardianDeadline $native)
        [void](Invoke-LoomCompilerScope $native $true $null)
        [Console]::Out.Write('{"ok":true,"value":null}')
    } else {
        Invoke-LoomGuardedWorker $native $PSCommandPath {
            [void](Invoke-LoomCompilerScope $native $true $null)
        }
    }
    Write-LoomProtectionPhase 'helper-exit' 0
    if ($script:loomProtectionDiagnosticFailed) { throw 'Unavailable' }
    exit 0
} catch {
    Write-LoomProtectionPhase $script:loomProtectionPhase ([int]$_.Exception.HResult)
    Write-LoomProtectionPhase 'helper-exit' 1
    [Console]::Out.Write('{"ok":false}')
    exit 1
}
