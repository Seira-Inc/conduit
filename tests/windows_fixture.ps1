Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$form = New-Object Windows.Forms.Form
$form.Text = 'Conduit isolated test pad'
$form.Size = [Drawing.Size]::new(560,310)
$form.StartPosition = 'CenterScreen'
$inputBox = New-Object Windows.Forms.TextBox
$inputBox.Location = [Drawing.Point]::new(20,20)
$inputBox.Size = [Drawing.Size]::new(500,30)
$inputBox.AccessibleName = 'Record title'
$outputLabel = New-Object Windows.Forms.Label
$outputLabel.Text = 'No record'
$outputLabel.Location = [Drawing.Point]::new(20,130)
$outputLabel.Size = [Drawing.Size]::new(500,50)
$create = New-Object Windows.Forms.Button
$create.Text = 'Create record'
$create.Location = [Drawing.Point]::new(20,70)
$create.Size = [Drawing.Size]::new(150,35)
$update = New-Object Windows.Forms.Button
$update.Text = 'Update record'
$update.Location = [Drawing.Point]::new(190,70)
$update.Size = [Drawing.Size]::new(150,35)
$delete = New-Object Windows.Forms.Button
$delete.Text = 'Delete record'
$delete.Location = [Drawing.Point]::new(360,70)
$delete.Size = [Drawing.Size]::new(150,35)
$create.Add_Click({ $outputLabel.Text = 'Record: ' + $inputBox.Text })
$update.Add_Click({ $outputLabel.Text = 'Updated: ' + $inputBox.Text })
$delete.Add_Click({ $outputLabel.Text = 'No record' })
$form.Controls.AddRange(@($inputBox,$outputLabel,$create,$update,$delete))
[void]$form.ShowDialog()

