$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Emit($value) { [Console]::WriteLine(($value | ConvertTo-Json -Depth 18 -Compress)) }

function WindowExecutable([string]$path) {
    # Some installed apps expose foo-launcher.exe while their windows belong to foo.exe.
    if ($path -match '(?i)-launcher\.exe$') {
        $candidate = $path -replace '(?i)-launcher\.exe$', '.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $path
}

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $operation = $request.operation
    $target = $request.target
    $arguments = $request.args

    if ($operation -eq 'catalog') {
        $apps = @(Get-StartApps | Sort-Object Name | ForEach-Object { @{name=$_.Name; id=$_.AppID} })
        Emit @{apps=$apps}
        exit
    }
    if ($operation -eq 'resolve') {
        $inputTarget = [string]$arguments.target
        if ($inputTarget -match '^pid:(\d+)$') {
            $process = Get-Process -Id ([int]$Matches[1])
            Emit @{name=$process.ProcessName; executable=$process.Path; pid=$process.Id}
            exit
        }
        if ([IO.Path]::IsPathRooted($inputTarget) -and (Test-Path -LiteralPath $inputTarget -PathType Leaf)) {
            $resolved = (Resolve-Path -LiteralPath $inputTarget).Path
            if ([IO.Path]::GetExtension($resolved) -ne '.exe') { throw 'Choose an installed .exe application.' }
            Emit @{name=[IO.Path]::GetFileNameWithoutExtension($resolved); executable=$resolved}
            exit
        }
        $allApps = @(Get-StartApps)
        $matches = @($allApps | Where-Object { $_.AppID -eq $inputTarget -or $_.Name -eq $inputTarget })
        if ($matches.Count -eq 0) { $matches = @($allApps | Where-Object { $_.Name.IndexOf($inputTarget, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) }
        if ($matches.Count -ne 1) { throw "Choose one exact app from the installed-app picker, or provide its full .exe path. Matches: $($matches.Name -join ', ')" }
        $app = $matches[0]
        $executable = $null
        if ($app.AppID -like '*!*') {
            $parts = $app.AppID.Split('!', 2)
            $package = Get-AppxPackage | Where-Object PackageFamilyName -eq $parts[0] | Select-Object -First 1
            if ($package) {
                $manifest = Get-AppxPackageManifest -Package $package.PackageFullName
                $application = $manifest.Package.Applications.Application | Where-Object Id -eq $parts[1] | Select-Object -First 1
                if ($application.Executable) { $executable = Join-Path $package.InstallLocation $application.Executable }
            }
        }
        if (-not $executable) {
            $shortcuts = @(
                Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
                Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
            )
            $shell = New-Object -ComObject WScript.Shell
            foreach ($directory in $shortcuts) {
                $links = Get-ChildItem -LiteralPath $directory -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Where-Object BaseName -eq $app.Name
                foreach ($link in $links) {
                    $candidate = $shell.CreateShortcut($link.FullName).TargetPath
                    if ($candidate -and [IO.Path]::GetExtension($candidate) -eq '.exe') { $executable = $candidate; break }
                }
                if ($executable) { break }
            }
        }
        if (-not $executable) { throw 'This Start menu entry does not expose an executable. Use the full .exe path or pid:<running process id>.' }
        Emit @{name=$app.Name; app_id=$app.AppID; executable=$executable}
        exit
    }

    Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Windows.Forms,System.Drawing
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class ConduitNative {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr info);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h,uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int x; public int y; }
}
'@
    [void][ConduitNative]::SetProcessDPIAware()

    function AppWindows {
        $valid = @{}
        $executable = WindowExecutable $target.executable
        foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
            try {
                if ($target.pid -and $process.Id -ne $target.pid) { continue }
                if ($process.Path -and ($process.Path -eq $target.executable -or $process.Path -eq $executable)) { $valid[$process.Id] = $true }
            } catch {}
        }
        $windows = [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
        foreach ($window in $windows) {
            if ($valid.ContainsKey($window.Current.ProcessId) -and $window.Current.NativeWindowHandle -ne 0) { $window }
        }
    }
    if ($operation -eq 'open') {
        $currentWindows = @(AppWindows)
        if ($currentWindows.Count -eq 0) {
            if ($target.pid) { throw 'The specified process has no app window. Reconnect using its installed app name or executable.' }
            if ($target.app_id) {
                $shell = New-Object -ComObject Shell.Application
                $shell.ShellExecute('explorer.exe', ('shell:AppsFolder\' + $target.app_id), '', 'open', 1)
            } else {
                Start-Process -FilePath $target.executable -WindowStyle Normal | Out-Null
            }
            $deadline = [DateTime]::UtcNow.AddSeconds(12)
            do {
                Start-Sleep -Milliseconds 250
                $currentWindows = @(AppWindows)
            } while ($currentWindows.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)
        }
        $operation = 'windows'
    }
    $windows = @(AppWindows)
    if ($operation -eq 'windows') {
        Emit @{windows=@($windows | ForEach-Object { @{handle=$_.Current.NativeWindowHandle; title=$_.Current.Name; process_id=$_.Current.ProcessId} })}
        exit
    }
    if ($arguments.window) { $windows = @($windows | Where-Object { $_.Current.NativeWindowHandle -eq $arguments.window }) }
    if ($windows.Count -ne 1) { throw 'Open the target app and use desktop_windows to choose one window belonging to it.' }
    $root = $windows[0]
    $handle = [IntPtr]$root.Current.NativeWindowHandle
    $bounds = $root.Current.BoundingRectangle

    function FocusTarget {
        [void][ConduitNative]::ShowWindow($handle, 9)
        [void][ConduitNative]::SetForegroundWindow($handle)
        Start-Sleep -Milliseconds 100
        if ([ConduitNative]::GetForegroundWindow() -ne $handle) { throw 'The app could not be focused. Bring it to the front and retry.' }
    }
    function MovePointer($point) {
        $b = $root.Current.BoundingRectangle
        if ($point.x -lt 0 -or $point.y -lt 0 -or $point.x -ge $b.Width -or $point.y -ge $b.Height) { throw 'Pointer is outside the target window.' }
        $p = New-Object ConduitNative+POINT
        $p.x = [int]($b.X + $point.x)
        $p.y = [int]($b.Y + $point.y)
        $hit = [ConduitNative]::GetAncestor([ConduitNative]::WindowFromPoint($p), 2)
        if ($hit -ne $handle) { throw 'Another window covers this coordinate. Bring the target app to the front.' }
        [void][ConduitNative]::SetCursorPos($p.x, $p.y)
    }

    if ($operation -eq 'screenshot') {
        $width = [int]$bounds.Width
        $height = [int]$bounds.Height
        if ($width -le 0 -or $height -le 0 -or $width * $height -gt 25000000) { throw 'Window size cannot be captured; restore or resize it.' }
        $bitmap = [Drawing.Bitmap]::new($width, $height)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $dc = $graphics.GetHdc()
        try { $captured = [ConduitNative]::PrintWindow($handle, $dc, 2) } finally { $graphics.ReleaseHdc($dc) }
        $stream = [IO.MemoryStream]::new()
        try {
            if (-not $captured) { throw 'This app does not support window capture.' }
            $bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
            Emit @{image_base64=[Convert]::ToBase64String($stream.ToArray()); mime_type='image/png'; width=$width; height=$height}
        } finally { $stream.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
        exit
    }
    if ($operation -eq 'press' -or $operation -eq 'type') {
        FocusTarget
        if ($operation -eq 'type') {
            $encoded = -join @(([string]$arguments.text).ToCharArray() | ForEach-Object {
                if ('+^%~()[]{}'.Contains([string]$_)) { '{' + $_ + '}' } elseif ($_ -eq "`n") { '{ENTER}' } elseif ($_ -eq "`r") { '' } else { [string]$_ }
            })
        } else {
            $parts = ([string]$arguments.key).Split('+')
            $prefix = ''
            foreach ($modifier in $parts[0..([Math]::Max(0,$parts.Length-2))]) {
                if ($parts.Length -gt 1) {
                    switch ($modifier.ToLower()) { 'control' {$prefix+='^'} 'ctrl' {$prefix+='^'} 'alt' {$prefix+='%'} 'shift' {$prefix+='+'} default {throw 'Supported modifiers are Control, Alt, and Shift.'} }
                }
            }
            $key = $parts[-1]
            $special = @{enter='ENTER'; delete='DELETE'; backspace='BACKSPACE'; tab='TAB'; escape='ESC'; esc='ESC'; home='HOME'; end='END'; arrowleft='LEFT'; arrowright='RIGHT'; arrowup='UP'; arrowdown='DOWN'; space=' '}
            if ($special.ContainsKey($key.ToLower())) { $encoded = $prefix + '{' + $special[$key.ToLower()] + '}' }
            elseif ($key -match '^[a-zA-Z0-9]$') { $encoded = $prefix + $key }
            elseif ($key -match '^F([1-9]|1[0-2])$') { $encoded = $prefix + '{' + $key + '}' }
            else { throw 'Unsupported shortcut. Use desktop_type for literal text.' }
        }
        [Windows.Forms.SendKeys]::SendWait($encoded)
        Emit @{ok=$true; action=$operation}
        exit
    }
    if ($operation -eq 'click_at' -or $operation -eq 'draw') {
        FocusTarget
        if ($operation -eq 'click_at') { $points = @($arguments) } else { $points = @($arguments.points) }
        # Validate every point before starting a stroke.
        foreach ($point in $points) {
            if ($point.x -lt 0 -or $point.y -lt 0 -or $point.x -ge $bounds.Width -or $point.y -ge $bounds.Height) { throw 'A point is outside the window.' }
        }
        MovePointer $points[0]
        [ConduitNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
        try {
            foreach ($point in $points) { MovePointer $point; Start-Sleep -Milliseconds 12 }
        } finally { [ConduitNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero) }
        Emit @{ok=$true; action=$operation}
        exit
    }

    $queue = [Collections.Generic.Queue[object]]::new()
    $queue.Enqueue($root)
    $elements = [Collections.Generic.List[object]]::new()
    $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
    while ($queue.Count -gt 0 -and $elements.Count -lt 500) {
        $element = $queue.Dequeue()
        $elements.Add($element)
        $child = $walker.GetFirstChild($element)
        while ($child -and ($queue.Count + $elements.Count) -lt 700) { $queue.Enqueue($child); $child=$walker.GetNextSibling($child) }
    }
    if ($operation -eq 'snapshot') {
        $controls = @()
        foreach ($element in $elements) {
            try {
                if ($element.Current.IsPassword -or $element.Current.IsOffscreen) { continue }
                $ref = [string]$root.Current.NativeWindowHandle + ':' + ($element.GetRuntimeId() -join '.')
                $patterns = @($element.GetSupportedPatterns() | ForEach-Object ProgrammaticName)
                $text = $null
                if ($patterns -contains 'TextPatternIdentifiers.Pattern') { $text = $element.GetCurrentPattern([Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(8000) }
                $controls += @{ref=$ref; name=$element.Current.Name; role=$element.Current.ControlType.ProgrammaticName; automation_id=$element.Current.AutomationId; enabled=$element.Current.IsEnabled; patterns=$patterns; text=$text}
            } catch {}
        }
        Emit @{window=$root.Current.NativeWindowHandle; title=$root.Current.Name; controls=$controls; truncated=($queue.Count -gt 0)}
        exit
    }
    $selected = @($elements | Where-Object { ([string]$root.Current.NativeWindowHandle + ':' + ($_.GetRuntimeId() -join '.')) -eq $arguments.ref })
    if ($selected.Count -ne 1) { throw 'Stale control reference. Call desktop_snapshot again.' }
    $element = $selected[0]
    if ($operation -eq 'fill') {
        $pattern = $null
        if (-not $element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { throw 'This control does not support setting a value. Focus it and use desktop_type.' }
        $pattern.SetValue([string]$arguments.text)
    } elseif ($operation -eq 'click') {
        $pattern = $null
        if ($element.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $pattern.Invoke() }
        elseif ($element.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) { $pattern.Select() }
        elseif ($element.TryGetCurrentPattern([Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) { $pattern.Toggle() }
        elseif ($element.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) { $pattern.Expand() }
        else { $element.SetFocus() }
    } else { throw 'Unknown desktop operation.' }
    Emit @{ok=$true; action=$operation}
} catch {
    Emit @{error=$_.Exception.Message}
    exit 1
}
